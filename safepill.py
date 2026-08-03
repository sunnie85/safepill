import streamlit as st
import streamlit.components.v1 as components
import time
import re
import os
import unicodedata
import hashlib
import json
import io
import base64
import os
import sys
from datetime import datetime, time as dtime, timedelta
from supabase import create_client
from google import genai
from PIL import Image

# ---- Mới: kiểu dữ liệu cấu hình công cụ tìm kiếm (grounding) của Gemini, dùng để ----
# yêu cầu AI trích dẫn nguồn uy tín (Drugs.com, Dược thư Quốc gia VN...) khi trả lời.
try:
    from google.genai import types as genai_types
    GEMINI_SEARCH_GROUNDING_AVAILABLE = True
except ImportError:
    GEMINI_SEARCH_GROUNDING_AVAILABLE = False

# ---- Mới: thư viện cho QR khẩn cấp & biểu đồ tuân thủ ----
# Cần thêm vào requirements.txt: qrcode[pil], matplotlib
try:
    import qrcode
    QRCODE_AVAILABLE = True
except ImportError:
    QRCODE_AVAILABLE = False

try:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    MATPLOTLIB_AVAILABLE = True
except ImportError:
    MATPLOTLIB_AVAILABLE = False
# =====================================================================================
# 1. CẤU HÌNH TRANG
# =====================================================================================
st.set_page_config(
    page_title="SafePill – Trợ Lý Dược Phẩm Thông Minh",
    page_icon="💊",
    layout="wide",
    initial_sidebar_state="expanded"
)

# ---- Mới: áp dụng theme giao diện SafePill (teal-slate) ----
sys.path.append(os.path.dirname(os.path.abspath(__file__)))
from theme_snippet import apply_safepill_theme
apply_safepill_theme()

DISCLAIMER = (
    "⚠️ SafePill là công cụ hỗ trợ nhắc nhở & tra cứu thông tin thuốc, "
    "KHÔNG thay thế chẩn đoán hoặc chỉ định của bác sĩ/dược sĩ. "
    "Trong trường hợp khẩn cấp, vui lòng liên hệ cơ sở y tế gần nhất."
)
# =====================================================================================
# 2. KẾT NỐI DỊCH VỤ (Supabase + Gemini) - có kiểm tra lỗi rõ ràng
# =====================================================================================
def load_secrets():
    """
    Đọc cấu hình theo THỨ TỰ ƯU TIÊN sau, để Ban giám khảo chỉ cần điền trực tiếp vào
    file appsettings/appsettings.json (đổi tên từ appsettings.example.json) mà KHÔNG
    cần tạo thêm bất kỳ file .streamlit/secrets.toml nào:
      1) File appsettings/appsettings.json nằm cùng cấp với file .py này
      2) st.secrets — dùng khi triển khai trên Streamlit Community Cloud
    """
    config = {}
    json_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "appsettings", "appsettings.json")
    if os.path.exists(json_path):
        try:
            with open(json_path, "r", encoding="utf-8") as f:
                config = json.load(f)
        except Exception as e:
            st.error(f"❌ Không đọc được file appsettings/appsettings.json (sai định dạng JSON?): {e}")
            st.stop()

    def get_value(key):
        val = config.get(key)
        # Bỏ qua nếu vẫn còn là placeholder dạng "Điền ... vào đây" chưa được điền thật
        if val and "Điền" not in str(val) and str(val).strip():
            return val
        if key in st.secrets:
            return st.secrets[key]
        return None

    keys = ("SUPABASE_URL", "SUPABASE_KEY", "GEMINI_KEY")
    values = {k: get_value(k) for k in keys}
    missing = [k for k, v in values.items() if not v]
    if missing:
        st.error(
            f"❌ Thiếu cấu hình: {', '.join(missing)}. "
            f"Hãy mở file appsettings/appsettings.json (đổi tên từ appsettings.example.json, "
            f"đặt cùng thư mục với safepill.py) và điền đầy đủ URL/Key vào đó, rồi chạy lại ứng dụng."
        )
        st.stop()
    return values["SUPABASE_URL"], values["SUPABASE_KEY"], values["GEMINI_KEY"]


@st.cache_resource(show_spinner=False)
def init_connections():
    url, key, gemini_key = load_secrets()
    sb_client = create_client(url, key)
    ai_client = genai.Client(api_key=gemini_key)
    return sb_client, ai_client


try:
    supabase, ai_gemini = init_connections()
    SERVICES_OK = True
except Exception as e:
    SERVICES_OK = False
    st.error(f"❌ Không thể khởi tạo kết nối dịch vụ: {e}")
    st.stop()

TABLE = "thuy_tien"
# Bảng 'thuy_tien': id, phone, full_name, pin, health_tree_score, face_data, face_hash, blood_type, diagnostic
# Cột 'diagnostic' dùng để lưu tủ thuốc (kèm nơi khám/bác sĩ/nơi cấp thuốc) dưới dạng chuỗi JSON,
# nhờ đó dữ liệu không còn bị mất khi tải lại trang hoặc đăng xuất.
#
# ---- QUAN TRỌNG: đảm bảo 1 số điện thoại chỉ đăng ký được 1 tài khoản ----
# Ứng dụng đã kiểm tra trùng số điện thoại ở tầng code (xem phone_already_registered() và
# submit_reg bên dưới), nhưng để chống trường hợp 2 người bấm "Đăng ký" gần như đồng thời
# (race condition), NÊN thêm ràng buộc UNIQUE ngay trên cột 'phone' tại Supabase:
#   -- Bước 1: kiểm tra xem đã có số điện thoại trùng nhau trong bảng chưa
#   select phone, count(*) from thuy_tien group by phone having count(*) > 1;
#   -- Bước 2 (nếu bước 1 không trả về dòng nào): thêm ràng buộc duy nhất
#   alter table thuy_tien add constraint thuy_tien_phone_unique unique (phone);
# Nếu bước 1 phát hiện dữ liệu trùng sẵn có, cần xử lý (xoá/gộp) các bản ghi trùng trước khi
# chạy bước 2, nếu không lệnh ALTER TABLE sẽ báo lỗi.

# ---- Mới: bảng phục vụ tính năng "Nhắc nhở từ người thân" ----
# Cần tạo 2 bảng này bằng SQL migration trên Supabase trước khi dùng (nếu chưa có sẵn):
#
# create table safepill_family_links (
#     id bigserial primary key,
#     owner_phone text not null,        -- người được theo dõi (chủ tủ thuốc)
#     member_phone text not null,       -- người thân được phép gửi nhắc nhở
#     member_name text,
#     status text default 'pending',    -- 'pending' | 'accepted' | 'declined'
#     created_at timestamptz default now()
# );
#
# create table safepill_family_reminders (
#     id bigserial primary key,
#     owner_phone text not null,        -- người sẽ nhận nhắc nhở
#     sender_phone text,
#     sender_name text,
#     message text not null,
#     target_time text,                 -- 'HH:MM' hoặc NULL nếu gửi ngay lập tức
#     delivered boolean default false,
#     created_at timestamptz default now()
# );
FAMILY_LINKS_TABLE = "safepill_family_links"
FAMILY_REMINDERS_TABLE = "safepill_family_reminders"

# ---- Mới: bảng lưu lịch sử tuân thủ điều trị theo ngày (phục vụ biểu đồ & xuất báo cáo) ----
# create table safepill_adherence_history (
#     id bigserial primary key,
#     owner_phone text not null,
#     log_date date not null,
#     total_tasks int default 0,
#     done_tasks int default 0,
#     rate numeric default 0,
#     created_at timestamptz default now(),
#     unique (owner_phone, log_date)
# );
ADHERENCE_HISTORY_TABLE = "safepill_adherence_history"


# =====================================================================================
# 3. HÀM TIỆN ÍCH BẢO MẬT & XỬ LÝ DỮ LIỆU
# =====================================================================================
def hash_pin(pin: str) -> str:
    """Băm mã PIN bằng SHA-256 trước khi lưu trữ, không bao giờ lưu PIN dạng thô."""
    return hashlib.sha256(pin.strip().encode("utf-8")).hexdigest()


def verify_pin(entered_pin: str, stored_value: str) -> bool:
    """
    Hỗ trợ tương thích ngược: nếu bản ghi cũ còn lưu PIN dạng thô (4 ký tự),
    vẫn so khớp được; bản ghi mới (đã băm SHA-256 dài 64 ký tự) so khớp theo hash.
    """
    if not stored_value:
        return False
    stored_value = str(stored_value).strip()
    if len(stored_value) == 64:
        return hash_pin(entered_pin) == stored_value
    return str(entered_pin).strip() == stored_value


def validate_phone(phone: str) -> bool:
    return bool(re.match(r"^0\d{9}$", phone.strip()))


def phone_already_registered(phone: str) -> bool:
    """
    MỚI — Kiểm tra ở tầng ứng dụng xem số điện thoại đã có tài khoản hay chưa, TRƯỚC khi insert.
    Đây là lớp bảo vệ chính (không phụ thuộc vào việc bảng có ràng buộc UNIQUE hay không); nên
    kết hợp thêm ràng buộc UNIQUE(phone) ở Supabase (xem ghi chú tại phần khai báo TABLE) để chống
    trường hợp 2 yêu cầu đăng ký gửi lên gần như đồng thời (race condition).
    """
    try:
        res = supabase.table(TABLE).select("phone").eq("phone", phone.strip()).limit(1).execute()
        return bool(res.data)
    except Exception:
        # Nếu không kiểm tra được (lỗi kết nối...), vẫn để luồng insert phía sau tự bắt lỗi
        # trùng khoá (nếu bảng có UNIQUE constraint) thay vì chặn cứng người dùng.
        return False


# ---- Mới: danh sách nhóm máu để lưu vào hồ sơ, phục vụ cấp cứu khẩn cấp ----
BLOOD_TYPE_OPTIONS = ["Chưa rõ", "A+", "A-", "B+", "B-", "AB+", "AB-", "O+", "O-"]


def average_hash(image_bytes: bytes, hash_size: int = 8) -> str:
    """
    Perceptual hash (aHash) đơn giản dùng để đối chiếu ảnh khuôn mặt ở mức demo.
    Đây KHÔNG phải nhận diện khuôn mặt sinh trắc học thật sự (không dùng embedding
    khuôn mặt chuyên dụng như FaceNet/Dlib) — phù hợp cho mục đích minh họa/khoa học
    kỹ thuật, và cần nâng cấp lên thư viện nhận diện khuôn mặt chuyên dụng khi triển
    khai thực tế.
    """
    try:
        img = Image.open(io.BytesIO(image_bytes)).convert("L").resize((hash_size, hash_size))
        pixels = list(img.getdata())
        avg = sum(pixels) / len(pixels)
        return "".join("1" if p > avg else "0" for p in pixels)
    except Exception:
        return ""


def hamming_distance(hash1: str, hash2: str) -> int:
    if not hash1 or not hash2 or len(hash1) != len(hash2):
        return 999
    return sum(c1 != c2 for c1, c2 in zip(hash1, hash2))


def extract_json_array(raw_text: str):
    """Trích xuất mảng JSON từ phản hồi AI dù có lẫn văn bản/markdown thừa."""
    cleaned = raw_text.strip()
    cleaned = re.sub(r"^```json", "", cleaned, flags=re.IGNORECASE).strip()
    cleaned = cleaned.replace("```", "").strip()
    match = re.search(r"\[.*\]", cleaned, re.DOTALL)
    if match:
        cleaned = match.group(0)
    return json.loads(cleaned)


def resolve_reminder_time(thoi_diem: str) -> str:
    """Chuyển 'Sáng/Trưa/Tối' hoặc giờ cụ thể (HH:MM) thành giờ HH:MM để đặt nhắc nhở."""
    if not thoi_diem:
        return "08:00"
    text = str(thoi_diem).strip()
    m = re.search(r"(\d{1,2}):(\d{2})", text)
    if m:
        h, mi = int(m.group(1)), int(m.group(2))
        if 0 <= h <= 23 and 0 <= mi <= 59:
            return f"{h:02d}:{mi:02d}"
    text_lower = text.lower()
    if "sáng" in text_lower:
        return "07:00"
    if "trưa" in text_lower:
        return "12:00"
    if "chiều" in text_lower:
        return "17:00"
    if "tối" in text_lower or "đêm" in text_lower:
        return "19:00"
    return "08:00"


# Cơ sở dữ liệu tương tác thuốc lâm sàng (khai báo 1 chiều, hệ thống tự đối chiếu 2 chiều)
# ---- MỚI: mỗi cặp tương tác có thêm trường "nguon" ghi rõ tài liệu tham khảo uy tín đã
# đối chiếu (Dược thư Quốc gia Việt Nam, Drugs.com Interaction Checker...) để tăng độ tin cậy
# và minh bạch nguồn gốc thông tin. Khuyến nghị: trước khi triển khai thực tế, nhóm thực hiện
# nên phối hợp dược sĩ rà soát lại toàn bộ dữ liệu, đối chiếu trực tiếp với ấn bản mới nhất của
# Dược thư Quốc gia Việt Nam và cơ sở dữ liệu Drugs.com/Lexicomp trước khi dùng cho mục đích lâm sàng.
DEFAULT_SOURCE_NOTE = "Dược thư Quốc gia Việt Nam; Drugs.com Interaction Checker"

INTERACTION_DATABASE = {
    "Aspirin": {"conflict": ["Ibuprofen", "Warfarin", "Naproxen", "Clopidogrel"],
                "severity": "Cao", "effect": "Tăng nguy cơ xuất huyết tiêu hóa nghiêm trọng.",
                "nguon": "Dược thư Quốc gia Việt Nam; Drugs.com (Aspirin Interactions)"},
    "Ibuprofen": {"conflict": ["Aspirin", "Corticoid", "Enalapril", "Losartan", "Furosemide"],
                  "severity": "Cao", "effect": "Giảm hiệu quả hạ huyết áp, tăng độc tính thận.",
                  "nguon": "Dược thư Quốc gia Việt Nam; Drugs.com (Ibuprofen Interactions)"},
    "Paracetamol": {"conflict": ["Alcohol", "Leflunomide", "Warfarin"],
                    "severity": "Trung bình", "effect": "Tăng độc tính và nguy cơ hủy hoại tế bào gan.",
                    "nguon": "Dược thư Quốc gia Việt Nam; Drugs.com (Acetaminophen Interactions)"},
    "Metformin": {"conflict": ["Contrast dye", "Cimetidine", "Alcohol"],
                  "severity": "Nghiêm trọng", "effect": "Tăng nguy cơ nhiễm toan lactic cấp tính.",
                  "nguon": "Dược thư Quốc gia Việt Nam; Drugs.com (Metformin Interactions)"},
    "Warfarin": {"conflict": ["Aspirin", "Paracetamol", "Ibuprofen", "Amiodarone"],
                 "severity": "Nghiêm trọng", "effect": "Tăng nguy cơ chảy máu do tăng tác dụng chống đông.",
                 "nguon": "Dược thư Quốc gia Việt Nam; Drugs.com (Warfarin Interactions)"},
    "Simvastatin": {"conflict": ["Amiodarone", "Clarithromycin", "Grapefruit juice"],
                    "severity": "Cao", "effect": "Tăng nguy cơ tiêu cơ vân (rhabdomyolysis).",
                    "nguon": "Dược thư Quốc gia Việt Nam; Drugs.com (Simvastatin Interactions)"},
    "Losartan": {"conflict": ["Ibuprofen", "Potassium", "Spironolactone"],
                 "severity": "Trung bình", "effect": "Tăng kali máu, giảm hiệu quả hạ áp.",
                 "nguon": "Dược thư Quốc gia Việt Nam; Drugs.com (Losartan Interactions)"},
    "Digoxin": {"conflict": ["Furosemide", "Amiodarone"],
                "severity": "Nghiêm trọng", "effect": "Tăng nguy cơ ngộ độc digoxin, rối loạn nhịp tim.",
                "nguon": "Dược thư Quốc gia Việt Nam; Drugs.com (Digoxin Interactions)"},
    "Clopidogrel": {"conflict": ["Aspirin", "Omeprazole"],
                    "severity": "Trung bình", "effect": "Giảm hiệu quả chống kết tập tiểu cầu.",
                    "nguon": "Dược thư Quốc gia Việt Nam; Drugs.com (Clopidogrel Interactions)"},
}


def build_symmetric_lookup(db: dict) -> dict:
    """Đảm bảo tra cứu được cả 2 chiều A→B và B→A dù dữ liệu chỉ khai báo 1 chiều."""
    lookup = {k: dict(v) for k, v in db.items()}
    for drug, info in db.items():
        for other in info["conflict"]:
            if other not in lookup:
                lookup[other] = {"conflict": [], "severity": info["severity"], "effect": info["effect"],
                                  "nguon": info.get("nguon", DEFAULT_SOURCE_NOTE)}
            if drug not in lookup[other]["conflict"]:
                lookup[other]["conflict"].append(drug)
    return lookup


INTERACTION_LOOKUP = build_symmetric_lookup(INTERACTION_DATABASE)


