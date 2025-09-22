import streamlit as st
from pypdf import PdfReader, PdfWriter
from pypdf.generic import RectangleObject
from copy import deepcopy
from pathlib import Path
import base64, io, re, unicodedata

import fitz  # PyMuPDF
from PIL import Image

# ================== CONFIG BÁSICA ==================
st.set_page_config(page_title="Etiquetas 4→1 + Lista no rodapé (robusto)", layout="wide")

# Oculta branding
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
    "<p style='text-align:center;margin-top:0.25rem;'>Casa pelo <b>PEDIDO</b>. A lista vai numa <b>faixa extra</b> abaixo da etiqueta (sem sobrepor o código de barras).</p>",
    unsafe_allow_html=True,
)
st.divider()

# ================== UPLOADER (500px + VERDE) ==================
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
REMOVE_BLANK   = True
DPI_CHECK      = 120
WHITE_THRESH   = 245
COVERAGE       = 0.995   # 99,5% branco = vazio

# faixa extra (embaixo da etiqueta)
OVERLAY_HEIGHT_PCT = 0.14
FONT_SIZE          = 7
MAX_LINES          = 4
MARGIN_X_PT        = 18
PAD_Y_PT           = 6

# ================== HELPERS ==================
def normalize_txt(t: str) -> str:
    t = unicodedata.normalize("NFKD", t)
    t = "".join(ch for ch in t if not unicodedata.combining(ch))
    return re.sub(r"\s+", " ", t).strip()

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

# ====== PEDIDO ======
# aceita: “TOKEN PACKAGE/PACOTE”, “ID Pedido: TOKEN”, “Pedido nº TOKEN”, etc.
ORDER_NEAR_RE = re.compile(
    r"(?:ID\s*PEDIDO|PEDIDO\s*N?[ºO]?|N[ºO]\s*DO\s*PEDIDO)[:\s#-]*([A-Z0-9\-\s]{8,})",
    re.I,
)

def extract_order_pick(text: str) -> str:
    up = text.upper()
    # antes de PACKAGE / PACOTE
    m = re.search(r"([A-Z0-9]{10,24})\s+(?:PACKAGE|PACOTE)\b", up)
    if m:
        tok = m.group(1)
        if not tok.startswith("BR") and re.search(r"[A-Z]", tok) and re.search(r"\d", tok):
            return tok
    # perto de "ID Pedido" (variações)
    m2 = ORDER_NEAR_RE.search(up)
    if m2:
        tok = re.sub(r"[^A-Z0-9]","", m2.group(1))
        if 8 <= len(tok) <= 24 and not tok.startswith("BR") and re.search(r"[A-Z]", tok) and re.search(r"\d", tok):
            return tok
    # fallback seguro
    for m3 in re.finditer(r"\b[A-Z0-9]{10,24}\b", up):
        tok = m3.group(0)
        if tok.startswith("BR"):
            continue
        if re.search(r"[A-Z]", tok) and re.search(r"\d", tok):
            return tok
    return ""

def extract_order_label(text: str, allowed: set[str]) -> str:
    up = text.upper()
    cands = []
    for m in re.finditer(r"\b([A-Z0-9]{10,24})\b", up):
        tok = m.group(1)
        if tok.startswith("BR"):  # ignora código logístico
            continue
        if len(re.findall(r"[A-Z]", tok)) < 2 or not re.search(r"\d", tok):
            continue
        cands.append(tok)
    for tok in cands:
        if tok in allowed:
            return tok
    return ""

# ====== PRODUTOS ======
STOP_WORDS = ["checklist de carregamento", "id pedido", "corte aqui", "pagamento", "assinatura"]
HEADER_PATTERNS = [
    re.compile(r"^\s*(produto.*vari(a|á)ç?ao.*qnt.*sku)\s*$", re.I),
    re.compile(r"^\s*qnt\s+sku\s*$", re.I),
]

