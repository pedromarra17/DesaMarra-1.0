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

# =============== Configuração da página/tema/branding ===============
st.set_page_config(page_title="Etiquetas Shopee – 4→1 / Empacotamento", layout="wide")
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
st.markdown("<h1 style='text-align:center;margin:.4rem 0 0'>Etiquetas Shopee</h1>", unsafe_allow_html=True)

mode = st.radio("Escolha o tipo de PDF:", ["PDF com 4 etiquetas", "PDF com lista de empacotamento"], horizontal=True)
st.divider()
uploaded_files = st.file_uploader("Selecione PDF(s) da Shopee", type=["pdf"], accept_multiple_files=True)
show_diag = st.toggle("Modo diagnóstico (CSV simples)", value=False)
process_btn = st.button("Processar")

# ================= Constantes / utilidades =================
REMOVE_BLANK = True
DPI_CHECK    = 120
WHITE_THR    = 245
COVERAGE     = 0.995

LATIN = r"A-Za-zÀ-ÖØ-öø-ÿ"

PT_PER_IN = 72.0
MM_PER_IN = 25.4
def mm_to_pt(mm): return PT_PER_IN * (mm / MM_PER_IN)
TARGET_W_PT = mm_to_pt(100)   # 10 cm
TARGET_H_PT = mm_to_pt(150)   # 15 cm

# fonte padrão (Helvetica do PDF)
FONT_NAME = "helv"

def normalize_txt(t: str) -> str:
    t = unicodedata.normalize("NFKD", t)
    t = "".join(ch for ch in t if not unicodedata.combining(ch))
    return re.sub(r"\s+", " ", t).strip()

def collapse_pairs(s: str) -> str:
    toks, out, buf = s.split(), [], []
    for t in toks:
        if re.fullmatch(rf"[{LATIN}]{{1,2}}", t): buf.append(t)
        else:
            if buf: out.append("".join(buf)); buf=[]
            out.append(t)
    if buf: out.append("".join(buf))
    return " ".join(out)

def norm_heavy(t: str) -> str:
    t = normalize_txt(t)
    t = collapse_pairs(t)
    return re.sub(r"(?:(?<=\b)[A-Za-z]\s(?=[A-Za-z]))+", lambda m: m.group(0).replace(" ",""), t)

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

def content_bbox(page: fitz.Page, clip: fitz.Rect, pad: float = 2.0) -> fitz.Rect:
    blocks = page.get_text("blocks", clip=clip)
    xs0, ys0, xs1, ys1 = [], [], [], []
    for b in blocks:
        x0, y0, x1, y1 = b[0], b[1], b[2], b[3]
        xs0.append(x0); ys0.append(y0); xs1.append(x1); ys1.append(y1)
    if not xs0:
        return clip
    bb = fitz.Rect(min(xs0), min(ys0), max(xs1), max(ys1)) & clip
    bb.x0 = max(clip.x0, bb.x0 - pad)
    bb.y0 = max(clip.y0, bb.y0 - pad)
    bb.x1 = min(clip.x1, bb.x1 + pad)
    bb.y1 = min(clip.y1, bb.y1 + pad)
    return bb

def trim_bbox_by_raster(doc: fitz.Document, page_idx: int, rect: fitz.Rect,
                        dpi: int = 200, white: int = 245, cov: float = 0.995,
                        pad_pt: float = 1.5) -> fitz.Rect:
    page = doc[page_idx]
    if rect.width <= 0 or rect.height <= 0:
        return rect
    scale = dpi / 72.0
    pix = page.get_pixmap(matrix=fitz.Matrix(scale, scale), clip=rect, alpha=False)
    if pix.width == 0 or pix.height == 0:
        return rect
    img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
    g = img.convert("L")
    w, h = g.size
    px = g.load()

    def row_is_white(y):
        return sum(1 for x in range(w) if px[x, y] >= white) / w >= cov
    def col_is_white(x):
        return sum(1 for y in range(h) if px[x, y] >= white) / h >= cov

    top = 0
    while top < h and row_is_white(top): top += 1
    if top == h: return rect
    bottom = h - 1
    while bottom >= 0 and row_is_white(bottom): bottom -= 1
    left = 0
    while left < w and col_is_white(left): left += 1
    right = w - 1
    while right >= 0 and col_is_white(right): right -= 1

    px2pt = lambda v: (v / dpi) * 72.0
    new = fitz.Rect(
        rect.x0 + px2pt(left)  - pad_pt,
        rect.y0 + px2pt(top)   - pad_pt,
        rect.x0 + px2pt(right+1) + pad_pt,
        rect.y0 + px2pt(bottom+1) + pad_pt,
    )
    return new & rect

