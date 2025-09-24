# separar_etiquetas.py
# pip install streamlit pypdf PyMuPDF pillow pandas

import streamlit as st
from pypdf import PdfReader, PdfWriter
from pypdf.generic import RectangleObject
from copy import deepcopy
from pathlib import Path
import base64, io, re, unicodedata
import fitz  # PyMuPDF
from PIL import Image
import pandas as pd

# ============================= UI =============================
st.set_page_config(page_title="Etiquetas 4→1 + Lista (QNT×SKU)", layout="wide")
st.markdown("""
<style>
#MainMenu, footer {visibility:hidden;}
header,[data-testid="stToolbar"],[data-testid="stDecoration"],.stDeployButton{display:none!important;}
div[class^="viewerBadge"],div[class*="viewerBadge"]{display:none!important;}
</style>
""", unsafe_allow_html=True)

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
st.markdown("<p style='text-align:center'>1 página por etiqueta. Rodapé: <b>QNT × SKU</b>. Casa por <b>Pedido</b>; se não achar, por <b>ordem</b>.</p>", unsafe_allow_html=True)
st.divider()

cols = st.columns([1,1,1,2])
with cols[0]:
    uploaded = st.file_uploader("Selecione o PDF da Shopee (etiquetas + listas)", type=["pdf"])
with cols[1]:
    show_diag = st.toggle("Modo diagnóstico", value=False, help="Gera CSV e PDF de preview com caixas.")

# ========================= Constantes =========================
REMOVE_BLANK = True
DPI_CHECK    = 120
WHITE_THR    = 245
COVERAGE     = 0.995

OVERLAY_HEIGHT_PCT = 0.14  # % da altura da etiqueta para faixa de rodapé
FONT_SIZE = 7
MAX_LINES = 12
MARGIN_X_PT = 18
PAD_Y_PT = 6

LATIN = r"A-Za-zÀ-ÖØ-öø-ÿ"

# ====================== Normalização texto ====================
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
    t = re.sub(r"(?:(?<=\b)[A-Za-z]\s(?=[A-Za-z]))+", lambda m: m.group(0).replace(" ",""), t)
    return t

# ================== Quadrantes & Página em branco =============
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

# ====================== Pedido (ID) etiqueta ==================
ORDER_NEAR = re.compile(r"(?:ID\s*PEDIDO|PEDIDO|Nº\s*PEDIDO)[:\s#-]*((?:[A-Z0-9]\s*){8,24})", re.I)
ORDER_TOKEN= re.compile(r"\b([A-Z0-9]{10,24})\b")

def extract_order(text: str) -> str:
    up = norm_heavy(text).upper()
    m = ORDER_NEAR.search(up)
    if m:
        tok = re.sub(r"\s+","", m.group(1))
        if not tok.startswith("BR") and re.search(r"[A-Z]", tok) and re.search(r"\d", tok):
            return tok
    for tok in ORDER_TOKEN.findall(up):
        if tok.startswith("BR"): continue
        if re.search(r"[A-Z]", tok) and re.search(r"\d", tok):
            return tok
    return ""

# =================== Parser da LISTA por colunas ==============
SKU_TOKEN_RE = re.compile(r"\b(?=[A-Z0-9\-\/\.]{3,32}\b)(?=.*[A-Z])(?=.*\d)[A-Z0-9\-\/\.]+\b", re.I)

def get_words(page: fitz.Page, rect: fitz.Rect):
    ws = page.get_text("words", clip=rect)
    words = []
    for x0,y0,x1,y1,w,*_ in ws:
        t = str(w).strip()
        if not t: continue
        words.append({
            "x0": x0, "y0": y0, "x1": x1, "y1": y1,
            "xc": (x0+x1)/2, "yc": (y0+y1)/2,
            "h":  (y1-y0),
            "t":  norm_heavy(t).upper()
        })
    words.sort(key=lambda k:(round(k["yc"],1), k["x0"]))
    return words

def group_by_lines(words, y_tol=3.5):
    """Agrupa 'words' em linhas (corrigido)."""
    lines = []
    for w in words:
        if not lines:
            lines.append([w]); continue
        last_line = lines[-1]
        last_y = sum(x["yc"] for x in last_line)/len(last_line)
        if abs(w["yc"] - last_y) <= y_tol:
            last_line.append(w)
        else:
            lines.append([w])
    return lines

