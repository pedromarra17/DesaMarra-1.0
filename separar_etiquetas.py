import streamlit as st
from pypdf import PdfReader, PdfWriter
from pypdf.generic import RectangleObject
from copy import deepcopy
import io

import streamlit as st
from pathlib import Path

# --- HEADER (fora de qualquer if) ---
LOGO_PATH = Path(__file__).with_name("logo.png")

if LOGO_PATH.exists():
    st.image(str(LOGO_PATH), width=140)
else:
    st.warning(f"Logo não encontrada: {LOGO_PATH.name}")

st.title("Separador de Etiquetas (4 -> 1)")
st.write("Envie seu PDF com 4 etiquetas por página e baixe o resultado pronto para impressão.")

# --- UPLOAD (depois do header) ---
uploaded_file = st.file_uploader("Selecione o PDF", type=["pdf"])


st.title("Separador de Etiquetas (4 por página ➜ 1 por página)")
st.write("Envie seu PDF com 4 etiquetas por página e baixe o resultado pronto para impressão.")

uploaded_file = st.file_uploader("Selecione o PDF", type=["pdf"])

if uploaded_file is not None:
    reader = PdfReader(uploaded_file)
    writer = PdfWriter()

    for page in reader.pages:
        mb = page.mediabox
        left, bottom, right, top = float(mb.left), float(mb.bottom), float(mb.right), float(mb.top)
        width, height = right - left, top - bottom

        quadrants = [
            (left, bottom + height/2, left + width/2, top),  # topo-esq
            (left + width/2, bottom + height/2, right, top),  # topo-dir
            (left, bottom, left + width/2, bottom + height/2),  # baixo-esq
            (left + width/2, bottom, right, bottom + height/2),  # baixo-dir
        ]

        for x0, y0, x1, y1 in quadrants:
            new_page = deepcopy(page)
            rect = RectangleObject([x0, y0, x1, y1])
            new_page.cropbox = rect
            new_page.mediabox = rect
            writer.add_page(new_page)

    output_pdf = io.BytesIO()
    writer.write(output_pdf)
    output_pdf.seek(0)

    st.success("Separação concluída! 🎉")
    st.download_button(
        label="📥 Baixar PDF separado",
        data=output_pdf,
        file_name="etiquetas_individuais.pdf",
        mime="application/pdf"
    )