def check_interaction(drug_a: str, drug_b: str):
    a, b = drug_a.strip().capitalize(), drug_b.strip().capitalize()
    info = INTERACTION_LOOKUP.get(a)
    if info and b in info["conflict"]:
        return {"thuoc_1": a, "thuoc_2": b, "severity": info["severity"], "effect": info["effect"],
                "nguon": info.get("nguon", DEFAULT_SOURCE_NOTE)}
    return None


def scan_cabinet_for_conflicts(med_data: list) -> list:
    names = [m.get("Tên thuốc", "").strip().capitalize() for m in med_data if m.get("Tên thuốc")]
    conflicts = []
    for i in range(len(names)):
        for j in range(i + 1, len(names)):
            result = check_interaction(names[i], names[j])
            if result:
                conflicts.append(result)
    return conflicts


# =====================================================================================
# 3A2. MỚI: TƯƠNG TÁC THUỐC – THỰC PHẨM / THẢO DƯỢC KIỂU VIỆT NAM
# =====================================================================================
# Cơ sở dữ liệu minh họa các cảnh báo phối hợp thuốc với rượu bia, bưởi, thuốc nam,
# thực phẩm chức năng... phổ biến ở Việt Nam nhưng thường bị các app quốc tế bỏ qua.
# Mỗi mục cũng có trường "nguon" ghi tài liệu tham khảo uy tín tương ứng.
VN_FOOD_HERB_DATABASE = {
    "Paracetamol": [
        {"item": "Rượu bia", "severity": "Cao",
         "effect": "Tăng nguy cơ tổn thương gan cấp tính, đặc biệt khi dùng liều cao kéo dài.",
         "nguon": "Drugs.com (Acetaminophen + Alcohol); Dược thư Quốc gia Việt Nam"},
    ],
    "Metformin": [
        {"item": "Rượu bia", "severity": "Nghiêm trọng",
         "effect": "Tăng nguy cơ nhiễm toan lactic, có thể đe dọa tính mạng.",
         "nguon": "Drugs.com (Metformin + Alcohol); Dược thư Quốc gia Việt Nam"},
    ],
    "Warfarin": [
        {"item": "Rau càng cua / rau ngót / cải xoăn (nhiều vitamin K)", "severity": "Trung bình",
         "effect": "Giảm tác dụng chống đông máu, tăng nguy cơ hình thành cục máu đông.",
         "nguon": "Drugs.com (Warfarin + Vitamin K foods); Dược thư Quốc gia Việt Nam"},
        {"item": "Thuốc nam / thực phẩm chức năng (đương quy, nhân sâm, tỏi cô đặc...)", "severity": "Cao",
         "effect": "Có thể tăng hoặc giảm tác dụng chống đông không kiểm soát, tăng nguy cơ chảy máu.",
         "nguon": "Drugs.com (Warfarin herbal interactions); khuyến cáo Bệnh viện Bạch Mai"},
    ],
    "Simvastatin": [
        {"item": "Nước ép bưởi / bưởi", "severity": "Cao",
         "effect": "Tăng nồng độ thuốc trong máu, tăng nguy cơ tiêu cơ vân (rhabdomyolysis).",
         "nguon": "Drugs.com (Simvastatin + Grapefruit); FDA Consumer Update"},
    ],
    "Aspirin": [
        {"item": "Rượu bia", "severity": "Cao",
         "effect": "Tăng nguy cơ xuất huyết tiêu hóa.",
         "nguon": "Drugs.com (Aspirin + Alcohol); Dược thư Quốc gia Việt Nam"},
        {"item": "Gừng, tỏi cô đặc (thực phẩm chức năng liều cao)", "severity": "Trung bình",
         "effect": "Tăng tác dụng chống kết tập tiểu cầu, tăng nguy cơ chảy máu.",
         "nguon": "Drugs.com (Aspirin herbal interactions)"},
    ],
    "Digoxin": [
        {"item": "Cam thảo (thuốc nam)", "severity": "Cao",
         "effect": "Gây hạ kali máu, tăng nguy cơ ngộ độc digoxin.",
         "nguon": "Drugs.com (Digoxin + Licorice); Dược thư Quốc gia Việt Nam"},
    ],
    "Clopidogrel": [
        {"item": "Rượu bia", "severity": "Trung bình",
         "effect": "Tăng nguy cơ kích ứng và chảy máu đường tiêu hóa.",
         "nguon": "Drugs.com (Clopidogrel + Alcohol)"},
    ],
}


def check_food_herb_conflicts(med_data: list) -> list:
    """Đối chiếu từng thuốc trong tủ thuốc với danh sách thực phẩm/thảo dược VN cần tránh phối hợp."""
    results = []
    for m in med_data:
        name = m.get("Tên thuốc", "").strip().capitalize()
        if name in VN_FOOD_HERB_DATABASE:
            for warn in VN_FOOD_HERB_DATABASE[name]:
                results.append({"thuoc": name, **warn})
    return results


def _strip_accents(text: str) -> str:
    """Bỏ dấu tiếng Việt để so khớp linh hoạt (VD: 'rượu' ~ 'ruou')."""
    normalized = unicodedata.normalize("NFD", text)
    without_marks = "".join(ch for ch in normalized if unicodedata.category(ch) != "Mn")
    return without_marks.replace("đ", "d").replace("Đ", "D")


def _fuzzy_food_item_match(user_input: str, item_field: str) -> bool:
    """
    So khớp linh hoạt giữa nội dung người dùng gõ (VD: "rượu", "bưởi") và trường "item"
    trong VN_FOOD_HERB_DATABASE (VD: "Rượu bia", "Nước ép bưởi / bưởi") — không phân biệt
    hoa/thường, không phân biệt dấu, và tách theo từng cụm ngăn cách bởi "/" hoặc ",".
    """
    if not user_input or not user_input.strip():
        return False
    ui = _strip_accents(user_input).strip().lower()
    for part in re.split(r"[/,]", item_field):
        p = _strip_accents(part).strip().lower()
        # Bỏ phần chú thích trong ngoặc để so khớp gọn hơn, VD: "Thuốc nam (đương quy...)"
        p_main = re.sub(r"\(.*?\)", "", p).strip()
        if not p_main:
            continue
        if ui in p_main or p_main in ui:
            return True
    return False


def check_food_herb_pair(input_a: str, input_b: str) -> list:
    """
    MỚI — Dùng cho công cụ tra cứu thủ công (tab "Tra cứu tương tác"): người dùng có thể gõ
    một thuốc và MỘT THỰC PHẨM/THẢO DƯỢC (VD: Aspirin + rượu) chứ không chỉ hai tên thuốc.
    check_interaction() chỉ tra trong INTERACTION_DATABASE (thuốc–thuốc) nên trước đây bỏ sót
    hoàn toàn các cặp thuốc–thực phẩm dù VN_FOOD_HERB_DATABASE đã có sẵn dữ liệu. Hàm này đối
    chiếu CẢ HAI CHIỀU nhập liệu với VN_FOOD_HERB_DATABASE để không bỏ sót cảnh báo.
    """
    results = []
    a_clean, b_clean = input_a.strip(), input_b.strip()
    a_key, b_key = a_clean.capitalize(), b_clean.capitalize()

    # Chiều 1: A là thuốc có trong CSDL, B là thực phẩm/thảo dược khớp với ô nhập
    if a_key in VN_FOOD_HERB_DATABASE:
        for warn in VN_FOOD_HERB_DATABASE[a_key]:
            if _fuzzy_food_item_match(b_clean, warn["item"]):
                results.append({"thuoc": a_key, **warn})

    # Chiều 2: B là thuốc có trong CSDL, A là thực phẩm/thảo dược khớp với ô nhập
    if b_key in VN_FOOD_HERB_DATABASE:
        for warn in VN_FOOD_HERB_DATABASE[b_key]:
            if _fuzzy_food_item_match(a_clean, warn["item"]):
                results.append({"thuoc": b_key, **warn})

    return results


# =====================================================================================
# 3B. HÀM TIỆN ÍCH: NHẮC NHỞ TỪ NGƯỜI THÂN (Supabase)
# =====================================================================================
FAMILY_TABLE_MISSING_MSG = (
    "⚠️ Chưa thể dùng tính năng người thân vì cơ sở dữ liệu chưa có bảng "
    "'safepill_family_links' / 'safepill_family_reminders'. Hãy chạy migration SQL "
    "(xem ghi chú phía trên phần khai báo TABLE trong code) rồi tải lại trang."
)


def _is_missing_table_error(err) -> bool:
    """
    SỬA LỖI — Trước đây chỉ nhận diện lỗi thiếu bảng qua 2 cụm từ "relation" và
    "does not exist" (kiểu lỗi PostgreSQL thô), nên khi Supabase/PostgREST trả về lỗi theo
    định dạng khác — VD: {"message": "Could not find the table 'public.safepill_family_reminders'
    in the schema cache", "code": "PGRST205", ...} — code không nhận ra, khiến người dùng thấy
    thông báo lỗi kỹ thuật khó hiểu thay vì hướng dẫn chạy migration SQL rõ ràng. Hàm này gộp
    thêm các dấu hiệu phổ biến của PostgREST khi bảng chưa tồn tại.
    """
    text = str(err).lower()
    signals = (
        "relation", "does not exist", "could not find the table",
        "schema cache", "pgrst205", "42p01",
    )
    return any(sig in text for sig in signals)


def create_family_invite(owner_phone: str, member_phone: str, member_name: str = "") -> tuple:
    """Chủ tủ thuốc (owner) mời một số điện thoại người thân (member) theo dõi/nhắc nhở mình."""
    try:
        supabase.table(FAMILY_LINKS_TABLE).insert({
            "owner_phone": owner_phone,
            "member_phone": member_phone.strip(),
            "member_name": member_name.strip(),
            "status": "pending",
        }).execute()
        return True, None
    except Exception as e:
        return False, str(e)


def fetch_family_members(owner_phone: str) -> list:
    """Danh sách người thân đã liên kết (mọi trạng thái) với owner_phone."""
    try:
        res = supabase.table(FAMILY_LINKS_TABLE).select("*").eq("owner_phone", owner_phone).execute()
        return res.data or []
    except Exception:
        return []


def fetch_pending_invites_for_member(member_phone: str) -> list:
    """Lời mời đang chờ chính người dùng (với vai trò người thân) phê duyệt."""
    try:
        res = (supabase.table(FAMILY_LINKS_TABLE).select("*")
               .eq("member_phone", member_phone).eq("status", "pending").execute())
        return res.data or []
    except Exception:
        return []


def fetch_owners_i_help(member_phone: str) -> list:
    """Danh sách chủ tủ thuốc mà người dùng hiện tại (với vai trò người thân) đã được chấp nhận theo dõi."""
    try:
        res = (supabase.table(FAMILY_LINKS_TABLE).select("*")
               .eq("member_phone", member_phone).eq("status", "accepted").execute())
        return res.data or []
    except Exception:
        return []


def update_family_link_status(link_id, new_status: str) -> tuple:
    try:
        supabase.table(FAMILY_LINKS_TABLE).update({"status": new_status}).eq("id", link_id).execute()
        return True, None
    except Exception as e:
        return False, str(e)


def delete_family_link(link_id) -> tuple:
    try:
        supabase.table(FAMILY_LINKS_TABLE).delete().eq("id", link_id).execute()
        return True, None
    except Exception as e:
        return False, str(e)


def send_family_reminder(owner_phone: str, sender_phone: str, sender_name: str,
                          message: str, target_time: str = None) -> tuple:
    """Người thân gửi một nhắc nhở đến owner_phone (gửi ngay nếu target_time=None)."""
    try:
        supabase.table(FAMILY_REMINDERS_TABLE).insert({
            "owner_phone": owner_phone,
            "sender_phone": sender_phone,
            "sender_name": sender_name,
            "message": message.strip(),
            "target_time": target_time,
            "delivered": False,
        }).execute()
        return True, None
    except Exception as e:
        return False, str(e)


def fetch_due_family_reminders(owner_phone: str) -> list:
    """
    Lấy các nhắc nhở của người thân dành cho owner_phone mà CHƯA hiển thị, và đã đến hạn:
    - target_time = None  → hiển thị ngay khi có (gửi tức thời)
    - target_time = 'HH:MM' → chỉ hiển thị khi giờ hiện tại đã khớp hoặc trễ hơn giờ đó trong ngày
    """
    try:
        res = (supabase.table(FAMILY_REMINDERS_TABLE).select("*")
               .eq("owner_phone", owner_phone).eq("delivered", False).execute())
        rows = res.data or []
    except Exception:
        return []
    now_hhmm = datetime.now().strftime("%H:%M")
    due = []
    for row in rows:
        t = row.get("target_time")
        if not t or t <= now_hhmm:
            due.append(row)
    return due


def mark_family_reminder_delivered(reminder_id) -> None:
    try:
        supabase.table(FAMILY_REMINDERS_TABLE).update({"delivered": True}).eq("id", reminder_id).execute()
    except Exception:
        pass


def send_escalation_alert_to_family(owner_phone: str, owner_name: str, drug_name: str, miss_count: int) -> list:
    """
    MỚI — Cảnh báo leo thang tự động: khi người dùng bỏ lỡ liên tiếp một thuốc mức độ
    nghiêm trọng, tự động gửi cảnh báo tới TẤT CẢ người thân đã 'accepted' của owner_phone.
    Tái sử dụng bảng safepill_family_reminders, chỉ đảo ngược chiều: owner_phone trong
    bảng lúc này là số điện thoại của NGƯỜI THÂN (người nhận cảnh báo).
    """
    members = fetch_family_members(owner_phone)
    accepted = [m for m in members if m.get("status") == "accepted"]
    sent_to = []
    alert_msg = (f"🚨 CẢNH BÁO: {owner_name or owner_phone} đã bỏ lỡ {miss_count} lần liên tiếp "
                 f"thuốc '{drug_name}' (mức độ nghiêm trọng cao). Vui lòng gọi điện hỏi thăm ngay!")
    for member in accepted:
        member_phone = member.get("member_phone")
        if not member_phone:
            continue
        ok, _ = send_family_reminder(
            owner_phone=member_phone,
            sender_phone=owner_phone,
            sender_name=f"{owner_name or owner_phone} (Cảnh báo tự động SafePill)",
            message=alert_msg,
            target_time=None,
        )
        if ok:
            sent_to.append(member_phone)
    return sent_to


def record_missed_dose(drug_name: str, severity: str) -> int:
    """Tăng bộ đếm bỏ lỡ liên tiếp cho một thuốc; trả về số lần bỏ lỡ liên tiếp hiện tại."""
    st.session_state.missed_streak[drug_name] = st.session_state.missed_streak.get(drug_name, 0) + 1
    return st.session_state.missed_streak[drug_name]


def reset_missed_dose(drug_name: str) -> None:
    st.session_state.missed_streak[drug_name] = 0


# ---- Mới: tự động cảnh báo người thân nếu SAU 30 PHÚT kể từ giờ hẹn mà vẫn chưa uống ----
AUTO_ESCALATION_MINUTES = 30


def build_adherence_task_key(drug_name: str, hhmm: str, med_obj) -> str:
    """
    Sinh key nhắc nhở DUY NHẤT và NHẤT QUÁN cho một task (thuốc + giờ hẹn), dùng chung
    giữa tab "Hôm nay" và bộ kiểm tra tự động cảnh báo quá giờ. TRƯỚC ĐÂY 2 nơi build key
    khác công thức nhau (1 nơi có kèm id(med_obj), 1 nơi không) khiến hàm tự động cảnh báo
    luôn tra ra "chưa uống" dù người dùng đã tick "Đã uống", gây báo động giả liên tục cho
    người thân. Nay gộp về DUY NHẤT một hàm để tránh lệch key.
    """
    return f"task_{drug_name}_{hhmm}_{id(med_obj)}"


