# separar_etiquetas.py
# pip install streamlit pypdf PyMuPDF pillow pandas

import streamlit as st
from pathlib import Path
from PIL import Image
import base64, io, re, unicodedata
import pandas as pd
import fitz  # PyMuPDF

# ---------------- UI / Branding ----------------
st.set_page_config(page_title="Etiquetas Shopee – Marra", layout="wide")
st.markdown("""
<style>
#MainMenu, footer {visibility:hidden;}
header,[data-testid="stToolbar"],[data-testid="stDecoration"],.stDeployButton{display:none!important;}
div[class^="viewerBadge"],div[class*="viewerBadge"]{display:none!important;}
</style>""", unsafe_allow_html=True)

BASE_DIR = Path(__file__).parent
LOGO_LIGHT = BASE_DIR / "logo_light.png"
LOGO_DARK  = BASE_DIR / "logo_dark.png"

def show_logo_center(px=420):
    theme_base = st.get_option("theme.base") or "light"
    p = LOGO_LIGHT if theme_base == "light" else LOGO_DARK
    if not p.exists(): p = LOGO_DARK if theme_base == "light" else LOGO_LIGHT
    if p.exists():
        st.markdown(
            f"<div style='text-align:center'><img src='data:image/png;base64,{base64.b64encode(p.read_bytes()).decode()}' "
            f"style='width:{px}px;margin:0 auto;display:block'/></div>",
            unsafe_allow_html=True
        )

show_logo_center()
st.markdown("<h1 style='text-align:center;margin:.4rem 0 0'>Etiquetas Shopee</h1>", unsafe_allow_html=True)

mode = st.radio("Escolha o tipo de PDF:", ["Apenas etiqueta", "Etiqueta com lista de empacotamento"], horizontal=True)
st.divider()
files = st.file_uploader("Selecione PDF(s) da Shopee", type=["pdf"], accept_multiple_files=True)
diag  = st.toggle("Modo diagnóstico (CSV simples)", value=False)
go    = st.button("Processar")

# ---------------- Constantes ----------------
REMOVE_BLANK = True
DPI_CHECK    = 120
WHITE_THR    = 245
COVERAGE     = 0.995

PT_PER_IN = 72.0
MM_PER_IN = 25.4
def mm_to_pt(mm): return PT_PER_IN * (mm / MM_PER_IN)
TARGET_W_PT = mm_to_pt(100)  # 10 cm
TARGET_H_PT = mm_to_pt(150)  # 15 cm
FONT_NAME   = "helv"         # Helvetica embutida

LATIN = r"A-Za-zÀ-ÖØ-öø-ÿ"

# ---------------- Utilidades ----------------
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
    g = img.convert("L"); hist = g.histogram()
    total = sum(hist); white_px = sum(hist[white:256])
    return (white_px/max(total,1)) >= cov

def content_bbox(page: fitz.Page, clip: fitz.Rect, pad: float = 2.0) -> fitz.Rect:
    blocks = page.get_text("blocks", clip=clip)
    xs0, ys0, xs1, ys1 = [], [], [], []
    for b in blocks:
        x0, y0, x1, y1 = b[0], b[1], b[2], b[3]
        xs0.append(x0); ys0.append(y0); xs1.append(x1); ys1.append(y1)
    if not xs0: return clip
    bb = fitz.Rect(min(xs0), min(ys0), max(xs1), max(ys1)) & clip
    bb.x0 = max(clip.x0, bb.x0 - pad); bb.y0 = max(clip.y0, bb.y0 - pad)
    bb.x1 = min(clip.x1, bb.x1 + pad); bb.y1 = min(clip.y1, bb.y1 + pad)
    return bb

def trim_bbox_by_raster(doc: fitz.Document, page_idx: int, rect: fitz.Rect,
                        dpi: int = 200, white: int = 245, cov: float = 0.995,
                        pad_pt: float = 1.5) -> fitz.Rect:
    """
    Faz crop fino por rasterização local para remover bordas totalmente brancas.
    Retorna um fitz.Rect dentro de 'rect' com pequena margem (pad_pt).
    """
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

    def row_is_white(y: int) -> bool:
        return sum(1 for x in range(w) if px[x, y] >= white) / w >= cov

    def col_is_white(x: int) -> bool:
        return sum(1 for y in range(h) if px[x, y] >= white) / h >= cov

    # varrer topo
    top = 0
    while top < h and row_is_white(top):
        top += 1
    if top == h:
        return rect

    # varrer base
    bottom = h - 1
    while bottom >= 0 and row_is_white(bottom):
        bottom -= 1

    # varrer esquerda
    left = 0
    while left < w and col_is_white(left):
        left += 1

    # varrer direita
    right = w - 1
    while right >= 0 and col_is_white(right):
        right -= 1

    # converter pixels -> pontos
    px2pt = lambda v: (v / dpi) * 72.0
    new_rect = fitz.Rect(
        rect.x0 + px2pt(left) - pad_pt,
        rect.y0 + px2pt(top) - pad_pt,
        rect.x0 + px2pt(right + 1) + pad_pt,
        rect.y0 + px2pt(bottom + 1) + pad_pt,
    )
    return new_rect & rect


