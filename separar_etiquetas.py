import streamlit as st
from pypdf import PdfReader, PdfWriter
from pypdf.generic import RectangleObject
from copy import deepcopy
from pathlib import Path
import base64, io, re, unicodedata

# raster/overlay
import fitz  # PyMuPDF
from PIL import Image
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import portrait

# ================== PAGE CONFIG ==================
st.set_page_config(page_title="Etiquetas 4→1 + Lista de Separação no Rodapé", layout="wide")

# ================== HIDE STREAMLIT BRANDING ==================
st.markdown("""
<style>
#MainMenu, footer {visibility: hidden;}
header, [data-testid="stToolbar"], [data-testid="stDecoration"], .stDeployButton {display: none !important;}
div[class^="viewerBadge"], div[class*="viewerBadge"] {display: none !important;}
[data-testid="stAppViewContainer"] a[href*="streamlit.io"] {display: none !important;}
a[href*="streamlit.io"][style*="position: fixed"], a[href*="streamlit.app"][style*="position: fixed"] {display: none !important;}
</style>
""", unsafe_allow_html=True)

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
              <img src="data:image/png;base64,{b64}" style="display:block;margin:0 auto;width:{width_px}px;" />
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
    "<p style='text-align:center;margin-top:0.25rem;'>PDF único da Shopee: casamos cada <b>Etiqueta</b> com a <b>Lista de Separação</b> pelo <b>PEDIDO</b> e imprimimos os <b>produtos</b> no rodapé da etiqueta.</p>",
    unsafe_allow_html=True,
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

# ================== INPUT ==================
uploaded_file = st.file_uploader("Selecione o PDF", type=["pdf"], key="uploader_main")

# ====== CONSTANTES ======
# limpeza de quadrantes vazios (após crop 4→1)
REMOVE_BLANK   = True
DPI_CHECK      = 120
WHITE_THRESH   = 245
COVERAGE       = 0.995   # 99,5% de branco = vazio

# rodapé (onde imprimir os produtos)
OVERLAY_HEIGHT_PCT = 0.16  # 16% da altura (ajuste fino se cobrir barra)
OVERLAY_MARGIN_X   = 18
FONT_SIZE          = 7
MAX_LINES          = 4

# ====== PALAVRAS-CHAVE (ajustadas ao seu PDF)
# etiquetas (ex.: "DANFE SIMPLIFICADO - ETIQUETA")
LABEL_HINTS = ["danfe", "etiqueta", "destinatário", "remetente"]
# listas de separação (no seu arquivo aparecem estes termos)
PICKLIST_HINTS = [
    "checklist de carregamento", "produto", "variação", "qnt", "sku", "id pedido", "corte aqui"
]

# ====== UTILS ======
def normalize_txt(t: str) -> str:
    t = unicodedata.normalize("NFKD", t)
    t = "".join(ch for ch in t if not unicodedata.combining(ch))
    return re.sub(r"\s+", " ", t).strip()

def is_picklist_page_text(page_text: str) -> bool:
    t = normalize_txt(page_text).lower()
    hit = 0
    for k in PICKLIST_HINTS:
        if k in t:
            hit += 1
    return hit >= 2  # exige pelo menos 2 sinais da lista

def is_label_page_text(page_text: str) -> bool:
    t = normalize_txt(page_text).lower()
    return any(k in t for k in LABEL_HINTS)

# ——— PEDIDO ———
# Regex principal: “Pedido: XXXXX” ou “ID Pedido XXXXX”
ORDER_NEAR_RE = re.compile(r"(?:pedido|id\s*pedido)[:\s#-]*([A-Z0-9\-\s]{8,})", re.I)

def extract_order_candidates(text: str) -> list[str]:
    """
    Extrai possíveis IDs de pedido mesmo que estejam quebrados (ex.: '25 09 17 NM 75 ND 5E').
    1) Prioriza captura perto de 'Pedido' / 'ID Pedido'.
    2) Se nada encontrado, procura tokens alfanum longos no texto todo.
    """
    up = text.upper()
    cands = []

    # 1) Perto de "Pedido" / "ID Pedido"
    for m in ORDER_NEAR_RE.finditer(up):
        raw = m.group(1)
        token = re.sub(r"[^A-Z0-9]", "", raw)
        if 10 <= len(token) <= 24 and not token.startswith("BR") and not token.isdigit():
            cands.append(token)

    # 2) Varredura geral (captura sequências alfanum longas, removendo espaços/hífens)
    for m in re.finditer(r"([A-Z0-9][A-Z0-9\-\s]{10,24})", up):
        token = re.sub(r"[^A-Z0-9]", "", m.group(1))
        if 10 <= len(token) <= 24 and not token.startswith("BR") and not token.isdigit():
            cands.append(token)

    # ordena pela maior probabilidade (tamanho maior primeiro) e deduplica
    uniq = []
    seen = set()
    for tk in sorted(cands, key=len, reverse=True):
        if tk not in seen:
            uniq.append(tk)
            seen.add(tk)
    return uniq

def pick_best_order(text: str) -> str:
    cands = extract_order_candidates(text)
    return cands[0] if cands else ""

# ——— PRODUTOS (a partir da lista de separação) ———
STOP_WORDS = ["checklist de carregamento", "id pedido", "corte aqui"]