def check_and_auto_escalate_overdue_doses(med_data_valid: list) -> list:
    """
    Rà soát các thuốc trong lịch hôm nay: nếu đã quá AUTO_ESCALATION_MINUTES phút kể từ giờ hẹn
    mà vẫn CHƯA được đánh dấu 'Đã uống', tự động gửi cảnh báo tới toàn bộ người thân đã 'accepted'
    (không cần người dùng chủ động bấm "Bỏ lỡ"). Mỗi task chỉ cảnh báo tự động MỘT LẦN (đánh dấu
    trong st.session_state.auto_escalated_keys) để tránh gửi trùng lặp liên tục.

    Lưu ý kỹ thuật: hàm này chỉ được đánh giá lại mỗi khi ứng dụng Streamlit thực thi lại kịch bản
    (người dùng thao tác, chuyển tab, hoặc tải lại trang). Vì Streamlit không có tiến trình nền,
    đây KHÔNG phải cơ chế giám sát 24/7 thực sự — nếu người dùng đóng hẳn ứng dụng, cảnh báo sẽ chỉ
    được gửi khi có ai đó mở lại ứng dụng sau mốc 30 phút. Để giám sát nền thực sự trong sản phẩm
    chính thức, nên triển khai thêm một tác vụ định kỳ phía máy chủ (ví dụ: Supabase Edge Function
    chạy theo lịch cron) độc lập với phiên làm việc của trình duyệt.
    """
    now = datetime.now()
    newly_escalated = []
    for med in med_data_valid:
        drug_name = med.get("Tên thuốc", "")
        hhmm = resolve_reminder_time(med.get("Thời điểm", ""))
        # SỬA LỖI: dùng đúng cùng công thức key với tab "Hôm nay" (build_adherence_task_key),
        # trước đây key ở đây thiếu id(med) nên không bao giờ khớp với trạng thái đã tick.
        key_name = build_adherence_task_key(drug_name, hhmm, med)
        if st.session_state.adherence_logs.get(key_name, False):
            continue  # đã uống rồi, không cần cảnh báo
        if key_name in st.session_state.auto_escalated_keys:
            continue  # đã cảnh báo tự động cho task này rồi
        try:
            h, mi = map(int, hhmm.split(":"))
            scheduled_dt = now.replace(hour=h, minute=mi, second=0, microsecond=0)
        except Exception:
            continue
        minutes_overdue = (now - scheduled_dt).total_seconds() / 60
        if minutes_overdue >= AUTO_ESCALATION_MINUTES:
            sent_to = send_escalation_alert_to_family(
                st.session_state.user_phone,
                st.session_state.current_profile.get("full_name", ""),
                f"{drug_name} (giờ hẹn {hhmm}, đã quá {AUTO_ESCALATION_MINUTES} phút chưa xác nhận uống)",
                1,
            )
            st.session_state.auto_escalated_keys.add(key_name)
            if sent_to:
                newly_escalated.append({"drug": drug_name, "time": hhmm, "sent_to": len(sent_to)})
    return newly_escalated


# ---- Mới: nhắc "sắp hết thuốc" dựa trên số lượng còn lại ----
LOW_STOCK_THRESHOLD = 5  # còn <= 5 liều thì cảnh báo sắp hết


def decrement_med_quantity(med_idx: int, task_key: str) -> None:
    """
    Trừ 1 đơn vị khỏi 'Số lượng còn lại' của thuốc khi được đánh dấu 'Đã uống',
    tránh trừ 2 lần cho cùng 1 task trong cùng 1 lần tick (dùng cờ đánh dấu theo task_key).
    """
    already = st.session_state.qty_decremented.get(task_key, False)
    if already:
        return
    med = st.session_state.med_data[med_idx]
    qty = med.get("Số lượng còn lại")
    if qty is not None:
        try:
            qty_val = int(qty)
            med["Số lượng còn lại"] = max(0, qty_val - 1)
        except (TypeError, ValueError):
            pass
    st.session_state.qty_decremented[task_key] = True


def restore_med_quantity(med_idx: int, task_key: str) -> None:
    """Hoàn tác trừ số lượng nếu người dùng bỏ tick 'Đã uống'."""
    if not st.session_state.qty_decremented.get(task_key, False):
        return
    med = st.session_state.med_data[med_idx]
    qty = med.get("Số lượng còn lại")
    if qty is not None:
        try:
            qty_val = int(qty)
            med["Số lượng còn lại"] = qty_val + 1
        except (TypeError, ValueError):
            pass
    st.session_state.qty_decremented[task_key] = False


# ---- Mới: lịch sử tuân thủ điều trị (biểu đồ + xuất báo cáo) ----
def log_adherence_snapshot(owner_phone: str, total_tasks: int, done_tasks: int) -> tuple:
    """Ghi/':cập nhật (upsert) tỷ lệ tuân thủ của HÔM NAY vào Supabase để dựng biểu đồ theo thời gian."""
    if total_tasks == 0:
        return True, None
    rate = round((done_tasks / total_tasks) * 100, 1)
    today_str = datetime.now().strftime("%Y-%m-%d")
    try:
        supabase.table(ADHERENCE_HISTORY_TABLE).upsert({
            "owner_phone": owner_phone,
            "log_date": today_str,
            "total_tasks": total_tasks,
            "done_tasks": done_tasks,
            "rate": rate,
        }, on_conflict="owner_phone,log_date").execute()
        return True, None
    except Exception as e:
        return False, str(e)


def fetch_adherence_history(owner_phone: str, days: int = 30) -> list:
    try:
        since = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
        res = (supabase.table(ADHERENCE_HISTORY_TABLE).select("*")
               .eq("owner_phone", owner_phone).gte("log_date", since)
               .order("log_date").execute())
        return res.data or []
    except Exception:
        return []


ADHERENCE_HISTORY_MISSING_MSG = (
    "⚠️ Chưa thể lưu/hiển thị lịch sử tuân thủ vì cơ sở dữ liệu chưa có bảng "
    "'safepill_adherence_history'. Hãy chạy migration SQL (xem ghi chú tại phần khai báo "
    "ADHERENCE_HISTORY_TABLE trong code) rồi tải lại trang."
)


# ---- Mới: thẻ QR khẩn cấp ----
def build_emergency_qr_text(profile: dict, med_data: list, conflicts: list, family_members: list) -> str:
    """Dựng nội dung văn bản gọn gàng để mã hoá vào QR khẩn cấp."""
    lines = [
        "=== SAFEPILL - THE KHAN CAP ===",
        f"Ho ten: {profile.get('full_name', 'N/A')}",
        f"SDT: {profile.get('phone', 'N/A')}",
        f"Nhom mau: {profile.get('blood_type') or 'Chua ro'}",
        "--- Danh sach thuoc dang dung ---",
    ]
    
    valid_meds = [m for m in med_data if m.get('Tên thuốc')]
    if valid_meds:
        for m in valid_meds[:4]:  # Lấy tối đa 4 thuốc chính
            lines.append(f"- {m.get('Tên thuốc', '')} | Lieu: {m.get('Liều lượng', '')}")
    else:
        lines.append("(Chua co du lieu thuoc)")
        
    accepted_family = [m for m in family_members if m.get("status") == "accepted"] if family_members else []
    if accepted_family:
        lines.append("--- Lien he nguoi than ---")
        for fm in accepted_family[:2]:  # Lấy tối đa 2 người thân
            lines.append(f"- {fm.get('member_name') or 'Nguoi than'}: {fm.get('member_phone', '')}")

    return "\n".join(lines)

def generate_qr_image(text: str):
    """Trả về ảnh PIL của mã QR chứa `text`, hoặc None nếu thư viện qrcode chưa được cài."""
    if not QRCODE_AVAILABLE:
        return None

    clean_text = str(text) if text else "N/A"
    
    # Giới hạn độ dài tối đa an toàn cho mã QR (khoảng 1000-1200 ký tự)
    if len(clean_text) > 1000:
        clean_text = clean_text[:950] + "\n...(Da cat bớt do qua dai)"

    # Khai báo QRCode KHÔNG truyền version cố định, để thư viện tự tính toán kích thước
    qr = qrcode.QRCode(
        error_correction=qrcode.constants.ERROR_CORRECT_L, # Dùng L để nhẹ dung lượng hơn M
        box_size=8,
        border=3,
    )

    try:
        qr.add_data(clean_text)
        qr.make(fit=True)
        return qr.make_image(fill_color="black", back_color="white").convert("RGB")
    except Exception:
        # Phương án dự phòng cực kỳ an toàn nếu dữ liệu vẫn bị quá tải
        qr_safe = qrcode.QRCode(
            error_correction=qrcode.constants.ERROR_CORRECT_L,
            box_size=8,
            border=3,
        )
        qr_safe.add_data(clean_text[:500])
        qr_safe.make(fit=True)
        return qr_safe.make_image(fill_color="black", back_color="white").convert("RGB")
# ---- Mới: ảnh hình nền (wallpaper) khoá màn hình chứa mã QR khẩn cấp ----
# Mục đích: cho phép quét mã NGAY TỪ MÀN HÌNH KHOÁ của điện thoại (iPhone/Android),
# không cần mở khoá máy — quan trọng khi người dùng ngất xỉu/bất tỉnh và người xung
# quanh không biết mật khẩu.
WALLPAPER_SIZES = {
    "iPhone (1170 x 2532)": (1170, 2532),
    "Android phổ biến (1080 x 2340)": (1080, 2340),
    "Vuông / máy tính bảng (1200 x 1600)": (1200, 1600),
}


def _load_font(size: int, bold: bool = False):
    from PIL import ImageFont
    candidates = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold
        else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ]
    for path in candidates:
        try:
            return ImageFont.truetype(path, size)
        except Exception:
            continue
    return ImageFont.load_default()


def generate_lockscreen_wallpaper(qr_img, profile: dict, conflicts: list, size_key: str = "iPhone (1170 x 2532)"):
    """
    Ghép mã QR khẩn cấp vào một ảnh nền dọc (đúng tỉ lệ màn hình điện thoại) kèm dòng chữ
    cảnh báo lớn, để người dùng đặt làm HÌNH NỀN MÀN HÌNH KHOÁ. Nhờ vậy, khi máy đang khoá,
    người sơ cứu vẫn thấy và quét được mã ngay mà không cần mở khoá điện thoại.
    """
    from PIL import Image as PILImage, ImageDraw

    width, height = WALLPAPER_SIZES.get(size_key, (1170, 2532))
    bg_color = (0, 40, 37)          # xanh đậm cùng tông thương hiệu SafePill
    accent_color = (255, 90, 90)    # đỏ cảnh báo
    canvas = PILImage.new("RGB", (width, height), bg_color)
    draw = ImageDraw.Draw(canvas)

    # Chừa khoảng trống phía trên (~18% chiều cao) để không che đồng hồ/ngày trên màn hình khoá
    top_margin = int(height * 0.16)
    bottom_margin = int(height * 0.10)

    title_font = _load_font(int(width * 0.062), bold=True)
    name_font = _load_font(int(width * 0.045), bold=True)
    info_font = _load_font(int(width * 0.036))
    small_font = _load_font(int(width * 0.030))

    y = top_margin
    title_text = "❗ THÔNG TIN Y TẾ KHẨN CẤP"
    tw = draw.textlength(title_text, font=title_font)
    draw.text(((width - tw) / 2, y), title_text, font=title_font, fill=accent_color)
    y += int(width * 0.062) + 24

    # Mã QR ở giữa, nền trắng viền bo để dễ quét kể cả trong điều kiện thiếu sáng
    qr_size = int(width * 0.62)
    qr_resized = qr_img.resize((qr_size, qr_size))
    pad = 24
    quiet_box = PILImage.new("RGB", (qr_size + pad * 2, qr_size + pad * 2), (255, 255, 255))
    quiet_box.paste(qr_resized, (pad, pad))
    qx = (width - quiet_box.width) // 2
    canvas.paste(quiet_box, (qx, y))
    y += quiet_box.height + 36

    name_text = profile.get("full_name", "") or "Chưa cập nhật tên"
    nw = draw.textlength(name_text, font=name_font)
    draw.text(((width - nw) / 2, y), name_text, font=name_font, fill=(255, 255, 255))
    y += int(width * 0.045) + 14

    blood_type = profile.get("blood_type")
    if blood_type and blood_type != "Chưa rõ":
        blood_text = f"🩸 Nhóm máu: {blood_type}"
        bw = draw.textlength(blood_text, font=name_font)
        draw.text(((width - bw) / 2, y), blood_text, font=name_font, fill=accent_color)
        y += int(width * 0.045) + 14

    phone_text = f"SĐT: {profile.get('phone', '')}"
    pw = draw.textlength(phone_text, font=info_font)
    draw.text(((width - pw) / 2, y), phone_text, font=info_font, fill=(220, 230, 228))
    y += int(width * 0.036) + 14

    if conflicts:
        warn_text = f"⚠️ Có {len(conflicts)} cảnh báo tương tác thuốc — xem chi tiết khi quét mã"
        ww = draw.textlength(warn_text, font=info_font)
        draw.text(((width - ww) / 2, y), warn_text, font=info_font, fill=accent_color)
        y += int(width * 0.036) + 14

    footer_text = "Quét mã QR để xem đầy đủ danh sách thuốc & liên hệ người thân — SafePill"
    fw = draw.textlength(footer_text, font=small_font)
    draw.text(((width - fw) / 2, height - bottom_margin), footer_text, font=small_font, fill=(180, 195, 192))

    return canvas


# ---- Mới: nhận diện thuốc bằng hình ảnh (hiển thị icon màu sắc/hình dạng) ----
SHAPE_ICON_MAP = {
    "tròn": "border-radius:50%;",
    "oval": "border-radius:50%;transform:scaleX(1.6);",
    "vien nen": "border-radius:6px;",
    "vuông": "border-radius:4px;",
    "con nhộng": "border-radius:50px;transform:scaleX(0.6) scaleY(1.4);",
}


def render_pill_icon_html(color: str, shape: str, size: int = 26) -> str:
    """Trả về đoạn HTML nhỏ vẽ hình viên thuốc (màu + hình dạng) để người già dễ nhận diện qua hình ảnh."""
    color_clean = (color or "#cccccc").strip()
    shape_key = (shape or "").strip().lower()
    shape_style = "border-radius:50%;"
    for key, style in SHAPE_ICON_MAP.items():
        if key in shape_key:
            shape_style = style
            break
    safe_color = color_clean if re.match(r"^#?[0-9a-fA-F]{3,8}$", color_clean) or re.match(
        r"^[a-zA-Z]+$", color_clean) else "#cccccc"
    return (f'<span style="display:inline-block;width:{size}px;height:{size}px;'
            f'background:{safe_color};{shape_style}border:1px solid #999;'
            f'vertical-align:middle;margin-right:6px;"></span>')


# ---- Mới: đọc to bằng giọng nói (Text-to-Speech, Web Speech API) ----
def build_tts_button_html(text: str, button_label: str = "🔊 Đọc to", key_suffix: str = "") -> str:
    """Trả về HTML nút bấm phát âm thanh đọc to `text` bằng giọng tiếng Việt của trình duyệt."""
    safe_text = json.dumps(text, ensure_ascii=False)
    btn_id = f"ttsBtn_{key_suffix}".replace(" ", "_")
    return f"""
    <button id="{btn_id}" style="padding:6px 12px;border-radius:8px;border:none;
    background:#006a62;color:white;cursor:pointer;font-size:13px;">{button_label}</button>
    <script>
    document.getElementById("{btn_id}").addEventListener("click", function() {{
        try {{
            const utter = new SpeechSynthesisUtterance({safe_text});
            utter.lang = "vi-VN";
            utter.rate = 0.95;
            window.speechSynthesis.cancel();
            window.speechSynthesis.speak(utter);
        }} catch (e) {{}}
    }});
    </script>
    """


# =====================================================================================
# 4. TRẠNG THÁI PHIÊN LÀM VIỆC
# =====================================================================================
DEFAULT_STATE = {
    "onboarded": False,
    "logged_in": False,
    "user_phone": None,
    "elderly_mode": False,
    "chat_history": [],
    "med_data": [],
    "current_profile": {},
    "adherence_logs": {},
    "reg_face_base64": None,
    # ---- Mới: nhắc nhở thủ công (không cần gắn với thuốc cụ thể) ----
    "custom_reminders": [],
    # ---- Mới: tuỳ chọn âm thanh nhắc nhở ----
    "reminder_sound": "beep",
    "reminder_volume": 0.6,
    # ---- Mới: bộ đếm bỏ lỡ liên tiếp theo từng thuốc (cảnh báo leo thang) ----
    "missed_streak": {},
    # ---- Mới: cờ đánh dấu đã trừ số lượng thuốc còn lại cho từng task hôm nay ----
    "qty_decremented": {},
    # ---- Mới: bật/tắt đọc to (Text-to-Speech) ----
    "tts_enabled": True,
    # ---- Mới: đã lưu snapshot tuân thủ hôm nay lên Supabase chưa (tránh ghi lặp lại) ----
    "adherence_logged_today": False,
    # ---- Mới: tập hợp các task đã tự động cảnh báo người thân do quá 30 phút chưa uống ----
    "auto_escalated_keys": lambda: set(),
    # ---- SỬA LỖI: ngày mà các trạng thái tuân thủ trong phiên (adherence_logs, missed_streak,
    # qty_decremented, auto_escalated_keys) đang phản ánh. TRƯỚC ĐÂY các trạng thái này không
    # bao giờ được làm mới theo ngày mới, nên "Đã uống hôm nay" của ngày hôm qua vẫn còn được
    # tính là "đã uống" cho ngày hôm nay, khiến tỷ lệ tuân thủ/báo cáo bị sai lệch (luôn ~100%
    # sau ngày đầu tiên sử dụng). Xem reset_daily_adherence_state_if_needed().
    "adherence_log_date": None,
}
for key, default_val in DEFAULT_STATE.items():
    if key not in st.session_state:
        st.session_state[key] = default_val() if callable(default_val) else default_val