def tighten_right_edge(page: fitz.Page, doc: fitz.Document, page_idx: int,
                       rect: fitz.Rect, pad_pt: float = 2.0) -> fitz.Rect:
    words = page.get_text("words", clip=rect)
    if words:
        max_x1 = max(w[2] for w in words)
        if max_x1 < rect.x1:
            return fitz.Rect(rect.x0, rect.y0, min(rect.x1, max_x1 + pad_pt), rect.y1)
    tight = trim_bbox_by_raster(doc, page_idx, rect, dpi=200, white=245, cov=0.997, pad_pt=1.0)
    return fitz.Rect(rect.x0, rect.y0, min(rect.x1, tight.x1), rect.y1)

# -------------- Extrair e redesenhar tabela (empacotamento) --------------
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
    if not words: return []
    x_prod = col_x.get("produto", list_clip.x0)
    x_sku  = col_x.get("sku", x_prod + 120)
    x_var  = col_x.get("variacao", x_sku + 120)
    x_qtd  = col_x.get("qtd", x_var + 120)
    # agrupa por linha
    lines={}
    for x0,y0,x1,y1,w,*_ in words:
        yc=(y0+y1)/2; bucket=None
        for k in lines:
            if abs(k-yc)<5: bucket=k; break
        if bucket is None: lines[yc]=[]; bucket=yc
        lines[bucket].append((x0,y0,x1,y1,str(w)))
    rows=[]
    for yc in sorted(lines.keys()):
        row={"produto":[], "sku":[], "variacao":[], "qtd":[]}
        for x0,y0,x1,y1,w in sorted(lines[yc], key=lambda t:t[0]):
            xm=(x0+x1)/2
            if   xm < x_sku: row["produto"].append(w)
            elif xm < x_var: row["sku"].append(w)
            elif xm < x_qtd: row["variacao"].append(w)
            else:            row["qtd"].append(w)
        def join(v): return norm_heavy(" ".join(v)).strip()
        R={"produto":join(row["produto"]),
           "sku":join(row["sku"]),
           "variacao":join(row["variacao"]),
           "qtd":join(row["qtd"]) or "1"}
        hdr=(R["produto"].upper().startswith("PRODUTO") or
             R["sku"].upper()=="SKU" or
             R["variacao"].upper().startswith("VARIAC") or
             R["qtd"].upper().startswith("QUANT"))
        if not hdr: rows.append(R)
    return rows

def draw_list_vector(page_out: fitz.Page, x, y, width, max_height, rows,
                     base_size=9.5, min_size=7.0,
                     col_ratio=(0.58, 0.18, 0.14, 0.10), line_gap=1.6):
    col_w = [width*r for r in col_ratio]
    def tb(rect, txt, size, align=0):
        return page_out.insert_textbox(rect, txt, fontsize=size, fontname=FONT_NAME, align=align)
    size = base_size
    while size >= min_size:
        cursor=y; ok=True
        for r in rows:
            h_prod = tb(fitz.Rect(x, cursor, x+col_w[0], cursor+1e4), r["produto"], size, 0)
            h_sku  = tb(fitz.Rect(x+col_w[0], cursor, x+col_w[0]+col_w[1], cursor+1e4), r["sku"], size, 0)
            h_var  = tb(fitz.Rect(x+col_w[0]+col_w[1], cursor, x+col_w[0]+col_w[1]+col_w[2], cursor+1e4), r["variacao"], size, 0)
            h_qtd  = tb(fitz.Rect(x+col_w[0]+col_w[1]+col_w[2], cursor, x+width, cursor+1e4), r["qtd"], size, 2)
            h=max(h_prod,h_sku,h_var,h_qtd); cursor+=h*line_gap
            if cursor-y>max_height+0.1: ok=False; break
        if ok:
            cursor=y
            for r in rows:
                h_prod = tb(fitz.Rect(x, cursor, x+col_w[0], cursor+1e4), r["produto"], size, 0)
                tb(fitz.Rect(x+col_w[0], cursor, x+col_w[0]+col_w[1], cursor+1e4), r["sku"], size, 0)
                tb(fitz.Rect(x+col_w[0]+col_w[1], cursor, x+col_w[0]+col_w[1]+col_w[2], cursor+1e4), r["variacao"], size, 0)
                tb(fitz.Rect(x+col_w[0]+col_w[1]+col_w[2], cursor, x+width, cursor+1e4), r["qtd"], size, 2)
                cursor += h_prod * line_gap
            return cursor-y
        size-=0.5
    # fallback mínimo
    cursor=y
    for r in rows:
        h_prod = tb(fitz.Rect(x, cursor, x+col_w[0], cursor+1e4), r["produto"], min_size, 0)
        tb(fitz.Rect(x+col_w[0], cursor, x+col_w[0]+col_w[1], cursor+1e4), r["sku"], min_size, 0)
        tb(fitz.Rect(x+col_w[0]+col_w[1], cursor, x+col_w[0]+col_w[1]+col_w[2], cursor+1e4), r["variacao"], min_size, 0)
        tb(fitz.Rect(x+col_w[0]+col_w[1]+col_w[2], cursor, x+width, cursor+1e4), r["qtd"], min_size, 2)
        cursor += h_prod * line_gap
    return cursor-y

