import streamlit as st

def apply_safepill_theme():
    st.markdown("""
        <style>
        :root {
            --sp-teal-900: #134e4a;
            --sp-teal-800: #115e59;
            --sp-teal-700: #0f766e;
            --sp-teal-600: #0d9488;
            --sp-teal-100: #ccfbf1;
            --sp-teal-50:  #f0fdfa;
            --sp-slate-800: #1e293b;
            --sp-slate-500: #64748b;
        }

        .stApp { 
            font-family: 'Inter', sans-serif !important; 
            background-color: #f8fafc; 
        }

        h1 { color: var(--sp-teal-800) !important; font-weight: 800 !important; letter-spacing: -0.02em; }
        h2, h3 { color: var(--sp-slate-800) !important; font-weight: 700 !important; }

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

        .stButton > button:not([kind="primary"]) {
            border-radius: 10px !important;
            border: 1px solid #e2e8f0 !important;
        }

        .stTabs [data-baseweb="tab-list"] {
            gap: 4px;
            border-bottom: 1px solid #e2e8f0;
        }
        </style>
    """, unsafe_allow_html=True)
