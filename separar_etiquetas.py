import streamlit as st
from pypdf import PdfReader, PdfWriter
from pypdf.generic import RectangleObject
from copy import deepcopy
from pathlib import Path
import io

# ====== HEADER ======
LOGO_PATH = Path(__file__).with_name("logo.png")
col1, col2 = st.columns([1, 3])
with col1:
    if LOGO_PATH.exists():
        st.image(str(LOGO_PATH), width=140)
with col2:
    st.title("Separador de Etiquetas (4 -> 1)")
    st.write("Envie seu PDF com 4 etiquetas por página e baixe o resultado pronto para impressão.")

# ====== UPLOADER (apenas um) ======
uploaded_file = st.file_uploader("Selecione o PDF", type=["pdf"], key="uploader_main")

# ====== PROCESSAMENTO ======
if uploaded_file is not None:
    reader = PdfReader(uploaded_file)
    writer = PdfWriter()

    for page in reader.pages:
        mb = page.mediabox
        left, bottom, right, top = float(mb.left), float(mb.bottom), float(mb.right), float(mb.top)
        width, height = right - left, top - bottom
