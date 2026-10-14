"""
pdf_cliente.py — Impressão "Listagem de Tópicos" em PDF, dentro da app
cloud (Fase 4b, 14/10/2026), pedido do Pedro: "exportar listagem de
tarefas em PDF, como a impressão na app de pc".

Versão enxuta, independente, do mesmo gerador já usado e validado em
`pdf.py` (app local) — usa o mesmo `pdf_lite.py` (sem dependências
externas, cópia verbatim), com a MESMA tabela resumida (Nº/Obra/Cliente/
Tópico/Estado/Data, sem Descrição/Observações) e o mesmo logótipo, para o
PDF gerado pelo DTO no telemóvel ficar visualmente igual ao que o Pedro já
gera no PC. Não importa nada de `db_assist.py`/`pdf.py` (a app cloud nunca
depende da app local) — por isso esta cópia existe, com os nomes de campo
que `db_cloud.listar_itens_leitura_por_diretor` já devolve.
"""
import os
from datetime import datetime

import pdf_lite

_PRETO = (0, 0, 0)
_CINZA_LINHA = (0.55, 0.55, 0.55)
_COR_PENDENTE = (0.86, 0.16, 0.16)       # vermelho — ainda por resolver
_COR_ASSUMIDO = (0.0, 0.40, 0.75)        # azul — alguém já está a tratar
_COR_EM_ORCAMENTO = (0.85, 0.55, 0.0)    # laranja — em orçamentação
_COR_RESOLVIDO = (0.0, 0.45, 0.16)       # verde — resolvido
_FUNDO_ESTADO = {
    "Pendente": _COR_PENDENTE,
    "Assumido": _COR_ASSUMIDO,
    "Em Orçamento": _COR_EM_ORCAMENTO,
    "Resolvido": _COR_RESOLVIDO,
}

LOGO_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "Logos", "logo2.png")
_logo_cache = {}


def _carregar_logo():
    """Se o ficheiro não existir, o PDF é gerado na mesma, só sem o logótipo."""
    if LOGO_PATH in _logo_cache:
        return _logo_cache[LOGO_PATH]
    resultado = None
    try:
        with open(LOGO_PATH, "rb") as f:
            dados = f.read()
        resultado = (dados, pdf_lite.image_size(dados))
    except Exception:
        resultado = None
    _logo_cache[LOGO_PATH] = resultado
    return resultado


def _truncar(texto: str, largura_max: float, size: float, estilo: str = "normal") -> str:
    texto = str(texto or "")
    if pdf_lite.text_width_pt(texto, size, estilo) <= largura_max:
        return texto
    while texto and pdf_lite.text_width_pt(texto + "…", size, estilo) > largura_max:
        texto = texto[:-1]
    return (texto + "…") if texto else "…"


def _formatar_data_pt(data_criacao: str) -> str:
    """`criado_em` vem de `itens_leitura_cloud` como texto (ISO ou
    'aaaa-mm-dd hh:mm:ss') — devolve só 'dd-mm-aaaa', sem rebentar se vier
    vazia/mal formada (devolve '—' nesse caso)."""
    if not data_criacao:
        return "—"
    try:
        return datetime.strptime(str(data_criacao)[:10], "%Y-%m-%d").strftime("%d-%m-%Y")
    except ValueError:
        return "—"


def _resumo_estados(itens: list) -> str:
    n_pend = sum(1 for i in itens if i.get("estado") == "Pendente")
    n_assum = sum(1 for i in itens if i.get("estado") == "Assumido")
    n_orc = sum(1 for i in itens if i.get("estado") == "Em Orçamento")
    n_resolv = sum(1 for i in itens if i.get("estado") == "Resolvido")
    return (
        f"{len(itens)} tópico(s): {n_pend} pendente(s), {n_assum} assumido(s), "
        f"{n_orc} em orçamento, {n_resolv} resolvido(s)"
    )


