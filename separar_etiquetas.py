import streamlit as st
from pypdf import PdfReader, PdfWriter
from pypdf.generic import RectangleObject
from copy import deepcopy
from pathlib import Path
import io

# Configurações da página
st.set_page_config(page_title="Separador de Etiquetas", page_icon="🧾", layout="wide")

# ====== HEADER CENTRALIZADO ======
LOGO_PATH = Path(__file__).with_name("logo.png")

c1, c2, c3 = st.columns([1, 3, 1])
with c2:
    if LOGO_PATH.exists():
        # ajuste o width conforme desejar (ex.: 260, 300, 340)
        st.image(str(LOGO_PATH), width=300)
    st.markdown(
        "<h1 style='text-align:center; margin: 0;'>Separador de Etiquetas (4 → 1)</h1>",
        unsafe_allow_html=True
    )
    st.markdown(
        "<p style='text-align:center; margin-top: 0.25rem;'>Envie seu PDF com 4 etiquetas por página e baixe o resultado pronto para impressão.</p>",
        unsafe_allow_html=True
    )

st.divider()

# ====== UPLOADER (apenas um) ======
uploaded_file = st.file_uploader("Selecione o PDF", type=["pdf"], key="uploader_main")

# ====== PROCESSAMENTO ======
if uploaded_file is not None:
    try:
        with st.spinner("Processando..."):
            reader = PdfReader(uploaded_file)
            writer = PdfWriter()

            for page in reader.pages:
                mb = page.mediabox
                left, bottom, right, top = float(mb.left), float(mb.bottom), float(mb.right), float(mb.top)
                width, height = right - left, top - bottom

                # 4 quadrantes (2x2): topo-esq, topo-dir, baixo-esq, baixo-dir
                quadrants = [
                    (left, bottom + height/2, left + width/2, top),
                    (left + width/2, bottom + height/2, right, top),
                    (left, bottom, left + width/2, bottom + height/2),
                    (left + width/2, bottom, right, bottom + height/2),
                ]

                for x0, y0, x1, y1 in quadrants:
                    p = deepcopy(page)
                    rect = RectangleObject([x0, y0, x1, y1])
                    p.cropbox = rect
                    p.mediabox = rect
                    writer.add_page(p)

            output_pdf = io.BytesIO()
            writer.
