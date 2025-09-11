import streamlit as st
from pypdf import PdfReader, PdfWriter
from pypdf.generic import RectangleObject
from copy import deepcopy
from pathlib import Path
import base64
import io

# ================== CONFIG DA PÁGINA ==================
st.set_page_config(page_title="Separador de Etiquetas", layout="wide")

# ---- esconder branding/menus/badge do Streamlit (não-oficial) ----
st.markdown("""
<style>
/* menu e rodapé */
#MainMenu, footer {visibility: hidden;}
/* toolbar/topbar */
header, [data-testid="stToolbar"], [data-testid="stDecoration"], .stDeployButton {display: none !important;}
/* badge "Hosted with Streamlit" (várias formas) */
div[class^="viewerBadge"], div[class*="viewerBadge"] {display: none !important;}
[data-testid="stAppViewContainer"] a[href*="streamlit.io"] {display: none !important;}
/* fallback extra: qualquer âncora fixa no canto inferior direito com "streamlit" */
a[href*="streamlit.io"][style*="position: fixed"], a[href*="streamlit.app"][style*="position: fixed"] {display: none !important;}
</style>
""", unsafe_allow_html=True)

# ================== HEADER: logo conforme tema ==================
BASE_DIR = Path(__file__).parent
LOGO_LIGHT = BASE_DIR / "logo_light.png"   # para fundo claro
LOGO_DARK  = BASE_DIR / "logo_dark.png"    # para fundo escuro

def show_logo_center(width_px=440):
    # Detecta tema atual (padrão: "light")
    theme_base = st.get_option("theme.base") or "light"
    logo_path = LOGO_LIGHT if theme_base == "light" else LOGO_DARK
    if not logo_path.exists():
        # fallback: se não existir a logo do tema, tenta a outra
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
            unsafe_allow_html=True
        )

show_logo_center(460)  # ajuste o tamanho da logo aqui

st.markdown(
    "<h1 style='text-align:center;margin:0.4rem 0 0 0;'>Separador de Etiquetas (4 → 1)</h1>",
    unsafe_allow_html=True
)
st.markdown(
    "<p style='text-align:center;margin-top:0.25rem;'>"
    "Envie seu PDF com 4 etiquetas por página e baixe o resultado pronto para impressão."
    "</p>",
    unsafe_allow_html=True
)

st.divider()

# ================== UPLOADER (APENAS 1) ==================
uploaded_file = st.file_uploader("Selecione o PDF", type=["pdf"], key="uploader_main")

# ================== FUNÇÃO PRINCIPAL ==================
def split_pdf_into_labels(file_like) -> bytes:
    reader = PdfReader(file_like)
    writer = PdfWriter()

    for page in reader.pages:
        mb = page.mediabox
        left, bottom, right, top = float(mb.left), float(mb.bottom), float(mb.right), float(mb.top)
        width, height = right - left, top - bottom

        # 4 quadrantes (2x2): topo-esq, topo-dir, baixo-esq, baixo-dir
        quads = [
            (left, bottom + height/2, left + width/2, top),
            (left + width/2, bottom + height/2, right, top),
            (left, bottom, left + width/2, bottom + height/2),
            (left + width/2, bottom, right, bottom + height/2),
        ]

        for x0, y0, x1, y1 in quads:
            p = deepcopy(page)
            rect = RectangleObject([x0, y0, x1, y1])
            p.cropbox = rect
            p.mediabox = rect
            writer.add_page(p)

    out = io.BytesIO()
    writer.write(out)
    out.seek(0)
    return out.getvalue()

# ================== EXECUÇÃO ==================
if uploaded_file is not None:
    try:
        with st.spinner("Processando..."):
            pdf_bytes = split_pdf_into_labels(uploaded_file)

        st.success("Pronto! Seu PDF foi gerado.")
        st.download_button(
            label="Baixar PDF separado",
            data=pdf_bytes,
            file_name="etiquetas_individuais.pdf",
            mime="application/pdf",
            key="download_main"
        )
    except Exception as e:
        st.error("Não foi possível processar o arquivo. Verifique se é um PDF válido.")
        st.exception(e)
else:
    st.info("Faça o upload de um PDF para iniciar o processamento.")
