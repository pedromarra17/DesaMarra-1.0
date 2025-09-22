import streamlit as st
from pypdf import PdfReader, PdfWriter
from pypdf.generic import RectangleObject
from copy import deepcopy
from pathlib import Path
import base64, io, re, unicodedata

# ---- raster / desenho direto no PDF (sem merge) ----
import fitz  # PyMuPDF
from PIL import Image

# ================== CONFIG ==================
st.set_page_config(page_title="Etiquetas 4→1 + Lista no rodapé (estável)", layout="wide")

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

# ================== LOGO ==================
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
    "<p style='text-align:center;margin-top:0.25rem;'>Pareamento pelo <b>PEDIDO</b>. Texto dos <b>produtos</b> é escrito diretamente no rodapé da etiqueta (sem mesclar PDFs).</p>",
    unsafe_allow_html=True,
)

st.divider()

# ================== UPLOADER (500px + verde) ==================
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

uploaded_file = st.file_uploader("Selecione o PDF da Shopee (etiquetas + lista)", type=["pdf"], key="uploader_main")

# ================== CONSTANTES ==================
# limpeza de quadrantes vazios
REMOVE_BLANK   = True
DPI_CHECK      = 120
WHITE_THRESH   = 245
COVERAGE       = 0.995   # 99,5% branco = vazio

# rodapé (onde imprimir os produtos)
OVERLAY_HEIGHT_PCT = 0.14  # 14% da altura da etiqueta
OVERLAY_MARGIN_X   = 18
FONT_SIZE          = 7
MAX_LINES          = 4

# palavras-chave
PICKLIST_HINTS = ["checklist de carregamento", "produto", "variação", "variacao", "qnt", "sku", "id pedido", "corte aqui"]
LABEL_HINTS    = ["danfe", "etiqueta", "destinatário", "destinatario", "remetente"]

# ================== HELPERS ==================
def normalize_txt(t: str) -> str:
    t = unicodedata.normalize("NFKD", t)
    t = "".join(ch for ch in t if not unicodedata.combining(ch))
    return re.sub(r"\s+", " ", t).strip()

def is_picklist_page_text(page_text: str) -> bool:
    t = normalize_txt(page_text).lower()
    return sum(1 for k in PICKLIST_HINTS if k in t) >= 2

def is_label_page_text(page_text: str) -> bool:
    t = normalize_txt(page_text).lower()
    return any(k in t for k in LABEL_HINTS)

# ====== PEDIDO ======
def extract_order_from_picklist_quad(text: str) -> str:
    """Tenta pegar o token imediatamente antes de 'PACKAGE' ou próximo de 'ID Pedido'."""
    up = text.upper()
    m = re.search(r"([A-Z0-9]{10,24})\s+PACKAGE\b", up)
    if m:
        tok = m.group(1)
        if not tok.startswith("BR") and re.search(r"[A-Z]", tok) and re.search(r"\d", tok):
            return tok
    m2 = re.search(r"ID\s*PEDIDO[^A-Z0-9]{0,20}([A-Z0-9]{10,24})", up)
    if m2:
        tok = re.sub(r"[^A-Z0-9]","", m2.group(1))
        if not tok.startswith("BR") and re.search(r"[A-Z]", tok) and re.search(r"\d", tok):
            return tok
    # fallback seguro: maior token alfanum sem BR
    for m3 in re.finditer(r"\b[A-Z0-9]{10,24}\b", up):
        tok = m3.group(0)
        if tok.startswith("BR"): 
            continue
        if re.search(r"[A-Z]", tok) and re.search(r"\d", tok):
            return tok
    return ""

def extract_order_from_label_quad(text: str, allowed_set: set[str]) -> str:
    """Na etiqueta só aceitamos tokens que aparecem nas picklists (evita CEP/NF)."""
    up = text.upper()
    cands = []
    for m in re.finditer(r"\b([A-Z0-9]{10,24})\b", up):
        tok = m.group(1)
        if tok.startswith("BR"):
            continue
        if len(re.findall(r"[A-Z]", tok)) < 2 or not re.search(r"\d", tok):
            continue
        cands.append(tok)
    for tok in cands:
        if tok in allowed_set:
            return tok
    return ""

