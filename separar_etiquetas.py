# pip install streamlit PyMuPDF pillow pandas
import streamlit as st
from pathlib import Path
import base64, io, re, unicodedata
import pandas as pd
import fitz  # PyMuPDF
from PIL import Image

# ----------------- UI -----------------
st.set_page_config(page_title="Etiquetas Shopee – Marra", layout="wide")
st.markdown("""
<style>
#MainMenu, footer {visibility:hidden;}
header,[data-testid="stToolbar"],[data-testid="stDecoration"]{display:none!important;}
div[class^="viewerBadge"]{display:none!important;}
</style>""", unsafe_allow_html=True)

BASE = Path(__file__).parent
def show_logo_center(px=420):
    logo = None
    for p in ("logo_dark.png","logo_light.png"):
        q = (BASE/p)
        if q.exists(): logo = q; break
    if logo:
        b64 = base64.b64encode(logo.read_bytes()).decode()
        st.markdown(f"<div style='text-align:center'><img src='data:image/png;base64,{b64}' style='width:{px}px'/></div>", unsafe_allow_html=True)
show_logo_center()

st.markdown("<h1 style='text-align:center;margin:.4rem 0 0'>Etiquetas Shopee</h1>", unsafe_allow_html=True)

c1,c2,c3,c4 = st.columns([1,1,1,2])
with c1:
    mode = st.radio("Tipo de PDF:", ["Apenas etiqueta", "Etiqueta com lista de empacotamento"])
with c2:
    diag = st.toggle("Modo diagnóstico", False)
with c3:
    fix_10x15 = st.toggle("Fixar saída em 10×15 cm (apenas etiqueta)", False)
with c4:
    st.write("")

st.divider()
files = st.file_uploader("Selecione PDF(s) da Shopee", type=["pdf"], accept_multiple_files=True)
go = st.button("Processar")

# ----------------- Const & helpers -----------------
PT_PER_IN = 72.0
MM_PER_IN = 25.4
def mm_to_pt(mm): return PT_PER_IN*(mm/MM_PER_IN)
PAGE_W = mm_to_pt(100)  # 10 cm
PAGE_H = mm_to_pt(150)  # 15 cm

DPI_MASK = 120
WHITE_THR = 245
BLANK_COV = 0.995

def normalize_txt(t:str)->str:
    t = unicodedata.normalize("NFKD", t)
    t = "".join(c for c in t if not unicodedata.combining(c))
    return re.sub(r"\s+"," ",t).strip()

def norm_heavy(t:str)->str:
    t = normalize_txt(t)
    # junta letras soltas A A A -> AAA
    return re.sub(r"(?:(?<=\\b)[A-Za-z]\\s(?=[A-Za-z]))+", lambda m: m.group(0).replace(" ",""), t)

def page_white_coverage(page: fitz.Page, clip: fitz.Rect, dpi=DPI_MASK, white=WHITE_THR) -> float:
    """0..1 de pixels brancos (1 é totalmente branco). Só para medição, não salva imagem."""
    scale = dpi/72.0
    pix = page.get_pixmap(matrix=fitz.Matrix(scale,scale), clip=clip, alpha=False)
    if pix.width==0 or pix.height==0: return 1.0
    img = Image.frombytes("RGB",(pix.width, pix.height), pix.samples)
    g = img.convert("L"); hist = g.histogram()
    total = sum(hist); whites = sum(hist[white:256])
    return whites/max(total,1)

def trim_by_raster(doc: fitz.Document, page_idx:int, rect: fitz.Rect,
                   dpi=200, white=245, cov=0.995, pad_pt=1.5)->fitz.Rect:
    """Aperta o retângulo removendo bordas brancas; retorna retângulo em pontos."""
    p = doc[page_idx]
    scale = dpi/72.0
    pix = p.get_pixmap(matrix=fitz.Matrix(scale,scale), clip=rect, alpha=False)
    if pix.width==0 or pix.height==0: return rect
    img = Image.frombytes("RGB",(pix.width,pix.height), pix.samples).convert("L")
    w,h = img.size; px = img.load()

    def row_white(y): return sum(1 for x in range(w) if px[x,y]>=white)/w >= cov
    def col_white(x): return sum(1 for y in range(h) if px[x,y]>=white)/h >= cov

    top=0
    while top<h and row_white(top): top+=1
    if top==h: return rect
    bot=h-1
    while bot>=0 and row_white(bot): bot-=1
    left=0
    while left<w and col_white(left): left+=1
    right=w-1
    while right>=0 and col_white(right): right-=1

    px2pt=lambda v:(v/dpi)*72.0
    res = fitz.Rect(
        rect.x0+px2pt(left)-pad_pt, rect.y0+px2pt(top)-pad_pt,
        rect.x0+px2pt(right+1)+pad_pt, rect.y0+px2pt(bot+1)+pad_pt
    )
    return res & rect

