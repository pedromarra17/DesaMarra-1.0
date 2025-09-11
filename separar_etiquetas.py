import streamlit as st
from pypdf import PdfReader, PdfWriter
from pypdf.generic import RectangleObject
from copy import deepcopy
from pathlib import Path
import base64, io

# robust blank-page detection
import fitz  # PyMuPDF
from PIL import Image

# ================== PAGE CONFIG ==================
st.set_page_config(page_title="Separador de Etiquetas", layout="wide")

# ================== HIDE STREAMLIT BRANDING ==================
st.markdown(
    """
    <style>
    #MainMenu, footer {visibility: hidden;}
    header, [data-testid="stToolbar"], [data-testid="stDecoration"], .stDeployButton {display: none !important;}
    div[class^="viewerBadge"], div[class*="viewerBadge"] {display: none !important;}
    [data-testid="stAppViewContainer"] a[href*="streamlit.io"] {display: none !important;}
    a[href*="streamlit.io"][style*="position: fixed"],
    a[href*="streamlit.app"][style*="position: fixed"] {display: none !important;}
    </style>
    """,
    unsafe_allow_html=True,
)

# ================== HEADER (logo por tema) ==================
BASE_DIR = Path(__file__).parent
LOGO_LIGHT = BASE_DIR / "logo_light.png"
LOGO_DARK  = BASE_DIR / "logo_dark.png"

def show_logo_center(width_px: int = 480):
    theme_base = st.get_option("theme.base") or "light"
    logo_path = LOGO_LIGHT if theme_base == "light" else LOGO_DARK
    if not logo_path.exists():
        logo_path = LOGO_DARK if theme_base == "light" else LOGO_LIGHT
    if logo_path.exists():
        b64 = base64.b64encode(logo_path.read_bytes()).decode()
        st.markdown(
            f"""
            <div style="text-align:center;">
              <img src="data:image/png;base64,{b64}"
                   style="display:block;margin:0 auto;width:{width_px}px;" />
            </div>
            """,
            unsafe_allow_html=True,
        )

show_logo_center(480)
st.markdown(
    "<h1 style='text-align:center;margin:0.4rem 0 0 0;'>Separador de Etiquetas (4 -> 1)</h1>",
    unsafe_allow_html=True,
)
st.markdown(
    "<p style='text-align:center;margin-top:0.25rem;'>Envie seu PDF com 4 etiquetas por página e baixe o resultado pronto para impressão.</p>",
    unsafe_allow_html=True,
)

st.divider()

# ================== UPLOADER STYLE (500px + verde) ==================
st.markdown(
    """
    <style>
    div[data-testid="stFileUploader"] > label { font-weight: 600; }
    div[data-testid="stFileUploader"] section[data-testid="stFileUploaderDropzone"]{
        width: 500px !important;
        max-width: 100%;
        margin: 0 auto !important;
        border-radius: 12px;
        background-color: #16A34A !important;
        border: 2px dashed rgba(255,255,255,0.6);
        padding: 1.25rem;
    }
    div[data-testid="stFileUploader"] section[data-testid="stFileUploaderDropzone"] *{
        color: #FFFFFF !important;
    }
    div[data-testid="stFileUploader"] section[data-testid="stFileUploaderDropzone"]:hover{
        background-color: #15803D !important;
