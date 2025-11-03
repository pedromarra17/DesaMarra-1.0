def process_mode_packing(pdf_bytes: bytes, diagnostic=False):
    """
    Empacotamento (2 colunas por página) -> 1 página 10x15 cm (somente vetor):
      • Recorta ETIQUETA (em cima) com trim de espaços brancos.
      • Recorta LISTA (embaixo), remove coluna '#', e aperta à direita (remove espaço morto).
      • Ajuste novo: ambos TENTAM usar a largura total; se estourar a altura, aplica-se um
        fator de compressão único (mantendo proporção) para caber em 10x15.
    """
    H_PAD = 0.0
    V_PAD = 0.0

    src = fitz.open(stream=pdf_bytes, filetype="pdf")
    out_doc = fitz.open()
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

            # 1) Topo "Checklist..."
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

            # 3) Remover coluna "#": usar borda esquerda da coluna PRODUTO
            header_band  = fitz.Rect(col.x0, table_head_y - 10, col.x1, table_head_y + 24)
            header_words = pg.get_text("words", clip=header_band)
            left_x, hash_right = None, None
            for x0,y0,x1,y1,w,*_ in header_words:
                t = norm_heavy(str(w)).upper().strip()
                if "PRODUTO" in t and left_x is None: left_x = x0
                if t in {"#", "Nº", "NO"}: hash_right = x1
            if left_x is None and hash_right is not None: left_x = hash_right + 6
            if left_x is None: left_x = col.x0 + 26

            # 4) Áreas brutas
            label_raw = fitz.Rect(col.x0, col.y0, col.x1, max(col.y0+20, checklist_top-4))
            list_raw  = fitz.Rect(left_x, table_head_y-1, col.x1, col.y1-6)

            # 5) BBox por blocks
            label_clip_blk = content_bbox(pg, label_raw)
            list_clip      = content_bbox(pg, list_raw)

            # 6) Trim mais apertado na ETIQUETA (remove bordas brancas incluindo códigos/QR)
            label_clip = trim_bbox_by_raster(src, pi, label_clip_blk, dpi=220, white=245, cov=0.997, pad_pt=1.0)

            # 7) Aperta a borda direita da LISTA (remove espaço morto após 'Quantidade')
            list_clip = tighten_right_edge(pg, src, pi, list_clip, pad_pt=2.0)

            # 8) Etiqueta obrigatória: se branca, pula a coluna
            if REMOVE_BLANK and quad_is_blank_by_raster(src, pi, label_clip):
                continue

            # 9) Mede e tenta usar a largura total para AMBOS
            lw, lh = label_clip.width, label_clip.height
            if quad_is_blank_by_raster(src, pi, list_clip):
                use_list = False; tw, th = lw, 0
            else:
                use_list = True;  tw, th = list_clip.width, list_clip.height

            content_w = TARGET_W_PT - 2*H_PAD
            content_h = TARGET_H_PT - 2*V_PAD

            # escalas independentes para ENCHER a largura
            sL = content_w / lw
            sT = content_w / tw if use_list else sL

            # alturas se ambos preencherem a largura
            HL = lh * sL
            HT = th * sT if use_list else 0.0
            Hsum_full = HL + HT

            # se couber, ótimo; se não, aplica fator comum 'c'
            if Hsum_full <= content_h or Hsum_full == 0:
                c = 1.0
            else:
                c = content_h / Hsum_full

            # dimensões finais após possível compressão
            Lw, Lh = content_w * c, HL * c
            Tw, Th = (content_w * c, HT * c) if use_list else (0, 0)

            # 10) cria página 10x15 e desenha vetor
            pg_new = out_doc.new_page(width=TARGET_W_PT, height=TARGET_H_PT)
            x = (TARGET_W_PT - Lw) / 2  # os dois terão mesma largura final
            y = V_PAD

            # etiqueta
            pg_new.show_pdf_page(
                fitz.Rect(x, y, x+Lw, y+Lh),
                src, pi, clip=label_clip
            )
            y += Lh

            # lista
            if use_list and Th > 0.5:
                pg_new.show_pdf_page(
                    fitz.Rect(x, y, x+Tw, y+Th),
                    src, pi, clip=list_clip
                )

            if diagnostic:
                diag_rows.append({
                    "src_page": pi+1, "col": ci,
                    "sL": round(sL,3), "sT": round(sT,3), "c": round(c,3),
                    "Lw": round(Lw,1), "Lh": round(Lh,1),
                    "Tw": round(Tw,1), "Th": round(Th,1),
                })

    buf = io.BytesIO()
    out_doc.save(buf, garbage=4, deflate=True)
    out_doc.close()
    buf.seek(0)
    return buf.getvalue(), pd.DataFrame(diag_rows)