def extract_products(text: str) -> list[str]:
    lines = [ln.strip() for ln in text.splitlines()]
    started = False
    items, cur = [], ""

    def is_header(line_low: str) -> bool:
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
            if is_header(low):
                started = True
            continue
        if is_header(low):
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
        # fallback: primeiras linhas “ricas”
        for ln in lines:
            l = normalize_txt(ln)
            if any(s in l.lower() for s in STOP_WORDS): break
            if ("produto" in l.lower() and "qnt" in l.lower() and "sku" in l.lower()): continue
            if len(l) > 3 and re.search(r"[A-Za-z]", l):
                items.append(l[:90] + ("..." if len(l) > 90 else ""))
            if len(items) >= MAX_LINES: break
    return items[:MAX_LINES]

# ================== PROCESSAMENTO ==================
def process_pdf(pdf_bytes: bytes, show_diag: bool = False):
    reader = PdfReader(io.BytesIO(pdf_bytes))
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")

    pick_by_order = {}   # order -> [produtos]
    label_quads = []     # candidatos a etiqueta (mesmo sem order)

    diag_picks, diag_labels = [], []  # diagnóstico

    # --- varre quadrante a quadrante ---
    for i in range(len(doc)):
        p_fitz = doc[i]
        qf = quadrants_fitz(p_fitz.rect)
        qp = quadrants_pypdf(reader.pages[i].mediabox)

        for rect_fitz, box_pdf in zip(qf, qp):
            txt = p_fitz.get_text("text", clip=rect_fitz) or ""

            # tenta picklist
            order_pk = extract_order_pick(txt)
            prods    = extract_products(txt) if order_pk else []
            if order_pk and prods:
                acc = pick_by_order.setdefault(order_pk, [])
                for p in prods:
                    if p not in acc:
                        acc.append(p)
                if show_diag:
                    diag_picks.append((order_pk, prods[:3]))
                continue

            # candidato a etiqueta (não branco)
            if REMOVE_BLANK and quad_is_blank_by_raster(doc, i, rect_fitz):
                continue
            label_quads.append({
                "page_idx": i, "pypdf_box": box_pdf, "fitz_rect": rect_fitz, "text": txt
            })

    allowed = set(pick_by_order.keys())
    labels_by_order = {}
    order_sequence = []
    for lab in label_quads:
        order = extract_order_label(lab["text"], allowed)
        if not order or order in labels_by_order:
            continue
        labels_by_order[order] = {"page_idx": lab["page_idx"], "pypdf_box": lab["pypdf_box"]}
        order_sequence.append(order)
        if show_diag:
            diag_labels.append(order)

    # ========== FALLBACK 1: sem nenhum 'order' casado, mas com etiquetas detectadas ==========
    if not order_sequence and label_quads:
        writer = PdfWriter()
        for lab in label_quads:
            p_src = reader.pages[lab["page_idx"]]
            x0, y0, x1, y1 = lab["pypdf_box"]
            p = deepcopy(p_src)
            rect = RectangleObject([x0, y0, x1, y1])
            p.cropbox = rect
            p.mediabox = rect
            writer.add_page(p)
        tmp = io.BytesIO()
        writer.write(tmp); tmp.seek(0)
        cropped_doc = fitz.open(stream=tmp.getvalue(), filetype="pdf")

        final_doc = fitz.open()
        for idx in range(len(cropped_doc)):
            src_pg = cropped_doc[idx]
            r = src_pg.rect
            # cria faixa mínima para pelo menos 1 linha
            lines_count = 2  # "Produtos:" + 1
            min_area_pt = PAD_Y_PT*2 + (FONT_SIZE + 2) * lines_count
            extra_h = max(r.height * OVERLAY_HEIGHT_PCT, min_area_pt)

            new_pg = final_doc.new_page(width=r.width, height=r.height + extra_h)
            new_pg.show_pdf_page(fitz.Rect(0, 0, r.width, r.height), cropped_doc, idx)

            box = fitz.Rect(MARGIN_X_PT, r.height + PAD_Y_PT,
                            r.width - MARGIN_X_PT, r.height + extra_h - PAD_Y_PT)
            new_pg.insert_textbox(box, "Produtos:\n• (não encontrado)", fontname="helv",
                                  fontsize=FONT_SIZE, align=0)

        out_buf = io.BytesIO()
        final_doc.save(out_buf)
        final_doc.close()
        out_buf.seek(0)
        diag = {"picks": diag_picks, "labels": diag_labels, "orders": order_sequence}
        return out_buf.getvalue(), diag

    # ========== FALLBACK 2: nada detectado mesmo ==========
    if not order_sequence and not label_quads:
        writer = PdfWriter()
        for i in range(len(reader.pages)):
            page = reader.pages[i]
            for (x0, y0, x1, y1) in quadrants_pypdf(page.mediabox):
                if REMOVE_BLANK and quad_is_blank_by_raster(doc, i, fitz.Rect(x0, y0, x1, y1)):
                    continue
                p = deepcopy(page)
                rect = RectangleObject([x0, y0, x1, y1])
                p.cropbox = rect
                p.mediabox = rect
                writer.add_page(p)
        out = io.BytesIO(); writer.write(out); out.seek(0)
        diag = {"picks": diag_picks, "labels": diag_labels, "orders": []}
        return out.getvalue(), diag

    # ========== CAMINHO NORMAL (há orders casados) ==========
    writer = PdfWriter()
    for order in order_sequence:
        info = labels_by_order[order]
        p_src = reader.pages[info["page_idx"]]
        x0, y0, x1, y1 = info["pypdf_box"]
        p = deepcopy(p_src)
        rect = RectangleObject([x0, y0, x1, y1])
        p.cropbox = rect
        p.mediabox = rect
        writer.add_page(p)

    tmp = io.BytesIO()
    writer.write(tmp)
    tmp.seek(0)
    cropped_doc = fitz.open(stream=tmp.getvalue(), filetype="pdf")

    final_doc = fitz.open()
    for idx, order in enumerate(order_sequence):
        src_pg = cropped_doc[idx]
        r = src_pg.rect
        products = pick_by_order.get(order, [])[:MAX_LINES]

        lines_count = 1 + max(1, len(products))
        min_area_pt = PAD_Y_PT*2 + (FONT_SIZE + 2) * lines_count
        extra_h = max(r.height * OVERLAY_HEIGHT_PCT, min_area_pt)

        new_pg = final_doc.new_page(width=r.width, height=r.height + extra_h)
        new_pg.show_pdf_page(fitz.Rect(0, 0, r.width, r.height), cropped_doc, idx)

        box = fitz.Rect(MARGIN_X_PT, r.height + PAD_Y_PT,
                        r.width - MARGIN_X_PT, r.height + extra_h - PAD_Y_PT)
        text = "Produtos:\n" + ("\n".join(f"• {p}" for p in products) if products else "• (não encontrado)")
        new_pg.insert_textbox(box, text, fontname="helv", fontsize=FONT_SIZE, align=0)

    out_buf = io.BytesIO()
    final_doc.save(out_buf)
    final_doc.close()
    out_buf.seek(0)

    diag = {"picks": diag_picks, "labels": diag_labels, "orders": order_sequence}
    return out_buf.getvalue(), diag