# ====== PRODUTOS ======
STOP_WORDS = ["checklist de carregamento", "id pedido", "corte aqui", "pagamento", "assinatura"]
HEADER_PATTERNS = [
    re.compile(r"^\s*(produto.*vari(a|á)ç?ao.*qnt.*sku)\s*$", re.I),
    re.compile(r"^\s*qnt\s+sku\s*$", re.I),
]

def extract_products_from_picklist(text: str) -> list[str]:
    lines = [ln.strip() for ln in text.splitlines()]
    started = False
    items, cur = [], ""

    def is_header_line(line_low: str) -> bool:
        if any(p.match(line_low) for p in HEADER_PATTERNS):
            return True
        if ("produto" in line_low and "qnt" in line_low and "sku" in line_low) and len(line_low) <= 60:
            return True
        return False

    def push():
        nonlocal cur
        t = normalize_txt(cur)
        if len(t) >= 2 and re.search(r"[A-Za-z]", t):
            items.append(t[:90] + ("..." if len(t) > 90 else ""))
        cur = ""

    for raw in lines:
        low = normalize_txt(raw).lower()

        if any(s in low for s in STOP_WORDS):
            break

        if not started:
            if is_header_line(low):
                started = True
            continue

        if is_header_line(low):
            continue
        if re.match(r"^\s*\d+\s*$", low):
            continue

        if re.match(r"^\s*\d+\s", raw) or (len(cur) > 0 and len(raw) > 40):
            if cur:
                push()
            cur = raw
        else:
            cur = (cur + " " + raw) if cur else raw

        if len(items) >= MAX_LINES:
            break

    if cur and len(items) < MAX_LINES:
        push()

    if not items:
        for ln in lines:
            l = normalize_txt(ln)
            if any(s in l.lower() for s in STOP_WORDS): break
            if is_header_line(l.lower()): continue
            if len(l) > 3 and re.search(r"[A-Za-z]", l):
                items.append(l[:90] + ("..." if len(l) > 90 else ""))
            if len(items) >= MAX_LINES: break
    return items[:MAX_LINES]

# ======= QUADRANTES =======
def quadrants_fitz(rect: fitz.Rect):
    W, H = rect.width, rect.height
    return [
        fitz.Rect(rect.x0,       rect.y0,       rect.x0 + W/2, rect.y0 + H/2),
        fitz.Rect(rect.x0 + W/2, rect.y0,       rect.x1,       rect.y0 + H/2),
        fitz.Rect(rect.x0,       rect.y0 + H/2, rect.x0 + W/2, rect.y1),
        fitz.Rect(rect.x0 + W/2, rect.y0 + H/2, rect.x1,       rect.y1),
    ]

