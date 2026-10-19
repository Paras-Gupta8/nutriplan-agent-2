"""Nutrition-themed look & feel: fresh green, teal, white, soft gray, dark text."""
import streamlit as st

COLORS = {
    "green": "#16A34A",
    "green_light": "#DCFCE7",
    "teal": "#0D9488",
    "teal_light": "#CCFBF1",
    "gray": "#F1F5F4",
    "border": "#E2E8E6",
    "text": "#12332B",
    "muted": "#5B7A72",
    "warn": "#D97706",
    "danger": "#DC2626",
}
STATUS_COLORS = {"success": "#16A34A", "warning": "#D97706", "revision_required": "#EA580C", "failed": "#DC2626"}

_CSS = f"""
<style>
.block-container {{ padding-top: 2rem; max-width: 1150px; }}
h1, h2, h3 {{ color: {COLORS['text']}; letter-spacing: -0.01em; }}
.np-header {{
    background: linear-gradient(120deg, {COLORS['green']} 0%, {COLORS['teal']} 100%);
    color: #fff; padding: 1.4rem 1.6rem; border-radius: 16px; margin-bottom: 1.2rem;
}}
.np-header h1 {{ color:#fff; margin:0; font-size:1.7rem; }}
.np-header p {{ margin:.3rem 0 0; opacity:.92; font-size:.98rem; }}
.np-card {{
    background:#fff; border:1px solid {COLORS['border']}; border-radius:14px;
    padding:1rem 1.1rem; height:100%; box-shadow:0 1px 2px rgba(18,51,43,.04);
}}
.np-card .label {{ color:{COLORS['muted']}; font-size:.78rem; text-transform:uppercase; letter-spacing:.05em; }}
.np-card .value {{ color:{COLORS['text']}; font-size:1.65rem; font-weight:700; line-height:1.25; }}
.np-card .sub {{ color:{COLORS['muted']}; font-size:.82rem; margin-top:.15rem; }}
.np-explain {{
    background:{COLORS['green_light']}; border-left:4px solid {COLORS['green']};
    padding:.7rem .9rem; border-radius:8px; color:{COLORS['text']}; font-size:.92rem;
}}
.np-disclaimer {{
    background:#FFFBEB; border:1px solid #FDE68A; color:#78350F;
    padding:.6rem .9rem; border-radius:10px; font-size:.84rem;
}}
.np-badge {{ display:inline-block; padding:.15rem .6rem; border-radius:999px; font-size:.78rem; font-weight:600; color:#fff; }}
[data-testid="stSidebar"] {{ background:{COLORS['gray']}; }}
div[data-testid="stForm"] {{ border:1px solid {COLORS['border']}; border-radius:14px; padding:1.2rem; background:#fff; }}
.stButton>button[kind="primary"], .stFormSubmitButton>button[kind="primary"],
.stFormSubmitButton>button {{ border-radius:10px; font-weight:600; }}
</style>
"""


def apply_theme() -> None:
    st.markdown(_CSS, unsafe_allow_html=True)