def detect_cells(doc: fitz.Document, page_idx:int, candidates=((1,1),(1,2),(2,1),(2,2))):
    p = doc[page_idx]; R = p.rect
    best=None
    for rows,cols in candidates:
        cells=[]; nonblank=0; fill=0.0
        cw=(R.x1-R.x0)/cols; ch=(R.y1-R.y0)/rows
        for r in range(rows):
            for c in range(cols):
                cell = fitz.Rect(R.x0+c*cw, R.y0+r*ch, R.x0+(c+1)*cw, R.y0+(r+1)*ch)
                white = page_white_coverage(p, cell)
                if white < BLANK_COV: nonblank+=1
                fill += 1.0-white
                cells.append((cell, white))
        score=(nonblank, round(fill,3))
        if best is None or score>best[0]:
            best=(score,(rows,cols),cells)
    # retorna só células ocupadas
    _, layout, cells = best
    occupied=[cell for (cell,white) in cells if white<BLANK_COV]
    return occupied if occupied else [doc[page_idx].rect], layout

# ----------------- Modo: Apenas etiqueta (vetorial) -----------------
def process_apenas_etiqueta(pdf_bytes: bytes, fix_10x15=False, diagnostic=False):
    src = fitz.open(stream=pdf_bytes, filetype="pdf")
    out = fitz.open(); rows=[]

    MIN_RATIO=0.18; MIN_ABS=24.0

    for i in range(len(src)):
        p = src[i]
        occ_cells, layout = detect_cells(src, i)

        for idx, cell in enumerate(occ_cells, start=1):
            bb = trim_by_raster(src, i, cell, dpi=200, white=245, cov=0.997, pad_pt=1.0)
            if (bb.width < max(cell.width*MIN_RATIO, MIN_ABS)) or (bb.height < max(cell.height*MIN_RATIO, MIN_ABS)):
                bb = cell

            if not fix_10x15:
                newp = out.new_page(width=bb.width, height=bb.height)
                newp.show_pdf_page(fitz.Rect(0,0,bb.width,bb.height), src, i, clip=bb)
            else:
                newp = out.new_page(width=PAGE_W, height=PAGE_H)
                # encaixa na 10×15 mantendo proporção (vetorial)
                scale = min(PAGE_W/bb.width, PAGE_H/bb.height)
                w = bb.width*scale; h = bb.height*scale
                x0=(PAGE_W-w)/2; y0=(PAGE_H-h)/2
                newp.show_pdf_page(fitz.Rect(x0,y0,x0+w,y0+h), src, i, clip=bb)

            if diagnostic:
                rows.append({"page":i+1,"cell_idx":idx,"layout":f"{layout[0]}x{layout[1]}",
                             "bb_w":round(bb.width,1),"bb_h":round(bb.height,1)})

    buf=io.BytesIO(); out.save(buf, garbage=4, deflate=True); out.close(); buf.seek(0)
    return buf.getvalue(), pd.DataFrame(rows)

# ----------------- Modo: Etiqueta + lista (10×15 cm) -----------------
FONT_NAME="helv"

def content_bbox(page: fitz.Page, clip: fitz.Rect, pad: float = 2.0) -> fitz.Rect:
    blocks = page.get_text("blocks", clip=clip)
    if not blocks: return clip
    xs0, ys0, xs1, ys1 = [], [], [], []
    for b in blocks:
        xs0.append(b[0]); ys0.append(b[1]); xs1.append(b[2]); ys1.append(b[3])
    bb = fitz.Rect(min(xs0),min(ys0),max(xs1),max(ys1)) & clip
    bb.x0=max(clip.x0, bb.x0-pad); bb.y0=max(clip.y0, bb.y0-pad)
    bb.x1=min(clip.x1, bb.x1+pad); bb.y1=min(clip.y1, bb.y1+pad)
    return bb

