# separar_etiquetas.py
import streamlit as st
from pypdf import PdfReader, PdfWriter
from pypdf.generic import RectangleObject
from copy import deepcopy
from pathlib import Path
import base64, io, re, unicodedata
import fitz  # PyMuPDF
from PIL import Image

# ================= UI =================
st.set_page_config(page_title="Etiquetas 4→1 + Lista (QNT×SKU)", layout="wide")
st.markdown("""
<style>
#MainMenu, footer {visibility:hidden;}
header, [data-testid="stToolbar"], [data-testid="stDecoration"], .stDeployButton {display:none!important;}
div[class^="viewerBadge"], div[class*="viewerBadge"]{display:none!important;}
</style>""", unsafe_allow_html=True)

BASE_DIR = Path(__file__).parent
LOGO_LIGHT = BASE_DIR / "logo_light.png"
LOGO_DARK  = BASE_DIR / "logo_dark.png"

def show_logo_center(width_px: int = 420):
    theme_base = st.get_option("theme.base") or "light"
    logo_path = LOGO_LIGHT if theme_base == "light" else LOGO_DARK
    if not logo_path.exists():
        logo_path = LOGO_DARK if theme_base == "light" else LOGO_LIGHT
    if logo_path.exists():
        b64 = base64.b64encode(logo_path.read_bytes()).decode()
        st.markdown(
            f"<div style='text-align:center'><img src='data:image/png;base64,{b64}' "
            f"style='width:{width_px}px;margin:0 auto;display:block'/></div>",
            unsafe_allow_html=True
        )

show_logo_center()
st.markdown("<h1 style='text-align:center;margin:.4rem 0 0'>Etiquetas (4→1) + Lista (QNT × SKU)</h1>", unsafe_allow_html=True)
st.markdown("<p style='text-align:center'>Uma página por etiqueta. Rodapé mostra <b>QNT × SKU</b>. Casa por <b>Pedido</b> e, se não achar, por <b>ordem</b>.</p>", unsafe_allow_html=True)
st.divider()

st.markdown("""
<style>
div[data-testid="stFileUploader"] section[data-testid="stFileUploaderDropzone"]{
  width:500px!important;margin:0 auto;border-radius:12px;background:#16A34A!important;
  border:2px dashed rgba(255,255,255,.6);padding:1.25rem
}
div[data-testid="stFileUploader"] section[data-testid="stFileUploaderDropzone"] *{color:#fff!important}
div[data-testid="stFileUploader"] section[data-testid="stFileUploaderDropzone"]:hover{background:#15803D!important}
</style>""", unsafe_allow_html=True)

up = st.file_uploader("Selecione o PDF da Shopee (etiquetas + listas)", type=["pdf"])

# =============== Constantes ===============
REMOVE_BLANK = True
DPI_CHECK    = 120
WHITE_THR    = 245
COVERAGE     = 0.995

OVERLAY_HEIGHT_PCT = 0.14
FONT_SIZE = 7
MAX_LINES = 10
MARGIN_X_PT = 18
PAD_Y_PT = 6

LATIN = r"A-Za-zÀ-ÖØ-öø-ÿ"

# =============== Normalização robusta ===============
def normalize_txt(t: str) -> str:
    t = unicodedata.normalize("NFKD", t)
    t = "".join(ch for ch in t if not unicodedata.combining(ch))
    return re.sub(r"\s+", " ", t).strip()

def collapse_pairs(s: str) -> str:
    toks = s.split()
    out, buf = [], []
    for t in toks:
        if re.fullmatch(rf"[{LATIN}]{{1,2}}", t):
            buf.append(t)
        else:
            if buf: out.append("".join(buf)); buf=[]
            out.append(t)
    if buf: out.append("".join(buf))
    return " ".join(out)

def norm_heavy(t: str) -> str:
    t = normalize_txt(t)
    t = collapse_pairs(t)
    # junta padrões "S K U", "Q N T"…
    t = re.sub(r"(?:(?<=\b)[A-Za-z]\s(?=[A-Za-z]))+", lambda m: m.group(0).replace(" ",""), t)
    return t

# =============== Quadrantes / Branco ===============
def quadrants_fitz(rect: fitz.Rect):
    W,H = rect.width, rect.height
    return [
        fitz.Rect(rect.x0,       rect.y0,       rect.x0+W/2, rect.y0+H/2),  # TL
        fitz.Rect(rect.x0+W/2,   rect.y0,       rect.x1,     rect.y0+H/2),  # TR
        fitz.Rect(rect.x0,       rect.y0+H/2,   rect.x0+W/2, rect.y1),      # BL
        fitz.Rect(rect.x0+W/2,   rect.y0+H/2,   rect.x1,     rect.y1),      # BR
    ]

def quadrants_pypdf(mb):
    l,b,r,t = float(mb.left), float(mb.bottom), float(mb.right), float(mb.top)
    w,h = r-l, t-b
    return [
        (l, b+h/2, l+w/2, t),
        (l+w/2, b+h/2, r, t),
        (l, b, l+w/2, b+h/2),
        (l+w/2, b, r, b+h/2),
    ]

