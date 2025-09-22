import streamlit as st
from pypdf import PdfReader, PdfWriter
from pypdf.generic import RectangleObject
from copy import deepcopy
from pathlib import Path
import base64, io, re, unicodedata

# libs para raster/overlay
import fitz  # PyMuPDF
from PIL import Image
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import portrait

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
    "<h1 style='text-align:center;margin:0.4rem 0 0 0;'>Etiquetas (4 → 1) + Lista de Separação no Rodapé</h1>",
    unsafe_allow_html=True,
)
st.markdown(
    "<p style='text-align:center;margin-top:0.25rem;'>Lê o PDF único da Shopee, encontra o <b>PEDIDO</b> e imprime os <b>produtos</b> da lista de separação no rodapé da etiqueta correspondente.</p>",
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

# ================== INPUT ==================
uploaded_file = st.file_uploader("Selecione o PDF", type=["pdf"], key="uploader_main")

# ====== CONSTANTES ======
REMOVE_BLANK   = True
DPI_CHECK      = 120
WHITE_THRESH   = 245
COVERAGE       = 0.995   # 99,5% de branco = vazio

OVERLAY_HEIGHT_PCT = 0.16  # faixa inferior para os produtos
OVERLAY_MARGIN_X   = 18
FONT_SIZE          = 7
MAX_LINES          = 4

# ====== UTILS ======
def normalize_txt(t: str) -> str:
    t = unicodedata.normalize("NFKD", t)
    t = "".join(ch for ch in t if not unicodedata.combining(ch))
    return re.sub(r"\s+", " ", t).strip()

# Pedido: pega por palavra-chave; fallback: tokens longos alfanum/hífen (evitando CEP/telefone)
ORDER_RE = re.compile(r"(?:pedido|id do pedido|order(?: id)?|ordem)\s*[:#-]?\s*([A-Z0-9\-]{6,})", re.I)
TOKEN_RE = re.compile(r"\b[A-Z0-9\-]{8,}\b")

def extract_order(text: str) -> str:
    raw = normalize_txt(text).upper()
    m = ORDER_RE.search(raw)
    if m:
        oid = m.group(1)
        # normaliza
        oid = re.sub(r"[^A-Z0-9\-]", "", oid)
        return oid
    # fallback: tenta maior token (evita pegar CEP/telefone por tamanho/formato)
    candidates = [tok for tok in TOKEN_RE.findall(raw) if not re.fullmatch(r"\d{8,}", tok)]
    if candidates:
        candidates.sort(key=len, reverse=True)
        return candidates[0]
    return ""

def extract_products_from_picklist(text: str) -> list[str]:
    """
    Extrai linhas de produtos da 'lista de separação'.
    Regras: ignora campos de endereço/assinatura e mantém linhas com letras.
    """
    lines = [ln.strip(" •-:") for ln in text.splitlines()]
    clean = []
    for ln in lines:
        l = normalize_txt(ln)
        if len(l) < 2:
            continue
        low = l.lower()
        if any(k in low for k in ["cpf", "cnpj", "assin", "declaro", "remetente", "destinatario", "destinatário", "endereco", "endereço"]):
            continue
        if re.search(r"[a-zA-Z]", l):
            # corta linhas muito compridas
            if len(l) > 80:
                l = l[:77] + "..."
            clean.append(l)
    # mantém as primeiras relevantes
    return clean[:MAX_LINES]

def quadrants_fitz(rect: fitz.Rect):
    W, H = rect.width, rect.height
    return [
        fitz.Rect(rect.x0,       rect.y0,       rect.x0 + W/2, rect.y0 + H/2),  # topo-esq
        fitz.Rect(rect.x0 + W/2, rect.y0,       rect.x1,       rect.y0 + H/2),  # topo-dir
        fitz.Rect(rect.x0,       rect.y0 + H/2, rect.x0 + W/2, rect.y1),        # baixo-esq
        fitz.Rect(rect.x0 + W/2, rect.y0 + H/2, rect.x1,       rect.y1),        # baixo-dir
    ]

def quadrants_pypdf(mb):
    left, bottom, right, top = float(mb.left), float(mb.bottom), float(mb.right), float(mb.top)
    width, height = right - left, top - bottom
    return [
        (left, bottom + height/2, left + width/2, top),           # topo-esq
        (left + width/2, bottom + height/2, right, top),          # topo-dir
        (left, bottom, left + width/2, bottom + height/2),        # baixo-esq
        (left + width/2, bottom, right, bottom + height/2),       # baixo-dir
    ]

def quad_is_blank_by_raster(doc: fitz.Document, page_index: int, clip_rect: fitz.Rect,
                            dpi: int = DPI_CHECK, white_thresh: int = WHITE_THRESH, coverage: float = COVERAGE) -> bool:
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

def make_overlay_pdf(w_pt: float, h_pt: float, products: list[str],
                     height_pct=OVERLAY_HEIGHT_PCT, margin_x=OVERLAY_MARGIN_X, font_size=FONT_SIZE) -> bytes:
    """Cria uma página PDF só com o rodapé de produtos, do mesmo tamanho da etiqueta."""
    buf = io.BytesIO()
    canv = canvas.Canvas(buf, pagesize=portrait((w_pt, h_pt)))
    area_h = h_pt * height_pct
    pad_y = 4
    y0 = pad_y + area_h - font_size - 2
    x0 = margin_x

    canv.setFont("Helvetica-Bold", font_size)
    canv.drawString(x0, y0 + 6, "Produtos:")
    y = y0 - 2

    canv.setFont("Helvetica", font_size)
    line_h = font_size + 2
    for ln in products[:MAX_LINES]:
        y -= line_h
        if y < pad_y + 2:
            break
        canv.drawString(x0, y, f"• {ln}")

    canv.showPage()
    canv.save()
    buf.seek(0)
    return buf.getvalue()

# ======= PIPELINE =======
PICKLIST_KEYWORDS = ["lista de separacao", "lista de separação", "separacao", "separação", "picking", "itens do pedido", "lista"]

def process_pdf_with_picklist(pdf_bytes: bytes) -> bytes:
    """
    - separa quadrantes (4→1) tanto de etiquetas quanto das listas de separação
    - extrai PEDIDO de ambos
    - cria overlay dos produtos da lista no rodapé da etiqueta com mesmo PEDIDO
    """
    reader_full = PdfReader(io.BytesIO(pdf_bytes))
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")

    labels = []   # itens com: order, fitz_rect, pypdf_box, page_idx, q_idx
    pickls = []   # idem, com products

    for i in range(len(doc)):
        p_fitz = doc[i]
        page_text = normalize_txt(p_fitz.get_text("text").lower())
        is_pick = any(k in page_text for k in PICKLIST_KEYWORDS)

        qf = quadrants_fitz(p_fitz.rect)
        qp = quadrants_pypdf(reader_full.pages[i].mediabox)

        for q_idx, (rect_fitz, box_pdf) in enumerate(zip(qf, qp)):
            quad_text = p_fitz.get_text("text", clip=rect_fitz) or ""
            if REMOVE_BLANK and not is_pick:
                if quad_is_blank_by_raster(doc, i, rect_fitz):
                    continue

            order_id = extract_order(quad_text)
            item = {
                "page_idx": i,
                "q_idx": q_idx,
                "fitz_rect": rect_fitz,
                "pypdf_box": box_pdf,
                "order": order_id,
                "text": quad_text,
            }

            if is_pick:
                products = extract_products_from_picklist(quad_text)
                item["products"] = products
                pickls.append(item)
            else:
                labels.append(item)

    # indexa listas por pedido (pode haver mais de uma; usa a primeira com produtos)
    pick_by_order = {}
    for p in pickls:
        if p["order"] and p.get("products"):
            pick_by_order.setdefault(p["order"], []).append(p)

    writer = PdfWriter()
    for lab in labels:
        p_src = reader_full.pages[lab["page_idx"]]
        x0, y0, x1, y1 = lab["pypdf_box"]
        p = deepcopy(p_src)
        rect = RectangleObject([x0, y0, x1, y1])
        p.cropbox = rect
        p.mediabox = rect

        if lab["order"] and lab["order"] in pick_by_order:
            # usa a primeira lista disponível para esse pedido
            dec = pick_by_order[lab["order"]][0]
            if dec.get("products"):
                w_pt = float(p.mediabox.right) - float(p.mediabox.left)
                h_pt = float(p.mediabox.top) - float(p.mediabox.bottom)
                overlay_bytes = make_overlay_pdf(w_pt, h_pt, dec["products"])
                ov_page = PdfReader(io.BytesIO(overlay_bytes)).pages[0]
                try:
                    p.merge_page(ov_page)
                except Exception:
                    p.merge_page(ov_page)

        writer.add_page(p)

    out = io.BytesIO()
    writer.write(out)
    out.seek(0)
    return out.getvalue()

# ================== RUN ==================
if uploaded_file is not None:
    try:
        pdf_bytes_in = uploaded_file.getvalue()
        with st.spinner("Processando e vinculando listas pelo PEDIDO..."):
            pdf_bytes_out = process_pdf_with_picklist(pdf_bytes_in)

        st.success("Pronto! Uma etiqueta por cliente com a lista (produtos) no rodapé.")
        st.download_button(
            label="Baixar PDF final",
            data=pdf_bytes_out,
            file_name="etiquetas_com_lista.pdf",
            mime="application/pdf",
            key="download_main",
        )
    except Exception as e:
        st.error("Não foi possível processar o arquivo. Verifique se é um PDF válido.")
        st.exception(e)
else:
    # centraliza o aviso
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