def quadrants_pypdf(mb):
    left, bottom, right, top = float(mb.left), float(mb.bottom), float(mb.right), float(mb.top)
    width, height = right - left, top - bottom
    return [
        (left, bottom + height/2, left + width/2, top),
        (left + width/2, bottom + height/2, right, top),
        (left, bottom, left + width/2, bottom + height/2),
        (left + width/2, bottom, right, bottom + height/2),
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

# ================== PIPELINE ==================
def process_pdf_with_picklist(pdf_bytes: bytes) -> bytes:
    """
    1) Extrai todos os PEDIDOS + produtos a partir das páginas de 'lista de separação'
    2) Seleciona 1 etiqueta por PEDIDO (dedup), cortando 4→1 com pypdf
    3) Escreve (com PyMuPDF) o rodapé de produtos diretamente nas páginas finais
    """
    # Abrimos o PDF completo com as duas libs
    reader_full = PdfReader(io.BytesIO(pdf_bytes))
    doc_full = fitz.open(stream=pdf_bytes, filetype="pdf")

    # ---------- 1) COLETAR LISTAS (PEDIDO -> produtos) ----------
    pick_by_order: dict[str, list[str]] = {}
    for i in range(len(doc_full)):
        page = doc_full[i]
        if not is_picklist_page_text(page.get_text("text")):
            continue
        qf = quadrants_fitz(page.rect)
        for rect in qf:
            qt = page.get_text("text", clip=rect) or ""
            order = extract_order_from_picklist_quad(qt)
            if not order:
                continue
            prods = extract_products_from_picklist(qt)
            if not prods:
                continue
            acc = pick_by_order.setdefault(order, [])
            for p in prods:
                if p not in acc:
                    acc.append(p)

    allowed_orders = set(pick_by_order.keys())
    if not allowed_orders:
        # sem listas não tem como casar; retorna só o 4→1 básico
        writer = PdfWriter()
        for i in range(len(reader_full.pages)):
            page = reader_full.pages[i]
            for (x0, y0, x1, y1) in quadrants_pypdf(page.mediabox):
                p = deepcopy(page)
                rect = RectangleObject([x0, y0, x1, y1])
                p.cropbox = rect
                p.mediabox = rect
                # opcional: drop blank
                if REMOVE_BLANK:
                    # checa com fitz
                    if quad_is_blank_by_raster(doc_full, i,
                                               fitz.Rect(x0, y0, x1, y1)):
                        continue
                writer.add_page(p)
        out = io.BytesIO(); writer.write(out); out.seek(0)
        return out.getvalue()

    # ---------- 2) SELECIONAR 1 ETIQUETA POR PEDIDO (DEDUP) ----------
    labels_by_order: dict[str, dict] = {}   # order -> info p/ crop
    order_sequence: list[str] = []          # ordem de saída
    for i in range(len(doc_full)):
        page = doc_full[i]
        if not is_label_page_text(page.get_text("text")):
            continue
        qf = quadrants_fitz(page.rect)
        qp = quadrants_pypdf(reader_full.pages[i].mediabox)
        for rect_fitz, box_pdf in zip(qf, qp):
            # remove quadrante vazio
            if REMOVE_BLANK and quad_is_blank_by_raster(doc_full, i, rect_fitz):
                continue
            qt = page.get_text("text", clip=rect_fitz) or ""
            order = extract_order_from_label_quad(qt, allowed_orders)
            if not order or order in labels_by_order:
                continue
            labels_by_order[order] = {"page_idx": i, "pypdf_box": box_pdf}
            order_sequence.append(order)

    # ---------- 3) CORTAR AS ETIQUETAS (pypdf) ----------
    writer = PdfWriter()
    for order in order_sequence:
        info = labels_by_order[order]
        p_src = reader_full.pages[info["page_idx"]]
        x0, y0, x1, y1 = info["pypdf_box"]
        p = deepcopy(p_src)
        rect = RectangleObject([x0, y0, x1, y1])
        p.cropbox = rect
        p.mediabox = rect
        writer.add_page(p)

    tmp = io.BytesIO()
    writer.write(tmp)
    tmp.seek(0)
    cropped_bytes = tmp.getvalue()

    # ---------- 4) ESCREVER O RODAPÉ (PyMuPDF, sem merge) ----------
    out_doc = fitz.open(stream=cropped_bytes, filetype="pdf")
    for page_idx, order in enumerate(order_sequence):
        products = pick_by_order.get(order, [])[:MAX_LINES]
        if not products:
            continue
        pg = out_doc[page_idx]
        r = pg.rect
        area_h = r.height * OVERLAY_HEIGHT_PCT
        pad = 4
        # caixa no rodapé
        box = fitz.Rect(OVERLAY_MARGIN_X, r.height - area_h - pad,
                        r.width - OVERLAY_MARGIN_X, r.height - pad)
        # monta texto
        lines = ["Produtos:"] + [f"• {t}" for t in products]
        text = "\n".join(lines)
        # escreve texto (PyMuPDF usa fontes padrão, não corrompe)
        pg.insert_textbox(box, text, fontname="helv", fontsize=FONT_SIZE, align=0)  # left

    # salva como bytes
    out_buf = io.BytesIO()
    out_doc.save(out_buf)
    out_doc.close()
    out_buf.seek(0)
    return out_buf.getvalue()

# ================== RUN ==================
if uploaded_file is not None:
    try:
        pdf_bytes_in = uploaded_file.getvalue()
        with st.spinner("Processando: casando PEDIDO e escrevendo rodapé..."):
            pdf_bytes_out = process_pdf_with_picklist(pdf_bytes_in)
        st.success("Pronto! 1 etiqueta por pedido, com os produtos no rodapé.")
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