def reset_daily_adherence_state_if_needed() -> None:
    """
    SỬA LỖI TUÂN THỦ THUỐC — Nếu sang ngày mới (so với lần cuối các trạng thái tuân thủ được
    ghi nhận trong phiên), tự động làm mới các bộ đếm để "Đã uống hôm nay" phản ánh đúng NGÀY
    HIỆN TẠI thay vì cộng dồn mãi mãi từ lần đăng nhập đầu tiên. Gọi hàm này ngay khi vào
    Dashboard, trước khi tính toán mọi số liệu tuân thủ.
    """
    today_str = datetime.now().strftime("%Y-%m-%d")
    if st.session_state.adherence_log_date != today_str:
        st.session_state.adherence_logs = {}
        st.session_state.missed_streak = {}
        st.session_state.qty_decremented = {}
        st.session_state.auto_escalated_keys = set()
        st.session_state.adherence_logged_today = False
        st.session_state.adherence_log_date = today_str


def load_profile_into_session(user_row: dict):
    st.session_state.logged_in = True
    st.session_state.user_phone = user_row.get("phone")
    st.session_state.current_profile = user_row
    diag = user_row.get("diagnostic")
    if diag and str(diag).startswith("["):
        try:
            st.session_state.med_data = json.loads(diag)
        except Exception:
            st.session_state.med_data = []


def save_med_data_to_supabase() -> None:
    """
    MỚI — Ghi toàn bộ tủ thuốc hiện tại (st.session_state.med_data), bao gồm cả 3 trường
    'Nơi khám bệnh' / 'Bác sĩ điều trị' / 'Nơi cấp thuốc', xuống cột 'diagnostic' của Supabase
    dưới dạng chuỗi JSON. Nhờ vậy dữ liệu KHÔNG bị mất khi người dùng tải lại trang hoặc
    đăng xuất/đăng nhập lại. Cần gọi hàm này ngay sau mỗi lần med_data bị thay đổi
    (thêm/xoá/sửa thuốc).
    """
    if not st.session_state.get("user_phone"):
        return
    try:
        supabase.table(TABLE).update({
            "diagnostic": json.dumps(st.session_state.med_data, ensure_ascii=False)
        }).eq("phone", st.session_state.user_phone).execute()
    except Exception as e:
        st.warning(f"⚠️ Không lưu được tủ thuốc lên máy chủ (dữ liệu chỉ tồn tại tạm trong phiên "
                   f"làm việc này): {e}")


def build_reminder_sound_script(sound_type: str, volume: float) -> str:
    """
    Trả về đoạn JS dùng chung để phát âm thanh nhắc nhở bằng Web Audio API
    (không cần file âm thanh bên ngoài, hoạt động cả khi offline).
    """
    return f"""
    function playReminderSound(type, volume) {{
        try {{
            const ctx = new (window.AudioContext || window.webkitAudioContext)();
            function tone(freq, start, dur) {{
                const osc = ctx.createOscillator();
                const gain = ctx.createGain();
                osc.frequency.value = freq;
                osc.type = 'sine';
                gain.gain.value = volume;
                osc.connect(gain);
                gain.connect(ctx.destination);
                osc.start(ctx.currentTime + start);
                osc.stop(ctx.currentTime + start + dur);
            }}
            if (type === 'chime') {{
                tone(523.25, 0, 0.2); tone(659.25, 0.2, 0.2); tone(783.99, 0.4, 0.3);
            }} else if (type === 'bell') {{
                tone(660, 0, 0.6); tone(880, 0.1, 0.5);
            }} else {{
                tone(880, 0, 0.15); tone(880, 0.25, 0.15);
            }}
        }} catch (e) {{}}
    }}
    """


# =====================================================================================
# MÀN HÌNH 1: ONBOARDING
# =====================================================================================
if not st.session_state.onboarded:
    st.markdown("<h1 style='text-align:center;color:#006a62;'>💊 SafePill</h1>", unsafe_allow_html=True)
    st.markdown("<p style='text-align:center;color:#555;'>Trợ lý dược phẩm thông minh — quét đơn thuốc, "
                "phát hiện tương tác nguy hiểm, nhắc uống thuốc đúng giờ.</p>", unsafe_allow_html=True)
    # SỬA LỖI GIAO DIỆN #1: bản gốc dựng 2 khối HTML card giới thiệu chồng lên nhau, gây rối giao diện.
    # Đã gộp về DUY NHẤT một khối "phone mockup" gọn gàng.
    # SỬA LỖI GIAO DIỆN #2: khối card cũ phụ thuộc vào Tailwind CSS tải qua CDN
    # (<script src="https://cdn.tailwindcss.com">). Khi script này tải chậm hoặc bị chặn, các class
    # tiện ích (w-full, text-xs, flex...) không có tác dụng, khiến đoạn mô tả không được bó khổ đúng
    # và bị .phone-container (overflow:hidden + text-align:center) cắt chữ đối xứng ở cả 2 mép (mất
    # chữ đầu "Quét" và giữa dòng). Nay bỏ hẳn phụ thuộc CDN, dùng CSS thuần + word-wrap an toàn, và
    # hiển thị qua components.html() (iframe cô lập) để tránh xung đột CSS với phần còn lại của trang.
    onboarding_html = """
    <div style="display:flex;justify-content:center;font-family:sans-serif;">
      <div style="box-sizing:border-box;width:320px;background:#ffffff;border-radius:32px;
      border:6px solid #1e293b;overflow:hidden;box-shadow:0 20px 40px -12px rgba(0,0,0,.45);
      padding:0 0 20px 0;">
        <img src="https://images.unsplash.com/photo-1576091160550-2173dba999ef?w=400"
        style="display:block;width:100%;height:150px;object-fit:cover;" />
        <div style="box-sizing:border-box;width:100%;padding:16px 18px 0 18px;text-align:center;">
          <h2 style="margin:0 0 8px 0;font-size:1.2rem;font-weight:700;color:#1e293b;">
            Giải Pháp Số Hóa Y Tế
          </h2>
          <p style="margin:0;font-size:0.85rem;line-height:1.5;color:#64748b;
          word-wrap:break-word;overflow-wrap:break-word;white-space:normal;">
            Quét đơn thuốc bằng camera, tự động phát hiện tương tác thuốc nguy hiểm và nhắc
            bạn uống thuốc đúng giờ mỗi ngày.
          </p>
        </div>
      </div>
    </div>
    """
    c1, c2, c3 = st.columns([1, 1.2, 1])
    with c2:
        components.html(onboarding_html, height=330, scrolling=False)
        if st.button("BẮT ĐẦU SỬ DỤNG ➔", type="primary", use_container_width=True):
            st.session_state.onboarded = True
            st.rerun()
    st.caption(DISCLAIMER)
# =====================================================================================
# MÀN HÌNH 2: XÁC THỰC (ĐĂNG NHẬP / ĐĂNG KÝ 5 CHẠM)
# =====================================================================================
elif not st.session_state.logged_in:
    c1, c2, c3 = st.columns([1, 1.6, 1])
    with c2:
        st.markdown("<h2 style='text-align:center;color:#006a62;'>🔐 Đăng nhập / Đăng ký</h2>",
                    unsafe_allow_html=True)
        tab_login, tab_register = st.tabs(["🔑 Đăng nhập", "🆕 Đăng ký nhanh (5 chạm)"])
        # ---------------- ĐĂNG NHẬP (Mã PIN hoặc FaceID) ----------------
        with tab_login:
            method = st.radio("Mở khóa bằng:", ["Mã PIN 4 số", "Khuôn mặt (FaceID)"], horizontal=True)
            if method == "Mã PIN 4 số":
                l_phone = st.text_input("Số điện thoại", placeholder="09xxxxxxxx")
                l_pin = st.text_input("Mã PIN (4 số)", type="password", max_chars=4, placeholder="****")
                if st.button("Đăng nhập", type="primary", use_container_width=True):
                    l_phone_clean = l_phone.replace(" ", "").strip()
                    l_pin_clean = l_pin.strip()
                    if not l_phone_clean or not l_pin_clean:
                        st.warning("⚠️ Vui lòng nhập đầy đủ số điện thoại và mã PIN.")
                    else:
                        with st.spinner("Đang xác thực..."):
                            try:
                                res = supabase.table(TABLE).select("*").eq("phone", l_phone_clean).execute()
                                if res.data:
                                    user_row = res.data[0]
                                    if verify_pin(l_pin_clean, user_row.get("pin")):
                                        load_profile_into_session(user_row)
                                        st.success("✅ Đăng nhập thành công!")
                                        st.rerun()
                                    else:
                                        st.error("❌ Mã PIN không chính xác.")
                                else:
                                    st.error("❌ Số điện thoại chưa được đăng ký.")
                            except Exception as e:
                                st.error(f"Lỗi kết nối cơ sở dữ liệu: {e}")
            else:  # FaceID
                st.info("📷 Nhìn thẳng vào camera để đối chiếu khuôn mặt đã đăng ký.")
                face_img = st.camera_input("Chụp ảnh xác thực", key="face_login")
                if face_img:
                    with st.spinner("🔍 Đang đối chiếu dữ liệu sinh trắc học..."):
                        try:
                            login_hash = average_hash(face_img.getvalue())
                            if not login_hash:
                                st.error("❌ Không xử lý được ảnh vừa chụp. Vui lòng thử lại với ảnh rõ nét hơn.")
                            else:
                                try:
                                    res = supabase.table(TABLE).select("phone, full_name, pin, face_hash").execute()
                                except Exception as col_err:
                                    st.error(
                                        "⚠️ Bảng dữ liệu chưa có cột lưu FaceID (face_data/face_hash). "
                                        "Hãy chạy migration SQL để thêm cột trước khi dùng FaceID."
                                    )
                                    res = None
                                if res is not None:
                                    candidates = [row for row in (res.data or []) if row.get("face_hash")]
                                    best_match, best_distance = None, 999
                                    for row in candidates:
                                        dist = hamming_distance(login_hash, row["face_hash"])
                                        if dist < best_distance:
                                            best_distance, best_match = dist, row
                                    # Ngưỡng đối chiếu (aHash 8x8 = 64 bit); dưới 10 bit khác biệt coi là khớp ở mức demo
                                    FACE_MATCH_THRESHOLD = 10
                                    if not candidates:
                                        st.warning(
                                            "⚠️ Chưa có tài khoản nào đăng ký FaceID. "
                                            "Hãy đăng nhập bằng mã PIN, sau đó bật FaceID trong phần đăng ký."
                                        )
                                    elif best_match and best_distance <= FACE_MATCH_THRESHOLD:
                                        # Lấy đầy đủ thông tin hồ sơ trước khi nạp vào session
                                        full_res = supabase.table(TABLE).select("*").eq(
                                            "phone", best_match["phone"]
                                        ).execute()
                                        if full_res.data:
                                            load_profile_into_session(full_res.data[0])
                                            st.success(f"✅ Xác thực FaceID thành công! Chào mừng "
                                                       f"{best_match.get('full_name', '')}.")
                                            st.rerun()
                                        else:
                                            st.error("❌ Không thể tải hồ sơ người dùng, vui lòng thử lại.")
                                    else:
                                        st.error("❌ Không tìm thấy khuôn mặt khớp trong hệ thống. "
                                                 "Vui lòng đăng nhập bằng mã PIN hoặc đăng ký tài khoản mới.")
                        except Exception as e:
                            st.error(f"Lỗi xác thực FaceID: {e}")
        # ---------------- ĐĂNG KÝ NHANH ----------------
        with tab_register:
            st.caption("Điền đầy đủ 4 trường bắt buộc (số điện thoại, họ tên, mã PIN, nhóm máu) — "
                       "FaceID vẫn là tuỳ chọn, có thể bật ngay hoặc bổ sung sau.")
            with st.form("quick_register_form", clear_on_submit=False):
                r_phone = st.text_input("📱 Số điện thoại", placeholder="09xxxxxxxx")
                r_name = st.text_input("👤 Họ và tên", placeholder="Nguyễn Văn A")
                pin_col1, pin_col2 = st.columns([3, 1])
                with pin_col1:
                    r_pin = st.text_input("🔢 Tạo mã PIN (4 số)", type="password", max_chars=4, placeholder="****")
                with pin_col2:
                    show_pin = st.checkbox("Hiện", help="Hiện mã PIN để tự kiểm tra, "
                                            "không cần nhập lại lần 2 (rút gọn thao tác)")
                if show_pin and r_pin:
                    st.caption(f"Mã PIN vừa nhập: `{r_pin}`")
                # ---- Mới: nhóm máu — BẮT BUỘC (không còn là tuỳ chọn) để hồ sơ khẩn cấp đầy đủ ----
                r_blood_type = st.selectbox(
                    "🩸 Nhóm máu (bắt buộc)", BLOOD_TYPE_OPTIONS,
                    help="Bắt buộc chọn để hồ sơ khẩn cấp (mã QR, hình nền màn hình khoá) luôn có "
                         "đủ thông tin khi cần cấp cứu.",
                )
                enable_face = st.checkbox("Thêm FaceID ngay bây giờ (tùy chọn)", value=False)
                reg_face_img = None
                if enable_face:
                    reg_face_img = st.camera_input("Chụp khuôn mặt", key="register_face_cam")
                submit_reg = st.form_submit_button("✅ ĐĂNG KÝ", use_container_width=True, type="primary")
                if submit_reg:
                    r_phone_clean, r_name_clean, r_pin_clean = r_phone.strip(), r_name.strip(), r_pin.strip()
                    if not r_phone_clean or not r_name_clean or not r_pin_clean:
                        st.error("❌ Vui lòng điền đầy đủ số điện thoại, họ tên và mã PIN.")
                    elif not validate_phone(r_phone_clean):
                        st.error("❌ Số điện thoại không hợp lệ (định dạng 10 số, bắt đầu bằng 0).")
                    elif len(r_pin_clean) != 4 or not r_pin_clean.isdigit():
                        st.error("❌ Mã PIN phải gồm đúng 4 chữ số.")
                    # ---- Mới: bắt buộc chọn nhóm máu, không được để mặc định "Chưa rõ" ----
                    elif r_blood_type == "Chưa rõ":
                        st.error("❌ Vui lòng chọn nhóm máu của bạn — đây là thông tin bắt buộc để hoàn "
                                 "tất đăng ký (phục vụ hồ sơ khẩn cấp).")
                    elif enable_face and reg_face_img is None:
                        st.error("❌ Bạn đã bật FaceID nhưng chưa chụp ảnh. Vui lòng chụp ảnh hoặc bỏ chọn FaceID.")
                    # ---- Mới: chặn đăng ký nếu số điện thoại đã có tài khoản ----
                    elif phone_already_registered(r_phone_clean):
                        st.error(
                            f"❌ Số điện thoại '{r_phone_clean}' đã được đăng ký trước đó. "
                            f"Mỗi số điện thoại chỉ được tạo 1 tài khoản — vui lòng chuyển sang tab "
                            f"**Đăng nhập** hoặc dùng số điện thoại khác."
                        )
                    else:
                        with st.spinner("Đang khởi tạo tài khoản..."):
                            try:
                                new_row = {
                                    "phone": r_phone_clean,
                                    "pin": hash_pin(r_pin_clean),
                                    "full_name": r_name_clean,
                                    "blood_type": r_blood_type,
                                }
                                face_hash = None
                                if enable_face and reg_face_img is not None:
                                    face_bytes = reg_face_img.getvalue()
                                    face_hash = average_hash(face_bytes)
                                    if not face_hash:
                                        st.warning(
                                            "⚠️ Không xử lý được ảnh khuôn mặt, tài khoản sẽ được tạo "
                                            "không kèm FaceID. Bạn có thể thêm lại sau."
                                        )
                                    else:
                                        new_row["face_data"] = base64.b64encode(face_bytes).decode("utf-8")
                                        new_row["face_hash"] = face_hash
                                try:
                                    resp = supabase.table(TABLE).insert(new_row).execute()
                                except Exception as insert_err:
                                    err_text = str(insert_err)
                                    missing_cols = []
                                    if face_hash and ("face_data" in err_text or "face_hash" in err_text
                                                       or "column" in err_text.lower()):
                                        missing_cols += ["face_data", "face_hash"]
                                    if "blood_type" in err_text or "column" in err_text.lower():
                                        missing_cols.append("blood_type")
                                    if missing_cols:
                                        # Bảng chưa có (một số) cột mới: thử lại không kèm các cột đó
                                        for col in set(missing_cols):
                                            new_row.pop(col, None)
                                        resp = supabase.table(TABLE).insert(new_row).execute()
                                        st.warning(
                                            "⚠️ Bảng dữ liệu chưa có đủ cột lưu FaceID/nhóm máu, nên tài khoản "
                                            "được tạo với các thông tin còn lại. Hãy chạy migration SQL thêm "
                                            "cột face_data/face_hash/blood_type rồi cập nhật lại sau trong Cài đặt."
                                        )
                                    else:
                                        raise
                                if resp.data:
                                    load_profile_into_session(resp.data[0])
                                    st.session_state.med_data = []
                                    st.success("✅ Tạo tài khoản thành công!")
                                    st.rerun()
                            except Exception as db_err:
                                err_msg = str(db_err)
                                if "duplicate key" in err_msg or "23505" in err_msg:
                                    st.error(f"❌ Số điện thoại '{r_phone_clean}' đã tồn tại. Vui lòng đăng nhập.")
                                else:
                                    st.error(f"❌ Lỗi cơ sở dữ liệu: {db_err}")
    st.caption(DISCLAIMER)
