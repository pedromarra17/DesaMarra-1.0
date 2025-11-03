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
show_diag = st.toggle("Modo diagnóstico (CSV + preview de caixas)", value=False)
process_btn = st.button("Processar")

# ================ Constantes comuns / utilidades ==============
REMOVE_BLANK = True
DPI_CHECK    = 120
WHITE_THR    = 245
COVERAGE     = 0.995

OVERLAY_HEIGHT_PCT = 0.14
FONT_SIZE = 7
MAX_LINES = 14
MARGIN_X_PT = 18
PAD_Y_PT = 6

LATIN = r"A-Za-zÀ-ÖØ-öø-ÿ"

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

# ==== detecção de pedidos + parser QNT×SKU (colunas / fallback) ====
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

# Aceita SKU com 3–32 chars contendo letras OU números (não precisa ter ambos)
SKU_TOKEN_RE = re.compile(r"\b[A-Z0-9][A-Z0-9\-\/\.]{2,31}\b", re.I)



def words_from(page: fitz.Page, rect: fitz.Rect):
    ws = page.get_text("words", clip=rect)
    words=[]
    for x0,y0,x1,y1,w,*_ in ws:
        t = str(w).strip()
        if not t: continue
        words.append({"x0":x0,"y0":y0,"x1":x1,"y1":y1,"xc":(x0+x1)/2,"yc":(y0+y1)/2,"h":(y1-y0),"t":norm_heavy(t).upper()})
    words.sort(key=lambda k:(round(k["yc"],1),k["x0"]))
    return words

def group_by_lines(words, y_tol=3.5):
    lines=[]
    for w in words:
        if not lines: lines.append([w]); continue
        last=lines[-1]; ly=sum(x["yc"] for x in last)/len(last)
        (last.append(w) if abs(w["yc"]-ly)<=y_tol else lines.append([w]))
    return lines

def merge_letters(line, gap_factor=0.6):
    if not line: return []
    avg_h=sum(w["h"] for w in line)/len(line); gap=avg_h*gap_factor
    out=[]; cur=None
    for w in sorted(line,key=lambda k:k["x0"]):
        if cur is None: cur=dict(w)
        elif w["x0"]-cur["x1"]<=gap:
            cur["x1"]=max(cur["x1"],w["x1"]); cur["xc"]=(cur["x0"]+cur["x1"])/2; cur["t"]=(cur["t"]+w["t"]).upper()
        else: out.append(cur); cur=dict(w)
    if cur: out.append(cur)
    return out

def find_header_cols(lines):
    for ln in lines:
        groups = merge_letters(ln)
        txts   = [g["t"] for g in groups]
        got_q  = any(t in ("QNT","QTD","QTDE") for t in txts)
        got_s  = "SKU" in txts
        got_p  = any("PRODUTO" in t for t in txts)
        got_v  = any(("VARIACAO" in t) or ("VARIAÇÃO" in t) for t in txts)
        if (got_p and got_s) and (got_q or got_v):
            cols={}
            for g in groups:
                if "PRODUTO" in g["t"]: cols["PRODUTO"]=g["xc"]
                if "VARIACAO" in g["t"] or "VARIAÇÃO" in g["t"]: cols["VARIACAO"]=g["xc"]
                if g["t"] in ("QNT","QTD","QTDE"): cols["QNT"]=g["xc"]
                if g["t"]=="SKU": cols["SKU"]=g["xc"]
            cols["y"]=groups[0]["yc"]
            if "QNT" in cols and "SKU" in cols: return cols
    return {}

def nearest_group(groups, x, max_dx=65):
    best,bd=None,1e9
    for g in groups:
        d=abs(g["xc"]-x)
        if d<bd: bd,best=d,g
    return best if bd<=max_dx else None