def _gerar_pdf_listagem_resumida(titulo: str, linhas_contexto: list, itens: list) -> bytes:
    """Tabela resumida — Nº | Obra | Cliente | Tópico | Estado | Data —
    mesmo layout de `pdf.gerar_pdf_listagem_por_fornecedor` (app local),
    sempre com a coluna Obra (o DTO pode ter assistências em mais do que
    uma obra)."""
    margin_l, margin_r, margin_t, margin_b = 42.0, 42.0, 46.0, 40.0
    content_w = pdf_lite.PAGE_W - margin_l - margin_r
    _logo = _carregar_logo()

    def _on_new_page(writer, pagina_idx):
        titulo_x = margin_l
        if pagina_idx == 0 and _logo:
            dados_logo, (lw, lh) = _logo
            logo_h = 26.0
            logo_w = logo_h * (lw / lh) if lh else logo_h
            try:
                writer.image(margin_l, writer.y - logo_h, logo_w, logo_h, dados_logo)
                titulo_x = margin_l + logo_w + 10
            except Exception:
                titulo_x = margin_l
        writer.text_at(titulo_x, writer.y, titulo, 15, "bold")
        writer.y -= 19
        for linha in linhas_contexto:
            writer.text_at(titulo_x, writer.y, linha, 9.5, "bold")
            writer.y -= 13
        writer.y -= 3
        writer.text_at(margin_l, writer.y, datetime.now().strftime("Gerado em %d-%m-%Y %H:%M"), 7.5, "italic")
        writer.y -= 14
        if pagina_idx > 0:
            writer.text_at(margin_l, writer.y, "(continuação)", 8, "italic")
            writer.y -= 12

    w = pdf_lite.PdfWriter(on_new_page=_on_new_page, margin_l=margin_l, margin_r=margin_r,
                            margin_t=margin_t, margin_b=margin_b)

    w.ensure_space(14)
    w.text_at(margin_l, w.y, _resumo_estados(itens), 8.5, "italic")
    w.y -= 16

    titulos = ["Nº", "Obra", "Cliente", "Tópico", "Estado", "Data"]
    col_pesos = [0.05, 0.23, 0.19, 0.23, 0.15, 0.15]
    larguras = [p * content_w for p in col_pesos]
    xs = [margin_l]
    for lrg in larguras:
        xs.append(xs[-1] + lrg)
    pad = 4.0
    header_h = 18.0
    row_h = 16.0
    size_cel = 8.0

    def _linha_cabecalho_tabela():
        w.ensure_space(header_h + 4)
        w.rect(margin_l, w.y - header_h, content_w, header_h, fill=(0.92, 0.92, 0.94), stroke=None)
        for i, t in enumerate(titulos):
            w.text_at(xs[i] + pad, w.y - header_h + 5.5, t, 8.2, "bold")
        w.y -= header_h

    if not itens:
        _linha_cabecalho_tabela()
        w.ensure_space(16)
        w.text_at(margin_l + pad, w.y - 12, "Nenhum tópico encontrado.", 8.5, "italic")
        w.y -= 16
    else:
        _linha_cabecalho_tabela()
        for idx, item in enumerate(itens, start=1):
            if w.y - row_h < w.margin_b:
                w.new_page()
                _linha_cabecalho_tabela()

            if idx % 2 == 0:
                w.rect(margin_l, w.y - row_h, content_w, row_h, fill=(0.975, 0.975, 0.98), stroke=None)

            topo_texto = w.y - 5.5 - size_cel * 0.78
            col = 0
            w.text_at(xs[col] + pad, topo_texto, str(item.get("item_id") or idx), size_cel, "normal")
            col += 1
            obra_txt = f"{item.get('processo') or '—'} ({item.get('obra_assunto') or '—'})"
            w.text_at(xs[col] + pad, topo_texto, _truncar(obra_txt, larguras[col] - 2 * pad, size_cel), size_cel, "normal")
            col += 1
            w.text_at(xs[col] + pad, topo_texto, _truncar(item.get("cliente") or "—", larguras[col] - 2 * pad, size_cel), size_cel, "normal")
            col += 1
            w.text_at(xs[col] + pad, topo_texto, _truncar(item.get("topico"), larguras[col] - 2 * pad, size_cel, "bold"), size_cel, "bold")
            col += 1

            estado = item.get("estado") or "Pendente"
            cor = _FUNDO_ESTADO.get(estado, _PRETO)
            w.rect(xs[col] + pad - 2, w.y - row_h + (row_h - 11) / 2, larguras[col] - 2 * pad + 4, 11, fill=cor, stroke=None)
            w.text_at(xs[col] + pad, w.y - row_h + (row_h - 11) / 2 + 2.7, estado, 6.8, "bold")
            col += 1
            w.text_at(xs[col] + pad, topo_texto, _formatar_data_pt(item.get("criado_em")), size_cel, "normal")

            for x in xs:
                w.line(x, w.y, x, w.y - row_h, width=0.4, color=_CINZA_LINHA)
            w.line(margin_l, w.y - row_h, margin_l + content_w, w.y - row_h, width=0.4, color=_CINZA_LINHA)
            w.y -= row_h

    return w.output()


def gerar_pdf_listagem_dto(nome_diretor: str, itens: list) -> bytes:
    """Pedido do Pedro, Fase 4b: "exportar listagem de tarefas em PDF, como
    a impressão na app de pc". `itens`: o que `db_cloud.
    listar_itens_leitura_por_diretor` devolve (já com processo/obra_assunto/
    cliente), ordenado por Obra e depois Cliente, tal como a impressão "por
    Fornecedor" na app local."""
    itens_ordenados = sorted(
        itens, key=lambda i: (str(i.get("processo") or ""), str(i.get("cliente") or ""), str(i.get("criado_em") or "")),
    )
    linhas_contexto = [f"Diretor de Obra: {nome_diretor}"]
    return _gerar_pdf_listagem_resumida("Listagem de Tópicos — Assistências Pós-Obra", linhas_contexto, itens_ordenados)