def extract_products_from_picklist(text: str) -> list[str]:
    """
    Heurística para pegar as linhas de produto:
    - começa após detectar cabeçalho 'Produto' / 'Variação' / 'Qnt' / 'SKU'
    - junta quebras de linha em descrições longas
    - para ao encontrar blocos finais (checklist/id pedido/corte aqui)
    """
    lines = [ln.strip() for ln in text.splitlines()]
    started = False
    items = []
    cur = ""

    def push_current():
        nonlocal cur
        t = normalize_txt(cur)
        if len(t) >= 2 and re.search(r"[A-Za-z]", t):
            if len(t) > 90:
                t = t[:87] + "..."
            items.append(t)
        cur = ""

    for raw in lines:
        low = normalize_txt(raw).lower()

        if any(s in low for s in STOP_WORDS):
            # fim da área útil
            break

        # detectar cabeçalho
        if not started:
            if ("produto" in low and "qnt" in low) or ("variação" in low) or ("variacao" in low):
                started = True
            continue

        # já estamos na área de itens
        if re.match(r"^\s*\d+\s*$", low):
            # números de linha sozinhos: ignora
            continue

        # nova linha de item quando começa com número+espaço ou quando a linha é longa
        if re.match(r"^\s*\d+\s", raw) or (len(cur) > 0 and len(raw) > 40):
            if cur:
                push_current()
            cur = raw
        else:
            # apenda fragmentos (descrições quebradas)
            sep = " " if cur else ""
            cur = f"{cur}{sep}{raw}"

        if len(items) >= MAX_LINES:
            break

    if cur and len(items) < MAX_LINES:
        push_current()

    # fallback: se nada capturado, pegue as 3-4 primeiras linhas “ricas”
    if not items:
        rough = []
        for ln in lines:
            l = normalize_txt(ln)
            if any(s in l.lower() for s in STOP_WORDS):
                break
            if len(l) > 3 and re.search(r"[A-Za-z]", l):
                rough.append(l if len(l) <= 90 else l[:87] + "...")
            if len(rough) >= MAX_LINES:
                break
        items = rough
    return items[:MAX_LINES]

# ======= QUADRANTES =======
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

# ======= BLANK CHECK (raster) =======
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

# ======= OVERLAY (reportlab) =======
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
def process_pdf_with_picklist(pdf_bytes: bytes) -> bytes:
    """
    - classifica quadrantes de cada página em: ETIQUETA ou LISTA (por texto)
    - extrai PEDIDO de ambos (corrigindo IDs quebrados)
    - imprime produtos da LISTA no rodapé da ETIQUETA de mesmo PEDIDO
    """
    reader = PdfReader(io.BytesIO(pdf_bytes))
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")

    labels = []    # {page_idx, q_idx, pypdf_box, fitz_rect, order}
    picklist = []  # {page_idx, q_idx, pypdf_box, fitz_rect, order, products}

    for i in range(len(doc)):
        page_fitz = doc[i]
        page_text = page_fitz.get_text("text")
        page_is_pick = is_picklist_page_text(page_text)
        page_is_label = is_label_page_text(page_text)

        qf = quadrants_fitz(page_fitz.rect)
        qp = quadrants_pypdf(reader.pages[i].mediabox)

        for q_idx, (rect_fitz, box_pdf) in enumerate(zip(qf, qp)):
            quad_text = page_fitz.get_text("text", clip=rect_fitz) or ""

            # filtra quadrantes vazios (apenas para etiquetas)
            if REMOVE_BLANK and not page_is_pick:
                if quad_is_blank_by_raster(doc, i, rect_fitz):
                    continue

            order = pick_best_order(quad_text)

            if page_is_pick:
                prods = extract_products_from_picklist(quad_text)
                picklist.append({
                    "page_idx": i, "q_idx": q_idx, "fitz_rect": rect_fitz,
                    "pypdf_box": box_pdf, "order": order, "products": prods
                })
            elif page_is_label or order:
                labels.append({
                    "page_idx": i, "q_idx": q_idx, "fitz_rect": rect_fitz,
                    "pypdf_box": box_pdf, "order": order
                })

    # indexa listas por pedido (usa a primeira com produtos não vazios)
    pick_by_order = {}
    for pk in picklist:
        if pk["order"] and pk.get("products"):
            pick_by_order.setdefault(pk["order"], []).append(pk)

    writer = PdfWriter()
    for lab in labels:
        p_src = reader.pages[lab["page_idx"]]
        x0, y0, x1, y1 = lab["pypdf_box"]
        p = deepcopy(p_src)
        rect = RectangleObject([x0, y0, x1, y1])
        p.cropbox = rect
        p.mediabox = rect

        if lab["order"] and lab["order"] in pick_by_order:
            pk = pick_by_order[lab["order"]][0]
            if pk.get("products"):
                w_pt = float(p.mediabox.right) - float(p.mediabox.left)
                h_pt = float(p.mediabox.top) - float(p.mediabox.bottom)
                overlay_bytes = make_overlay_pdf(w_pt, h_pt, pk["products"])
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
        with st.spinner("Processando e vinculando pelo PEDIDO..."):
            pdf_bytes_out = process_pdf_with_picklist(pdf_bytes_in)

        st.success("Pronto! Uma etiqueta por cliente, com produtos da Lista no rodapé.")
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
    # aviso centralizado
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