def extract_items_QNTxSKU(page: fitz.Page, rect: fitz.Rect, max_lines=MAX_LINES):
    """
    Extrai '- QNTx SKU' em layouts de 'Checklist de carregamento' e listas por colunas.
    Mais tolerante a quebras logo abaixo do cabeçalho e SKU/QNT minimalistas.
    """
    # tolerâncias
    Y_TOL        = 6.0       # agrupar linhas
    MAX_DX       = 140       # casar grupo com coluna
    SKIP_HDR_PAD = 6.0       # margem abaixo do cabeçalho

    def _words(rect_):
        ws = page.get_text("words", clip=rect_)
        words=[]
        for x0,y0,x1,y1,w,*_ in ws:
            t = str(w).strip()
            if not t: continue
            words.append({
                "x0":x0,"y0":y0,"x1":x1,"y1":y1,
                "xc":(x0+x1)/2,"yc":(y0+y1)/2,"h":(y1-y0),
                "t":norm_heavy(t).upper()
            })
        words.sort(key=lambda k:(round(k["yc"],1),k["x0"]))
        return words

    def _group_by_lines(words):
        lines=[]
        for w in words:
            if not lines: lines.append([w]); continue
            ly = sum(x["yc"] for x in lines[-1])/len(lines[-1])
            if abs(w["yc"]-ly) <= Y_TOL:
                lines[-1].append(w)
            else:
                lines.append([w])
        return lines

    def _merge_letters(line, gap_factor=0.8):
        if not line: return []
        avg_h = sum(w["h"] for w in line)/len(line)
        gap   = avg_h * gap_factor
        out, cur = [], None
        for w in sorted(line, key=lambda k:k["x0"]):
            if cur is None: cur = dict(w)
            elif w["x0"] - cur["x1"] <= gap:
                cur["x1"] = max(cur["x1"], w["x1"])
                cur["xc"] = (cur["x0"] + cur["x1"])/2
                cur["t"]  = (cur["t"] + " " + w["t"]).strip()
            else:
                out.append(cur); cur = dict(w)
        if cur: out.append(cur)
        return out

    def _find_header(lines):
        for ln in lines:
            g = _merge_letters(ln)
            txts = [x["t"] for x in g]
            has_sku = any("SKU" in t for t in txts)
            has_qnt = any(t in ("QNT","QTD","QTDE") or ("QUANTIDADE" in t) for t in txts)
            has_prd = any("PRODUTO" in t for t in txts)
            if has_sku and has_qnt and has_prd:
                cols={}
                for x in g:
                    if "SKU" in x["t"]: cols["SKU"]=x["xc"]
                    if x["t"] in ("QNT","QTD","QTDE") or ("QUANTIDADE" in x["t"]): cols["QNT"]=x["xc"]
                cols["y"]=g[0]["yc"]
                if "SKU" in cols and "QNT" in cols:
                    return cols
        return {}

    def _nearest(groups, x):
        best,bd=None,1e9
        for g in groups:
            d=abs(g["xc"]-x)
            if d<bd: bd,best=d,g
        return best if bd<=MAX_DX else None

    words = _words(rect)
    if not words: return []

    lines = _group_by_lines(words)
    cols  = _find_header(lines)

    # ---------- Fallback textual se não identificou colunas ----------
    if not cols:
        raw = norm_heavy(page.get_text("text", clip=rect))
        if not (("SKU" in raw) and (("QNT" in raw) or ("QTD" in raw) or ("QTDE" in raw) or ("QUANTIDADE" in raw))):
            return []
        items=[]
        for ln in [l.strip() for l in raw.splitlines() if l.strip()]:
            base = norm_heavy(ln)
            mqty = (re.search(r"(?:QNT|QTD|QTDE|QUANTIDADE)\s*[:x\-]*\s*(\d{1,3})", base, re.I)
                    or re.search(r"\b(\d{1,3})\s*x\b", base, re.I)
                    or re.search(r"\bx\s*(\d{1,3})\b", base, re.I))
            qty = int(mqty.group(1)) if mqty else 1
            msku = re.search(r"\bS\s*K\s*U[:\s\-]*([A-Z0-9\s\-\/\.]{3,})", base, re.I)
            sku  = None
            if msku:
                cand=re.sub(r"\s+","", msku.group(1))
                mt=SKU_TOKEN_RE.search(cand); sku=mt.group(0) if mt else None
            if not sku:
                mt=SKU_TOKEN_RE.search(base); sku=mt.group(0) if mt else None
            if sku:
                item=f"- {qty}x {sku}"
                if item not in items: items.append(item)
            if len(items)>=max_lines: break
        return items

    # ---------- Com colunas detectadas ----------
    items=[]
    header_y = cols["y"] + SKIP_HDR_PAD

    for ln in lines:
        if not ln or ln[0]["yc"] <= header_y:
            continue

        groups = _merge_letters(ln)
        line_tx = " ".join(g["t"] for g in groups)

        # pega o grupo MAIS PRÓXIMO de cada coluna e usa o texto bruto do grupo
        gq = _nearest(groups, cols["QNT"])
        gs = _nearest(groups, cols["SKU"])

        # QNT
        qty = None
        if gq:
            # tenta número no próprio grupo
            m = re.search(r"\b(\d{1,3})\b", gq["t"])
            if m: qty = int(m.group(1))
        if qty is None:
            # tenta no texto da linha
            m = (re.search(r"\b(\d{1,3})\s*x\b", line_tx, re.I)
                 or re.search(r"\bx\s*(\d{1,3})\b", line_tx, re.I)
                 or re.search(r"(?:QNT|QTD|QTDE|QUANTIDADE)\s*[:x\-]*\s*(\d{1,3})", line_tx, re.I))
            if m: qty = int(m.group(1))
        if qty is None: qty = 1

        # SKU
        sku = None
        if gs:
            # remove a palavra SKU eventual e separadores
            raw = re.sub(r"\bSKU\b[:\- ]*", "", gs["t"]).strip()
            mt  = SKU_TOKEN_RE.search(raw)
            if mt: sku = mt.group(0)
            else:
                # como último recurso, tenta na linha toda
                mt = SKU_TOKEN_RE.search(line_tx)
                if mt: sku = mt.group(0)

        if sku:
            items.append(f"- {qty}x {sku}")
            if len(items) >= max_lines:
                break

    return items