def quad_is_blank_by_raster(doc: fitz.Document, page_idx: int, clip: fitz.Rect,
                            dpi=DPI_CHECK, white=WHITE_THR, cov=COVERAGE) -> bool:
    p = doc[page_idx]
    scale = dpi/72.0
    pix = p.get_pixmap(matrix=fitz.Matrix(scale,scale), clip=clip, alpha=False)
    if pix.width==0 or pix.height==0: return True
    img = Image.frombytes("RGB",(pix.width,pix.height),pix.samples)
    gray = img.convert("L"); hist = gray.histogram()
    total = sum(hist); white_px = sum(hist[white:256])
    return (white_px/max(total,1)) >= cov

# =============== Pedido (ID) ===============
ORDER_WORD_NEAR = re.compile(r"(?:ID\s*PEDIDO|PEDIDO)[:\s#-]*([A-Z0-9]{8,24})", re.I)
ORDER_TOKEN_RE  = re.compile(r"\b([A-Z0-9]{10,24})\b")

def extract_order(text: str) -> str:
    up = norm_heavy(text).upper()
    m = ORDER_WORD_NEAR.search(up)
    if m:
        tok = m.group(1)
        if not tok.startswith("BR") and re.search(r"[A-Z]", tok) and re.search(r"\d", tok):
            return tok
    for tok in ORDER_TOKEN_RE.findall(up):
        if tok.startswith("BR"): continue
        if re.search(r"[A-Z]", tok) and re.search(r"\d", tok):
            return tok
    return ""

# =============== Lista (QNT × SKU) ===============
# SKU precisa ter letras + números (evita códigos grandes da etiqueta)
SKU_TOKEN_RE = re.compile(r"\b(?=[A-Z0-9\-\/\.]{4,32}\b)(?=.*[A-Z])(?=.*\d)[A-Z0-9\-\/\.]+\b")

QTY_PATTS = [
    re.compile(r"(?:QNT|QTD|QTDE|QUANTIDADE)\s*[:x\-]*\s*(\d{1,3})", re.I),
    re.compile(r"\b(\d{1,3})\s*(?:UN|UND|PCS|PC|PÇS|PÇ)\b", re.I),
    re.compile(r"\b(\d{1,3})\s*x\b", re.I),
    re.compile(r"\bx\s*(\d{1,3})\b", re.I),
]

EXCLUDE_LABEL_WORDS = [
    "danfe","destinat","remetente","agência","agencia","bairro","cep","emissão","emissao","série","serie",
    "residencial","remetente","agencia"
]

def extract_rows(text: str) -> list[str]:
    t = norm_heavy(text)
    # corta após “corte aqui” / “checklist…”
    t = re.split(r"(?:checklist de carregamento|corte aqui)", t, flags=re.I)[0]
    lines = [ln.strip() for ln in t.splitlines() if ln.strip()]
    rows, cur = [], []
    for ln in lines:
        if re.match(r"^\d+\b", ln):
            if cur: rows.append(" ".join(cur)); cur=[]
            ln = re.sub(r"^\d+\s*[-.)]?\s*", "", ln)
        cur.append(ln)
    if cur: rows.append(" ".join(cur))
    return rows

def is_list_block(text: str) -> bool:
    t = norm_heavy(text).lower()
    if "lista de separacao" in t or "produtos" in t:
        return True
    # heurística: ter 'sku' ou 'qnt' no bloco
    return ("sku" in t or "qnt" in t or "qtd" in t or "qtde" in t)

def extract_products_from_quad(text: str) -> list[str]:
    """Retorna itens no formato '- 2x ABC-123'."""
    raw = norm_heavy(text)
    low = raw.lower()
    if any(w in low for w in EXCLUDE_LABEL_WORDS):
        return []
    if not is_list_block(raw):
        return []

    rows = extract_rows(raw)
    if not rows:
        rows = [ln.strip() for ln in raw.splitlines() if ln.strip()]

    items = []
    for row in rows:
        base = norm_heavy(row)

        # QNT
        qty = None
        for p in QTY_PATTS:
            m = p.search(base)
            if m:
                try: qty = int(m.group(1)); break
                except: pass
        if qty is None:
            m = re.search(r"\b(\d{1,3})\s*x\b|\bx\s*(\d{1,3})\b", base, re.I)
            if m: qty = int(m.group(1) or m.group(2))
        if qty is None: qty = 1

        # SKU: preferir após 'SKU:'
        sku = None
        msku = re.search(r"\bS\s*K\s*U[:\s\-]*([A-Z0-9\s\-\/\.]{3,})", base, re.I)
        if msku:
            cand = re.sub(r"\s+", "", msku.group(1))
            mt = SKU_TOKEN_RE.search(cand)
            if mt: sku = mt.group(1)
        if not sku:
            toks = [t for t in SKU_TOKEN_RE.findall(base) if not t.startswith("BR")]
            if toks: sku = toks[-1]

        if sku:
            line = f"- {qty}x {sku}"
            if line not in items:
                items.append(line)
        if len(items) >= MAX_LINES:
            break

    # precisa ter pelo menos 1 item com SKU real
    if not any(SKU_TOKEN_RE.search(x) for x in items):
        return []
    return items

