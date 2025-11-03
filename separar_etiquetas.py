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

# ============================= UI / TEMA =============================
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

# ========================= Constantes / Utilidades =========================
REMOVE_BLANK = True
DPI_CHECK    = 120
WHITE_THR    = 245
COVERAGE     = 0.995

LATIN = r"A-Za-zÀ-ÖØ-öø-ÿ"

# --- tamanho de saída 10x15 cm (para o modo empacotamento) ---
PT_PER_IN = 72.0
MM_PER_IN = 25.4
def mm_to_pt(mm): return PT_PER_IN * (mm / MM_PER_IN)

TARGET_W_PT = mm_to_pt(100)   # 10 cm  ≈ 283.46 pt
TARGET_H_PT = mm_to_pt(150)   # 15 cm  ≈ 425.20 pt

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

# ============================== 4 ETIQUETAS ==============================
def process_mode_4up(pdf_bytes: bytes, diagnostic=False):
    reader = PdfReader(io.BytesIO(pdf_bytes))
    doc    = fitz.open(stream=pdf_bytes, filetype="pdf")

    def quads_fitz(rect: fitz.Rect):
        W,H = rect.width, rect.height
        return [
            fitz.Rect(rect.x0,       rect.y0,       rect.x0+W/2, rect.y0+H/2),  # TL
            fitz.Rect(rect.x0+W/2,   rect.y0,       rect.x1,     rect.y0+H/2),  # TR
            fitz.Rect(rect.x0,       rect.y0+H/2,   rect.x0+W/2, rect.y1    ),  # BL
            fitz.Rect(rect.x0+W/2,   rect.y0+H/2,   rect.x1,     rect.y1    ),  # BR
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

# ====================== HELPER (cortar espaços brancos) ======================
def content_bbox(page: fitz.Page, clip: fitz.Rect, pad: float = 2.0) -> fitz.Rect:
    """Menor retângulo com conteúdo dentro de 'clip' (por blocks)."""
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

# ========================= LISTA DE EMPACOTAMENTO =========================
def process_mode_packing(pdf_bytes: bytes, diagnostic=False):
    """
    Empacotamento (2 colunas por página): gera 1 página por etiqueta contendo:
      [ETIQUETA] + [TABELA a partir de SKU/QUANTIDADE]
    Remove espaços brancos, remove a coluna '#', e ajusta a saída para 10x15 cm.
    """
    src = fitz.open(stream=pdf_bytes, filetype="pdf")
    assembled = fitz.open()
    diag_rows = []

    def norm_blocks(page, clip):
        out=[]
        for b in page.get_text("blocks", clip=clip):
            x0,y0,x1,y1 = b[0],b[1],b[2],b[3]
            txt = b[4] if len(b)>=5 else ""
            out.append((x0,y0,x1,y1,str(txt)))
        return out

    for pi in range(len(src)):
        pg = src[pi]; R = pg.rect
        left  = fitz.Rect(R.x0, R.y0, (R.x0+R.x1)/2, R.y1)
        right = fitz.Rect((R.x0+R.x1)/2, R.y0, R.x1, R.y1)

        for ci, col in enumerate([left, right], start=1):
            blocks = norm_blocks(pg, col)

            # 1) Detecta topo do "Checklist..."
            checklist_top = None
            for x0,y0,x1,y1,txt in blocks:
                if "CHECKLIST" in norm_heavy(txt).upper():
                    checklist_top = y0; break
            if checklist_top is None:
                for x0,y0,x1,y1,txt in blocks:
                    if "ID PEDIDO" in norm_heavy(txt).upper():
                        checklist_top = y0; break
            if checklist_top is None:
                checklist_top = col.y0 + col.height*0.62

            # 2) Cabeçalho da tabela (SKU + QUANTIDADE)
            table_head_y = None
            for x0,y0,x1,y1,txt in blocks:
                if y0 >= checklist_top - 2:
                    up = norm_heavy(txt).upper()
                    if ("SKU" in up) and ("QUANTIDADE" in up or up.endswith("QUANTIDADE")):
                        table_head_y = y0; break
            if table_head_y is None:
                table_head_y = checklist_top + 28

            # 3) NOVO: detectar borda esquerda da tabela pela coluna "PRODUTO"
            header_band = fitz.Rect(col.x0, table_head_y - 10, col.x1, table_head_y + 24)
            header_words = pg.get_text("words", clip=header_band)

            left_x = None
            hash_right = None
            for x0, y0, x1, y1, w, *rest in header_words:
                txt = norm_heavy(str(w)).upper().strip()
                if "PRODUTO" in txt and left_x is None:
                    left_x = x0           # início da coluna PRODUTO
                if txt in {"#", "Nº", "NO"}:
                    hash_right = x1       # fim da coluna '#'

            if left_x is None and hash_right is not None:
                left_x = hash_right + 6   # pequeno pad para não cortar borda
            if left_x is None:
                left_x = col.x0 + 26      # fallback se nada encontrado

            # 4) Áreas brutas
            label_raw = fitz.Rect(col.x0, col.y0, col.x1, max(col.y0+20, checklist_top-4))
            list_raw  = fitz.Rect(left_x, table_head_y-1, col.x1, col.y1-6)  # começa em PRODUTO

            # 5) Corta branco
            label_clip = content_bbox(pg, label_raw)
            list_clip  = content_bbox(pg, list_raw)

            # 6) Pula colunas sem etiqueta
            if REMOVE_BLANK and quad_is_blank_by_raster(src, pi, label_clip):
                continue

            # 7) Mede e monta página empilhada (etiqueta + lista)
            lw, lh = label_clip.width, label_clip.height
            if quad_is_blank_by_raster(src, pi, list_clip):
                use_list = False; tw, th = lw, 0
            else:
                use_list = True;  tw, th = list_clip.width, list_clip.height

            final_w = max(lw, tw)
            final_h = lh + th
            page_out = assembled.new_page(width=final_w, height=final_h)

            # etiqueta topo
            page_out.show_pdf_page(
                fitz.Rect(0, 0, lw, lh),
                src, pi, clip=label_clip
            )
            # lista embaixo (sem coluna '#')
            if use_list:
                page_out.show_pdf_page(
                    fitz.Rect(0, lh, tw, lh+th),
                    src, pi, clip=list_clip
                )

            if diagnostic:
                diag_rows.append({
                    "page": pi+1, "col": ci,
                    "checklist_top": round(checklist_top,1),
                    "table_head_y": round(table_head_y,1),
                    "left_x": round(left_x,1),
                    "label_clip": f"{round(label_clip.x0,1)},{round(label_clip.y0,1)}-{round(label_clip.x1,1)},{round(label_clip.y1,1)}",
                    "list_clip":  f"{round(list_clip.x0,1)},{round(list_clip.y0,1)}-{round(list_clip.x1,1)},{round(list_clip.y1,1)}",
                })

    # 8) Encaixa cada página final em 10x15 cm (centralizado, mantendo proporção)
    out_doc = fitz.open()
    for i in range(len(assembled)):
        src_pg = assembled[i]
        sw, sh = src_pg.rect.width, src_pg.rect.height
        scale = min(TARGET_W_PT / sw, TARGET_H_PT / sh)
        tw, th = sw * scale, sh * scale
        dx = (TARGET_W_PT - tw) / 2
        dy = (TARGET_H_PT - th) / 2

        pg_new = out_doc.new_page(width=TARGET_W_PT, height=TARGET_H_PT)
        pg_new.show_pdf_page(
            fitz.Rect(dx, dy, dx+tw, dy+th),
            assembled, i
        )

    buf = io.BytesIO()
    out_doc.save(buf)
    out_doc.close()
    buf.seek(0)
    return buf.getvalue(), pd.DataFrame(diag_rows)

# ================================= RUN =================================
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