# ---------------- Modo: APENAS ETIQUETA (4→1 robusto com PyMuPDF) ----------------
def process_apenas_etiqueta(pdf_bytes: bytes, diagnostic=False):
    """
    Para páginas com 4 etiquetas por folha:
      - Divide em 4 quadrantes via PyMuPDF (respeita rotação/mediabox)
      - Trim de brancos em cada etiqueta
      - Cada etiqueta sai em UMA página (tamanho original da etiqueta)
    """
    src = fitz.open(stream=pdf_bytes, filetype="pdf")
    out = fitz.open()
    rows = []
    for i in range(len(src)):
        p = src[i]; R = p.rect
        quads = [
            fitz.Rect(R.x0, R.y0, (R.x0+R.x1)/2, (R.y0+R.y1)/2),
            fitz.Rect((R.x0+R.x1)/2, R.y0, R.x1, (R.y0+R.y1)/2),
            fitz.Rect(R.x0, (R.y0+R.y1)/2, (R.x0+R.x1)/2, R.y1),
            fitz.Rect((R.x0+R.x1)/2, (R.y0+R.y1)/2, R.x1, R.y1),
        ]
        for qi, q in enumerate(quads, start=1):
            # “Safeguard” para páginas em branco ou áreas vazias
            bb = content_bbox(p, q, pad=2.0)
            bb = trim_bbox_by_raster(src, i, bb, dpi=220, white=245, cov=0.997, pad_pt=1.0)
            if bb.width < 5 or bb.height < 5:  # muito pequeno = vazio
                continue
            if REMOVE_BLANK and quad_is_blank_by_raster(src, i, bb):
                continue
            # nova página do tamanho da etiqueta recortada
            newp = out.new_page(width=bb.width, height=bb.height)
            newp.show_pdf_page(fitz.Rect(0,0,bb.width,bb.height), src, i, clip=bb)
            if diagnostic: rows.append({"src_page": i+1, "quad": qi, "w": round(bb.width,1), "h": round(bb.height,1)})

    buf = io.BytesIO()
    out.save(buf, garbage=4, deflate=True); out.close(); buf.seek(0)
    return buf.getvalue(), pd.DataFrame(rows)

