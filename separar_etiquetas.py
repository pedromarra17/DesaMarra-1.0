import streamlit as st
from pypdf import PdfReader, PdfWriter
from pypdf.generic import RectangleObject
from copy import deepcopy
from pathlib import Path
import base64, io, re, unicodedata

import fitz  # PyMuPDF
from PIL import Image

# ================== CONFIG BÁSICA ==================
st.set_page_config(page_title="Etiquetas 4→1 + Lista (QNT + SKU)", layout="wide")

# Oculta branding/toolbar (no Cloud o selo pode não sumir 100%)
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
    "<p style='text-align:center;margin-top:0.25rem;'>Casa pelo <b>PEDIDO</b> quando possível; se não, pareia por <b>ordem</b> (1ª etiqueta ↔ 1ª lista). A lista vai numa <b>faixa extra</b> abaixo da etiqueta (sem sobrepor o código de barras).</p>",
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
OVERLAY_HEIGHT_PCT = 0.14   # % da ALTURA da etiqueta como faixa mínima
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

# ====== DETECÇÃO DE PEDIDO ======
ORDER_NEAR_RE = re.compile(
    r"(?:ID\s*PEDIDO|PEDIDO\s*N?[ºO]?|N[ºO]\s*DO\s*PEDIDO)[:\s#-]*((?:[A-Z0-9]\s*){8,24})",
    re.I,
)

def extract_order_pick(text: str) -> str:
    up = norm_heavy(text).upper()

    # TOKEN (com espaços opcionais) + PACKAGE/PACOTE
    m = re.search(r"((?:[A-Z0-9]\s*){10,24})\s*(?:PACKAGE|PACOTE)\b", up)
    if m:
        tok = re.sub(r"\s+", "", m.group(1))
        if not tok.startswith("BR") and re.search(r"[A-Z]", tok) and re.search(r"\d", tok):
            return tok

    # ID Pedido / Pedido nº ...
    m2 = ORDER_NEAR_RE.search(up)
    if m2:
        tok = re.sub(r"\s+", "", m2.group(1))
        if 8 <= len(tok) <= 24 and not tok.startswith("BR") and re.search(r"[A-Z]", tok) and re.search(r"\d", tok):
            return tok

    # fallback seguro
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

# ====== LISTA: QNT + SKU (conservador) ======
EXCLUDE_LABEL_WORDS = [
    "danfe", "destinat", "remetente", "agência", "agencia",
    "bairro", "cep", "emissão", "emissao", "série", "serie"
]
STOP_WORDS = ["checklist de carregamento", "id pedido", "corte aqui", "pagamento", "assinatura"]

# SKU deve ter ao menos UMA LETRA e UM DÍGITO (evita códigos da etiqueta)
SKU_TOKEN_RE = re.compile(r"\b(?=[A-Z0-9\-]{5,32}\b)(?=.*[A-Z])(?=.*\d)[A-Z0-9\-]+\b")

QTY_PATTS = [
    re.compile(r"(?:QNT|QTD|QTDE|QUANTIDADE)\s*[:x\-]*\s*(\d{1,3})", re.I),
    re.compile(r"\b(\d{1,3})\s*(?:UN|UND|PCS|PÇS|PC|PÇ)\b", re.I),
    re.compile(r"\bx\s*(\d{1,3})\b", re.I),
    re.compile(r"\b(\d{1,3})\s*x\b", re.I),
]