# =============== Pipeline ===============
def process_pdf(pdf_bytes: bytes, show_diag=False):
    reader = PdfReader(io.BytesIO(pdf_bytes))
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")

    pick_by_order = {}     # pedido -> [linhas]
    pick_free = []         # listas sem pedido (ordem)
    labels = []            # [{'page_idx','pypdf_box','text'}]
    diag = {"lists": [], "labels": []}

    for i in range(len(doc)):
        p_fitz = doc[i]
        qf = quadrants_fitz(p_fitz.rect)
        qp = quadrants_pypdf(reader.pages[i].mediabox)

        for rect_fitz, box_pdf in zip(qf, qp):
            txt = p_fitz.get_text("text", clip=rect_fitz) or ""

            # LISTA?
            prods = extract_products_from_quad(txt)
            if prods:
                order = extract_order(txt)
                if order:
                    acc = pick_by_order.setdefault(order, [])
                    for it in prods:
                        if it not in acc: acc.append(it)
                    if show_diag: diag["lists"].append((order, acc[:3]))
                else:
                    pick_free.append(prods)
                    if show_diag: diag["lists"].append(("sem_pedido", prods[:3]))
                continue

            # ETIQUETA?
            if REMOVE_BLANK and quad_is_blank_by_raster(doc, i, rect_fitz):
                continue
            labels.append({"page_idx": i, "pypdf_box": box_pdf, "text": txt})
            if show_diag:
                diag["labels"].append(extract_order(txt) or "(sem pedido)")

    # Recorta TODAS as etiquetas
    if not labels:
        # nada reconhecido — fallback 4→1 puro
        w = PdfWriter()
        for i in range(len(reader.pages)):
            page = reader.pages[i]
            for (x0,y0,x1,y1) in quadrants_pypdf(page.mediabox):
                if REMOVE_BLANK and quad_is_blank_by_raster(doc, i, fitz.Rect(x0,y0,x1,y1)):
                    continue
                p = deepcopy(page); rect = RectangleObject([x0,y0,x1,y1])
                p.cropbox = rect; p.mediabox = rect
                w.add_page(p)
        out = io.BytesIO(); w.write(out); out.seek(0)
        return out.getvalue(), diag

    w = PdfWriter()
    for lab in labels:
        page = reader.pages[lab["page_idx"]]
        x0,y0,x1,y1 = lab["pypdf_box"]
        p = deepcopy(page)
        rect = RectangleObject([x0,y0,x1,y1])
        p.cropbox = rect; p.mediabox = rect
        w.add_page(p)
    buf = io.BytesIO(); w.write(buf); buf.seek(0)
    cropped = fitz.open(stream=buf.getvalue(), filetype="pdf")

    # Monta 1 página por etiqueta + rodapé
    final_doc = fitz.open()
    used_orders = set()
    free_idx = 0

    for idx, lab in enumerate(labels):
        src_pg = cropped[idx]
        r = src_pg.rect

        order = extract_order(lab["text"])
        if order in pick_by_order and order not in used_orders:
            products = pick_by_order[order][:MAX_LINES]
            used_orders.add(order)
        else:
            products = pick_free[free_idx][:MAX_LINES] if free_idx < len(pick_free) else []
            if free_idx < len(pick_free): free_idx += 1

        # altura extra do rodapé
        lines_count = 1 + max(1, len(products))
        min_area = PAD_Y_PT*2 + (FONT_SIZE+2)*lines_count
        extra_h = max(r.height*OVERLAY_HEIGHT_PCT, min_area)

        new_pg = final_doc.new_page(width=r.width, height=r.height+extra_h)
        new_pg.show_pdf_page(fitz.Rect(0,0,r.width,r.height), cropped, idx)

        box = fitz.Rect(MARGIN_X_PT, r.height+PAD_Y_PT, r.width-MARGIN_X_PT, r.height+extra_h-PAD_Y_PT)
        text = "Lista de separação:\n" + ("\n".join(products) if products else "- (não encontrado)")
        new_pg.insert_textbox(box, text, fontname="helv", fontsize=FONT_SIZE, align=0)

    out = io.BytesIO(); final_doc.save(out); final_doc.close(); out.seek(0)
    return out.getvalue(), diag

# ================= RUN =================
if up:
    try:
        pdf_in = up.getvalue()
        with st.spinner("Processando etiquetas e listas…"):
            pdf_out, diag = process_pdf(pdf_in, show_diag=True)

        st.success("Pronto! 1 etiqueta por página, com a lista (QNT × SKU) no rodapé.")
        st.download_button("Baixar PDF final", data=pdf_out, file_name="etiquetas_com_lista.pdf", mime="application/pdf")

        with st.expander("Diagnóstico"):
            st.write(diag)
    except Exception as e:
        st.error("Falha ao processar o PDF.")
        st.exception(e)
else:
    st.info("Faça o upload de um PDF para iniciar.")
