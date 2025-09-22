import streamlit as st
from pypdf import PdfReader, PdfWriter
from pypdf.generic import RectangleObject
from copy import deepcopy
from pathlib import Path
import base64, io, re, unicodedata

# libs extras
import fitz  # PyMuPDF
from PIL import Image
from rapidfuzz import fuzz
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import portrait

# ================== PAGE CONFIG ==================
st.set_page_config(page_title="Separador de Etiquetas", layout="wide")

# ================== HIDE STREAMLIT BRANDING ==================
st.markdown(
    """
    <style>
    #MainMenu, footer {visibility: hidden;}
    header, [data-testid="stToolbar"], [data-testid="stDecoration"], .stDeployButton {display: none !important;}
    div[class^="viewerBadge"], div[class*="viewerBadge"] {display: none !important;}
    [data-testid="stAppViewContainer"] a[href*="streamlit.io"] {display: none !important;}
    a[href*="streamlit.io"][style*="position: fixed"],
    a[href*="streamlit.app"][style*="position: fixed"] {display: none !important;}
    </style>
    """,
    unsafe_allow_html=True,
)

# ================== HEADER (logo por tema) ==================
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
              <img src="data:image/png;base64,{b64}"
                   style="display:block;margin:0 auto;width:{width_px}px;" />
            </div>
            """,
            unsafe_allow_html=True,
        )

show_logo_center(480)
st.markdown(
    "<h1 style='text-align:center;margin:0.4rem 0 0 0;'>Separador de Etiquetas (4 -> 1) + Produtos</h1>",
    unsafe_allow_html=True,
)
st.markdown(
    "<p style='text-align:center;margin-top:0.25rem;'>Processa o PDF da Shopee, casa cada etiqueta com sua Declaração e imprime os <b>produtos</b> no rodapé da etiqueta.</p>",
    unsafe_allow_html=True,
)

st.divider()

# ================== UPLOADER STYLE (500px + verde) ==================
st.markdown(
    """
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
    """,
    unsafe_allow_html=True,
)

# ================== CONTROLES (básico) ==================
uploaded_file = st.file_uploader("Selecione o PDF", type=["pdf"], key="uploader_main")

# parâmetros internos
REMOVE_BLANK = True
DPI_CHECK = 120         # para checagem de "página em branco"
WHITE_THRESH = 245
COVERAGE = 0.995        # 99,5% branco = vazio

# overlay (onde colocar os produtos na etiqueta)
OVERLAY_HEIGHT_PCT = 0.16   # 16% da altura da etiqueta (ajustável)
OVERLAY_MARGIN_X = 18       # margem esquerda/direita em pontos
FONT_SIZE = 7               # texto pequeno
MAX_LINES = 4               # linhas de produtos no rodapé

# ================== UTILS ==================
def normalize_txt(t: str) -> str:
    t = unicodedata.normalize("NFKD", t)
    t = "".join(ch for ch in t if not unicodedata.combining(ch))
    return re.sub(r"\s+", " ", t).strip()

CEP_RE   = re.compile(r"\b\d{5}-?\d{3}\b")
PHONE_RE = re.compile(r"\b(?:\(?\d{2}\)?\s*)?\d{4,5}-?\d{4}\b")
ORDER_RE = re.compile(r"\b(?:pedido|ordem|order|id|ref)[:\s#-]*([A-Za-z0-9\-]{6,})", re.I)

def extract_features(text: str):
    t = normalize_txt(text.lower())
    cep   = CEP_RE.search(t)
    phone = PHONE_RE.search(t)
    order = ORDER_RE.search(t)
    return {
        "cep": cep.group(0).replace("-", "") if cep else "",
        "phone": re.sub(r"\D", "", phone.group(0)) if phone else "",
        "order": (order.group(1) if order else "").strip(),
        "raw": t
    }

def score_pair(label_ft, decl_ft):
    s_text  = fuzz.partial_ratio(label_ft["raw"], decl_ft["raw"]) / 100.0
    s_cep   = 0.6 if label_ft["cep"] and label_ft["cep"] == decl_ft["cep"] else 0.0
    s_phone = 0.3 if label_ft["phone"] and label_ft["phone"] == decl_ft["phone"] else 0.0
    s_order = 0.8 if label_ft["order"] and label_ft["order"] == decl_ft["order"] else 0.0
    # peso total (normalizado aprox)
    return 0.5*s_text + s_cep + s_phone + s_order

def extract_products(text: str):
    """
    Pega linhas de produtos da declaração.
    Regras simples: procura seção após palavras-chave e linhas com letras e números.
    """
    t = normalize_txt(text)
    # tenta cortar depois de uma âncora comum
    anchors = ["conteudo", "conteúdo", "produtos", "itens", "descricao", "descrição"]
    pos = -1
    for a in anchors:
        p = t.lower().find(a)
        if p != -1:
            pos = max(pos, p)
    if pos != -1:
        t = t[pos:]

    # separa por quebras originais (PyMuPDF preserva alguma estrutura)
    lines = [ln.strip(" •-:") for ln in text.splitlines()]
    clean = []
    for ln in lines:
        l = normalize_txt(ln)
        if len(l) < 2:
            continue
        # ignora coisas claramente de endereço/CPF/assinaturas
        if any(k in l.lower() for k in ["cpf", "cnpj", "assin", "declaro", "remetente", "destinatario", "destinatário", "endereco", "endereço"]):
            continue
        # aceita linhas com letras + (quantidade opcional)
        if re.search(r"[A-Za-z]", l):
            clean.append(l)

    # tenta reduzir a ruído pegando as 4 primeiras "boas"
    out = []
    for ln in clean:
        # colapsa múltiplos espaços
        ln = re.sub(r"\s{2,}", " ", ln)
        # corta muito longa
        if len(ln) > 80:
            ln = ln[:77] + "..."
        out.append(ln)
        if len(out) >= MAX_LINES:
            break

    return out

# ======= QUADRANTES =======
def quadrants_fitz(rect: fitz.Rect):
    W, H = rect.width, rect.height
    return [
        fitz.Rect(rect.x0,       rect.y0,       rect.x0 + W/2, rect.y0 + H/2),  # topo-esq
        fitz.Rect(rect.x0 + W/2, rect.y0,       rect.x1,       rect.y0 + H/2),  # topo-dir
        fitz.Rect(rect.x0,       rect.y0 + H/2, rect.x0 + W/2, rect.y1),        # baixo-esq
        fitz.Rect(rect.x0 + W/2, rect.y0 + H/2, rect.x1,       rect.y1),        # baixo-dir
    ]

def quadrants_pypdf(mb):
    left, bottom, right, top = float(mb.left), float(mb.bottom), float(mb.right), float(mb.top)
    width, height = right - left, top - bottom
    return [
        (left, bottom + height/2, left + width/2, top),           # topo-esq
        (left + width/2, bottom + height/2, right, top),          # topo-dir
        (left, bottom, left + width/2, bottom + height/2),        # baixo-esq
        (left + width/2, bottom, right, bottom + height/2),       # baixo-dir
    ]

# ======= BLANK CHECK (raster) =======
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

# ======= OVERLAY (reportlab) =======
def make_overlay_pdf(w_pt: float, h_pt: float, products: list[str], height_pct=OVERLAY_HEIGHT_PCT,
                     margin_x=OVERLAY_MARGIN_X, font_size=FONT_SIZE) -> bytes:
    """
    Cria um PDF do mesmo tamanho da etiqueta com as linhas de produtos
    desenhadas no 'rodapé' (área inferior da página).
    """
    buf = io.BytesIO()
    # pagesize em pontos - usa portrait pelo tamanho arbitrário
    canv = canvas.Canvas(buf, pagesize=portrait((w_pt, h_pt)))
    # área do rodapé
    pad_y = 4
    area_h = h_pt * height_pct
    y0 = pad_y + area_h - font_size - 2
    x0 = margin_x
    x1 = w_pt - margin_x

    # título opcional pequeno
    canv.setFont("Helvetica-Bold", font_size)
    canv.drawString(x0, y0 + 6, "Produtos:")
    y = y0 - 2

    canv.setFont("Helvetica", font_size)
    line_h = font_size + 2
    for ln in products[:MAX_LINES]:
        y -= line_h
        if y < pad_y + 2:
            break
        canv.drawString(x0, y, f"• {ln}")

    canv.showPage()
    canv.save()
    buf.seek(0)
    return buf.getvalue()

# ======= PIPELINE =======
def process_pdf_with_products(pdf_bytes: bytes) -> bytes:
    """
    1) Detecta quais páginas são etiquetas e quais são declarações (por palavra-chave).
    2) Separa quadrantes 4->1 para cada tipo.
    3) Casa etiqueta <-> declaração por CEP/telefone/nome (fuzzy).
    4) Extrai ‘produtos’ da declaração.
    5) Cria overlay no rodapé e funde no PDF da etiqueta (sem segunda página).
    """
    # Abrimos com as duas libs
    reader_full = PdfReader(io.BytesIO(pdf_bytes))
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")

    # Coleta de quads
    labels = []  # cada item: dict(page_idx, fitz_rect, pypdf_box, text, feats)
    decls  = []  # idem
    for i in range(len(doc)):
        page_fitz = doc[i]
        page_text = page_fitz.get_text("text")
        is_decl = "declara" in normalize_txt(page_text).lower()  # captura "declaração", "declaracao"
        qf = quadrants_fitz(page_fitz.rect)
        qp = quadrants_pypdf(reader_full.pages[i].mediabox)

        for q_idx, (rect_fitz, box_pdf) in enumerate(zip(qf, qp)):
            # texto por quadrante
            t = page_fitz.get_text("text", clip=rect_fitz) or ""
            item = {
                "page_idx": i,
                "q_idx": q_idx,
                "fitz_rect": rect_fitz,
                "pypdf_box": box_pdf,
                "text": t,
                "feats": extract_features(t),
            }
            if is_decl:
                decls.append(item)
            else:
                labels.append(item)

    # Filtra quadrantes em branco (sobretudo rastro de páginas adicionais)
    if REMOVE_BLANK:
        labels = [it for it in labels if not quad_is_blank_by_raster(doc, it["page_idx"], it["fitz_rect"])]

    # Para cada declaração, também extraímos produtos
    for d in decls:
        d["products"] = extract_products(d["text"])

    # Casa etiqueta x decl
    remaining = decls.copy()
    matches = {}  # key: (page_idx, q_idx) de etiqueta -> dict decl
    for lab in labels:
        best = None
        best_score = -1
        for dc in remaining:
            sc = score_pair(lab["feats"], dc["feats"])
            if sc > best_score:
                best, best_score = dc, sc
        if best is not None and best_score >= 0.55:  # limiar razoável
            matches[(lab["page_idx"], lab["q_idx"])] = best
            remaining.remove(best)  # evita duplicidade

    # Monta saída
    writer = PdfWriter()
    for lab in labels:
        p_src = reader_full.pages[lab["page_idx"]]
        x0, y0, x1, y1 = lab["pypdf_box"]
        p = deepcopy(p_src)
        rect = RectangleObject([x0, y0, x1, y1])
        p.cropbox = rect
        p.mediabox = rect

        # overlay de produtos se encontrou match com lista não vazia
        dec = matches.get((lab["page_idx"], lab["q_idx"]))
        if dec and dec.get("products"):
            w_pt = float(p.mediabox.right) - float(p.mediabox.left)
            h_pt = float(p.mediabox.top) - float(p.mediabox.bottom)
            overlay_bytes = make_overlay_pdf(w_pt, h_pt, dec["products"])
            ov_page = PdfReader(io.BytesIO(overlay_bytes)).pages[0]
            # mescla o overlay na etiqueta
            try:
                p.merge_page(ov_page)  # pypdf ainda expõe merge_page
            except Exception:
                # fallback para versões mais novas (se necessário)
                p.merge_page(ov_page)
        writer.add_page(p)

    out = io.BytesIO()
    writer.write(out)
    out.seek(0)
    return out.getvalue()

# ================== RUN ==================
if uploaded_file is not None:
    try:
        pdf_bytes_in = uploaded_file.getvalue()
        with st.spinner("Processando e montando produtos nas etiquetas..."):
            pdf_bytes_out = process_pdf_with_products(pdf_bytes_in)

        st.success("Pronto! Uma etiqueta por cliente, com produtos no rodapé.")
        st.download_button(
            label="Baixar PDF final",
            data=pdf_bytes_out,
            file_name="etiquetas_com_produtos.pdf",
            mime="application/pdf",
            key="download_main",
        )
    except Exception as e:
        st.error("Não foi possível processar o arquivo. Verifique se é um PDF válido.")
        st.exception(e)
else:
    # centraliza o aviso
    st.markdown(
        """
        <style>
        .info-centered [data-testid="stAlert"]{
            width: 500px !important;
            max-width: 100% !important;
            margin: 0 auto !important;
            border-radius: 12px;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )
    st.markdown('<div class="info-centered">', unsafe_allow_html=True)
    st.info("Faça o upload de um PDF para iniciar o processamento.")
    st.markdown('</div>', unsafe_allow_html=True)