def merge_letters(line, gap_factor=0.6):
    if not line: return []
    avg_h = sum(w["h"] for w in line)/len(line)
    gap = avg_h * gap_factor
    out = []; cur = None
    for w in sorted(line, key=lambda k:k["x0"]):
        if cur is None:
            cur = dict(w)
        else:
            if w["x0"] - cur["x1"] <= gap:
                cur["x1"] = max(cur["x1"], w["x1"])
                cur["xc"] = (cur["x0"]+cur["x1"])/2
                cur["t"]  = (cur["t"] + w["t"]).upper()
            else:
                out.append(cur); cur = dict(w)
    if cur: out.append(cur)
    return out

def find_header_cols(lines):
    for ln in lines:
        groups = merge_letters(ln)
        txts = [g["t"] for g in groups]
        got_qnt = any(t in ("QNT","QTD","QTDE") for t in txts)
        got_sku = "SKU" in txts
        got_prod = any("PRODUTO" in t for t in txts)
        got_var  = any(("VARIACAO" in t) or ("VARIAÇÃO" in t) for t in txts)
        if (got_prod and got_sku) and (got_qnt or got_var):
            cols = {}
            for g in groups:
                if "PRODUTO" in g["t"]: cols["PRODUTO"] = g["xc"]
                if "VARIACAO" in g["t"] or "VARIAÇÃO" in g["t"]: cols["VARIACAO"] = g["xc"]
                if g["t"] in ("QNT","QTD","QTDE"): cols["QNT"] = g["xc"]
                if g["t"] == "SKU": cols["SKU"] = g["xc"]
            cols["y"] = groups[0]["yc"]
            if "QNT" in cols and "SKU" in cols:
                return cols
    return {}

def nearest_group(groups, x, max_dx=65):
    if not groups: return None
    best, bd = None, 1e9
    for g in groups:
        d = abs(g["xc"] - x)
        if d < bd:
            bd, best = d, g
    return best if bd <= max_dx else None

def extract_list_by_columns(page: fitz.Page, rect: fitz.Rect) -> list[str]:
    words = get_words(page, rect)
    if not words: return []
    lines = group_by_lines(words)
    cols = find_header_cols(lines)
    if not cols:
        return []

    items = []
    header_y = cols["y"]
    for ln in lines:
        if not ln or ln[0]["yc"] <= header_y + 2:
            continue
        groups = merge_letters(ln)
        line_txt = " ".join(g["t"] for g in groups)

        if "CHECKLIST" in line_txt or "CORTE" in line_txt:
            break

        gq = nearest_group(groups, cols["QNT"])
        gs = nearest_group(groups, cols["SKU"])
        if not gq and not gs:
            continue

        qty = None
        if gq:
            m = re.search(r"\b(\d{1,3})\b", gq["t"])
            if m: qty = int(m.group(1))
        if qty is None:
            m = re.search(r"\b(\d{1,3})\s*x\b|\bx\s*(\d{1,3})\b", line_txt, re.I)
            if m: qty = int(m.group(1) or m.group(2))
        if qty is None: qty = 1

        sku = None
        if gs:
            mt = SKU_TOKEN_RE.search(gs["t"])
            if mt: sku = mt.group(0)
        if not sku:
            mt = SKU_TOKEN_RE.search(line_txt)
            if mt: sku = mt.group(0)

        if sku:
            items.append(f"- {qty}x {sku}")
        if len(items) >= MAX_LINES: break
    return items

# ======== Fallback textual para lista (sem colunas válidas) ========
QTY_PATTS = [
    re.compile(r"(?:QNT|QTD|QTDE|QUANTIDADE)\s*[:x\-]*\s*(\d{1,3})", re.I),
    re.compile(r"\b(\d{1,3})\s*(?:UN|UND|PCS|PC|PÇS|PÇ)\b", re.I),
    re.compile(r"\b(\d{1,3})\s*x\b", re.I),
    re.compile(r"\bx\s*(\d{1,3})\b", re.I),
]

def extract_list_by_lines(text: str) -> list[str]:
    raw = norm_heavy(text)
    lines = [ln.strip() for ln in raw.splitlines() if ln.strip()]
    # mantém apenas regiões que parecem "lista"
    if not ("SKU" in raw and ("QNT" in raw or "QTD" in raw or "QTDE" in raw)):
        return []
    items = []
    for ln in lines:
        base = norm_heavy(ln)
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

        msku = re.search(r"\bS\s*K\s*U[:\s\-]*([A-Z0-9\s\-\/\.]{3,})", base, re.I)
        sku = None
        if msku:
            cand = re.sub(r"\s+","", msku.group(1))
            mt = SKU_TOKEN_RE.search(cand)
            if mt: sku = mt.group(0)
        if not sku:
            mt = SKU_TOKEN_RE.search(base)
            if mt: sku = mt.group(0)
        if sku:
            item = f"- {qty}x {sku}"
            if item not in items: items.append(item)
        if len(items) >= MAX_LINES: break
    return items