def tighten_right_edge(page: fitz.Page, doc: fitz.Document, page_idx: int,
                       rect: fitz.Rect, pad_pt: float = 2.0) -> fitz.Rect:
    words = page.get_text("words", clip=rect)
    if words:
        max_x1 = max(w[2] for w in words)
        if max_x1 < rect.x1:
            return fitz.Rect(rect.x0, rect.y0, min(rect.x1, max_x1 + pad_pt), rect.y1)
    tight = trim_bbox_by_raster(doc, page_idx, rect, dpi=200, white=245, cov=0.997, pad_pt=1.0)
    return fitz.Rect(rect.x0, rect.y0, min(rect.x1, tight.x1), rect.y1)

# ---------- helpers para reconstruir a tabela com word-wrap ----------
def find_column_edges_from_header(page: fitz.Page, header_band: fitz.Rect):
    words = page.get_text("words", clip=header_band)
    key = {}
    for x0,y0,x1,y1,w,*_ in words:
        t = norm_heavy(str(w)).upper()
        if "PRODUTO"   in t and "produto"   not in key: key["produto"]   = x0
        if t == "SKU" or t.endswith("SKU"):             key["sku"]       = x0
        if "VARIACAO" in t:                             key["variacao"]  = x0
        if "QUANTIDADE" in t or t.startswith("QTD"):    key["qtd"]       = x0
    return key

def extract_list_rows(page: fitz.Page, list_clip: fitz.Rect, col_x: dict):
    words = page.get_text("words", clip=list_clip)
    if not words:
        return []
    x_prod = col_x.get("produto", list_clip.x0)
    x_sku  = col_x.get("sku", x_prod + 120)
    x_var  = col_x.get("variacao", x_sku + 120)
    x_qtd  = col_x.get("qtd", x_var + 120)

    lines = {}
    for x0,y0,x1,y1,w,*_ in words:
        yc = (y0 + y1) / 2
        bucket = None
        for k in lines:
            if abs(k - yc) < 5:
                bucket = k; break
        if bucket is None:
            lines[yc] = []
            bucket = yc
        lines[bucket].append((x0,y0,x1,y1,str(w)))

    rows=[]
    for yc in sorted(lines.keys()):
        row = {"produto":[], "sku":[], "variacao":[], "qtd":[]}
        for x0,y0,x1,y1,w in sorted(lines[yc], key=lambda t:t[0]):
            xm = (x0+x1)/2
            if xm < x_sku:            row["produto"].append(w)
            elif xm < x_var:          row["sku"].append(w)
            elif xm < x_qtd:          row["variacao"].append(w)
            else:                     row["qtd"].append(w)
        def join(v): return norm_heavy(" ".join(v)).strip()
        r = {
            "produto":  join(row["produto"]),
            "sku":      join(row["sku"]),
            "variacao": join(row["variacao"]),
            "qtd":      join(row["qtd"]) or "1",
        }
        hdr = (r["produto"].upper().startswith("PRODUTO") or
               r["sku"].upper()=="SKU" or
               r["variacao"].upper().startswith("VARIAC") or
               r["qtd"].upper().startswith("QUANT"))
        if not hdr:
            rows.append(r)
    return rows

# === função de desenho CORRIGIDA (usa fontname=) ===
def draw_list_vector(page_out: fitz.Page, x, y, width, max_height, rows,
                     base_size=9.5, min_size=7.0,
                     col_ratio=(0.58, 0.18, 0.14, 0.10), line_gap=1.6):
    """
    Desenha a lista reflowada: Produto | SKU | Variação | Quantidade.
    Faz autofit p/ caber na altura e usa 'fontname=' (compatível com PyMuPDF antigo).
    """
    col_w = [width*r for r in col_ratio]

    def tb(rect, txt, size, align=0):
        return page_out.insert_textbox(rect, txt, fontsize=size, fontname=FONT_NAME, align=align)

    size = base_size
    while size >= min_size:
        cursor = y; ok = True
        for r in rows:
            h_prod = tb(fitz.Rect(x, cursor, x+col_w[0], cursor+1e4), r["produto"], size, align=0)
            h_sku  = tb(fitz.Rect(x+col_w[0], cursor, x+col_w[0]+col_w[1], cursor+1e4), r["sku"], size, align=0)
            h_var  = tb(fitz.Rect(x+col_w[0]+col_w[1], cursor, x+col_w[0]+col_w[1]+col_w[2], cursor+1e4), r["variacao"], size, align=0)
            h_qtd  = tb(fitz.Rect(x+col_w[0]+col_w[1]+col_w[2], cursor, x+width, cursor+1e4), r["qtd"], size, align=2)
            h = max(h_prod, h_sku, h_var, h_qtd)
            cursor += h * line_gap
            if cursor - y > max_height + 0.1:
                ok = False; break
        if ok:
            cursor = y
            for r in rows:
                h_prod = tb(fitz.Rect(x, cursor, x+col_w[0], cursor+1e4), r["produto"], size, align=0)
                tb(fitz.Rect(x+col_w[0], cursor, x+col_w[0]+col_w[1], cursor+1e4), r["sku"], size, align=0)
                tb(fitz.Rect(x+col_w[0]+col_w[1], cursor, x+col_w[0]+col_w[1]+col_w[2], cursor+1e4), r["variacao"], size, align=0)
                tb(fitz.Rect(x+col_w[0]+col_w[1]+col_w[2], cursor, x+width, cursor+1e4), r["qtd"], size, align=2)
                cursor += h_prod * line_gap
            return cursor - y
        size -= 0.5

    # fallback mínimo
    cursor = y
    for r in rows:
        h_prod = tb(fitz.Rect(x, cursor, x+col_w[0], cursor+1e4), r["produto"], min_size, align=0)
        tb(fitz.Rect(x+col_w[0], cursor, x+col_w[0]+col_w[1], cursor+1e4), r["sku"], min_size, align=0)
        tb(fitz.Rect(x+col_w[0]+col_w[1], cursor, x+col_w[0]+col_w[1]+col_w[2], cursor+1e4), r["variacao"], min_size, align=0)
        tb(fitz.Rect(x+col_w[0]+col_w[1]+col_w[2], cursor, x+width, cursor+1e4), r["qtd"], min_size, align=2)
        cursor += h_prod * line_gap
    return cursor - y

