# separar_etiquetas.py
# pip install streamlit pypdf PyMuPDF pillow requests pandas

import streamlit as st
from pypdf import PdfReader, PdfWriter
from pypdf.generic import RectangleObject
from copy import deepcopy
from pathlib import Path
import base64, io, re, unicodedata, zipfile
import fitz  # PyMuPDF
from PIL import Image
import requests
from urllib.parse import urlparse, unquote

# ============================= UI =============================
st.set_page_config(page_title="Etiquetas 4→1 + Lista (QNT×SKU)", layout="wide")
st.markdown("""
<style>
#MainMenu, footer {visibility:hidden;}
header,[data-testid="stToolbar"],[data-testid="stDecoration"],.stDeployButton{display:none!important;}
div[class^="viewerBadge"],div[class*="viewerBadge"]{display:none!important;}
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
st.markdown("<p style='text-align:center'>1 página por etiqueta. Rodapé: <b>QNT × SKU</b>. Casa por <b>Pedido</b>; se não achar, por <b>ordem</b>.</p>", unsafe_allow_html=True)
st.divider()

src = st.radio("Como deseja enviar?", ["Upload de PDF(s)", "Link(s) de PDF/ZIP"], horizontal=True)
uploaded_files = None
urls_text = ""
cookie_header = ""
custom_headers = ""

if src == "Upload de PDF(s)":
    uploaded_files = st.file_uploader("Selecione PDF(s) – Shopee (etiquetas + listas)", type=["pdf"], accept_multiple_files=True)
else:
    urls_text = st.text_area("Cole 1 link por linha (PDF direto ou ZIP com PDFs dentro):", height=120,
                             placeholder="https://.../arquivo.pdf\nhttps://.../lote.zip")
    with st.expander("Se o link exigir login/sessão (Shopee), informe cabeçalhos opcionalmente"):
        cookie_header = st.text_input("Cookie:", placeholder="spc_ecid=...; SPC_SI=...; ...")
        custom_headers = st.text_area("Headers extras (opcional, JSON simples: chave:valor por linha)",
                                      placeholder="User-Agent: Mozilla/5.0\nReferer: https://shopee.com.br/")

process_btn = st.button("Baixar e Processar" if src == "Link(s) de PDF/ZIP" else "Processar")

# ========================= Constantes =========================
REMOVE_BLANK = True
DPI_CHECK    = 120
WHITE_THR    = 245
COVERAGE     = 0.995

OVERLAY_HEIGHT_PCT = 0.14
FONT_SIZE = 7
MAX_LINES = 12
MARGIN_X_PT = 18
PAD_Y_PT = 6

LATIN = r"A-Za-zÀ-ÖØ-öø-ÿ"

# ====================== Normalização ==========================
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

# ================== Quadrantes / Branco =======================
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

# ====================== Pedido (ID) ===========================
ORDER_NEAR  = re.compile(r"(?:ID\s*PEDIDO|PEDIDO|Nº\s*PEDIDO)[:\s#-]*((?:[A-Z0-9]\s*){8,24})", re.I)
ORDER_TOKEN = re.compile(r"\b([A-Z0-9]{10,24})\b")

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

# =================== Lista por COLUNAS ========================
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
        if d < bd: bd, best = d, g
    return best if bd <= max_dx else None

def extract_list_by_columns(page: fitz.Page, rect: fitz.Rect) -> list[str]:
    words = get_words(page, rect)
    if not words: return []
    lines = group_by_lines(words)
    cols = find_header_cols(lines)
    if not cols: return []
    items = []
    header_y = cols["y"]
    for ln in lines:
        if not ln or ln[0]["yc"] <= header_y + 2:  # pular cabeçalho
            continue
        groups = merge_letters(ln)
        line_txt = " ".join(g["t"] for g in groups)
        if "CHECKLIST" in line_txt or "CORTE" in line_txt: break
        gq = nearest_group(groups, cols["QNT"])
        gs = nearest_group(groups, cols["SKU"])
        if not gq and not gs: continue
        # QNT
        qty = None
        if gq:
            m = re.search(r"\b(\d{1,3})\b", gq["t"])
            if m: qty = int(m.group(1))
        if qty is None:
            m = re.search(r"\b(\d{1,3})\s*x\b|\bx\s*(\d{1,3})\b", line_txt, re.I)
            if m: qty = int(m.group(1) or m.group(2))
        if qty is None: qty = 1
        # SKU
        sku = None
        if gs:
            mt = SKU_TOKEN_RE.search(gs["t"])
            if mt: sku = mt.group(0)
        if not sku:
            mt = SKU_TOKEN_RE.search(line_txt)
            if mt: sku = mt.group(0)
        if sku: items.append(f"- {qty}x {sku}")
        if len(items) >= MAX_LINES: break
    return items

# ======== Fallback textual para lista (casos raros) ===========
QTY_PATTS = [
    re.compile(r"(?:QNT|QTD|QTDE|QUANTIDADE)\s*[:x\-]*\s*(\d{1,3})", re.I),
    re.compile(r"\b(\d{1,3})\s*(?:UN|UND|PCS|PC|PÇS|PÇ)\b", re.I),
    re.compile(r"\b(\d{1,3})\s*x\b", re.I),
    re.compile(r"\bx\s*(\d{1,3})\b", re.I),
]
def extract_list_by_lines(text: str) -> list[str]:
    raw = norm_heavy(text)
    if not ("SKU" in raw and ("QNT" in raw or "QTD" in raw or "QTDE" in raw)):
        return []
    items = []
    for ln in [l.strip() for l in raw.splitlines() if l.strip()]:
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

# ======================== Pipeline ============================
def process_pdf(pdf_bytes: bytes):
    reader = PdfReader(io.BytesIO(pdf_bytes))
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")

    label_quads, list_quads = [], []

    for i in range(len(doc)):
        p_fitz = doc[i]
        qf = quadrants_fitz(p_fitz.rect)
        qp = quadrants_pypdf(reader.pages[i].mediabox)
        for rect_fitz, box_pdf in zip(qf, qp):
            txt = p_fitz.get_text("text", clip=rect_fitz) or ""
            # tenta lista (colunas)
            prods = extract_list_by_columns(p_fitz, rect_fitz)
            if not prods:  # fallback textual
                prods = extract_list_by_lines(txt)
            if prods:
                list_quads.append(dict(page_idx=i, pypdf_box=box_pdf, fitz_rect=rect_fitz, text=txt, items=prods))
                continue
            # etiqueta
            if REMOVE_BLANK and quad_is_blank_by_raster(doc, i, rect_fitz):
                continue
            label_quads.append(dict(page_idx=i, pypdf_box=box_pdf, fitz_rect=rect_fitz, text=txt))

    # recorta etiquetas
    if not label_quads:
        w = PdfWriter()
        for i in range(len(reader.pages)):
            page = reader.pages[i]
            for (x0,y0,x1,y1) in quadrants_pypdf(page.mediabox):
                if REMOVE_BLANK and quad_is_blank_by_raster(doc, i, fitz.Rect(x0,y0,x1,y1)): continue
                p = deepcopy(page); rect = RectangleObject([x0,y0,x1,y1])
                p.cropbox = rect; p.mediabox = rect; w.add_page(p)
        out = io.BytesIO(); w.write(out); out.seek(0)
        return out.getvalue()

    w = PdfWriter()
    for lab in label_quads:
        psrc = reader.pages[lab["page_idx"]]
        x0,y0,x1,y1 = lab["pypdf_box"]
        p = deepcopy(psrc); rect = RectangleObject([x0,y0,x1,y1])
        p.cropbox = rect; p.mediabox = rect; w.add_page(p)
    tmp = io.BytesIO(); w.write(tmp); tmp.seek(0)
    cropped = fitz.open(stream=tmp.getvalue(), filetype="pdf")

    # listas indexadas
    lists_by_order = {}
    lists_in_order = []
    for lst in list_quads:
        oid = extract_order(lst["text"])
        if oid:
            lists_by_order[oid] = lst["items"]
        else:
            lists_in_order.append(lst["items"])

    final_doc = fitz.open()
    used_orders = set()
    idx_free = 0

    for idx, lab in enumerate(label_quads):
        src_pg = cropped[idx]; r = src_pg.rect
        order = extract_order(lab["text"])
        if order and order in lists_by_order and order not in used_orders:
            items = lists_by_order[order][:MAX_LINES]; used_orders.add(order)
        else:
            items = lists_in_order[idx_free][:MAX_LINES] if idx_free < len(lists_in_order) else []
            if idx_free < len(lists_in_order): idx_free += 1

        lines_count = 1 + max(1, len(items))
        min_area = PAD_Y_PT*2 + (FONT_SIZE+2)*lines_count
        extra_h = max(r.height*OVERLAY_HEIGHT_PCT, min_area)

        new_pg = final_doc.new_page(width=r.width, height=r.height+extra_h)
        new_pg.show_pdf_page(fitz.Rect(0,0,r.width,r.height), cropped, idx)
        box = fitz.Rect(MARGIN_X_PT, r.height+PAD_Y_PT, r.width-MARGIN_X_PT, r.height+extra_h-PAD_Y_PT)
        text = "Lista de separação:\n" + ("\n".join(items) if items else "- (não encontrado)")
        new_pg.insert_textbox(box, text, fontname="helv", fontsize=FONT_SIZE, align=0)

    out = io.BytesIO(); final_doc.save(out); final_doc.close(); out.seek(0)
    return out.getvalue()

# ====================== Downloader por URL ====================
def parse_headers_text(cookie_header: str, extra_headers: str):
    headers = {}
    if cookie_header.strip():
        headers["Cookie"] = cookie_header.strip()
    if extra_headers.strip():
        for line in extra_headers.splitlines():
            if ":" in line:
                k,v = line.split(":",1)
                headers[k.strip()] = v.strip()
    # user-agent padrão pra evitar bloqueio bobo
    headers.setdefault("User-Agent","Mozilla/5.0")
    return headers

def filename_from_url(url: str) -> str:
    path = unquote(urlparse(url).path)
    name = Path(path).name or "arquivo.pdf"
    return name

def fetch_urls_to_pdfs(urls: list[str], cookie_header: str, extra_headers: str):
    """
    Baixa URLs. Se ZIP, extrai PDFs. Retorna lista de (nome_arquivo, bytes_pdf).
    """
    headers = parse_headers_text(cookie_header, extra_headers)
    out = []
    for url in urls:
        url = url.strip()
        if not url: continue
        try:
            with requests.get(url, headers=headers, timeout=30, allow_redirects=True) as r:
                r.raise_for_status()
                content = r.content
                fname = filename_from_url(url)
                ctype = r.headers.get("Content-Type","").lower()

                if fname.lower().endswith(".zip") or "zip" in ctype:
                    zf = zipfile.ZipFile(io.BytesIO(content))
                    for zname in zf.namelist():
                        if zname.lower().endswith(".pdf"):
                            out.append((Path(zname).name, zf.read(zname)))
                elif fname.lower().endswith(".pdf") or "pdf" in ctype:
                    out.append((fname if fname.lower().endswith(".pdf") else fname + ".pdf", content))
                else:
                    st.warning(f"URL não parece PDF/ZIP: {url}")
        except Exception as e:
            st.error(f"Falha ao baixar {url}: {e}")
    return out

# =========================== RUN =============================
def run_from_upload(files):
    results = []
    for f in files:
        try:
            pdf_in = f.getvalue()
            pdf_out = process_pdf(pdf_in)
            results.append((f.name, pdf_out))
        except Exception as e:
            st.error(f"Erro processando {f.name}: {e}")
    return results

def run_from_urls(text_urls, cookie_header, extra_headers):
    urls = [u.strip() for u in text_urls.splitlines() if u.strip()]
    pairs = fetch_urls_to_pdfs(urls, cookie_header, extra_headers)
    results = []
    for name, pdf_bytes in pairs:
        try:
            pdf_out = process_pdf(pdf_bytes)
            results.append((name, pdf_out))
        except Exception as e:
            st.error(f"Erro processando {name}: {e}")
    return results

if process_btn:
    if src == "Upload de PDF(s)":
        if not uploaded_files:
            st.warning("Selecione pelo menos um PDF.")
        else:
            with st.spinner("Processando..."):
                results = run_from_upload(uploaded_files)
    else:
        if not urls_text.strip():
            st.warning("Cole pelo menos um link.")
        else:
            with st.spinner("Baixando e processando..."):
                results = run_from_urls(urls_text, cookie_header, custom_headers)

    # Saída (um ou vários)
    if 'results' in locals() and results:
        if len(results) == 1:
            name, data = results[0]
            base = Path(name).stem + "_processado.pdf"
            st.success("Pronto! 1 etiqueta por página, com a lista (QNT × SKU) no rodapé.")
            st.download_button("Baixar PDF", data=data, file_name=base, mime="application/pdf")
        else:
            # zipa
            buf = io.BytesIO()
            with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as z:
                for name, data in results:
                    base = Path(name).stem + "_processado.pdf"
                    z.writestr(base, data)
            buf.seek(0)
            st.success(f"Pronto! {len(results)} arquivos processados.")
            st.download_button("Baixar todos (ZIP)", data=buf.getvalue(), file_name="etiquetas_processadas.zip", mime="application/zip")
else:
    st.info("Envie PDF(s) por upload **ou** cole os link(s) e clique em **Baixar e Processar**.")
