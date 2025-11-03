def process_mode_packing(pdf_bytes: bytes, diagnostic=False):
    """
    Empacotamento (2 colunas por página) -> saída 10x15 cm:
      • Página 1: ETIQUETA (altura limitada) + início da TABELA (sem coluna '#').
      • Páginas seguintes: continuação da TABELA, mantendo legibilidade (sem encolher demais).
    """
    # ===== Parâmetros de layout =====
    LABEL_MAX_RATIO = 0.45   # etiqueta ocupa no máx. 45% da altura 10x15
    H_PAD = 0.0              # sem margem horizontal (use 0–6 pt se quiser respiro)

    src = fitz.open(stream=pdf_bytes, filetype="pdf")
    diag_rows = []

    def norm_blocks(page, clip):
        out=[]
        for b in page.get_text("blocks", clip=clip):
            x0,y0,x1,y1 = b[0],b[1],b[2],b[3]
            txt = b[4] if len(b)>=5 else ""
            out.append((x0,y0,x1,y1,str(txt)))
        return out

    out_doc = fitz.open()

    for pi in range(len(src)):
        pg = src[pi]; R = pg.rect
        left  = fitz.Rect(R.x0, R.y0, (R.x0+R.x1)/2, R.y1)
        right = fitz.Rect((R.x0+R.x1)/2, R.y0, R.x1, R.y1)

        for ci, col in enumerate([left, right], start=1):
            blocks = norm_blocks(pg, col)

            # 1) topo do "Checklist..."
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

            # 2) cabeçalho da TABELA (SKU + QUANTIDADE)
            table_head_y = None
            for x0,y0,x1,y1,txt in blocks:
                if y0 >= checklist_top - 2:
                    up = norm_heavy(txt).upper()
                    if ("SKU" in up) and ("QUANTIDADE" in up or up.endswith("QUANTIDADE")):
                        table_head_y = y0; break
            if table_head_y is None:
                table_head_y = checklist_top + 28

            # 3) detectar borda esquerda da tabela pela coluna "PRODUTO" (remover coluna '#')
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
                left_x = hash_right + 6
            if left_x is None:
                left_x = col.x0 + 26

            # 4) áreas brutas
            label_raw = fitz.Rect(col.x0, col.y0, col.x1, max(col.y0+20, checklist_top-4))
            list_raw  = fitz.Rect(left_x, table_head_y-1, col.x1, col.y1-6)

            # 5) corta branco
            label_clip = content_bbox(pg, label_raw)
            list_clip  = content_bbox(pg, list_raw)

            # 6) se etiqueta vazia, ignora coluna inteira
            if REMOVE_BLANK and quad_is_blank_by_raster(src, pi, label_clip):
                continue

            # ===== Escalas base (por largura) =====
            content_w = TARGET_W_PT - 2*H_PAD
            lw, lh = label_clip.width, label_clip.height
            tw, th = list_clip.width,  list_clip.height

            # escala de largura para ambos
            label_scale_w = content_w / lw
            list_scale_w  = content_w / tw

            # altura reservada para a etiqueta (cap por ratio)
            label_h_cap = TARGET_H_PT * LABEL_MAX_RATIO
            label_h_render = min(label_h_cap, lh * label_scale_w)

            # altura disponível para a lista na 1ª página
            first_list_space = TARGET_H_PT - label_h_render
            if first_list_space < 24:  # se sobrar quase nada, joga tudo da lista para páginas seguintes
                first_list_space = 0

            # função para "consumir" a lista em janelas (segmentos) que caibam na altura restante
            def yield_list_segments(start_y, avail_height_pt):
                """Gera sub-rects da lista (em coords originais) que cabem em 'avail_height_pt' já mapeados por list_scale_w."""
                if avail_height_pt <= 0:
                    return
                max_src_h = avail_height_pt / list_scale_w  # altura em coords originais que cabe
                cur_y = start_y
                end_y = list_clip.y1
                if max_src_h <= 0:
                    return
                seg_y1 = min(cur_y + max_src_h, end_y)
                if seg_y1 - cur_y >= 1:  # evita segmentos minúsculos
                    yield fitz.Rect(list_clip.x0, cur_y, list_clip.x1, seg_y1)

            # ===== Página 1: etiqueta + início da lista =====
            pg_new = out_doc.new_page(width=TARGET_W_PT, height=TARGET_H_PT)

            # desenha etiqueta (topo)
            pg_new.show_pdf_page(
                fitz.Rect(H_PAD, 0, H_PAD + lw*label_scale_w, label_h_render),
                src, pi, clip=label_clip
            )

            # desenha o primeiro pedaço da lista (se couber algo)
            next_y = list_clip.y0
            if first_list_space > 0 and th > 0 and not quad_is_blank_by_raster(src, pi, list_clip):
                for seg in yield_list_segments(next_y, first_list_space):
                    seg_h_pt = (seg.height) * list_scale_w
                    pg_new.show_pdf_page(
                        fitz.Rect(H_PAD, label_h_render, H_PAD + tw*list_scale_w, label_h_render + seg_h_pt),
                        src, pi, clip=seg
                    )
                    next_y = seg.y1
                    break  # só 1 segmento na primeira página

            # ===== Páginas seguintes: só lista (continuação) =====
            while next_y < list_clip.y1 - 0.5:
                pg_cont = out_doc.new_page(width=TARGET_W_PT, height=TARGET_H_PT)
                avail = TARGET_H_PT  # página inteira para lista
                # cabem vários segmentos por página? na prática, usamos 1 retângulo contínuo por página
                max_src_h = avail / list_scale_w
                seg = fitz.Rect(list_clip.x0, next_y, list_clip.x1, min(next_y + max_src_h, list_clip.y1))
                seg_h_pt = (seg.height) * list_scale_w
                pg_cont.show_pdf_page(
                    fitz.Rect(H_PAD, 0, H_PAD + tw*list_scale_w, seg_h_pt),
                    src, pi, clip=seg
                )
                next_y = seg.y1

            if diagnostic:
                diag_rows.append({
                    "page_src": pi+1, "col": ci,
                    "label_h_cap": round(label_h_cap,1),
                    "label_h_render": round(label_h_render,1),
                    "first_list_space": round(first_list_space,1),
                    "list_total_h": round(th*list_scale_w,1)
                })

    buf = io.BytesIO()
    out_doc.save(buf)
    out_doc.close()
    buf.seek(0)
    return buf.getvalue(), pd.DataFrame(diag_rows)