# ---------------- Modo: ETIQUETA + LISTA (10×15) ----------------
def process_empacotamento(pdf_bytes: bytes, diagnostic=False):
    H_PAD = 0.0; V_PAD = 0.0
    src = fitz.open(stream=pdf_bytes, filetype="pdf")
    out_doc = fitz.open(); diag_rows=[]

    for pi in range(len(src)):
        pg = src[pi]; R = pg.rect
        cols = [fitz.Rect(R.x0, R.y0, (R.x0+R.x1)/2, R.y1),
                fitz.Rect((R.x0+R.x1)/2, R.y0, R.x1, R.y1)]
        for ci, col in enumerate(cols, start=1):
            blocks = pg.get_text("blocks", clip=col)
            # achar topo do checklist
            checklist_top=None
            for b in blocks:
                if "CHECKLIST" in norm_heavy(str(b[4])).upper():
                    checklist_top=b[1]; break
            if checklist_top is None:
                for b in blocks:
                    if "ID PEDIDO" in norm_heavy(str(b[4])).upper():
                        checklist_top=b[1]; break
            if checklist_top is None: checklist_top = col.y0 + col.height*0.62
            # header tabela
            table_head_y=None
            for b in blocks:
                if b[1] >= checklist_top - 2:
                    up=norm_heavy(str(b[4])).upper()
                    if ("SKU" in up) and ("QUANTIDADE" in up or up.endswith("QUANTIDADE")):
                        table_head_y=b[1]; break
            if table_head_y is None: table_head_y = checklist_top + 28

            label_raw = fitz.Rect(col.x0, col.y0, col.x1, max(col.y0+20, checklist_top-4))
            list_raw  = fitz.Rect(col.x0, table_head_y-1, col.x1, col.y1-6)

            label_clip_blk = content_bbox(pg, label_raw)
            label_clip     = trim_bbox_by_raster(src, pi, label_clip_blk, dpi=220, white=245, cov=0.997, pad_pt=1.0)

            header_band = fitz.Rect(col.x0, table_head_y - 10, col.x1, table_head_y + 24)
            cols_x = find_column_edges_from_header(pg, header_band)
            if "produto" not in cols_x: cols_x["produto"] = list_raw.x0 + 26

            list_clip = content_bbox(pg, list_raw)
            list_clip = tighten_right_edge(pg, src, pi, list_clip, pad_pt=2.0)

            if REMOVE_BLANK and quad_is_blank_by_raster(src, pi, label_clip):
                continue

            lw, lh = label_clip.width, label_clip.height
            content_w = TARGET_W_PT - 2*H_PAD
            content_h = TARGET_H_PT - 2*V_PAD
            sL = content_w / lw; HL = lh * sL

            rows = extract_list_rows(pg, list_clip, cols_x)

            pg_new = out_doc.new_page(width=TARGET_W_PT, height=TARGET_H_PT)
            x = (TARGET_W_PT - content_w) / 2; y = V_PAD
            pg_new.show_pdf_page(fitz.Rect(x,y,x+content_w,y+HL), src, pi, clip=label_clip)
            y += HL
            used_h = draw_list_vector(pg_new, x, y, content_w, content_h - HL, rows)

            total_h = HL + used_h
            if total_h > content_h + 0.1:
                c = content_h / total_h
                out_doc.delete_page(-1)
                pg_new = out_doc.new_page(width=TARGET_W_PT, height=TARGET_H_PT)
                x = (TARGET_W_PT - content_w*c) / 2; y = V_PAD
                pg_new.show_pdf_page(fitz.Rect(x,y,x+content_w*c,y+HL*c), src, pi, clip=label_clip)
                y += HL*c
                draw_list_vector(pg_new, x, y, content_w*c, content_h - HL*c, rows)

            if diagnostic: diag_rows.append({"page": pi+1, "col": ci, "rows": len(rows)})

    buf = io.BytesIO()
    out_doc.save(buf, garbage=4, deflate=True); out_doc.close(); buf.seek(0)
    return buf.getvalue(), pd.DataFrame(diag_rows)

# ---------------- RUN ----------------
if go:
    if not files:
        st.warning("Selecione pelo menos um PDF.")
    else:
        with st.spinner("Processando..."):
            outputs=[]
            for f in files:
                try:
                    b = f.getvalue()
                    if mode == "Apenas etiqueta":
                        data, df = process_apenas_etiqueta(b, diagnostic=diag)
                        outname = Path(f.name).stem + "_apenas_etiqueta.pdf"
                    else:
                        data, df = process_empacotamento(b, diagnostic=diag)
                        outname = Path(f.name).stem + "_empacotamento_10x15.pdf"
                    outputs.append((outname, data, df))
                except Exception as e:
                    st.error(f"Erro processando {f.name}: {e}")

        if outputs:
            if len(outputs)==1:
                name, data, df = outputs[0]
                st.success("Pronto!")
                st.download_button("Baixar PDF", data=data, file_name=name, mime="application/pdf")
                if diag and not df.empty: st.dataframe(df, use_container_width=True)
            else:
                import zipfile
                zbuf=io.BytesIO()
                with zipfile.ZipFile(zbuf,"w",compression=zipfile.ZIP_DEFLATED) as z:
                    for name,data,_ in outputs: z.writestr(name, data)
                zbuf.seek(0)
                st.success(f"Pronto! {len(outputs)} arquivos processados.")
                st.download_button("Baixar todos (ZIP)", data=zbuf.getvalue(), file_name="processados.zip", mime="application/zip")
else:
    st.info("Faça upload do(s) PDF(s), escolha o modo e clique em **Processar**.")
