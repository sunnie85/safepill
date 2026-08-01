# =====================================================================================
# THEME SAFEPILL — dán hàm này vào safepill.py, gọi NGAY SAU dòng st.set_page_config(...)
# Mục đích: nhuộm lại giao diện Streamlit theo đúng bảng màu/phong cách của landing page
# (teal-slate, font Inter, glass-card, bo góc, gradient) mà KHÔNG cần đổi bất kỳ logic nào
# trong app. Chỉ cần thêm 1 dòng gọi hàm, không phải sửa từng widget.
# =====================================================================================

import streamlit as st


def apply_safepill_theme():
    st.markdown("""
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700;800&display=swap" rel="stylesheet">
    <style>
    :root {
        --sp-teal-900: #134e4a;
        --sp-teal-800: #115e59;
        --sp-teal-700: #0f766e;
        --sp-teal-600: #0d9488;
        --sp-teal-100: #ccfbf1;
        --sp-teal-50:  #f0fdfa;
        --sp-slate-800:#1e293b;
        --sp-slate-500:#64748b;
        --sp-slate-100:#f1f5f9;
        --sp-amber-500:#f59e0b;
        --sp-red-600:  #dc2626;
    }

    /* ---------- Nền & font chung ---------- */
    html, body, [class*="css"]  {
        font-family: 'Inter', sans-serif !important;
    }
    .stApp {
        background-color: #f8fafc;
    }

    /* ---------- Tiêu đề ---------- */
    h1 { color: var(--sp-teal-800) !important; font-weight: 800 !important; letter-spacing: -0.02em; }
    h2, h3 { color: var(--sp-slate-800) !important; font-weight: 700 !important; }

    /* ---------- Sidebar: gradient teal đậm giống khối "Tầm nhìn" trong landing page ---------- */
    section[data-testid="stSidebar"] {
        background: linear-gradient(180deg, #0f766e 0%, #115e59 100%) !important;
    }
    section[data-testid="stSidebar"] * {
        color: #f0fdfa !important;
    }
    section[data-testid="stSidebar"] hr {
        border-color: rgba(255,255,255,0.15) !important;
    }
    section[data-testid="stSidebar"] .stButton button {
        background: rgba(255,255,255,0.12) !important;
        color: white !important;
        border: 1px solid rgba(255,255,255,0.25) !important;
    }

    /* ---------- Nút bấm chính (primary) ---------- */
    .stButton > button[kind="primary"], .stFormSubmitButton > button[kind="primary"] {
        background-color: var(--sp-teal-600) !important;
        border: none !important;
        border-radius: 10px !important;
        font-weight: 600 !important;
        padding: 0.55rem 1.2rem !important;
        transition: all 0.2s ease;
    }
    .stButton > button[kind="primary"]:hover, .stFormSubmitButton > button[kind="primary"]:hover {
        background-color: var(--sp-teal-700) !important;
        transform: translateY(-1px);
        box-shadow: 0 6px 14px rgba(13,148,136,0.25);
    }
    /* Nút phụ (secondary) */
    .stButton > button:not([kind="primary"]) {
        border-radius: 10px !important;
        border: 1px solid #e2e8f0 !important;
    }

    /* ---------- Tabs: giống nav-link teal underline trong landing page ---------- */
    .stTabs [data-baseweb="tab-list"] {
        gap: 4px;
        border-bottom: 1px solid #e2e8f0;
    }
    .stTabs [data-baseweb="tab"] {
        color: var(--sp-slate-500) !important;
        font-weight: 500 !important;
        padding: 10px 14px !important;
    }
    .stTabs [aria-selected="true"] {
        color: var(--sp-teal-700) !important;
        border-bottom: 2px solid var(--sp-teal-600) !important;
        font-weight: 600 !important;
    }

    /* ---------- Metric cards: style glass-card ---------- */
    div[data-testid="stMetric"] {
        background: rgba(255,255,255,0.9);
        border: 1px solid rgba(226,232,240,0.8);
        border-radius: 16px;
        padding: 16px 18px;
        box-shadow: 0 1px 3px rgba(0,0,0,0.04);
    }
    div[data-testid="stMetricLabel"] { color: var(--sp-slate-500) !important; }
    div[data-testid="stMetricValue"] { color: var(--sp-teal-700) !important; font-weight: 700 !important; }

    /* ---------- Expander: bo góc, viền nhẹ giống card ---------- */
    div[data-testid="stExpander"] {
        border: 1px solid #e2e8f0 !important;
        border-radius: 14px !important;
        background: white !important;
        overflow: hidden;
    }
    div[data-testid="stExpander"] summary {
        font-weight: 600 !important;
        color: var(--sp-slate-800) !important;
    }

    /* ---------- Form container ---------- */
    div[data-testid="stForm"] {
        background: white;
        border: 1px solid #e2e8f0;
        border-radius: 16px;
        padding: 1.2rem;
    }

    /* ---------- Input / selectbox / textarea ---------- */
    .stTextInput input, .stTextArea textarea, .stNumberInput input,
    div[data-baseweb="select"] > div {
        border-radius: 10px !important;
        border-color: #e2e8f0 !important;
    }
    .stTextInput input:focus, .stTextArea textarea:focus {
        border-color: var(--sp-teal-600) !important;
        box-shadow: 0 0 0 1px var(--sp-teal-600) !important;
    }

    /* ---------- Alert boxes: đồng bộ tông teal / amber / red ---------- */
    div[data-testid="stAlert"] {
        border-radius: 12px !important;
        border: none !important;
    }
    /* success -> teal */
    div[data-testid="stAlert"][data-baseweb-type="success"], .stSuccess {
        background-color: var(--sp-teal-50) !important;
    }
    /* warning -> amber */
    .stWarning { background-color: #fffbeb !important; }
    /* error -> red */
    .stError { background-color: #fef2f2 !important; }

    /* ---------- Progress bar ---------- */
    div[data-testid="stProgress"] > div > div {
        background-color: var(--sp-teal-600) !important;
        border-radius: 999px !important;
    }

    /* ---------- Divider mảnh, gọn ---------- */
    hr { border-color: #e2e8f0 !important; }

    /* ---------- Badge/pill nhỏ dùng cho caption nổi bật (tuỳ chọn dùng thủ công) ---------- */
    .sp-badge {
        display:inline-block;
        background:var(--sp-teal-100);
        color:var(--sp-teal-800);
        font-size:11px;
        font-weight:700;
        padding:3px 10px;
        border-radius:999px;
        text-transform:uppercase;
        letter-spacing:0.03em;
    }
    </style>
    """, unsafe_allow_html=True)


# =====================================================================================
# CÁCH DÙNG trong safepill.py:
#
#   st.set_page_config(...)
#   from theme_snippet import apply_safepill_theme   # hoặc dán thẳng hàm vào file
#   apply_safepill_theme()
#
# Chỉ cần 1 dòng gọi hàm ngay sau set_page_config, không cần sửa bất kỳ đoạn logic nào khác.
# Toàn bộ nút, tab, sidebar, form, metric, alert trong app sẽ tự động đổi theo theme mới.
# =====================================================================================