# ================== RUN ==================
if uploaded_file is not None:
    try:
        pdf_in = uploaded_file.getvalue()
        with st.spinner("Processando (detecção robusta + faixa extra)..."):
            pdf_out, diag = process_pdf(pdf_in, show_diag=True)

        st.success("Pronto! 1 etiqueta por pedido, com a lista no rodapé (sem sobrepor).")
        st.download_button("Baixar PDF final", data=pdf_out, file_name="etiquetas_com_lista.pdf", mime="application/pdf")

        with st.expander("Diagnóstico (ver pedidos e itens detectados)"):
            st.write("Pedidos detectados nas listas (prévia de itens):")
            if diag["picks"]:
                for oid, sample in diag["picks"]:
                    st.write(f"• {oid} → {sample}")
            else:
                st.write("Nenhuma lista de separação detectada.")

            st.write("Pedidos detectados nas etiquetas:")
            if diag["labels"]:
                st.write(", ".join(diag["labels"]))
            else:
                st.write("Nenhuma etiqueta casada com as listas.")

            st.write("Ordem final de saída:", ", ".join(diag["orders"]) if diag["orders"] else "(vazia)")

    except Exception as e:
        st.error("Não foi possível processar o arquivo. Verifique se é um PDF válido.")
        st.exception(e)
else:
    st.info("Faça o upload de um PDF para iniciar o processamento.")