def find_header_y(page: fitz.Page, col: fitz.Rect):
    blocks = page.get_text("blocks", clip=col)
    top=None
    for b in blocks:
        if "CHECKLIST" in norm_heavy(str(b[4])).upper(): top=b[1]; break
    if top is None:
        for b in blocks:
            if "ID PEDIDO" in norm_heavy(str(b[4])).upper(): top=b[1]; break
    if top is None: top = col.y0 + col.height*0.62
    # procura a linha com SKU / QUANTIDADE
    head=None
    for b in blocks:
        if b[1] >= top-2:
            t = norm_heavy(str(b[4])).upper()
            if ("SKU" in t) and ("QUANTIDADE" in t or t.endswith("QUANTIDADE")):
                head=b[1]; break
    if head is None: head = top + 28
    return top, head

def find_column_edges_from_header(page: fitz.Page, header_band: fitz.Rect):
    words = page.get_text("words", clip=header_band)
    key={}
    for x0,y0,x1,y1,w,*_ in words:
        t = norm_heavy(str(w)).upper()
        if "PRODUTO" in t and "produto" not in key: key["produto"]=x0
        if t=="SKU" or t.endswith("SKU"): key["sku"]=x0
        if "VARIACAO" in t: key["variacao"]=x0
        if "QUANTIDADE" in t or t.startswith("QTD"): key["qtd"]=x0
    return key

def extract_rows(page: fitz.Page, list_clip: fitz.Rect, col_x: dict):
    words = page.get_text("words", clip=list_clip)
    if not words: return []
    x_prod = col_x.get("produto", list_clip.x0)
    x_sku  = col_x.get("sku", x_prod+120)
    x_var  = col_x.get("variacao", x_sku+120)
    x_qtd  = col_x.get("qtd", x_var+120)
    lines={}
    for x0,y0,x1,y1,w,*_ in words:
        yc=(y0+y1)/2; k=None
        for ky in lines:
            if abs(ky-yc)<5: k=ky; break
        if k is None: lines[yc]=[]; k=yc
        lines[k].append((x0,y0,x1,y1,str(w)))
    rows=[]
    for yc in sorted(lines.keys()):
        row={"p":[], "s":[], "v":[], "q":[]}
        for x0,y0,x1,y1,w in sorted(lines[yc], key=lambda t:t[0]):
            xm=(x0+x1)/2
            if   xm < x_sku: row["p"].append(w)
            elif xm < x_var: row["s"].append(w)
            elif xm < x_qtd: row["v"].append(w)
            else:            row["q"].append(w)
        def J(a): return norm_heavy(" ".join(a)).strip()
        R={"produto":J(row["p"]), "sku":J(row["s"]), "variacao":J(row["v"]), "qtd":J(row["q"]) or "1"}
        hdr=(R["produto"].upper().startswith("PRODUTO") or R["sku"].upper()=="SKU" or
             R["variacao"].upper().startswith("VARIAC") or R["qtd"].upper().startswith("QUANT"))
        if not hdr: rows.append(R)
    return rows

def draw_table(page: fitz.Page, x, y, width, max_h, rows,
               base=9.5, minf=7.0, col_ratio=(0.58,0.18,0.14,0.10), gap=1.6):
    cw=[width*r for r in col_ratio]
    def tb(r,txt,fs,al=0): return page.insert_textbox(r, txt, fontsize=fs, fontname="helv", align=al)
    fs=base
    while fs>=minf:
        cur=y; ok=True
        for r in rows:
            hp=tb(fitz.Rect(x,cur,x+cw[0],cur+1e4), r["produto"], fs, 0)
            tb(fitz.Rect(x+cw[0],cur,x+cw[0]+cw[1],cur+1e4), r["sku"], fs, 0)
            tb(fitz.Rect(x+cw[0]+cw[1],cur,x+cw[0]+cw[1]+cw[2],cur+1e4), r["variacao"], fs, 0)
            tb(fitz.Rect(x+cw[0]+cw[1]+cw[2],cur,x+width,cur+1e4), r["qtd"], fs, 2)
            cur += hp*gap
            if cur-y>max_h+0.1: ok=False; break
        if ok:
            cur=y
            for r in rows:
                hp=tb(fitz.Rect(x,cur,x+cw[0],cur+1e4), r["produto"], fs, 0)
                tb(fitz.Rect(x+cw[0],cur,x+cw[0]+cw[1],cur+1e4), r["sku"], fs, 0)
                tb(fitz.Rect(x+cw[0]+cw[1],cur,x+cw[0]+cw[1]+cw[2],cur+1e4), r["variacao"], fs, 0)
                tb(fitz.Rect(x+cw[0]+cw[1]+cw[2],cur,x+width,cur+1e4), r["qtd"], fs, 2)
                cur += hp*gap
            return cur-y
        fs-=0.5
    # último recurso
    cur=y
    for r in rows:
        hp=tb(fitz.Rect(x,cur,x+cw[0],cur+1e4), r["produto"], minf, 0)
        tb(fitz.Rect(x+cw[0],cur,x+cw[0]+cw[1],cur+1e4), r["sku"], minf, 0)
        tb(fitz.Rect(x+cw[0]+cw[1],cur,x+cw[0]+cw[1]+cw[2],cur+1e4), r["variacao"], minf, 0)
        tb(fitz.Rect(x+cw[0]+cw[1]+cw[2],cur,x+width,cur+1e4), r["qtd"], minf, 2)
        cur += hp*gap
    return cur-y