# ==================== Modo 4 etiquetas (4→1) ====================
def process_mode_4up(pdf_bytes: bytes, diagnostic=False):
    reader = PdfReader(io.BytesIO(pdf_bytes))
    doc    = fitz.open(stream=pdf_bytes, filetype="pdf")

    def quads_fitz(rect: fitz.Rect):
        W,H = rect.width, rect.height
        return [
            fitz.Rect(rect.x0,       rect.y0,       rect.x0+W/2, rect.y0+H/2),
            fitz.Rect(rect.x0+W/2,   rect.y0,       rect.x1,     rect.y0+H/2),
            fitz.Rect(rect.x0,       rect.y0+H/2,   rect.x0+W/2, rect.y1    ),
            fitz.Rect(rect.x0+W/2,   rect.y0+H/2,   rect.x1,     rect.y1    ),
        ]

    def quads_pdf(mb):
        l,b,r,t = float(mb.left), float(mb.bottom), float(mb.right), float(mb.top)
        w,h = r-l, t-b
        return [(l,b+h/2,l+w/2,t),(l+w/2,b+h/2,r,t),(l,b,l+w/2,b+h/2),(l+w/2,b,r,b+h/2)]

    writer = PdfWriter()
    diag = []

    for i in range(len(reader.pages)):
        page      = reader.pages[i]
        rects_pdf = quads_pdf(page.mediabox)
        rects_fit = quads_fitz(doc[i].rect)

        for qidx, ((x0,y0,x1,y1), clip) in enumerate(zip(rects_pdf, rects_fit), start=1):
            if REMOVE_BLANK and quad_is_blank_by_raster(doc, i, clip):
                continue
            p = deepcopy(page)
            rect = RectangleObject([x0, y0, x1, y1])
            p.cropbox = rect
            p.mediabox = rect
            writer.add_page(p)
            if diagnostic:
                diag.append({"page": i+1, "quad": qidx})

    out = io.BytesIO()
    writer.write(out)
    out.seek(0)
    return out.getvalue(), pd.DataFrame(diag)

