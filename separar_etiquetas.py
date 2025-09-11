import streamlit as st
from pypdf import PdfReader, PdfWriter
from pypdf.generic import RectangleObject
from copy import deepcopy
from pathlib import Path
import base64, io

# --- robust blank-page detection ---
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
    }
    </style>
    """,
    unsafe_allow_html=True,
)

# ================== CONTROLES ==================
uploaded_file = st.file_uploader("Selecione o PDF", type=["pdf"], key="uploader_main")
remove_blank = st.checkbox("Remover páginas em branco", value=True)
with st.expander("Avançado: sensibilidade da remoção"):
    dpi = st.slider("DPI para checagem", 72, 220, 120, 8)
    white_threshold = st.slider("Limiar de branco (0-255)", 230, 255, 245, 1)
    coverage = st.slider("Cobertura mínima de branco", 0.90, 1.00, 0.995, 0.001)

# ================== BLANK PAGE (raster) ==================
def quad_is_blank_by_raster(doc: fitz.Document, page_index: int, clip_rect: fitz.Rect,
                            dpi: int = 120, white_thresh: int = 245, coverage: float = 0.995) -> bool:
    p = doc[page_index]
    scale = dpi / 72.0
    pix = p.get_pixmap(matrix=fitz.Matrix(scale, scale), clip=clip_rect, alpha=False)
    if pix.width == 0 or pix.height == 0:
        return True
    img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
    gray = img.convert("L")
    hist = gray.histogram()
    total = sum(hist)
    white_pixels = sum(hist[white_thresh:256])
    frac_white = white_pixels / max(total, 1)
    return frac_white >= coverage

# ================== SPLIT + DROP BLANK ==================
def split_pdf_into_labels_bytes(pdf_bytes: bytes, drop_blank=True,
                                dpi=120, white_thresh=245, coverage=0.995) -> bytes:
    reader = PdfReader(io.BytesIO(pdf_bytes))
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    writer = PdfWriter()

    for idx, page in enumerate(reader.pages):
        mb = page.mediabox
        left, bottom, right, top = float(mb.left), float(mb.bottom), float(mb.right), float(mb.top)
        width, height = right - left, top - bottom

        pypdf_quads = [
            (left, bottom + height/2, left + width/2, top),           # topo-esq
            (left + width/2, bottom + height/2, right, top),          # topo-dir
            (left, bottom, left + width/2, bottom + height/2),        # baixo-esq
            (left + width/2, bottom, right, bottom + height/2),       # baixo-dir
        ]

        r = doc[idx].rect
        W, H = r.width, r.height
        fitz_quads = [
            fitz.Rect(r.x0,       r.y0,       r.x0 + W/2, r.y0 + H/2),  # topo-esq
            fitz.Rect(r.x0 + W/2, r.y0,       r.x1,       r.y0 + H/2),  # topo-dir
            fitz.Rect(r.x0,       r.y0 + H/2, r.x0 + W/2, r.y1),        # baixo-esq
            fitz.Rect(r.x0 + W/2, r.y0 + H/2, r.x1,       r.y1),        # baixo-dir
        ]

        for (x0, y0, x1, y1), clip in zip(pypdf_quads, fitz_quads):
            if drop_blank and quad_is_blank_by_raster(doc, idx, clip, dpi=dpi, white_thresh=white_thresh, coverage=coverage):
                continue
            p = deepcopy(page)
            rect = RectangleObject([x0, y0, x1, y1])
            p.cropbox = rect
            p.mediabox = rect
            writer.add_page(p)

    out = io.BytesIO()
    writer.write(out)
    out.seek(0)
    return out.getvalue()

# ================== RUN ==================
if uploaded_file is not None:
    try:
        pdf_bytes_in = uploaded_file.getvalue()
        with st.spinner("Processando..."):
            pdf_bytes_out = split_pdf_into_labels_bytes(
                pdf_bytes_in,
                drop_blank=remove_blank,
                dpi=dpi,
                white_thresh=white_threshold,
                coverage=coverage,
            )
        st.success("Pronto! Seu PDF foi gerado.")
        st.download_button(
            label="Baixar PDF separado",
            data=pdf_bytes_out,
            file_name="etiquetas_individuais.pdf",
            mime="application/pdf",
            key="download_main",
        )
    except Exception as e:
        st.error("Não foi possível processar o arquivo. Verifique se é um PDF válido.")
        st.exception(e)
else:
    # centralizar o aviso
    st.markdown(
        """
        <style>
        .info-centered [data-testid="stAlert"]{
            width: 500px !important;
            max-width: 100% !important;
            margin: 0 auto !important;
            border-radius: 12px;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )
    st.markdown('<div class="info-centered">', unsafe_allow_html=True)
    st.info("Faça o upload de um PDF para iniciar o processamento.")
    st.markdown('</div>', unsafe_allow_html=True)