def extract_products_from_quad(text: str) -> list[str]:
    """
    Detecta LISTA de separação com heurística conservadora:
    - deve conter 'SKU' OU ('Produto' E 'QNT/QTD/QTDE/Quantidade');
    - não pode conter palavras típicas de etiqueta;
    - deve haver ao menos 1 SKU real (letras+núm).
    Retorna linhas '2× Nome — SKU'.
    """
    raw = norm_heavy(text)
    s = raw.lower()

    # exclusões típicas de etiqueta
    if any(w in s for w in EXCLUDE_LABEL_WORDS):
        return []

    # pistas de lista
    has_sku_word  = re.search(r"s\s*k\s*u", s) is not None
    has_prod_word = re.search(r"p\s*r\s*o\s*d\s*u\s*t\s*o", s) is not None
    has_qty_word  = any(re.search(pat, s) for pat in [r"q\s*n\s*t", r"q\s*t\s*d", r"q\s*t\s*d\s*e", r"q\s*u\s*a\s*n\s*t"])

    # conta SKUs válidos
    lines = [ln.strip() for ln in raw.splitlines() if ln.strip()]
    all_skus = []
    for ln in lines:
        all_skus += [t for t in SKU_TOKEN_RE.findall(ln) if not t.startswith("BR")]

    if not ((has_sku_word or (has_prod_word and has_qty_word)) and len(set(all_skus)) >= 1):
        return []

    items, buf = [], []

    def flush():
        nonlocal buf
        if not buf:
            return
        base = re.sub(r"\s+", " ", " ".join(buf)).strip()

        # qty
        qty = None
        for p in QTY_PATTS:
            m = p.search(base)
            if m:
                try:
                    qty = int(m.group(1)); break
                except:
                    pass
        if qty is None:
            qty = 1

        # sku (prefere após 'SKU')
        sku = None
        msku = re.search(r"S\s*K\s*U[:\s\-]*([A-Z0-9\s\-]{4,})", base, re.I)
        if msku:
            cand = re.sub(r"\s+", "", msku.group(1))
            mt = SKU_TOKEN_RE.search(cand)
            if mt:
                sku = mt.group(1)
        if not sku:
            toks = [t for t in SKU_TOKEN_RE.findall(base) if not t.startswith("BR")]
            if toks:
                sku = toks[-1]

        # nome (antes de QNT/SKU)
        name = base
        cut = min([pos for pos in [
            name.lower().find(" qnt"), name.lower().find(" qtd"), name.lower().find(" qtde"),
            name.lower().find(" quantidade"), name.lower().find(" sku")
        ] if pos >= 0] or [len(name)])
        name = re.sub(r"^\s*\d+\s*[-.)]\s*", "", name[:cut]).strip(" -•–:;")

        line = f"{qty}× {name}" + (f" — {sku}" if sku else "")
        line = re.sub(r"\s+", " ", line).strip()
        if line and line not in items:
            items.append(line[:110] + ("..." if len(line) > 110 else ""))
        buf = []

    # agrupa por tamanho/pistas
    for ln in lines:
        low = ln.lower()
        if any(sw in low for sw in STOP_WORDS):
            break
        buf.append(ln)
        if len(" ".join(buf)) >= 60 or any(w in low for w in [" sku", " qnt", " qtd", " qtde", " quantidade"]):
            flush()
        if len(items) >= MAX_LINES:
            break
    flush()

    return items[:MAX_LINES]

# ================== PIPELINE ==================
def process_pdf(pdf_bytes: bytes, show_diag: bool = False):
    reader = PdfReader(io.BytesIO(pdf_bytes))
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")

    pick_by_order = {}     # order -> [linhas formatadas]
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

            # tenta extrair itens direto (não depende de cabeçalho)
            prods = extract_products_from_quad(txt)
            if prods:
                # achou lista -> tenta PEDIDO próximo
                order_pk = extract_order_pick(txt)
                if order_pk:
                    acc = pick_by_order.setdefault(order_pk, [])
                    for p in prods:
                        if p not in acc:
                            acc.append(p)
                    if show_diag:
                        diag_picks.append((order_pk, prods[:3]))
                else:
                    pick_no_order.append(prods)  # salva para parear por ordem
                    if show_diag:
                        diag_picks.append(("(sem pedido)", prods[:3]))
                # nunca tratar como etiqueta se parece lista
                continue

            # possível etiqueta (descarta branco)
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
        if show_diag:
            diag_labels.append(order)

    # ===== CAMINHO 1: há orders casados =====
    if order_sequence:
        writer = PdfWriter()
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

        final_doc = fitz.open()
        for idx, order in enumerate(order_sequence):
            src_pg = cropped_doc[idx]
            r = src_pg.rect
            products = pick_by_order.get(order, [])
            # se não achou por pedido, tenta por ordem
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

    # ===== CAMINHO 2: sem orders casados → corta TODAS etiquetas e pareia por ORDEM =====
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

    # ===== CAMINHO 3: último fallback → 4→1 básico =====
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
            st.write("Ordem final (quando casou por pedido):", ", ".join(diag["orders"]) if diag["orders"] else "(n/a)")
    except Exception as e:
        st.error("Não foi possível processar o arquivo. Verifique se é um PDF válido.")
        st.exception(e)
else:
    st.info("Faça o upload de um PDF para iniciar o processamento.")
