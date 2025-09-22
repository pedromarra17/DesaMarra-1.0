import streamlit as st
from pypdf import PdfReader, PdfWriter
from pypdf.generic import RectangleObject
from copy import deepcopy
from pathlib import Path
import base64, io, re, unicodedata

import fitz  # PyMuPDF
from PIL import Image

# ================== CONFIG ==================
st.set_page_config(page_title="Etiquetas 4→1 + Lista (QNT + SKU)", layout="wide")

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
    "<h1 style='text-align:center;margin:0.4rem 0 0 0;'>Etiquetas (4 → 1) + Lista de separação (QNT + SKU)</h1>",
    unsafe_allow_html=True,
)
st.markdown(
    "<p style='text-align:center;margin-top:0.25rem;'>Casa pelo <b>PEDIDO</b> quando possível. Se não, pareia por <b>ordem</b> (1ª etiqueta ↔ 1ª lista, etc.). A lista é impressa numa faixa abaixo, sem cobrir o código de barras.</p>",
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

# faixa extra
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

LATIN = r"A-Za-zÀ-ÖØ-öø-ÿ"

def deinterleave_letters(s: str) -> str:
    # "P r o d u t o" -> "Produto"
    return re.sub(rf"((?:[{LATIN}]\s){{2,}}[{LATIN}])",
                  lambda m: re.sub(r"\s+", "", m.group(0)),
                  s)

def norm_heavy(t: str) -> str:
    t = normalize_txt(t)
    t = deinterleave_letters(t)
    t = re.sub(r"\s+", " ", t)
    return t

def contains_fuzzy(s: str, word: str) -> bool:
    patt = r"\s*".join(list(word))
    return re.search(patt, s, re.I) is not None

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

# --- classificação picklist ---
def is_picklist_like(text: str) -> bool:
    s = norm_heavy(text)
    return (contains_fuzzy(s, "produto") and contains_fuzzy(s, "sku")) \
           or contains_fuzzy(s, "checklist") \
           or contains_fuzzy(s, "corte aqui")

# ====== PEDIDO ======
ORDER_NEAR_RE = re.compile(
    r"(?:ID\s*PEDIDO|PEDIDO\s*N?[ºO]?|N[ºO]\s*DO\s*PEDIDO)[:\s#-]*((?:[A-Z0-9]\s*){8,24})",
    re.I,
)

def extract_order_pick(text: str) -> str:
    up = norm_heavy(text).upper()
    m = re.search(r"((?:[A-Z0-9]\s*){10,24})\s*(?:PACKAGE|PACOTE)\b", up)
    if m:
        tok = re.sub(r"\s+", "", m.group(1))
        if not tok.startswith("BR") and re.search(r"[A-Z]", tok) and re.search(r"\d", tok):
            return tok
    m2 = ORDER_NEAR_RE.search(up)
    if m2:
        tok = re.sub(r"\s+", "", m2.group(1))
        if 8 <= len(tok) <= 24 and not tok.startswith("BR") and re.search(r"[A-Z]", tok) and re.search(r"\d", tok):
            return tok
    m3 = re.search(r"((?:[A-Z0-9]\s*){10,24})", up)
    if m3:
        tok = re.sub(r"\s+", "", m3.group(1))
        if not tok.startswith("BR") and re.search(r"[A-Z]", tok) and re.search(r"\d", tok):
            return tok
    return ""

def extract_order_label(text: str, allowed: set[str]) -> str:
    up = norm_heavy(text).upper()
    cands = []
    for m in re.finditer(r"\b([A-Z0-9]{10,24})\b", up):
        tok = m.group(1)
        if tok.startswith("BR"):
            continue
        if len(re.findall(r"[A-Z]", tok)) < 2 or not re.search(r"\d", tok):
            continue
        cands.append(tok)
    for tok in cands:
        if tok in allowed:
            return tok
    return ""

# ====== PRODUTOS (QNT + SKU) ======
STOP_WORDS = ["checklist de carregamento", "id pedido", "corte aqui", "pagamento", "assinatura"]
SKU_TOKEN_RE = re.compile(r"\b([A-Z0-9\-]{6,32})\b")
QTY_PATTS = [
    re.compile(r"(?:QNT|QTD|QUANTIDADE)\s*[:x\-]*\s*(\d{1,3})", re.I),
    re.compile(r"\b(\d{1,3})\s*(?:UN|UND|PCS|PÇS|PC|PÇ)\b", re.I),
    re.compile(r"\bx\s*(\d{1,3})\b", re.I),
    re.compile(r"\b(\d{1,3})\s*x\b", re.I),
]

def _split_picklist_rows(text: str) -> list[str]:
    t = norm_heavy(text)
    lines = [ln.strip() for ln in t.splitlines()]
    rows, buf, started = [], [], False

    def is_header(s: str) -> bool:
        s = s.lower()
        return ("produto" in s and "qnt" in s and "sku" in s)

    for raw in lines:
        low = raw.lower()
        if any(s in low for s in STOP_WORDS):
            break
        if not started:
            if is_header(low):
                started = True
            continue
        if is_header(low):
            if buf:
                rows.append(" ".join(buf)); buf = []
            continue
        if len(" ".join(buf)) >= 60:
            rows.append(" ".join(buf)); buf = []
        buf.append(raw)

    if buf:
        rows.append(" ".join(buf))

    if not rows:
        for ln in lines:
            l = re.sub(r"\s+", " ", ln).strip()
            if any(s in l.lower() for s in STOP_WORDS): break
            if len(l) > 3 and re.search(r"[A-Za-z]", l):
                rows.append(l)
            if len(rows) >= MAX_LINES: break
    return rows

def extract_products_from_picklist(text: str) -> list[str]:
    rows = _split_picklist_rows(text)
    display = []
    for row in rows:
        base = norm_heavy(row)

        # QNT
        qty = None
        for p in QTY_PATTS:
            m = p.search(base)
            if m:
                try:
                    qty = int(m.group(1)); break
                except: pass
        if qty is None: qty = 1

        # SKU (1) após a palavra SKU
        sku = None
        msku = re.search(r"S\s*K\s*U[:\s\-]*([A-Z0-9\s\-]{4,})", base, re.I)
        if msku:
            cand = re.sub(r"\s+", "", msku.group(1))
            mtk = SKU_TOKEN_RE.search(cand)
            if mtk: sku = mtk.group(1)
        # SKU (2) último token grande (evita BR e CEP)
        if not sku:
            toks = [t for t in SKU_TOKEN_RE.findall(base) if not t.startswith("BR")]
            if toks: sku = toks[-1]

        # Nome (antes de QNT/SKU)
        name = base
        cut = min([pos for pos in [
            name.lower().find(" qnt"), name.lower().find(" qtd"), name.lower().find(" quantidade"),
            name.lower().find(" sku")
        ] if pos >= 0] or [len(name)])
        name = name[:cut].strip(" -•–:;")
        name = re.sub(r"^\s*\d+\s*[-.)]\s*", "", name).strip()

        # Linha final
        if sku:
            line = f"{qty}× {name} — {sku}"
        else:
            line = f"{qty}× {name}"
        line = re.sub(r"\s+", " ", line).strip()
        if len(line) > 110: line = line[:107] + "..."
        if line and line not in display:
            display.append(line)
        if len(display) >= MAX_LINES: break
    return display

# ================== PROCESSAMENTO ==================
def process_pdf(pdf_bytes: bytes, show_diag: bool = False):
    reader = PdfReader(io.BytesIO(pdf_bytes))
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")

    pick_by_order = {}     # order -> [linhas]
    pick_no_order = []     # listas sem pedido (para pareamento por ordem)
    label_quads = []       # candidatos a etiqueta
    diag_picks, diag_labels = [], []

    # --- varre quadrante a quadrante ---
    for i in range(len(doc)):
        p_fitz = doc[i]
        qf = quadrants_fitz(p_fitz.rect)
        qp = quadrants_pypdf(reader.pages[i].mediabox)

        for rect_fitz, box_pdf in zip(qf, qp):
            txt = p_fitz.get_text("text", clip=rect_fitz) or ""

            # 1) picklist-like?
            if is_picklist_like(txt):
                order_pk = extract_order_pick(txt)
                prods    = extract_products_from_picklist(txt)
                if order_pk and prods:
                    acc = pick_by_order.setdefault(order_pk, [])
                    for p in prods:
                        if p not in acc: acc.append(p)
                    if show_diag: diag_picks.append((order_pk, prods[:3]))
                elif prods:
                    pick_no_order.append(prods)  # salva para parear por ordem
                    if show_diag: diag_picks.append(("(sem pedido)", prods[:3]))
                continue

            # 2) etiqueta (descarta branco)
            if REMOVE_BLANK and quad_is_blank_by_raster(doc, i, rect_fitz):
                continue
            label_quads.append({
                "page_idx": i, "pypdf_box": box_pdf, "fitz_rect": rect_fitz, "text": txt
            })

    # --- resolve etiquetas casadas por PEDIDO ---
    allowed = set(pick_by_order.keys())
    labels_by_order = {}
    order_sequence = []
    for lab in label_quads:
        order = extract_order_label(lab["text"], allowed)
        if not order or order in labels_by_order:
            continue
        labels_by_order[order] = {"page_idx": lab["page_idx"], "pypdf_box": lab["pypdf_box"]}
        order_sequence.append(order)
        if show_diag: diag_labels.append(order)

    # --- corta etiquetas (pypdf) ---
    writer = PdfWriter()
    label_pages = []  # (width,height) depois do crop, na ordem que serão impressas
    if order_sequence:
        # somente casadas
        for order in order_sequence:
            info = labels_by_order[order]
            p_src = reader.pages[info["page_idx"]]
            x0, y0, x1, y1 = info["pypdf_box"]
            p = deepcopy(p_src)
            rect = RectangleObject([x0, y0, x1, y1])
            p.cropbox = rect; p.mediabox = rect
            writer.add_page(p)
        tmp = io.BytesIO(); writer.write(tmp); tmp.seek(0)
        cropped_doc = fitz.open(stream=tmp.getvalue(), filetype="pdf")
        # monta final com faixa
        final_doc = fitz.open()
        for idx, order in enumerate(order_sequence):
            src_pg = cropped_doc[idx]
            r = src_pg.rect
            products = pick_by_order.get(order, [])
            # fallback: se não achou por pedido, pega por ordem
            if not products and pick_no_order:
                take = min(idx, len(pick_no_order)-1)
                products = pick_no_order[take]
            products = products[:MAX_LINES]

            lines_count = 1 + max(1, len(products))
            min_area_pt = PAD_Y_PT*2 + (FONT_SIZE + 2) * lines_count
            extra_h = max(r.height * OVERLAY_HEIGHT_PCT, min_area_pt)

            new_pg = final_doc.new_page(width=r.width, height=r.height + extra_h)
            new_pg.show_pdf_page(fitz.Rect(0, 0, r.width, r.height), cropped_doc, idx)

            box = fitz.Rect(MARGIN_X_PT, r.height + PAD_Y_PT,
                            r.width - MARGIN_X_PT, r.height + extra_h - PAD_Y_PT)
            text = "Lista de separação:\n" + (
                "\n".join(f"• {p}" for p in products) if products else "• (não encontrado)"
            )
            new_pg.insert_textbox(box, text, fontname="helv", fontsize=FONT_SIZE, align=0)

        out_buf = io.BytesIO(); final_doc.save(out_buf); final_doc.close(); out_buf.seek(0)
        diag = {"picks": diag_picks, "labels": diag_labels, "orders": order_sequence}
        return out_buf.getvalue(), diag

    # --- FALLBACK: não casou por pedido -> corta TODAS etiquetas e pareia por ORDEM com pick_no_order ---
    if label_quads:
        writer = PdfWriter()
        for lab in label_quads:
            p_src = reader.pages[lab["page_idx"]]
            x0, y0, x1, y1 = lab["pypdf_box"]
            p = deepcopy(p_src)
            rect = RectangleObject([x0, y0, x1, y1])
            p.cropbox = rect; p.mediabox = rect
            writer.add_page(p)
        tmp = io.BytesIO(); writer.write(tmp); tmp.seek(0)
        cropped_doc = fitz.open(stream=tmp.getvalue(), filetype="pdf")

        final_doc = fitz.open()
        for idx in range(len(cropped_doc)):
            src_pg = cropped_doc[idx]
            r = src_pg.rect
            products = pick_no_order[idx] if idx < len(pick_no_order) else []
            products = products[:MAX_LINES]

            lines_count = 1 + max(1, len(products))
            min_area_pt = PAD_Y_PT*2 + (FONT_SIZE + 2) * lines_count
            extra_h = max(r.height * OVERLAY_HEIGHT_PCT, min_area_pt)

            new_pg = final_doc.new_page(width=r.width, height=r.height + extra_h)
            new_pg.show_pdf_page(fitz.Rect(0, 0, r.width, r.height), cropped_doc, idx)

            box = fitz.Rect(MARGIN_X_PT, r.height + PAD_Y_PT,
                            r.width - MARGIN_X_PT, r.height + extra_h - PAD_Y_PT)
            text = "Lista de separação:\n" + (
                "\n".join(f"• {p}" for p in products) if products else "• (não encontrado)"
            )
            new_pg.insert_textbox(box, text, fontname="helv", fontsize=FONT_SIZE, align=0)

        out_buf = io.BytesIO(); final_doc.save(out_buf); final_doc.close(); out_buf.seek(0)
        diag = {"picks": diag_picks, "labels": diag_labels, "orders": []}
        return out_buf.getvalue(), diag

    # --- último fallback: nada detectado -> 4→1 básico ---
    writer = PdfWriter()
    for i in range(len(reader.pages)):
        page = reader.pages[i]
        for (x0, y0, x1, y1) in quadrants_pypdf(page.mediabox):
            if REMOVE_BLANK and quad_is_blank_by_raster(doc, i, fitz.Rect(x0, y0, x1, y1)):
                continue
            p = deepcopy(page)
            rect = RectangleObject([x0, y0, x1, y1])
            p.cropbox = rect; p.mediabox = rect
            writer.add_page(p)
    out = io.BytesIO(); writer.write(out); out.seek(0)
    diag = {"picks": diag_picks, "labels": diag_labels, "orders": []}
    return out.getvalue(), diag

# ================== RUN ==================
if uploaded_file is not None:
    try:
        pdf_in = uploaded_file.getvalue()
        with st.spinner("Processando (QNT + SKU + pareamento por ordem)…"):
            pdf_out, diag = process_pdf(pdf_in, show_diag=True)

        st.success("Pronto! 1 etiqueta por cliente, com a Lista de separação (QNT + SKU) no rodapé.")
        st.download_button("Baixar PDF final", data=pdf_out,
                           file_name="etiquetas_com_lista.pdf", mime="application/pdf")

        with st.expander("Diagnóstico (pedidos e itens detectados)"):
            st.write("Listas detectadas (pedido → amostra):")
            if diag["picks"]:
                for oid, sample in diag["picks"]:
                    st.write(f"• {oid} → {sample}")
            else:
                st.write("Nenhuma lista reconhecida.")
            st.write("Etiquetas casadas por PEDIDO:", ", ".join(diag["labels"]) if diag["labels"] else "(nenhuma)")
            st.write("Ordem final (se casou por pedido):", ", ".join(diag["orders"]) if diag["orders"] else "(n/a)")
    except Exception as e:
        st.error("Não foi possível processar o arquivo. Verifique se é um PDF válido.")
        st.exception(e)
else:
    st.info("Faça o upload de um PDF para iniciar o processamento.")