# =====================================================================================
# MÀN HÌNH 3: DASHBOARD CHÍNH
# =====================================================================================
else:
    # SỬA LỖI TUÂN THỦ THUỐC: làm mới các bộ đếm tuân thủ (đã uống/bỏ lỡ/số lượng đã trừ/cảnh
    # báo tự động) khi sang ngày mới, để "Đã uống hôm nay" và báo cáo luôn phản ánh đúng ngày
    # hiện tại thay vì cộng dồn từ những ngày trước.
    reset_daily_adherence_state_if_needed()

    detected_conflicts = scan_cabinet_for_conflicts(st.session_state.med_data)
    # ---- Mới: kiểm tra nhắc nhở do người thân gửi tới, đã đến hạn hiển thị ----
    due_family_reminders = fetch_due_family_reminders(st.session_state.user_phone)
    pending_family_invites = fetch_pending_invites_for_member(st.session_state.user_phone)
    with st.sidebar:
        st.image("https://cdn-icons-png.flaticon.com/512/3022/3022574.png", width=60)
        st.title("SafePill")
        st.caption(f"Xin chào: **{st.session_state.current_profile.get('full_name', '')}**")
        st.caption(f"SĐT: `{st.session_state.user_phone}`")
        blood_display = st.session_state.current_profile.get("blood_type")
        if blood_display and blood_display != "Chưa rõ":
            st.caption(f"🩸 Nhóm máu: **{blood_display}**")
        st.divider()
        st.subheader("📊 Tỷ lệ tuân thủ")
        if st.session_state.med_data:
            total_tasks = len(st.session_state.med_data)
            done_tasks = sum(1 for v in st.session_state.adherence_logs.values() if v)
            rate = int((done_tasks / total_tasks) * 100) if total_tasks else 0
            st.progress(rate / 100)
            st.metric("Đã uống hôm nay", f"{rate}%")
        else:
            st.info("Chưa có lịch trình thuốc.")
        if pending_family_invites:
            st.divider()
            st.warning(f"👪 Bạn có {len(pending_family_invites)} lời mời làm người thân đang chờ "
                       f"phê duyệt — xem ở tab **Cài đặt → Người thân**.")
        st.divider()
        st.session_state.elderly_mode = st.toggle("🔎 Giao diện chữ to (dễ đọc)",
                                                    value=st.session_state.elderly_mode)
        if st.button("🚪 Đăng xuất"):
            for key, default_val in DEFAULT_STATE.items():
                st.session_state[key] = default_val() if callable(default_val) else default_val
            st.session_state.onboarded = True
            st.rerun()

    if st.session_state.elderly_mode:
        st.markdown(
            "<style> p,span,label,button,h3,h2,input,li {font-size:22px !important;} "
            "th,td {font-size:19px !important;} </style>",
            unsafe_allow_html=True,
        )

    st.title("💊 SafePill – Trung Tâm Quản Lý Dược Phẩm")
    st.caption(DISCLAIMER)

    m1, m2, m3 = st.columns(3)
    m1.metric("Số thuốc đang quản lý", f"{len(st.session_state.med_data)}")
    if detected_conflicts:
        m2.metric("Tương tác thuốc", "🚨 CÓ CẢNH BÁO", delta="Cần xem xét ngay", delta_color="inverse")
    else:
        m2.metric("Tương tác thuốc", "✅ An toàn", delta="Không phát hiện xung đột")
    med_data_valid = [m for m in st.session_state.med_data if m.get("Tên thuốc")]
    todays_reminders = len(med_data_valid)
    m3.metric("Lịch nhắc hôm nay", f"{todays_reminders} khung giờ")

    st.divider()
    tab_home, tab_ocr, tab_cabinet, tab_matrix, tab_expert, tab_report, tab_qr, tab_settings = st.tabs([
        "🏠 Hôm nay",
        "📷 Quét đơn thuốc",
        "🗄️ Tủ thuốc số",
        "🔬 Tra cứu tương tác",
        "🤖 Hỏi đáp AI",
        "📈 Báo cáo tuân thủ",
        "🆘 QR khẩn cấp",
        "⚙️ Cài đặt",
    ])

    # ---------------- TAB HÔM NAY: nhắc nhở theo giờ ----------------
    with tab_home:
        st.header("🏠 Lịch uống thuốc hôm nay")

        # ===== MỚI: tự động cảnh báo người thân nếu quá 30 phút chưa uống thuốc =====
        auto_escalated_now = check_and_auto_escalate_overdue_doses(med_data_valid)
        if auto_escalated_now:
            for item in auto_escalated_now:
                st.error(
                    f"🚨 Đã quá {AUTO_ESCALATION_MINUTES} phút kể từ giờ hẹn **{item['time']}** mà "
                    f"**{item['drug']}** vẫn chưa được xác nhận uống — SafePill đã tự động báo cho "
                    f"{item['sent_to']} người thân đang theo dõi bạn."
                )

        # ===== MỚI: Thêm / quản lý nhắc nhở thủ công (không cần gắn với thuốc) =====
        with st.expander("➕ Thêm / quản lý nhắc nhở thủ công", expanded=False):
            st.caption("Đặt nhắc nhở tuỳ ý (đo huyết áp, tái khám, uống nước...) không cần gắn với "
                       "một loại thuốc cụ thể trong tủ thuốc.")
            with st.form("add_custom_reminder_form", clear_on_submit=True):
                rc1, rc2 = st.columns([3, 2])
                custom_label = rc1.text_input("Nội dung nhắc nhở", placeholder="VD: Đo huyết áp")
                custom_time = rc2.time_input("Giờ nhắc", value=dtime(8, 0))
                submit_custom = st.form_submit_button("➕ Thêm nhắc nhở")
                if submit_custom:
                    if not custom_label.strip():
                        st.warning("⚠️ Vui lòng nhập nội dung nhắc nhở.")
                    else:
                        st.session_state.custom_reminders.append({
                            "label": custom_label.strip(),
                            "time": custom_time.strftime("%H:%M"),
                        })
                        st.success("✅ Đã thêm nhắc nhở thủ công.")
                        st.rerun()

            if st.session_state.custom_reminders:
                st.markdown("**Danh sách nhắc nhở thủ công:**")
                for cidx, cr in enumerate(list(st.session_state.custom_reminders)):
                    ccols = st.columns([1, 3, 1])
                    ccols[0].markdown(f"**{cr['time']}**")
                    ccols[1].markdown(cr["label"])
                    if ccols[2].button("🗑️", key=f"del_custom_{cidx}"):
                        st.session_state.custom_reminders.pop(cidx)
                        st.rerun()
            else:
                st.caption("Chưa có nhắc nhở thủ công nào.")

        st.divider()

        # ===== MỚI: Nhắc nhở do người thân gửi tới, đã đến hạn =====
        family_reminder_payload = []
        if due_family_reminders:
            st.markdown("**👪 Nhắc nhở từ người thân:**")
            for fr in due_family_reminders:
                sender = fr.get("sender_name") or fr.get("sender_phone") or "Người thân"
                st.warning(f"**{sender}** nhắc bạn: {fr.get('message', '')}")
                family_reminder_payload.append({
                    "name": f"{sender} nhắc: {fr.get('message', '')}",
                    "time": datetime.now().strftime("%H:%M"),
                })
                mark_family_reminder_delivered(fr.get("id"))
            st.divider()

        if not med_data_valid and not st.session_state.custom_reminders and not family_reminder_payload:
            st.info("Chưa có thuốc hoặc nhắc nhở nào. Hãy quét đơn thuốc hoặc thêm nhắc nhở thủ công ở trên.")
        else:
            reminder_payload = list(family_reminder_payload)

            if med_data_valid:
                schedule = sorted(
                    med_data_valid,
                    key=lambda m: resolve_reminder_time(m.get("Thời điểm", "")),
                )
                for med in schedule:
                    hhmm = resolve_reminder_time(med.get("Thời điểm", ""))
                    # SỬA LỖI: dùng hàm dùng chung build_adherence_task_key() để key luôn khớp với
                    # hàm tự động cảnh báo quá giờ (check_and_auto_escalate_overdue_doses).
                    key_name = build_adherence_task_key(med.get("Tên thuốc"), hhmm, med)
                    taken = st.session_state.adherence_logs.get(key_name, False)
                    # Chỉ số thật của thuốc này trong med_data (để trừ số lượng còn lại đúng bản ghi)
                    real_idx = next((i for i, m in enumerate(st.session_state.med_data) if m is med), None)
                    if st.session_state.tts_enabled:
                        cols = st.columns([1, 3, 2, 1.3, 1.3, 1.3])
                    else:
                        cols = st.columns([1, 3, 2, 1.3, 1.3])
                    cols[0].markdown(f"**{hhmm}**")
                    pill_icon = render_pill_icon_html(med.get("Màu sắc", ""), med.get("Hình dạng", ""))
                    cols[1].markdown(
                        f"{pill_icon} **{med.get('Tên thuốc', '')}** — {med.get('Liều lượng', '')}",
                        unsafe_allow_html=True,
                    )
                    cols[2].markdown(f"_{med.get('Thời điểm', '')}_")
                    checked = cols[3].checkbox("Đã uống", value=taken, key=f"checked_{key_name}")
                    missed_clicked = cols[4].button("❌ Bỏ lỡ", key=f"missed_{key_name}")
                    # ---- Mới: nút đọc to (Text-to-Speech), có thể tắt trong Cài đặt ----
                    if st.session_state.tts_enabled:
                        with cols[5]:
                            # SỬA LỖI: st.html() KHÔNG nhận tham số height (gây TypeError:
                            # "HtmlMixin.html() got an unexpected keyword argument 'height'" và
                            # sập toàn bộ tab "Hôm nay"). Đoạn này có <script> cần chạy thật sự,
                            # nên dùng components.html() (hỗ trợ height, chạy JS ổn định trong iframe).
                            components.html(
                                build_tts_button_html(
                                    f"Đến giờ uống {med.get('Tên thuốc','')}, liều {med.get('Liều lượng','')}, "
                                    f"vào {med.get('Thời điểm','')}",
                                    button_label="🔊", key_suffix=key_name,
                                ), height=42,
                            )
                    if checked != taken:
                        st.session_state.adherence_logs[key_name] = checked
                        if checked:
                            reset_missed_dose(med.get("Tên thuốc", ""))
                            if real_idx is not None:
                                decrement_med_quantity(real_idx, key_name)
                        else:
                            if real_idx is not None:
                                restore_med_quantity(real_idx, key_name)
                        save_med_data_to_supabase()
                        st.rerun()
                    if missed_clicked:
                        st.session_state.adherence_logs[key_name] = False
                        drug_name = med.get("Tên thuốc", "")
                        info = INTERACTION_LOOKUP.get(drug_name.strip().capitalize())
                        severity = info.get("severity") if info else "Chưa xác định"
                        streak = record_missed_dose(drug_name, severity="Medium")
                        st.warning(f"⚠️ Đã ghi nhận bỏ lỡ liều **{drug_name}** ({streak} lần liên tiếp).")
                        if streak >= 2 and severity in ("Cao", "Nghiêm trọng"):
                            sent_to = send_escalation_alert_to_family(
                                st.session_state.user_phone,
                                st.session_state.current_profile.get("full_name", ""),
                                drug_name, streak,
                            )
                            if sent_to:
                                st.error(f"🚨 Đã tự động cảnh báo {len(sent_to)} người thân vì bỏ lỡ "
                                         f"thuốc mức độ **{severity}** liên tiếp {streak} lần!")
                        st.rerun()
                    # ---- Mới: cảnh báo sắp hết thuốc ----
                    qty_left = med.get("Số lượng còn lại")
                    if qty_left is not None:
                        try:
                            if int(qty_left) <= LOW_STOCK_THRESHOLD:
                                st.warning(f"📉 **{med.get('Tên thuốc','')}** chỉ còn **{qty_left}** liều "
                                           f"— hãy chuẩn bị mua thêm hoặc tái khám sớm!")
                        except (TypeError, ValueError):
                            pass
                    reminder_payload.append({"name": med.get("Tên thuốc", ""), "time": hhmm})

            if st.session_state.custom_reminders:
                st.markdown("**Nhắc nhở thủ công hôm nay:**")
                for cr in sorted(st.session_state.custom_reminders, key=lambda x: x["time"]):
                    st.markdown(f"- ⏰ **{cr['time']}** — {cr['label']}")
                    reminder_payload.append({"name": cr["label"], "time": cr["time"]})

            st.divider()
            st.caption("🔔 Trình duyệt sẽ gửi thông báo kèm âm thanh nhắc nhở đúng giờ nếu bạn cho phép "
                       "Notification và giữ tab này đang mở.")
            reminder_json = json.dumps(reminder_payload, ensure_ascii=False)
            sound_type = st.session_state.reminder_sound
            sound_volume = st.session_state.reminder_volume
            sound_js_fn = build_reminder_sound_script(sound_type, sound_volume)
            # SỬA LỖI: st.html() không nhận height -> gây TypeError, dùng components.html() thay thế.
            components.html(f"""
            <script>
            const meds = {reminder_json};
            const soundType = "{sound_type}";
            const soundVolume = {sound_volume};
            if (window.Notification && Notification.permission !== "granted") {{
                Notification.requestPermission();
            }}
            {sound_js_fn}
            function checkReminders() {{
                const now = new Date();
                const hhmm = String(now.getHours()).padStart(2,'0') + ":" + String(now.getMinutes()).padStart(2,'0');
                meds.forEach(m => {{
                    if (m.time === hhmm) {{
                        if (window.Notification && Notification.permission === "granted") {{
                            new Notification("💊 SafePill nhắc nhở", {{ body: m.name + " — đến giờ rồi!" }});
                        }}
                        playReminderSound(soundType, soundVolume);
                    }}
                }});
            }}
            setInterval(checkReminders, 30000);
            </script>
            """, height=0)
            if detected_conflicts:
                st.error("⚠️ Tủ thuốc hiện có cảnh báo tương tác — xem chi tiết ở tab **Tủ thuốc số**.")

    # ---------------- TAB QUÉT ĐƠN THUỐC (Vision AI) ----------------
    with tab_ocr:
        st.header("📷 Số hóa đơn thuốc bằng AI")
        st.info("Chụp ảnh trực tiếp, hoặc tải lên ảnh đơn thuốc/hồ sơ bệnh án đã có sẵn (viết tay, "
                 "vỉ thuốc, hoặc ảnh scan) — hệ thống sẽ tự động bóc tách tên thuốc, liều lượng và "
                 "thời điểm uống.")

        # ===== MỚI: Thông tin nơi khám / bác sĩ / nơi cấp thuốc — LUÔN HIỂN THỊ, áp dụng cho
        # CẢ quét AI lẫn nhập tay. Điền 1 lần ở đây, hệ thống sẽ tự gắn vào mọi thuốc được thêm
        # vào tủ thuốc trong lượt này (quét ảnh hoặc thêm thủ công), không cần gõ lại từng thuốc. =====
        st.markdown("### 🏥 Thông tin nơi khám & cấp thuốc")
        st.caption("Điền thông tin của đơn thuốc này — sẽ tự động áp dụng cho mọi thuốc bạn quét "
                   "bằng AI hoặc thêm thủ công bên dưới. Có thể để trống nếu không có.")
        rx_col1, rx_col2, rx_col3 = st.columns(3)
        rx_clinic = rx_col1.text_input(
            "Nơi khám bệnh", placeholder="VD: BV Chợ Rẫy", key="rx_common_clinic",
        )
        rx_doctor = rx_col2.text_input(
            "Bác sĩ điều trị", placeholder="VD: BS. Nguyễn Văn A", key="rx_common_doctor",
        )
        rx_pharmacy = rx_col3.text_input(
            "Nơi cấp thuốc", placeholder="VD: Nhà thuốc Long Châu", key="rx_common_pharmacy",
        )
        st.divider()

        # ---- Prompt dùng chung cho AI bóc tách dữ liệu y tế từ ảnh (đơn thuốc / vỉ thuốc / hồ sơ bệnh án) ----
        PRESCRIPTION_VISION_PROMPT = """
Bạn là chuyên gia bóc tách dữ liệu y tế. Phân tích hình ảnh này (đơn thuốc, vỉ thuốc, hoặc trang hồ sơ
bệnh án có kê đơn thuốc) và trả về DUY NHẤT một mảng JSON hợp lệ, không kèm markdown hay giải thích thêm,
theo đúng cấu trúc:
[
    {
        "Tên thuốc": "...",
        "Liều lượng": "...",
        "Thời điểm": "Sáng|Trưa|Chiều|Tối hoặc giờ cụ thể HH:MM",
        "Loại": "...",
        "Màu sắc": "màu chủ đạo của viên thuốc quan sát được, ví dụ: trắng, đỏ, vàng, xanh (để trống nếu không thấy rõ)",
        "Hình dạng": "hình dạng viên thuốc quan sát được: tròn | oval | viên nén | vuông | con nhộng (để trống nếu không rõ)",
        "Nơi khám bệnh": "tên bệnh viện/phòng khám ghi trên đơn (để trống nếu không thấy)",
        "Bác sĩ điều trị": "tên bác sĩ kê đơn ghi trên đơn (để trống nếu không thấy)",
        "Nơi cấp thuốc": "tên nhà thuốc/quầy thuốc cấp phát (để trống nếu không thấy)"
    }
]
Nếu chữ viết khó đọc, hãy suy luận hợp lý dựa trên bao bì hoặc tên thuốc phổ biến.
Trường "Màu sắc" và "Hình dạng" giúp người già không đọc được chữ nhỏ vẫn nhận diện được thuốc qua hình ảnh minh hoạ.
Các trường "Nơi khám bệnh", "Bác sĩ điều trị", "Nơi cấp thuốc" thường lặp lại giống nhau cho mọi loại
thuốc trong CÙNG một đơn/toa — nếu đơn chỉ ghi thông tin này một lần ở đầu hoặc cuối trang, hãy áp dụng
lại giá trị đó cho TẤT CẢ các thuốc được bóc tách từ đơn đó.
Nếu ảnh không chứa thông tin đơn thuốc/thuốc nào, hãy trả về mảng JSON rỗng: []
"""

        def analyze_prescription_image(pil_img, clinic_override: str, doctor_override: str,
                                        pharmacy_override: str):
            """
            MỚI — Hàm dùng CHUNG để gọi AI Gemini bóc tách 1 ảnh đơn thuốc/hồ sơ bệnh án, dù ảnh đến
            từ camera (st.camera_input) hay tải lên từ máy (st.file_uploader). Trả về (parsed_meds, None)
            nếu thành công, hoặc (None, error_message) nếu thất bại — để nơi gọi tự quyết định hiển thị.
            """
            response = ai_gemini.models.generate_content(
                model="gemini-flash-latest",
                contents=[pil_img, PRESCRIPTION_VISION_PROMPT],
            )
            parsed_meds = extract_json_array(response.text)
            if not isinstance(parsed_meds, list):
                raise ValueError("AI không trả về danh sách thuốc hợp lệ.")
            # ---- Nếu người dùng đã điền ô "Thông tin nơi khám & cấp thuốc" ở trên, ưu tiên dùng giá
            # trị đó (đáng tin cậy hơn AI đoán từ ảnh); nếu để trống thì giữ nguyên kết quả AI. ----
            for pm in parsed_meds:
                if clinic_override.strip():
                    pm["Nơi khám bệnh"] = clinic_override.strip()
                if doctor_override.strip():
                    pm["Bác sĩ điều trị"] = doctor_override.strip()
                if pharmacy_override.strip():
                    pm["Nơi cấp thuốc"] = pharmacy_override.strip()
            return parsed_meds

        # ===== SỬA: bổ sung lựa chọn thứ 3 "Tải ảnh có sẵn lên" (VD: ảnh chụp/scan hồ sơ bệnh án,
        # đơn thuốc lưu sẵn trong máy/thư viện ảnh) — không bắt buộc phải dùng camera trực tiếp. =====
        has_prescription = st.radio(
            "Bạn muốn thêm thuốc bằng cách nào?",
            [
                "📷 Chụp ảnh trực tiếp bằng camera",
                "📁 Tải ảnh có sẵn lên (đơn thuốc / hồ sơ bệnh án đã chụp hoặc scan)",
                "✍️ Không có ảnh, tôi sẽ nhập thuốc thủ công",
            ],
            horizontal=False,
        )

        if has_prescription.startswith("📷"):
            img_file = st.camera_input("Chụp ảnh đơn thuốc / vỉ thuốc", key="clinical_vision_cam")
            if img_file:
                with st.spinner("🤖 Đang phân tích hình ảnh bằng AI..."):
                    try:
                        pil_img = Image.open(io.BytesIO(img_file.getvalue()))
                        parsed_meds = analyze_prescription_image(pil_img, rx_clinic, rx_doctor, rx_pharmacy)
                        if not parsed_meds:
                            st.warning("⚠️ AI không nhận diện được thuốc nào trong ảnh này. Hãy thử chụp "
                                       "lại rõ nét hơn, hoặc nhập tay ở khung bên dưới.")
                        else:
                            st.session_state.med_data.extend(parsed_meds)
                            save_med_data_to_supabase()
                            st.success(f"✅ Đã thêm {len(parsed_meds)} loại thuốc vào tủ thuốc!")
                            st.rerun()
                    except Exception as ex:
                        st.error(f"❌ Không thể phân tích ảnh: {ex}")
                        st.caption("Gợi ý: chụp ảnh rõ nét hơn, đủ sáng, hoặc chọn "
                                   "\"Không có ảnh, tôi sẽ nhập thuốc thủ công\" ở trên để nhập tay.")
            manual_expanded = False
            manual_title = "➕ Thêm thuốc thủ công (nếu AI không nhận diện được, hoặc muốn bổ sung thêm)"

        elif has_prescription.startswith("📁"):
            # ---- MỚI: tải ảnh có sẵn lên (khác với chụp camera trực tiếp) — hỗ trợ nhiều ảnh cùng lúc
            # để quét trọn 1 hồ sơ bệnh án nhiều trang, hoặc nhiều đơn thuốc/vỉ thuốc khác nhau. ----
            uploaded_files = st.file_uploader(
                "Tải ảnh đơn thuốc / hồ sơ bệnh án (có thể chọn nhiều ảnh cùng lúc)",
                type=["png", "jpg", "jpeg", "webp"],
                accept_multiple_files=True,
                key="clinical_vision_upload",
            )
            if uploaded_files:
                st.caption(f"Đã chọn {len(uploaded_files)} ảnh. Xem trước bên dưới:")
                preview_cols = st.columns(min(len(uploaded_files), 4))
                for i, uf in enumerate(uploaded_files):
                    with preview_cols[i % len(preview_cols)]:
                        st.image(uf, use_container_width=True, caption=uf.name)
                if st.button("🤖 Phân tích tất cả ảnh bằng AI", type="primary", use_container_width=True,
                             key="analyze_uploaded_btn"):
                    all_parsed = []
                    failed_files = []
                    with st.spinner(f"🤖 Đang phân tích {len(uploaded_files)} ảnh bằng AI..."):
                        for uf in uploaded_files:
                            try:
                                pil_img = Image.open(io.BytesIO(uf.getvalue()))
                                parsed_meds = analyze_prescription_image(
                                    pil_img, rx_clinic, rx_doctor, rx_pharmacy
                                )
                                all_parsed.extend(parsed_meds)
                            except Exception as ex:
                                failed_files.append((uf.name, str(ex)))
                    if all_parsed:
                        st.session_state.med_data.extend(all_parsed)
                        save_med_data_to_supabase()
                        st.success(f"✅ Đã thêm {len(all_parsed)} loại thuốc từ {len(uploaded_files)} ảnh "
                                   f"vào tủ thuốc!")
                    if failed_files:
                        for fname, err in failed_files:
                            st.error(f"❌ Không thể phân tích ảnh **{fname}**: {err}")
                    if not all_parsed and not failed_files:
                        st.warning("⚠️ AI không nhận diện được thuốc nào trong các ảnh đã tải lên. Hãy "
                                   "thử ảnh rõ nét hơn, hoặc nhập tay ở khung bên dưới.")
                    if all_parsed:
                        st.rerun()
            manual_expanded = False
            manual_title = "➕ Thêm thuốc thủ công (nếu AI không nhận diện được, hoặc muốn bổ sung thêm)"

        else:
            st.success(
                "👍 Không sao cả! Bạn có thể bỏ qua bước chụp/tải ảnh và nhập trực tiếp thông tin thuốc "
                "ở khung bên dưới — vẫn đầy đủ tính năng nhắc nhở, cảnh báo tương tác như khi quét đơn."
            )
            manual_expanded = True
            manual_title = "✍️ Nhập thuốc thủ công"

        with st.expander(manual_title, expanded=manual_expanded):
            with st.form("manual_add_form", clear_on_submit=True):
                mc1, mc2, mc3, mc4 = st.columns(4)
                man_name = mc1.text_input("Tên thuốc")
                man_dose = mc2.text_input("Liều lượng", value="1 viên")
                man_time = mc3.selectbox("Thời điểm", ["Sáng", "Trưa", "Chiều", "Tối"])
                man_type = mc4.text_input("Loại/nhóm thuốc")
                mc5, mc6, mc7 = st.columns(3)
                man_color = mc5.text_input("Màu sắc viên thuốc (tuỳ chọn)", placeholder="VD: trắng, đỏ")
                man_shape = mc6.selectbox("Hình dạng (tuỳ chọn)",
                                           ["", "Tròn", "Oval", "Viên nén", "Vuông", "Con nhộng"])
                man_qty = mc7.number_input("Số lượng còn lại (tuỳ chọn)", min_value=0, value=0, step=1)
                # ---- Mới: 3 ô Nơi khám bệnh / Bác sĩ điều trị / Nơi cấp thuốc hiển thị TRỰC TIẾP
                # trong form nhập thuốc thủ công, mặc định lấy giá trị từ ô "Thông tin nơi khám &
                # cấp thuốc" ở đầu trang, nhưng người dùng vẫn thấy rõ và có thể sửa lại ngay tại đây.
                st.markdown("**Thông tin nơi khám & cấp thuốc cho thuốc này**")
                mc8, mc9, mc10 = st.columns(3)
                man_clinic = mc8.text_input(
                    "Nơi khám bệnh", value=rx_clinic, placeholder="VD: BV Chợ Rẫy", key="man_clinic",
                )
                man_doctor = mc9.text_input(
                    "Bác sĩ điều trị", value=rx_doctor, placeholder="VD: BS. Nguyễn Văn A", key="man_doctor",
                )
                man_pharmacy = mc10.text_input(
                    "Nơi cấp thuốc", value=rx_pharmacy, placeholder="VD: Nhà thuốc Long Châu", key="man_pharmacy",
                )
                if st.form_submit_button("Thêm vào tủ thuốc"):
                    if man_name.strip():
                        new_med_entry = {
                            "Tên thuốc": man_name.strip(), "Liều lượng": man_dose.strip(),
                            "Thời điểm": man_time, "Loại": man_type.strip(),
                            "Màu sắc": man_color.strip(), "Hình dạng": man_shape,
                            "Nơi khám bệnh": man_clinic.strip(),
                            "Bác sĩ điều trị": man_doctor.strip(),
                            "Nơi cấp thuốc": man_pharmacy.strip(),
                        }
                        if man_qty > 0:
                            new_med_entry["Số lượng còn lại"] = int(man_qty)
                        st.session_state.med_data.append(new_med_entry)
                        save_med_data_to_supabase()
                        st.success("✅ Đã thêm thuốc.")
                        st.rerun()
                    else:
                        st.warning("Vui lòng nhập tên thuốc.")

    # ---------------- TAB TỦ THUỐC SỐ ----------------
    with tab_cabinet:
        st.header("🗄️ Tủ thuốc số & nhật ký tuân thủ")
        if detected_conflicts:
            st.error("🚨 **CẢNH BÁO:** Phát hiện tương tác thuốc trong tủ thuốc hiện tại!")
            for c in detected_conflicts:
                st.markdown(f"> **{c['thuoc_1']}** ↔ **{c['thuoc_2']}** \n"
                            f"> Mức độ: **{c['severity']}** — {c['effect']}  \n"
                            f"> _Nguồn tham khảo: {c.get('nguon', DEFAULT_SOURCE_NOTE)}_")
            st.warning("⚠️ Vui lòng tham khảo ý kiến bác sĩ/dược sĩ trước khi tiếp tục phối hợp các thuốc trên.")

        # ===== MỚI: Cảnh báo tương tác thuốc – thực phẩm/thảo dược kiểu Việt Nam =====
        food_herb_warnings = check_food_herb_conflicts(st.session_state.med_data)
        if food_herb_warnings:
            st.warning("🍽️ **Cảnh báo tương tác với thực phẩm/thảo dược phổ biến ở Việt Nam:**")
            for w in food_herb_warnings:
                st.markdown(f"> **{w['thuoc']}** ↔ *{w['item']}* — Mức độ: **{w['severity']}**  \n"
                            f"> {w['effect']}  \n"
                            f"> _Nguồn tham khảo: {w.get('nguon', DEFAULT_SOURCE_NOTE)}_")
            st.caption("Cơ sở dữ liệu minh họa, chưa đầy đủ toàn bộ thuốc nam/TPCN trên thị trường.")

        if not med_data_valid:
            st.info("Tủ thuốc trống. Hãy quét đơn thuốc hoặc thêm thuốc thủ công ở tab trước.")
        else:
            st.subheader("📋 Danh mục thuốc hiện có")
            for idx, med in enumerate(list(st.session_state.med_data)):
                cols = st.columns([0.6, 2.4, 2, 2, 1.6, 1.4, 1])
                cols[0].markdown(render_pill_icon_html(med.get("Màu sắc", ""), med.get("Hình dạng", "")),
                                  unsafe_allow_html=True)
                cols[1].markdown(f"**{med.get('Tên thuốc','')}**")
                cols[2].markdown(med.get("Liều lượng", ""))
                cols[3].markdown(med.get("Thời điểm", ""))
                cols[4].markdown(med.get("Loại", ""))
                qty_left = med.get("Số lượng còn lại")
                qty_display = f"{qty_left} liều" if qty_left is not None else "—"
                cols[5].markdown(qty_display)
                if cols[6].button("🗑️", key=f"del_{idx}"):
                    st.session_state.med_data.pop(idx)
                    save_med_data_to_supabase()
                    st.rerun()
                # ---- Mới: hiển thị nơi khám/bác sĩ/nơi cấp thuốc nếu có ----
                clinic = med.get("Nơi khám bệnh", "")
                doctor = med.get("Bác sĩ điều trị", "")
                pharmacy = med.get("Nơi cấp thuốc", "")
                if clinic or doctor or pharmacy:
                    detail_bits = []
                    if clinic:
                        detail_bits.append(f"🏥 Nơi khám: {clinic}")
                    if doctor:
                        detail_bits.append(f"👨‍⚕️ BS điều trị: {doctor}")
                    if pharmacy:
                        detail_bits.append(f"💊 Nơi cấp thuốc: {pharmacy}")
                    st.caption(" | ".join(detail_bits))

    # ---------------- TAB TRA CỨU TƯƠNG TÁC ----------------
    with tab_matrix:
        st.header("🔬 Tra cứu & mô phỏng tương tác thuốc")
        st.caption("Kiểm tra nhanh 2 loại thuốc, hoặc 1 thuốc với thực phẩm/thảo dược (VD: rượu bia, "
                   "bưởi, thuốc nam...), trước khi phối hợp sử dụng.")
        col_t1, col_t2 = st.columns(2)
        t1 = col_t1.text_input("Thuốc A", value="Aspirin")
        t2 = col_t2.text_input("Thuốc B (hoặc thực phẩm/thảo dược)", value="Ibuprofen")
        if st.button("Kiểm tra tương tác", type="primary", use_container_width=True):
            # ---- SỬA LỖI: trước đây chỉ gọi check_interaction() (chỉ tra thuốc–thuốc),
            # nên các cặp thuốc–thực phẩm/thảo dược (VD: Aspirin + rượu) luôn báo "an toàn"
            # dù VN_FOOD_HERB_DATABASE đã có sẵn dữ liệu. Giờ đối chiếu CẢ 2 cơ sở dữ liệu. ----
            drug_result = check_interaction(t1, t2)
            food_results = check_food_herb_pair(t1, t2)

            if drug_result:
                st.error(f"🚨 PHÁT HIỆN TƯƠNG TÁC THUỐC–THUỐC (Mức độ: {drug_result['severity']})")
                st.markdown(f"- **Cặp thuốc:** `{drug_result['thuoc_1']}` và `{drug_result['thuoc_2']}`\n"
                            f"- **Hệ quả:** {drug_result['effect']}\n"
                            f"- **Nguồn tham khảo:** {drug_result.get('nguon', DEFAULT_SOURCE_NOTE)}\n"
                            f"- **Khuyến cáo:** Không tự ý phối hợp, hỏi ý kiến bác sĩ/dược sĩ.")

            if food_results:
                for fr in food_results:
                    st.warning(f"⚠️ PHÁT HIỆN TƯƠNG TÁC THUỐC–THỰC PHẨM/THẢO DƯỢC (Mức độ: {fr['severity']})")
                    st.markdown(f"- **{fr['thuoc']}** ↔ *{fr['item']}*\n"
                                f"- **Hệ quả:** {fr['effect']}\n"
                                f"- **Nguồn tham khảo:** {fr.get('nguon', DEFAULT_SOURCE_NOTE)}\n"
                                f"- **Khuyến cáo:** Tránh phối hợp, hỏi ý kiến bác sĩ/dược sĩ nếu cần dùng chung.")

            if not drug_result and not food_results:
                st.success(f"✅ Chưa ghi nhận tương tác giữa `{t1.strip().capitalize()}` và "
                           f"`{t2.strip().capitalize()}` trong cơ sở dữ liệu hiện tại "
                           f"(đã kiểm tra cả thuốc–thuốc và thuốc–thực phẩm/thảo dược).")
        st.caption("Lưu ý: cơ sở dữ liệu minh họa chỉ bao gồm một số hoạt chất và thực phẩm/thảo dược "
                   "phổ biến, không thay thế tra cứu dược thư chính thức.")

    # ---------------- TAB HỎI ĐÁP AI ----------------
    with tab_expert:
        st.header("🤖 Trợ lý hỏi đáp về thuốc & sức khỏe")
        st.caption(DISCLAIMER)
        st.caption(
            "🔎 Trợ lý được yêu cầu ưu tiên đối chiếu các nguồn uy tín (Drugs.com, Dược thư Quốc gia "
            "Việt Nam, MedlinePlus, các bệnh viện lớn...) và đính kèm liên kết nguồn ở cuối câu trả lời "
            "khi tìm được, giúp bạn tự kiểm chứng lại thông tin."
        )
        for message in st.session_state.chat_history:
            with st.chat_message(message["role"]):
                st.markdown(message["content"])
        user_query = st.chat_input("Hỏi về liều lượng, tác dụng phụ, triệu chứng...")
        if user_query:
            st.session_state.chat_history.append({"role": "user", "content": user_query})
            with st.chat_message("user"):
                st.markdown(user_query)
            with st.chat_message("assistant"):
                with st.spinner("Đang phân tích..."):
                    try:
                        full_prompt = f"""
Bạn là trợ lý dược sĩ AI của ứng dụng SafePill. Luôn nhắc người dùng đây là thông tin tham khảo,
không thay thế chỉ định của bác sĩ, và đề nghị đi khám nếu triệu chứng nghiêm trọng hoặc kéo dài.
Khi trả lời về liều dùng, tác dụng phụ hoặc tương tác thuốc, ƯU TIÊN đối chiếu các nguồn uy tín như
Drugs.com, Dược thư Quốc gia Việt Nam, MedlinePlus, hoặc các bệnh viện/tổ chức y tế lớn (Mayo Clinic,
Bệnh viện Bạch Mai, Bệnh viện Chợ Rẫy...) để đảm bảo thông tin chính xác nhất có thể.
Thông tin bệnh nhân: {st.session_state.current_profile.get('full_name', 'Ẩn danh')}.
Tủ thuốc hiện tại: {st.session_state.med_data}.
Tương tác đã phát hiện: {detected_conflicts}.
Câu hỏi: '{user_query}'.
Hãy trả lời ngắn gọn, chính xác, dễ hiểu bằng tiếng Việt.
"""
                        # ---- Mới: bật công cụ tìm kiếm (grounding) của Gemini nếu SDK hỗ trợ, để AI
                        # có thể đối chiếu thông tin thực tế từ các trang uy tín và trả về nguồn trích dẫn.
                        if GEMINI_SEARCH_GROUNDING_AVAILABLE:
                            try:
                                response = ai_gemini.models.generate_content(
                                    model="gemini-flash-latest",
                                    contents=full_prompt,
                                    config=genai_types.GenerateContentConfig(
                                        tools=[genai_types.Tool(google_search=genai_types.GoogleSearch())]
                                    ),
                                )
                            except Exception:
                                # SDK/model hiện tại không hỗ trợ grounding -> quay về gọi thường
                                response = ai_gemini.models.generate_content(
                                    model="gemini-flash-latest", contents=full_prompt,
                                )
                        else:
                            response = ai_gemini.models.generate_content(
                                model="gemini-flash-latest", contents=full_prompt,
                            )
                        ai_response = response.text or ""

                        # ---- Mới: trích xuất các nguồn tham khảo (grounding citations) nếu có ----
                        source_links = []
                        try:
                            candidate = response.candidates[0]
                            grounding = getattr(candidate, "grounding_metadata", None)
                            chunks = getattr(grounding, "grounding_chunks", None) if grounding else None
                            if chunks:
                                for chunk in chunks:
                                    web_info = getattr(chunk, "web", None)
                                    if web_info and getattr(web_info, "uri", None):
                                        title = getattr(web_info, "title", None) or web_info.uri
                                        source_links.append((title, web_info.uri))
                        except Exception:
                            source_links = []

                        if source_links:
                            ai_response += "\n\n**Nguồn tham khảo:**\n"
                            seen_uris = set()
                            for title, uri in source_links:
                                if uri in seen_uris:
                                    continue
                                seen_uris.add(uri)
                                ai_response += f"- [{title}]({uri})\n"

                        st.markdown(ai_response)
                        st.session_state.chat_history.append({"role": "assistant", "content": ai_response})
                    except Exception as e:
                        st.error(f"Lỗi kết nối AI: {e}")

    # ---------------- MỚI: TAB BÁO CÁO TUÂN THỦ (biểu đồ theo thời gian + xuất PDF/ảnh) ----------------
    with tab_report:
        st.header("📈 Báo cáo tuân thủ điều trị")
        st.caption("Theo dõi tỷ lệ tuân thủ theo ngày và xuất báo cáo để mang đi khám bệnh.")

        total_tasks_today = len(med_data_valid)
        done_tasks_today = sum(1 for v in st.session_state.adherence_logs.values() if v)

        rc1, rc2 = st.columns([2, 1])
        rc1.metric("Tỷ lệ tuân thủ hôm nay",
                    f"{int((done_tasks_today/total_tasks_today)*100) if total_tasks_today else 0}%")
        if rc2.button("💾 Lưu tuân thủ hôm nay vào lịch sử", use_container_width=True):
            if total_tasks_today == 0:
                st.warning("⚠️ Chưa có lịch thuốc hôm nay để lưu.")
            else:
                ok, err = log_adherence_snapshot(st.session_state.user_phone, total_tasks_today, done_tasks_today)
                if ok:
                    st.session_state.adherence_logged_today = True
                    st.success("✅ Đã lưu snapshot tuân thủ hôm nay.")
                else:
                    st.error(ADHERENCE_HISTORY_MISSING_MSG if _is_missing_table_error(err)
                              else f"Lỗi: {err}")

        st.divider()
        st.subheader("📉 Biểu đồ tuân thủ theo thời gian")
        history_rows = fetch_adherence_history(st.session_state.user_phone, days=30)
        if not history_rows:
            st.info("ℹ️ Chưa có dữ liệu lịch sử. Hãy bấm nút 'Lưu tuân thủ hôm nay vào lịch sử' mỗi ngày "
                    "để bắt đầu tích luỹ dữ liệu cho biểu đồ.")
        elif not MATPLOTLIB_AVAILABLE:
            st.warning("⚠️ Thư viện matplotlib chưa được cài đặt trên máy chủ. Hãy thêm 'matplotlib' vào "
                       "requirements.txt để hiển thị biểu đồ.")
            st.table(history_rows)
        else:
            dates = [r["log_date"] for r in history_rows]
            rates = [float(r.get("rate", 0)) for r in history_rows]
            fig, ax = plt.subplots(figsize=(9, 4))
            ax.plot(dates, rates, marker="o", color="#006a62", linewidth=2)
            ax.set_ylim(0, 105)
            ax.set_ylabel("Tỷ lệ tuân thủ (%)")
            ax.set_xlabel("Ngày")
            ax.set_title(f"Tuân thủ điều trị — {st.session_state.current_profile.get('full_name','')}")
            ax.grid(True, alpha=0.3)
            plt.xticks(rotation=45, ha="right")
            fig.tight_layout()
            st.pyplot(fig)

            # ---- Xuất báo cáo dạng PDF (dùng chính biểu đồ matplotlib, không cần thư viện PDF khác) ----
            pdf_buffer = io.BytesIO()
            fig.savefig(pdf_buffer, format="pdf")
            pdf_buffer.seek(0)
            png_buffer = io.BytesIO()
            fig.savefig(png_buffer, format="png", dpi=150)
            png_buffer.seek(0)
            dl1, dl2 = st.columns(2)
            dl1.download_button("📄 Tải báo cáo PDF", data=pdf_buffer,
                                  file_name=f"bao_cao_tuan_thu_{st.session_state.user_phone}.pdf",
                                  mime="application/pdf", use_container_width=True)
            dl2.download_button("🖼️ Tải ảnh PNG", data=png_buffer,
                                  file_name=f"bao_cao_tuan_thu_{st.session_state.user_phone}.png",
                                  mime="image/png", use_container_width=True)
            plt.close(fig)
            st.caption("💡 Mang file PDF/ảnh này đi khám để bác sĩ nắm được mức độ tuân thủ điều trị của bạn.")

    # ---------------- MỚI: TAB THẺ QR KHẨN CẤP ----------------
    with tab_qr:
        st.header("🆘 Thẻ QR khẩn cấp")
        st.info(
            "ℹ️ Mã QR này chứa danh sách thuốc đang dùng, cảnh báo tương tác và số điện thoại người thân. "
            "In và dán lên ví hoặc tủ thuốc — khi gặp cấp cứu, người xung quanh hoặc nhân viên y tế chỉ "
            "cần quét mã là biết ngay bạn đang dùng thuốc gì."
        )
        family_members_for_qr = fetch_family_members(st.session_state.user_phone)
        qr_text = build_emergency_qr_text(
            st.session_state.current_profile, st.session_state.med_data,
            detected_conflicts, family_members_for_qr,
        )
        if not QRCODE_AVAILABLE:
            st.warning("⚠️ Thư viện 'qrcode' chưa được cài đặt trên máy chủ. Hãy thêm 'qrcode[pil]' vào "
                       "requirements.txt để bật tính năng này.")
        else:
            qr_img = generate_qr_image(qr_text)
            qcol1, qcol2 = st.columns([1, 1.4])
            with qcol1:
                st.image(qr_img, caption="Quét mã để xem thông tin khẩn cấp", width=280)
                qr_buf = io.BytesIO()
                qr_img.save(qr_buf, format="PNG")
                qr_buf.seek(0)
                st.download_button("⬇️ Tải mã QR (PNG)", data=qr_buf,
                                     file_name=f"safepill_qr_khan_cap_{st.session_state.user_phone}.png",
                                     mime="image/png", use_container_width=True)
            with qcol2:
                st.markdown("**Nội dung được mã hoá trong QR:**")
                st.code(qr_text, language=None)

            st.divider()
            st.subheader("🔒 Đặt làm hình nền màn hình khoá")
            st.info(
                "💡 Rất khuyến khích: đặt ảnh này làm **hình nền màn hình khoá (Lock Screen)** của điện "
                "thoại. Nhờ vậy, khi máy đang khoá và bạn không tỉnh táo để mở khoá, người sơ cứu hoặc "
                "nhân viên y tế vẫn nhìn thấy và quét được mã QR ngay trên màn hình khoá mà **không cần "
                "mật khẩu**."
            )
            size_choice = st.selectbox("Chọn kích thước theo loại máy", list(WALLPAPER_SIZES.keys()))
            wallpaper_img = generate_lockscreen_wallpaper(
                qr_img, st.session_state.current_profile, detected_conflicts, size_choice
            )
            wcol1, wcol2 = st.columns([1, 1.4])
            with wcol1:
                st.image(wallpaper_img, caption="Xem trước hình nền", width=260)
                wallpaper_buf = io.BytesIO()
                wallpaper_img.save(wallpaper_buf, format="PNG")
                wallpaper_buf.seek(0)
                st.download_button(
                    "⬇️ Tải hình nền màn hình khoá", data=wallpaper_buf,
                    file_name=f"safepill_lockscreen_{st.session_state.user_phone}.png",
                    mime="image/png", use_container_width=True, type="primary",
                )
            with wcol2:
                st.markdown(
                    "**Cách đặt làm hình nền màn hình khoá:**\n\n"
                    "- **iPhone:** Tải ảnh → mở app *Ảnh* → chọn ảnh vừa tải → bấm nút Chia sẻ → "
                    "*Dùng làm hình nền* → chọn **Màn hình khoá** (Lock Screen) → Xong.\n"
                    "- **Android:** Tải ảnh → mở ảnh trong *Thư viện* → chạm menu ⋮ → *Đặt làm hình nền* → "
                    "chọn **Màn hình khoá**.\n\n"
                    "⚠️ Lưu ý: một số dòng máy có Face ID/vân tay hoặc widget đồng hồ có thể che một phần "
                    "hình — hãy tự kiểm tra lại màn hình khoá sau khi đặt để đảm bảo mã QR không bị che."
                )
        st.caption(
            "ℹ️ Lưu ý: mã QR chỉ chứa thông tin bạn tự khai báo trong SafePill, không thay thế hồ sơ bệnh án "
            "chính thức. Hãy cập nhật lại mã mỗi khi thay đổi thuốc."
        )

    # ---------------- TAB CÀI ĐẶT ----------------
    with tab_settings:
        st.header("⚙️ Cài đặt")
        sub_account, sub_schedule, sub_notification, sub_family = st.tabs([
            "👤 Tài khoản", "⏰ Lịch uống thuốc", "🔔 Thông báo & Âm thanh", "👪 Người thân",
        ])

        # ===== TÀI KHOẢN =====
        with sub_account:
            st.subheader("Thông tin cá nhân")
            with st.form("update_profile_form"):
                new_name = st.text_input(
                    "Họ và tên",
                    value=st.session_state.current_profile.get("full_name", ""),
                )
                # ---- Mới: chỉnh sửa/bổ sung nhóm máu, hữu ích cho tình huống cấp cứu ----
                current_blood = st.session_state.current_profile.get("blood_type") or "Chưa rõ"
                new_blood_type = st.selectbox(
                    "🩸 Nhóm máu",
                    BLOOD_TYPE_OPTIONS,
                    index=BLOOD_TYPE_OPTIONS.index(current_blood) if current_blood in BLOOD_TYPE_OPTIONS else 0,
                )
                submit_name = st.form_submit_button("Lưu thông tin")
                if submit_name:
                    if not new_name.strip():
                        st.error("❌ Họ và tên không được để trống.")
                    else:
                        try:
                            try:
                                supabase.table(TABLE).update({
                                    "full_name": new_name.strip(), "blood_type": new_blood_type,
                                }).eq("phone", st.session_state.user_phone).execute()
                            except Exception as update_err:
                                if "blood_type" in str(update_err) or "column" in str(update_err).lower():
                                    # Bảng chưa có cột blood_type: chỉ lưu họ tên
                                    supabase.table(TABLE).update({"full_name": new_name.strip()}).eq(
                                        "phone", st.session_state.user_phone
                                    ).execute()
                                    st.warning(
                                        "⚠️ Bảng dữ liệu chưa có cột 'blood_type'. Hãy chạy migration SQL "
                                        "thêm cột này để lưu nhóm máu."
                                    )
                                else:
                                    raise
                            st.session_state.current_profile["full_name"] = new_name.strip()
                            st.session_state.current_profile["blood_type"] = new_blood_type
                            st.success("✅ Đã cập nhật thông tin cá nhân.")
                            st.rerun()
                        except Exception as e:
                            st.error(f"Lỗi cập nhật: {e}")

            st.divider()
            st.subheader("Đổi mã PIN")
            with st.form("change_pin_form", clear_on_submit=True):
                old_pin = st.text_input("Mã PIN hiện tại", type="password", max_chars=4)
                new_pin = st.text_input("Mã PIN mới", type="password", max_chars=4)
                confirm_pin = st.text_input("Xác nhận mã PIN mới", type="password", max_chars=4)
                submit_pin = st.form_submit_button("Đổi PIN")
                if submit_pin:
                    old_pin_clean = old_pin.strip()
                    new_pin_clean = new_pin.strip()
                    confirm_pin_clean = confirm_pin.strip()
                    if not old_pin_clean or not new_pin_clean or not confirm_pin_clean:
                        st.error("❌ Vui lòng điền đầy đủ cả 3 trường.")
                    elif not verify_pin(old_pin_clean, st.session_state.current_profile.get("pin")):
                        st.error("❌ Mã PIN hiện tại không đúng.")
                    elif len(new_pin_clean) != 4 or not new_pin_clean.isdigit():
                        st.error("❌ Mã PIN mới phải gồm đúng 4 chữ số.")
                    elif new_pin_clean != confirm_pin_clean:
                        st.error("❌ Xác nhận mã PIN mới không khớp.")
                    else:
                        try:
                            new_hash = hash_pin(new_pin_clean)
                            supabase.table(TABLE).update({"pin": new_hash}).eq(
                                "phone", st.session_state.user_phone
                            ).execute()
                            st.session_state.current_profile["pin"] = new_hash
                            st.success("✅ Đã đổi mã PIN thành công.")
                        except Exception as e:
                            st.error(f"Lỗi cập nhật: {e}")

            st.divider()
            st.subheader("FaceID")
            has_face = bool(st.session_state.current_profile.get("face_hash"))
            if has_face:
                st.success("✅ Tài khoản đã đăng ký FaceID.")
            else:
                st.info("ℹ️ Tài khoản chưa đăng ký FaceID.")
            with st.expander("📷 Chụp lại / đăng ký FaceID mới"):
                new_face_img = st.camera_input("Chụp khuôn mặt", key="settings_face_cam")
                if new_face_img is not None and st.button("Lưu FaceID", key="save_face_btn"):
                    try:
                        face_bytes = new_face_img.getvalue()
                        new_face_hash = average_hash(face_bytes)
                        if not new_face_hash:
                            st.error("❌ Không xử lý được ảnh, vui lòng thử lại.")
                        else:
                            supabase.table(TABLE).update({
                                "face_data": base64.b64encode(face_bytes).decode("utf-8"),
                                "face_hash": new_face_hash,
                            }).eq("phone", st.session_state.user_phone).execute()
                            st.session_state.current_profile["face_hash"] = new_face_hash
                            st.success("✅ Đã lưu FaceID mới.")
                            st.rerun()
                    except Exception as e:
                        st.error(
                            f"❌ Không thể lưu FaceID (kiểm tra đã chạy migration thêm cột face_data/face_hash "
                            f"chưa): {e}"
                        )
            if has_face:
                if st.button("🗑️ Xoá FaceID", key="remove_face_btn"):
                    try:
                        supabase.table(TABLE).update({"face_data": None, "face_hash": None}).eq(
                            "phone", st.session_state.user_phone
                        ).execute()
                        st.session_state.current_profile["face_hash"] = None
                        st.success("✅ Đã xoá FaceID khỏi tài khoản.")
                        st.rerun()
                    except Exception as e:
                        st.error(f"Lỗi xoá FaceID: {e}")

        # ===== LỊCH UỐNG THUỐC =====
        with sub_schedule:
            st.subheader("Chỉnh giờ nhắc & liều lượng từng loại thuốc")
            if not med_data_valid:
                st.info("Chưa có thuốc nào trong tủ thuốc để đặt lịch. Hãy quét đơn hoặc thêm thuốc thủ công.")
            else:
                for idx, med in enumerate(list(st.session_state.med_data)):
                    med_label = med.get("Tên thuốc") or f"Thuốc #{idx + 1}"
                    with st.expander(f"💊 {med_label}"):
                        col1, col2 = st.columns(2)
                        new_dose = col1.text_input(
                            "Liều lượng", value=med.get("Liều lượng", ""), key=f"sched_dose_{idx}"
                        )
                        default_hhmm = resolve_reminder_time(med.get("Thời điểm", ""))
                        h, mnt = default_hhmm.split(":")
                        default_time_obj = dtime(int(h), int(mnt))
                        new_time = col2.time_input(
                            "Giờ nhắc chính xác", value=default_time_obj, key=f"sched_time_{idx}"
                        )
                        # ---- Mới: chỉnh màu sắc/hình dạng/số lượng còn lại ----
                        col3, col4, col5 = st.columns(3)
                        new_color = col3.text_input(
                            "Màu sắc viên thuốc", value=med.get("Màu sắc", ""), key=f"sched_color_{idx}"
                        )
                        new_shape = col4.selectbox(
                            "Hình dạng", ["", "Tròn", "Oval", "Viên nén", "Vuông", "Con nhộng"],
                            index=(["", "Tròn", "Oval", "Viên nén", "Vuông", "Con nhộng"]
                                   .index(med.get("Hình dạng")) if med.get("Hình dạng") in
                                   ["", "Tròn", "Oval", "Viên nén", "Vuông", "Con nhộng"] else 0),
                            key=f"sched_shape_{idx}",
                        )
                        new_qty = col5.number_input(
                            "Số lượng còn lại", min_value=0,
                            value=int(med.get("Số lượng còn lại", 0) or 0), step=1, key=f"sched_qty_{idx}",
                        )
                        # ---- Mới: chỉnh nơi khám bệnh / bác sĩ điều trị / nơi cấp thuốc ----
                        col6, col7, col8 = st.columns(3)
                        new_clinic = col6.text_input(
                            "Nơi khám bệnh", value=med.get("Nơi khám bệnh", ""), key=f"sched_clinic_{idx}"
                        )
                        new_doctor = col7.text_input(
                            "Bác sĩ điều trị", value=med.get("Bác sĩ điều trị", ""), key=f"sched_doctor_{idx}"
                        )
                        new_pharmacy = col8.text_input(
                            "Nơi cấp thuốc", value=med.get("Nơi cấp thuốc", ""), key=f"sched_pharmacy_{idx}"
                        )
                        if st.button("Lưu thay đổi", key=f"sched_save_{idx}"):
                            st.session_state.med_data[idx]["Liều lượng"] = new_dose
                            st.session_state.med_data[idx]["Thời điểm"] = new_time.strftime("%H:%M")
                            st.session_state.med_data[idx]["Màu sắc"] = new_color
                            st.session_state.med_data[idx]["Hình dạng"] = new_shape
                            st.session_state.med_data[idx]["Số lượng còn lại"] = int(new_qty)
                            st.session_state.med_data[idx]["Nơi khám bệnh"] = new_clinic
                            st.session_state.med_data[idx]["Bác sĩ điều trị"] = new_doctor
                            st.session_state.med_data[idx]["Nơi cấp thuốc"] = new_pharmacy
                            save_med_data_to_supabase()
                            st.success(f"✅ Đã cập nhật lịch nhắc cho {med_label}.")
                            st.rerun()
                st.caption(
                    "💡 Lịch nhắc & thông tin nơi khám/bác sĩ/nơi cấp thuốc được lưu bền lên Supabase "
                    "(cột 'diagnostic'), sẽ không mất khi tải lại trang hoặc đăng nhập lại."
                )

        # ===== MỚI: THÔNG BÁO & ÂM THANH =====
        with sub_notification:
            st.subheader("Tuỳ chỉnh thông báo & âm thanh nhắc nhở")
            st.caption(
                "Âm thanh sẽ phát cùng lúc với thông báo trên trình duyệt điện thoại/máy tính khi đến "
                "giờ nhắc uống thuốc hoặc nhắc nhở thủ công. Cần cho phép quyền Notification và giữ "
                "tab SafePill đang mở (hoặc chạy nền) để nhận được nhắc nhở."
            )
            sound_options = {"beep": "🔔 Beep (mặc định)", "chime": "🎐 Chime (chuông nhẹ)", "bell": "🔔 Bell (chuông lớn)"}
            sound_keys = list(sound_options.keys())
            selected_sound = st.selectbox(
                "Loại âm thanh nhắc nhở",
                options=sound_keys,
                format_func=lambda x: sound_options[x],
                index=sound_keys.index(st.session_state.reminder_sound),
            )
            selected_volume = st.slider(
                "Âm lượng", min_value=0.0, max_value=1.0,
                value=float(st.session_state.reminder_volume), step=0.1,
            )
            if (selected_sound != st.session_state.reminder_sound
                    or selected_volume != st.session_state.reminder_volume):
                st.session_state.reminder_sound = selected_sound
                st.session_state.reminder_volume = selected_volume
                st.success("✅ Đã lưu cài đặt âm thanh nhắc nhở.")

            st.markdown("**Nghe thử âm thanh:**")
            test_sound_js = build_reminder_sound_script(selected_sound, selected_volume)
            # SỬA LỖI: st.html() không nhận height -> gây TypeError, dùng components.html() thay thế.
            components.html(f"""
            <button id="testSoundBtn" style="padding:8px 16px;border-radius:8px;border:none;
            background:#006a62;color:white;cursor:pointer;font-size:14px;">▶ Nghe thử</button>
            <script>
            {test_sound_js}
            document.getElementById('testSoundBtn').addEventListener('click', function() {{
                playReminderSound("{selected_sound}", {selected_volume});
            }});
            </script>
            """, height=60)

            st.caption(
                "💡 Mẹo: trên điện thoại, hãy thêm SafePill vào màn hình chính (Add to Home Screen) và "
                "cho phép quyền Thông báo trong trình duyệt để nhận nhắc nhở ổn định hơn."
            )

            st.divider()
            st.subheader("🔊 Đọc to bằng giọng nói (Text-to-Speech)")
            st.session_state.tts_enabled = st.toggle(
                "Hiện nút 🔊 đọc to tên thuốc/liều lượng ở tab Hôm nay",
                value=st.session_state.tts_enabled,
            )
            st.caption(
                "👴 Dành cho người già không quen thao tác chữ nhỏ: bấm nút 🔊 cạnh mỗi thuốc để nghe "
                "đọc to tên thuốc, liều lượng và thời điểm uống bằng giọng tiếng Việt của trình duyệt."
            )

        # ===== MỚI: NGƯỜI THÂN =====
        with sub_family:
            st.subheader("👪 Người thân nhắc nhở tôi")
            st.caption(
                "Mời một người thân (đã có tài khoản SafePill) để họ có thể gửi nhắc nhở trực tiếp "
                "đến bạn — ví dụ: \"Con nhắc mẹ uống thuốc huyết áp nhé!\". Người thân cần đăng nhập "
                "bằng chính số điện thoại của họ và chấp nhận lời mời trước khi gửi được nhắc nhở."
            )
            with st.form("invite_family_form", clear_on_submit=True):
                fcol1, fcol2 = st.columns(2)
                invite_phone = fcol1.text_input("SĐT người thân", placeholder="09xxxxxxxx")
                invite_name = fcol2.text_input("Tên gợi nhớ (tuỳ chọn)", placeholder="VD: Con trai")
                submit_invite = st.form_submit_button("📨 Gửi lời mời")
                if submit_invite:
                    invite_phone_clean = invite_phone.strip()
                    if not validate_phone(invite_phone_clean):
                        st.error("❌ Số điện thoại không hợp lệ.")
                    elif invite_phone_clean == st.session_state.user_phone:
                        st.error("❌ Không thể tự mời chính mình.")
                    else:
                        ok, err = create_family_invite(
                            st.session_state.user_phone, invite_phone_clean, invite_name
                        )
                        if ok:
                            st.success(f"✅ Đã gửi lời mời đến {invite_phone_clean}.")
                            st.rerun()
                        else:
                            st.error(FAMILY_TABLE_MISSING_MSG if _is_missing_table_error(err)
                                      else f"Lỗi: {err}")

            my_family_members = fetch_family_members(st.session_state.user_phone)
            if my_family_members:
                st.markdown("**Danh sách người thân:**")
                status_label = {"pending": "⏳ Đang chờ", "accepted": "✅ Đã chấp nhận", "declined": "❌ Đã từ chối"}
                for link in my_family_members:
                    lcols = st.columns([3, 2, 2, 1])
                    lcols[0].markdown(f"**{link.get('member_name') or link.get('member_phone')}**")
                    lcols[1].markdown(link.get("member_phone", ""))
                    lcols[2].markdown(status_label.get(link.get("status"), link.get("status", "")))
                    if lcols[3].button("🗑️", key=f"del_link_{link.get('id')}"):
                        # SỬA LỖI: trước đây bỏ qua kết quả trả về (ok, err) nên luôn rerun như thể
                        # xoá thành công dù Supabase có thể đã từ chối (thường do thiếu RLS policy
                        # DELETE cho vai trò anon), khiến người thân "xoá mãi không mất".
                        ok, err = delete_family_link(link.get("id"))
                        if ok:
                            st.rerun()
                        else:
                            st.error(FAMILY_TABLE_MISSING_MSG if _is_missing_table_error(err)
                                      else f"❌ Không xoá được liên kết: {err}")
            else:
                st.caption("Chưa mời người thân nào.")

            st.divider()
            st.subheader("📥 Lời mời đang chờ tôi phê duyệt")
            if pending_family_invites:
                for inv in pending_family_invites:
                    icols = st.columns([3, 2, 2])
                    icols[0].markdown(f"Chủ tủ thuốc: **{inv.get('owner_phone')}**")
                    if icols[1].button("✅ Chấp nhận", key=f"accept_{inv.get('id')}"):
                        # SỬA LỖI QUAN TRỌNG: đây chính là lỗi "bấm Chấp nhận mãi không được" —
                        # trước đây kết quả trả về (ok, err) bị bỏ qua nên UI LUÔN báo "Đã chấp
                        # nhận thành công" dù Supabase thực tế từ chối cập nhật (thường do thiếu
                        # RLS policy UPDATE cho vai trò anon trên bảng safepill_family_links).
                        # Lời mời vì vậy không bao giờ chuyển sang "accepted" và cứ hiện lại mãi.
                        ok, err = update_family_link_status(inv.get("id"), "accepted")
                        if ok:
                            st.success("✅ Đã chấp nhận làm người thân theo dõi.")
                            st.rerun()
                        else:
                            st.error(
                                FAMILY_TABLE_MISSING_MSG if _is_missing_table_error(err) else
                                f"❌ Không cập nhật được trạng thái lời mời. Nguyên nhân thường gặp: "
                                f"bảng 'safepill_family_links' chưa có RLS policy cho phép UPDATE với "
                                f"vai trò anon. Chi tiết lỗi: {err}"
                            )
                    if icols[2].button("❌ Từ chối", key=f"decline_{inv.get('id')}"):
                        ok, err = update_family_link_status(inv.get("id"), "declined")
                        if ok:
                            st.rerun()
                        else:
                            st.error(FAMILY_TABLE_MISSING_MSG if _is_missing_table_error(err)
                                      else f"❌ Không cập nhật được trạng thái lời mời: {err}")
            else:
                st.caption("Không có lời mời nào đang chờ.")

            st.divider()
            st.subheader("📤 Gửi nhắc nhở cho người thân tôi đang theo dõi")
            owners_i_help = fetch_owners_i_help(st.session_state.user_phone)
            if not owners_i_help:
                st.caption(
                    "Bạn chưa được ai chấp nhận cho vai trò người thân. Khi có người mời và bạn chấp "
                    "nhận ở mục trên, họ sẽ xuất hiện tại đây để bạn gửi nhắc nhở."
                )
            else:
                owner_options = {o.get("owner_phone"): o.get("owner_phone") for o in owners_i_help}

                # ---- Phần chọn giờ nằm NGOÀI form (đặt TRƯỚC form) để rerun ngay khi đổi radio ----
                send_mode = st.radio(
                    "Thời điểm gửi", ["Gửi ngay", "Đặt giờ cụ thể"],
                    horizontal=True, key="family_reminder_send_mode",
                )
                scheduled_time = None
                if send_mode == "Đặt giờ cụ thể":
                    st.caption("Chọn giờ và phút muốn gửi nhắc nhở:")
                    time_col1, time_col2 = st.columns(2)
                    selected_hour = time_col1.selectbox(
                        "Giờ", options=list(range(1, 25)), index=7,
                        format_func=lambda h: f"{h:02d} giờ", key="family_reminder_hour",
                    )
                    selected_minute = time_col2.selectbox(
                        "Phút", options=list(range(0, 60)), index=0,
                        format_func=lambda m: f"{m:02d} phút", key="family_reminder_minute",
                    )
                    # "24 giờ" hiển thị cho dễ hiểu (đếm 1→24) nhưng lưu xuống DB dạng HH:MM
                    # chuẩn 24h, nên 24 giờ được quy về 00 giờ
                    real_hour = selected_hour % 24
                    scheduled_time = f"{real_hour:02d}:{selected_minute:02d}"

                # ---- Form chỉ còn 2 ô nhập + nút submit, TẤT CẢ đều thụt lề trong khối with này ----
                with st.form("send_family_reminder_form", clear_on_submit=True):
                    target_owner = st.selectbox("Gửi nhắc nhở cho", options=list(owner_options.keys()))
                    reminder_msg = st.text_area(
                        "Nội dung nhắc nhở",
                        placeholder="VD: Nhớ uống thuốc huyết áp buổi tối nhé!",
                    )
                    submit_send = st.form_submit_button("📨 Gửi nhắc nhở")
                    if submit_send:
                        if not reminder_msg.strip():
                            st.warning("⚠️ Vui lòng nhập nội dung nhắc nhở.")
                        else:
                            ok, err = send_family_reminder(
                                owner_phone=target_owner,
                                sender_phone=st.session_state.user_phone,
                                sender_name=st.session_state.current_profile.get("full_name", ""),
                                message=reminder_msg,
                                target_time=scheduled_time,
                            )
                            if ok:
                                st.success("✅ Đã gửi nhắc nhở! Người nhận sẽ thấy thông báo kèm âm thanh "
                                           "khi mở/đang mở SafePill (đúng giờ nếu bạn đặt lịch).")
                            else:
                                st.error(FAMILY_TABLE_MISSING_MSG if _is_missing_table_error(err)
                                          else f"Lỗi: {err}")

            st.caption(
                "ℹ️ Lưu ý: nhắc nhở từ người thân chỉ hiển thị và phát âm thanh khi người nhận đang mở "
                "hoặc tải lại trang SafePill (chưa có push notification nền thật sự khi tắt trình duyệt)."
            )