# ======================== Classificador ========================
LABEL_HINTS = ["DANFE SIMPLIFICADO - ETIQUETA","DESTINAT","REMETENTE","AGÊNCIA","AGENCIA"]
LIST_HINTS  = ["SKU","QNT","QTD","QTDE","PRODUTO","VARIA","LISTA DE SEPARACAO","LISTA DE SEPARAÇÃO","DECLARACAO","DECLARAÇÃO"]

def classify_block(text: str) -> str:
    s = norm_heavy(text).upper()
    if any(h in s for h in LABEL_HINTS): return "label"
    if any(h in s for h in LIST_HINTS):  return "list"
    return "unknown"

# ======================== Pipeline ============================
def process_pdf(pdf_bytes: bytes, diagnostic=False):
    reader = PdfReader(io.BytesIO(pdf_bytes))
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")

    label_quads = []
    list_quads  = []
    unknowns    = []

    diag_rows = []

    for i in range(len(doc)):
        p_fitz = doc[i]
        qf = quadrants_fitz(p_fitz.rect)
        qp = quadrants_pypdf(reader.pages[i].mediabox)

        for qidx, (rect_fitz, box_pdf) in enumerate(zip(qf, qp)):
            txt = p_fitz.get_text("text", clip=rect_fitz) or ""

            # tenta classificar
            c = classify_block(txt)
            if c == "list":
                prods = extract_list_by_columns(p_fitz, rect_fitz)
                if not prods:
                    prods = extract_list_by_lines(txt)
                if prods:
                    list_quads.append(dict(page_idx=i, pypdf_box=box_pdf, fitz_rect=rect_fitz, text=txt, items=prods))
                else:
                    unknowns.append(dict(page_idx=i, pypdf_box=box_pdf, fitz_rect=rect_fitz, text=txt))
                    c = "unknown"
            elif c == "label":
                if REMOVE_BLANK and quad_is_blank_by_raster(doc, i, rect_fitz):
                    continue
                label_quads.append(dict(page_idx=i, pypdf_box=box_pdf, fitz_rect=rect_fitz, text=txt))
            else:
                # tenta ver se é lista pelos itens
                prods = extract_list_by_columns(p_fitz, rect_fitz)
                if not prods:
                    prods = extract_list_by_lines(txt)
                if prods:
                    c = "list"
                    list_quads.append(dict(page_idx=i, pypdf_box=box_pdf, fitz_rect=rect_fitz, text=txt, items=prods))
                else:
                    # pode ser etiqueta
                    if REMOVE_BLANK and quad_is_blank_by_raster(doc, i, rect_fitz):
                        continue
                    c = "label" if "DANFE" in norm_heavy(txt).upper() else "unknown"
                    if c == "label":
                        label_quads.append(dict(page_idx=i, pypdf_box=box_pdf, fitz_rect=rect_fitz, text=txt))
                    else:
                        unknowns.append(dict(page_idx=i, pypdf_box=box_pdf, fitz_rect=rect_fitz, text=txt))

            if diagnostic:
                pedido = extract_order(txt) or ""
                sample = norm_heavy(txt)[:120]
                diag_rows.append({
                    "page": i+1, "quad": qidx+1, "tipo": c, "pedido": pedido, "amostra": sample
                })

    # Fallback estrutural: se unknowns existem e contagens batem com 4/8, tenta
    # considerar unknowns depois das etiquetas como listas (ordem).
    if label_quads and not list_quads and unknowns:
        # heurística: tudo que aparece depois da última etiqueta vira lista
        last_label_idx = max((x["page_idx"] for x in label_quads), default=-1)
        for blk in unknowns:
            if blk["page_idx"] >= last_label_idx:
                # tenta extrair itens de novo
                prods = extract_list_by_columns(doc[blk["page_idx"]], blk["fitz_rect"])
                if not prods:
                    prods = extract_list_by_lines(blk["text"])
                if prods:
                    blk["items"] = prods
                    list_quads.append(blk)

    # Se ainda não houver listas, seguimos sem (rodapé “não encontrado”)
    # =================== Recorte de etiquetas ===================
    if not label_quads:
        # fallback 4→1 simples
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
        return out.getvalue(), pd.DataFrame(diag_rows)

    # Recorta todas as etiquetas
    w = PdfWriter()
    for lab in label_quads:
        psrc = reader.pages[lab["page_idx"]]
        x0,y0,x1,y1 = lab["pypdf_box"]
        p = deepcopy(psrc)
        rect = RectangleObject([x0,y0,x1,y1])
        p.cropbox = rect; p.mediabox = rect
        w.add_page(p)
    tmp = io.BytesIO(); w.write(tmp); tmp.seek(0)
    cropped = fitz.open(stream=tmp.getvalue(), filetype="pdf")

    # =================== Pareamento etiqueta ↔ lista ===================
    # 1) por Pedido
    lists_by_order = {}
    for lst in list_quads:
        oid = extract_order(lst["text"])
        if oid:
            lists_by_order.setdefault(oid, [])
            lists_by_order[oid] = lst["items"]  # última vence

    # 2) por ordem
    lists_in_order = [x["items"] for x in list_quads if not extract_order(x["text"])]

    final_doc = fitz.open()
    used_orders = set()
    idx_free = 0

    for idx, lab in enumerate(label_quads):
        src_pg = cropped[idx]
        r = src_pg.rect

        order = extract_order(lab["text"])
        if order and order in lists_by_order and order not in used_orders:
            items = lists_by_order[order][:MAX_LINES]
            used_orders.add(order)
        else:
            items = lists_in_order[idx_free][:MAX_LINES] if idx_free < len(lists_in_order) else []
            if idx_free < len(lists_in_order): idx_free += 1

        # altura extra do rodapé
        lines_count = 1 + max(1, len(items))
        min_area = PAD_Y_PT*2 + (FONT_SIZE+2)*lines_count
        extra_h = max(r.height*OVERLAY_HEIGHT_PCT, min_area)

        new_pg = final_doc.new_page(width=r.width, height=r.height+extra_h)
        new_pg.show_pdf_page(fitz.Rect(0,0,r.width,r.height), cropped, idx)

        box = fitz.Rect(MARGIN_X_PT, r.height+PAD_Y_PT, r.width-MARGIN_X_PT, r.height+extra_h-PAD_Y_PT)
        text = "Lista de separação:\n" + ("\n".join(items) if items else "- (não encontrado)")
        new_pg.insert_textbox(box, text, fontname="helv", fontsize=FONT_SIZE, align=0)

    out = io.BytesIO(); final_doc.save(out); final_doc.close(); out.seek(0)

    # =================== Saídas de diagnóstico ===================
    diag_df = pd.DataFrame(diag_rows)
    if diagnostic:
        # CSV
        csv_buf = io.StringIO()
        diag_df.to_csv(csv_buf, index=False)
        st.download_button("Baixar DIAGNÓSTICO (CSV)",
                           data=csv_buf.getvalue().encode("utf-8"),
                           file_name="diagnostico_quadrantes.csv",
                           mime="text/csv")
        # PDF com caixas
        prev = fitz.open(stream=pdf_bytes, filetype="pdf")
        mark = fitz.open()
        red   = (1,0,0); green = (0,0.6,0)
        for i in range(len(prev)):
            pg = mark.new_page(width=prev[i].rect.width, height=prev[i].rect.height)
            pg.show_pdf_page(prev[i].rect, prev, i)
            # desenha quadros
            for blk in label_quads:
                if blk["page_idx"]==i: pg.draw_rect(blk["fitz_rect"], color=green, width=1)
            for blk in list_quads:
                if blk["page_idx"]==i: pg.draw_rect(blk["fitz_rect"], color=red, width=1)
        pb = io.BytesIO(); mark.save(pb); mark.close(); pb.seek(0)
        st.download_button("Baixar PREVIEW (PDF com caixas)", data=pb.getvalue(),
                           file_name="preview_caixas.pdf", mime="application/pdf")

    return out.getvalue(), diag_df

# =========================== RUN =============================
if uploaded:
    try:
        pdf_in = uploaded.getvalue()
        with st.spinner("Processando etiquetas e listas…"):
            pdf_out, diag_df = process_pdf(pdf_in, diagnostic=show_diag)

        st.success("Pronto! 1 etiqueta por página, com a lista (QNT × SKU) no rodapé.")
        st.download_button("Baixar PDF final", data=pdf_out, file_name="etiquetas_com_lista.pdf", mime="application/pdf")

        if show_diag:
            st.dataframe(diag_df, use_container_width=True)
    except Exception as e:
        st.error("Falha ao processar o PDF.")
        st.exception(e)
else:
    st.info("Faça o upload de um PDF para iniciar.")