# ========================= MODO 1: 4 etiquetas =========================
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

    label_quads=[]; list_quads=[]; diag_rows=[]
    for i in range(len(doc)):
        pf=doc[i]; qf=quads_fitz(pf.rect); qp=quads_pdf(reader.pages[i].mediabox)
        for qidx,(rf,bp) in enumerate(zip(qf,qp)):
            txt = pf.get_text("text", clip=rf) or ""
            # tenta lista
            prods = extract_items_QNTxSKU(pf, rf)
            if prods:
                list_quads.append(dict(page_idx=i, pypdf_box=bp, fitz_rect=rf, text=txt, items=prods))
                typ="list"
            else:
                if REMOVE_BLANK and quad_is_blank_by_raster(doc,i,rf): continue
                label_quads.append(dict(page_idx=i, pypdf_box=bp, fitz_rect=rf, text=txt))
                typ="label"
            if diagnostic:
                pedido = extract_order(txt) or ""
                diag_rows.append({"page":i+1,"quad":qidx+1,"tipo":typ,"pedido":pedido,"amostra":norm_heavy(txt)[:120]})

    # se nada classificar como etiqueta, apenas 4→1
    if not label_quads:
        w=PdfWriter()
        for i in range(len(reader.pages)):
            page=reader.pages[i]
            for (x0,y0,x1,y1) in quads_pdf(page.mediabox):
                if REMOVE_BLANK and quad_is_blank_by_raster(doc,i,fitz.Rect(x0,y0,x1,y1)): continue
                p=deepcopy(page); rect=RectangleObject([x0,y0,x1,y1]); p.cropbox=rect; p.mediabox=rect; w.add_page(p)
        out=io.BytesIO(); w.write(out); out.seek(0)
        return out.getvalue(), pd.DataFrame(diag_rows)

    # recorte todas as etiquetas
    w=PdfWriter()
    for q in label_quads:
        page=reader.pages[q["page_idx"]]
        x0,y0,x1,y1=q["pypdf_box"]; p=deepcopy(page); rect=RectangleObject([x0,y0,x1,y1])
        p.cropbox=rect; p.mediabox=rect; w.add_page(p)
    tmp=io.BytesIO(); w.write(tmp); tmp.seek(0)
    cropped=fitz.open(stream=tmp.getvalue(), filetype="pdf")

    # listas por pedido e por ordem
    lists_by_order={}; lists_in_order=[]
    for lst in list_quads:
        oid=extract_order(lst["text"])
        if oid: lists_by_order[oid]=lst["items"]
        else:   lists_in_order.append(lst["items"])

    final_doc=fitz.open(); used=set(); idx_free=0
    for i,q in enumerate(label_quads):
        src=cropped[i]; r=src.rect; order=extract_order(q["text"])
        if order and order in lists_by_order and order not in used:
            items=lists_by_order[order][:MAX_LINES]; used.add(order)
        else:
            items=lists_in_order[idx_free][:MAX_LINES] if idx_free<len(lists_in_order) else []
            if idx_free<len(lists_in_order): idx_free+=1

        lines_count = 1 + max(1,len(items))
        min_area = PAD_Y_PT*2 + (FONT_SIZE+2)*lines_count
        extra_h = max(r.height*OVERLAY_HEIGHT_PCT, min_area)

        pg = final_doc.new_page(width=r.width, height=r.height+extra_h)
        pg.show_pdf_page(fitz.Rect(0,0,r.width,r.height), cropped, i)
        box=fitz.Rect(MARGIN_X_PT, r.height+PAD_Y_PT, r.width-MARGIN_X_PT, r.height+extra_h-PAD_Y_PT)
        text="Lista de separação:\n"+("\n".join(items) if items else "- (não encontrado)")
        pg.insert_textbox(box, text, fontname="helv", fontsize=FONT_SIZE, align=0)

    out=io.BytesIO(); final_doc.save(out); final_doc.close(); out.seek(0)
    return out.getvalue(), pd.DataFrame(diag_rows)