def process_empacotamento(pdf_bytes: bytes, diagnostic=False):
    src = fitz.open(stream=pdf_bytes, filetype="pdf")
    out = fitz.open(); rows=[]
    for i in range(len(src)):
        p=src[i]; R=p.rect
        cols=[fitz.Rect(R.x0,R.y0,(R.x0+R.x1)/2,R.y1), fitz.Rect((R.x0+R.x1)/2,R.y0,R.x1,R.y1)]
        for j,col in enumerate(cols, start=1):
            top,head = find_header_y(p,col)
            label_raw = fitz.Rect(col.x0,col.y0,col.x1,max(col.y0+20, top-4))
            list_raw  = fitz.Rect(col.x0, head-1, col.x1, col.y1-6)
            label_clip = trim_by_raster(src, i, content_bbox(p,label_raw), dpi=220, white=245, cov=0.997, pad_pt=1.0)

            header_band = fitz.Rect(col.x0, head-10, col.x1, head+24)
            cols_x = find_column_edges_from_header(p, header_band)
            if "produto" not in cols_x: cols_x["produto"]=list_raw.x0+26

            list_clip = trim_by_raster(src, i, content_bbox(p,list_raw), dpi=220, white=245, cov=0.997, pad_pt=1.0)

            # nova página 10×15
            newp = out.new_page(width=PAGE_W, height=PAGE_H)
            # encaixa o cabeçalho
            lw,lh = label_clip.width, label_clip.height
            scale = (PAGE_W-0)/lw; HL = lh*scale
            newp.show_pdf_page(fitz.Rect(0,(PAGE_H-HL-(PAGE_H- HL)), PAGE_W, HL), src, i, clip=label_clip)

            # tabela
            rows_data = extract_rows(p, list_clip, cols_x)
            y = HL; x = 0; avail = PAGE_H - HL
            used = draw_table(newp, x, y, PAGE_W, avail, rows_data)
            if diagnostic: rows.append({"page":i+1,"col":j,"rows":len(rows_data),"used_h":round(used,1)})
    buf=io.BytesIO(); out.save(buf, garbage=4, deflate=True); out.close(); buf.seek(0)
    return buf.getvalue(), pd.DataFrame(rows)

# ----------------- RUN -----------------
if go:
    if not files:
        st.warning("Selecione pelo menos um PDF.")
    else:
        outs=[]
        for f in files:
            try:
                b=f.getvalue()
                if mode=="Apenas etiqueta":
                    pdf, df = process_apenas_etiqueta(b, fix_10x15=fix_10x15, diagnostic=diag)
                    name = Path(f.name).stem + ("_apenas_etiqueta_10x15.pdf" if fix_10x15 else "_apenas_etiqueta.pdf")
                else:
                    pdf, df = process_empacotamento(b, diagnostic=diag)
                    name = Path(f.name).stem + "_empacotamento_10x15.pdf"
                outs.append((name,pdf,df))
            except Exception as e:
                st.error(f"Erro processando {f.name}: {e}")

        if outs:
            if len(outs)==1:
                n,d,df = outs[0]
                st.success("Pronto!")
                st.download_button("Baixar PDF", data=d, file_name=n, mime="application/pdf")
                if diag and not df.empty: st.dataframe(df, use_container_width=True)
            else:
                import zipfile
                zbuf=io.BytesIO()
                with zipfile.ZipFile(zbuf,"w",compression=zipfile.ZIP_DEFLATED) as z:
                    for n,d,_ in outs: z.writestr(n,d)
                zbuf.seek(0)
                st.success(f"{len(outs)} arquivos processados.")
                st.download_button("Baixar ZIP", data=zbuf.getvalue(), file_name="processados.zip", mime="application/zip")
else:
    st.info("Faça upload do(s) PDF(s), escolha o modo e clique em **Processar**.")