# ========== Modo Lista de Empacotamento (1 pág 10×15, reflow) ==========
def process_mode_packing(pdf_bytes: bytes, diagnostic=False):
    H_PAD = 0.0
    V_PAD = 0.0

    src = fitz.open(stream=pdf_bytes, filetype="pdf")
    out_doc = fitz.open()
    diag_rows = []

    for pi in range(len(src)):
        pg = src[pi]; R = pg.rect
        cols = [fitz.Rect(R.x0, R.y0, (R.x0+R.x1)/2, R.y1),
                fitz.Rect((R.x0+R.x1)/2, R.y0, R.x1, R.y1)]

        for ci, col in enumerate(cols, start=1):
            blocks = pg.get_text("blocks", clip=col)

            checklist_top = None
            for b in blocks:
                if "CHECKLIST" in norm_heavy(str(b[4])).upper():
                    checklist_top = b[1]; break
            if checklist_top is None:
                for b in blocks:
                    if "ID PEDIDO" in norm_heavy(str(b[4])).upper():
                        checklist_top = b[1]; break
            if checklist_top is None:
                checklist_top = col.y0 + col.height*0.62

            table_head_y = None
            for b in blocks:
                if b[1] >= checklist_top - 2:
                    up = norm_heavy(str(b[4])).upper()
                    if ("SKU" in up) and ("QUANTIDADE" in up or up.endswith("QUANTIDADE")):
                        table_head_y = b[1]; break
            if table_head_y is None: table_head_y = checklist_top + 28

            label_raw = fitz.Rect(col.x0, col.y0, col.x1, max(col.y0+20, checklist_top-4))
            list_raw  = fitz.Rect(col.x0, table_head_y-1, col.x1, col.y1-6)

            # etiqueta: bbox + trim
            label_clip_blk = content_bbox(pg, label_raw)
            label_clip     = trim_bbox_by_raster(src, pi, label_clip_blk, dpi=220, white=245, cov=0.997, pad_pt=1.0)

            # lista: detectar colunas, bbox e apertar direita
            header_band = fitz.Rect(col.x0, table_head_y - 10, col.x1, table_head_y + 24)
            cols_x = find_column_edges_from_header(pg, header_band)
            if "produto" not in cols_x:
                cols_x["produto"] = list_raw.x0 + 26
            list_clip = content_bbox(pg, list_raw)
            list_clip = tighten_right_edge(pg, src, pi, list_clip, pad_pt=2.0)

            if REMOVE_BLANK and quad_is_blank_by_raster(src, pi, label_clip):
                continue

            # dimensões/escala para etiqueta preencher largura
            lw, lh = label_clip.width, label_clip.height
            content_w = TARGET_W_PT - 2*H_PAD
            content_h = TARGET_H_PT - 2*V_PAD
            sL = content_w / lw
            HL = lh * sL

            # extrai e reflow das linhas
            rows = extract_list_rows(pg, list_clip, cols_x)

            # cria página
            pg_new = out_doc.new_page(width=TARGET_W_PT, height=TARGET_H_PT)
            x = (TARGET_W_PT - content_w) / 2
            y = V_PAD

            # etiqueta
            pg_new.show_pdf_page(fitz.Rect(x, y, x+content_w, y+HL), src, pi, clip=label_clip)
            y += HL

            # lista reflowada
            used_h = draw_list_vector(pg_new, x, y, content_w, content_h - HL, rows)

            total_h = HL + used_h
            if total_h > content_h + 0.1:
                c = content_h / total_h
                out_doc.delete_page(-1)
                pg_new = out_doc.new_page(width=TARGET_W_PT, height=TARGET_H_PT)
                x = (TARGET_W_PT - content_w*c) / 2
                y = V_PAD
                pg_new.show_pdf_page(fitz.Rect(x, y, x+content_w*c, y+HL*c), src, pi, clip=label_clip)
                y += HL*c
                draw_list_vector(pg_new, x, y, content_w*c, content_h - HL*c, rows)

            if diagnostic:
                diag_rows.append({"page": pi+1, "col": ci, "rows": len(rows)})

    buf = io.BytesIO()
    out_doc.save(buf, garbage=4, deflate=True)
    out_doc.close()
    buf.seek(0)
    return buf.getvalue(), pd.DataFrame(diag_rows)

# =============================== RUN ===============================
if process_btn:
    if not uploaded_files:
        st.warning("Selecione pelo menos um PDF.")
    else:
        with st.spinner("Processando..."):
            results=[]
            for f in uploaded_files:
                try:
                    pdf_in = f.getvalue()
                    if mode == "PDF com 4 etiquetas":
                        data, diag = process_mode_4up(pdf_in, diagnostic=show_diag)
                    else:
                        data, diag = process_mode_packing(pdf_in, diagnostic=show_diag)
                    results.append((f.name, data, diag))
                except Exception as e:
                    st.error(f"Erro processando {f.name}: {e}")

        if results:
            if len(results)==1:
                name,data,diag = results[0]
                base = Path(name).stem + ("_4x1.pdf" if mode=="PDF com 4 etiquetas" else "_empacotamento_10x15.pdf")
                st.success("Pronto!")
                st.download_button("Baixar PDF", data=data, file_name=base, mime="application/pdf")
                if show_diag and not diag.empty:
                    st.dataframe(diag, use_container_width=True)
            else:
                import zipfile
                buf=io.BytesIO()
                with zipfile.ZipFile(buf,"w",compression=zipfile.ZIP_DEFLATED) as z:
                    for name,data,_ in results:
                        base = Path(name).stem + ("_4x1.pdf" if mode=="PDF com 4 etiquetas" else "_empacotamento_10x15.pdf")
                        z.writestr(base, data)
                buf.seek(0)
                st.success(f"Pronto! {len(results)} arquivos processados.")
                st.download_button("Baixar todos (ZIP)", data=buf.getvalue(), file_name="processados.zip", mime="application/zip")
else:
    st.info("Faça upload do(s) PDF(s), escolha o modo e clique em **Processar**.")
