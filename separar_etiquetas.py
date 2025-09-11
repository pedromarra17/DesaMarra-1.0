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
#MainMenu, footer {visibility: hidden;}
header, [data-testid="stToolbar"], [data-testid="stDecoration"], .stDeployButton {display: none !important;}
div[class^="viewerBadge"], div[class*="viewerBadge"] {display: none !important;}
[data-testid="stAppViewContainer"] a[href*="streamlit.io"] {display: none !important;}
a[href*="streamlit.io"][style*="position: fixed"], a[href*="streamlit.app"][style*="position: fixed"] {display: none !important;}
</style>
""", unsafe_allow_html=True)

# ================== HEADER: logo conforme tema ==================
BASE_DIR = Path(__file__).parent
LOGO_LIGHT = BASE_DIR / "logo_light.png"   # para fundo claro
LOGO_DARK  = BASE_DIR / "logo_dark.png"    # para fundo escuro

def show_logo_center(width_px=480):
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
            unsafe_allow_html=True
        )

show_logo_center(480)

st.markdown(
    "<h1 style='text-align:center;margin:0.4rem 0 0 0;'>Separador de Etiquetas (4 -> 1)</h1>",
    unsafe_allow_html=True
)
st.markdown(
    "<p style='text-align:center;margin-top:0.25rem;'>"
    "Envie seu PDF com 4 etiquetas por página e baixe o resultado pronto para impressão."
    "</p>",
    unsafe_allow_html=True
)

st.divider()

# ================== ESTILO DO UPLOADER (500px + VERDE) ==================
st.markdown("""
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
}
</style>
""", unsafe_allow_html=True)

# ================== CONTROLES ==================
uploaded_file = st.file_uploader("Selecione o PDF", type=["pdf"], key="uploader_main")
remove_blank = st.checkbox("Remover páginas em branco", value=True)

# ================== HEURÍSTICA: página em branco ==================
def is_blank_page(page) -> bool:
    """
    Heurística leve (sem rasterizar):
    - se não há texto e o stream de conteúdo é vazio/whitespace ou só comandos inertes,
      consideramos 'em branco'.
    """
    # 1) texto
    txt = (page.extract_text() or "").strip()
    if txt:
        return False

    # 2) conteúdo do stream
    try:
        contents = page.get_contents()  # pode ser None, um único stream, ou lista
    except Exception:
        contents = None

    if contents is None:
        return True

    # agrega bytes do(s) stream(s)
    data = b""
    try:
        if isinstance(contents, list):
            for c in contents:
                if hasattr(c, "get_data"):
                    data += c.get_data()
        else:
            if hasattr(contents, "get_data"):
                data = contents.get_data()
    except Exception:
        # se algo deu ruim ao ler, assume não em branco pra não descartar indevidamente
        return False

    d = b"".join(data.split())  # remove whitespace

    if len(d) == 0:
        return True

    # Se tiver operadores típicos de desenho/texto/imagem, presume não branco.
    # Observação: pode haver falsos positivos se o desenho estiver fora da área visível;
    # para 100% de acurácia, usar render (PyMuPDF). Mantemos simples por enquanto.
    markers = [b'Tj', b'TJ', b'/Do', b're', b'm', b'l', b'S', b's', b'f', b'B', b'BI', b'BT', b'ET']
    if any(m in d for m in markers):
        return False

    return True

# ================== FUNÇÃO PRINCIPAL ==================
def split_pdf_into_labels(file_like, drop_blank=True) -> bytes:
    reader = PdfReader(file_like)
    writer = PdfWriter()

    for page in reader.pages:
        mb = page.mediabox
        left, bottom, right, top = float(mb.left), float(mb.bottom), float(mb.right), float(mb.top)
        width, height = right - left, top - bottom

        quads = [
            (left, bottom + height/2, left + width/2, top),   # topo-esq
            (left + width/2, bottom + height/2, right, top),  # topo-dir
            (left, bottom, left + width/2, bottom + height/2),# baixo-esq
            (left + width/2, bottom, right, bottom + height/2)# baixo-dir
        ]

        for x0, y0, x1, y1 in quads:
            p = deepcopy(page)
            rect = RectangleObject([x0, y0, x1, y1])
            p.cropbox = rect
            p.mediabox = rect

            if drop_blank and is_blank_page(p):
                continue

            writer.add_page(p)

    out = io.BytesIO()
    writer.write(out)
    out.seek(0)
    return out.getvalue()

# ================== EXECUÇÃO ==================
if uploaded_file is not None:
    try:
        with st.spinner("Processando..."):
            pdf_bytes = split_pdf_into_labels(uploaded_file, drop_blank=remove_blank)

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
    # centralizar e limitar a largura do aviso
    st.markdown("""
    <style>
    .info-centered [data-testid="stAlert"]{
        width: 500px !important;
        max-width: 100% !important;
        margin: 0 auto !important;
        border-radius: 12px;
    }
    </style>
    """, unsafe_allow_html=True)
    st.markdown('<div class="info-centered">', unsafe_allow_html=True)
    st.info("Faça o upload de um PDF para iniciar o processamento.")
    st.markdown('</div>', unsafe_allow_html=True)