# ==================== MODO 2: lista de empacotamento ====================
def process_mode_packing(pdf_bytes: bytes, diagnostic=False):
    """
    PDF com 2 etiquetas por página; abaixo de cada etiqueta vem 'Checklist de carregamento'.
    Saída: 1 página por etiqueta, com a lista QNT×SKU no rodapé (sem cabeçalhos).
    """
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    reader = PdfReader(io.BytesIO(pdf_bytes))

    final_doc = fitz.open()
    diag_rows=[]

    for pi in range(len(doc)):
        pg = doc[pi]; R = pg.rect
        # divide a página em duas colunas iguais
        left  = fitz.Rect(R.x0, R.y0, (R.x0+R.x1)/2, R.y1)
        right = fitz.Rect((R.x0+R.x1)/2, R.y0, R.x1, R.y1)
        for ci, col in enumerate([left, right], start=1):
            # localizar o topo do 'Checklist de carregamento' dentro da coluna (robusto p/ 7 ou 8 campos)
            txt_col = pg.get_text("blocks", clip=col)
            checklist_top = None
            for b in txt_col:
                x0, y0, x1, y1 = b[0], b[1], b[2], b[3]
                txt = b[4] if len(b) >= 5 else ""
                if "CHECKLIST" in norm_heavy(str(txt)).upper():
                    checklist_top = y0
                    break
            if checklist_top is None:
                # fallback: procura também por 'ID Pedido' como pista de início da lista
                for b in txt_col:
                    x0, y0, x1, y1 = b[0], b[1], b[2], b[3]
                    txt = b[4] if len(b) >= 5 else ""
                    if "ID PEDIDO" in norm_heavy(str(txt)).upper():
                        checklist_top = y0
                        break
            if checklist_top is None:
                # fallback final: usa 62% da coluna como quebra etiqueta/lista
                checklist_top = col.y0 + (col.height * 0.62)

            # etiqueta = da borda superior até um pouco acima do checklist
            label_rect = fitz.Rect(col.x0, col.y0, col.x1, checklist_top-6)

            # região da lista (somente itens, sem cabeçalhos extras)
            list_rect  = fitz.Rect(col.x0, checklist_top+10, col.x1, col.y1-10)

            # extrai itens
            items = extract_items_QNTxSKU(pg, list_rect)

            # pular colunas totalmente em branco
            if REMOVE_BLANK and quad_is_blank_by_raster(doc, pi, label_rect) and not items:
                continue

            # recorta a etiqueta preservando vetor com pypdf
            psrc = reader.pages[pi]
            x0,y0,x1,y1 = label_rect.x0, label_rect.y0, label_rect.x1, label_rect.y1
            p = deepcopy(psrc)
            rect = RectangleObject([x0, y0, x1, y1])
            p.cropbox = rect; p.mediabox = rect
            temp_w = PdfWriter(); temp_w.add_page(p)
            buf = io.BytesIO(); temp_w.write(buf); buf.seek(0)
            cropped = fitz.open(stream=buf.getvalue(), filetype="pdf")
            cr = cropped[0].rect

            # calcula rodapé
            lines_count = 1 + max(1, len(items))
            min_area    = PAD_Y_PT*2 + (FONT_SIZE+2)*lines_count
            extra_h     = max(cr.height*OVERLAY_HEIGHT_PCT, min_area)

            new_pg = final_doc.new_page(width=cr.width, height=cr.height+extra_h)
            new_pg.show_pdf_page(fitz.Rect(0,0,cr.width,cr.height), cropped, 0)
            box = fitz.Rect(MARGIN_X_PT, cr.height+PAD_Y_PT, cr.width-MARGIN_X_PT, cr.height+extra_h-PAD_Y_PT)
            text = "Itens (QNT × SKU):\n" + ("\n".join(items) if items else "- (não encontrado)")
            new_pg.insert_textbox(box, text, fontname="helv", fontsize=FONT_SIZE, align=0)

            if diagnostic:
                amostra = norm_heavy(pg.get_text("text", clip=list_rect))[:120]
                diag_rows.append({"page":pi+1,"col":ci,"itens_detectados":len(items),"amostra":amostra})

    out=io.BytesIO(); final_doc.save(out); final_doc.close(); out.seek(0)
    return out.getvalue(), pd.DataFrame(diag_rows)


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
                base = Path(name).stem + ("_4x1.pdf" if mode=="PDF com 4 etiquetas" else "_empacotamento.pdf")
                st.success("Pronto!")
                st.download_button("Baixar PDF", data=data, file_name=base, mime="application/pdf")
                if show_diag and not diag.empty:
                    st.dataframe(diag, use_container_width=True)
            else:
                import zipfile
                buf=io.BytesIO()
                with zipfile.ZipFile(buf,"w",compression=zipfile.ZIP_DEFLATED) as z:
                    for name,data,_ in results:
                        base = Path(name).stem + ("_4x1.pdf" if mode=="PDF com 4 etiquetas" else "_empacotamento.pdf")
                        z.writestr(base, data)
                buf.seek(0)
                st.success(f"Pronto! {len(results)} arquivos processados.")
                st.download_button("Baixar todos (ZIP)", data=buf.getvalue(), file_name="processados.zip", mime="application/zip")
else:
    st.info("Faça upload do(s) PDF(s), escolha o modo e clique em **Processar**.")
