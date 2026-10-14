"""
pdf_lite.py — gerador de PDF, sem NENHUMA dependência externa (só a
biblioteca padrão do Python), usado pelas Atas de Reunião (documentos.py).

Substitui o `fpdf2` (usado numa 1ª versão desta funcionalidade) — o Pedro
não consegue correr `pip install` sozinho no PC dele sem instruções, e isto
já tinha acontecido antes com `plotly`/`anthropic`. Escrever o PDF à mão
(objetos PDF 1.4 clássicos, tipos de letra padrão Helvetica) elimina de vez
essa fricção: o botão "Guardar Ata"/"Gerar PDF" passa a funcionar sempre,
sem nenhuma instalação extra.

Suporta o que o modelo de "Ata de Reunião de Obra" (12/09/2026, pedido do
Pedro com um PDF de referência) precisa: texto normal/negrito/itálico com
quebra automática, retângulos com contorno/preenchimento (barras cinzentas
de secção, caixas de pontos, checkboxes), linhas, várias páginas, e um
cabeçalho/rodapé repetido em todas as páginas. Os anexos das Atas continuam
só listados pelo nome (nunca embutidos) — mas suporta desenhar UMA imagem
(o logótipo da CS Construtora, pedido em 12/09/2026) via `PdfWriter.image`,
com um descodificador de PNG escrito de raiz (só `struct`/`zlib`, biblioteca
padrão) — ver `_decode_png` abaixo.

Os tipos de letra usados (Helvetica/Helvetica-Bold/Helvetica-Oblique/
Helvetica-BoldOblique) são 4 dos 14 tipos de letra "standard" do PDF —
qualquer leitor de PDF já os tem embutidos, não é preciso embutir nada no
ficheiro. Codificação de texto: WinAnsiEncoding (~ cp1252), que cobre todos
os acentos/cedilhas do português (á, ã, â, à, ç, é, ê, í, ó, ô, õ, ú, ...).
"""
from __future__ import annotations

import struct
import zlib

# Largura de cada carácter (por 1000 unidades de "em"), métricas padrão do
# tipo de letra Helvetica (dados públicos, os mesmos usados por qualquer
# gerador de PDF) — usadas só para quebrar linhas no sítio certo.
_HELVETICA_WIDTHS = {
    " ": 278, "!": 278, '"': 355, "#": 556, "$": 556, "%": 889, "&": 667,
    "'": 191, "(": 333, ")": 333, "*": 389, "+": 584, ",": 278, "-": 333,
    ".": 278, "/": 278, "0": 556, "1": 556, "2": 556, "3": 556, "4": 556,
    "5": 556, "6": 556, "7": 556, "8": 556, "9": 556, ":": 278, ";": 278,
    "<": 584, "=": 584, ">": 584, "?": 556, "@": 1015,
    "A": 667, "B": 667, "C": 722, "D": 722, "E": 667, "F": 611, "G": 778,
    "H": 722, "I": 278, "J": 500, "K": 667, "L": 556, "M": 833, "N": 722,
    "O": 778, "P": 667, "Q": 778, "R": 722, "S": 667, "T": 611, "U": 722,
    "V": 667, "W": 944, "X": 667, "Y": 667, "Z": 611,
    "[": 278, "\\": 278, "]": 278, "^": 469, "_": 556, "`": 333,
    "a": 556, "b": 556, "c": 500, "d": 556, "e": 556, "f": 278, "g": 556,
    "h": 556, "i": 222, "j": 222, "k": 500, "l": 222, "m": 833, "n": 556,
    "o": 556, "p": 556, "q": 556, "r": 333, "s": 500, "t": 278, "u": 556,
    "v": 500, "w": 722, "x": 500, "y": 500, "z": 500,
    "{": 334, "|": 260, "}": 334, "~": 584,
}
_DEFAULT_WIDTH = 556  # usado para acentos/letras fora da tabela acima

PAGE_W = 595.28
PAGE_H = 841.89
MARGIN_L = 50.0
MARGIN_R = 50.0
MARGIN_T = 50.0
MARGIN_B = 40.0
CONTENT_W = PAGE_W - MARGIN_L - MARGIN_R

# style -> (chave da tabela de larguras, /Fn usado no conteúdo)
_STYLES = {
    "normal": ("n", "/F1"),
    "bold": ("b", "/F2"),
    "italic": ("n", "/F3"),
    "bolditalic": ("b", "/F4"),
}
_BOLD_SCALE = 1.08  # aproximação: Helvetica-Bold é ligeiramente mais largo

_CINZA_CLARO = (0.90, 0.90, 0.90)
_CINZA_HEADER = (0.83, 0.83, 0.83)
_PRETO = (0, 0, 0)


def _char_width(ch: str, bold: bool) -> float:
    w = _HELVETICA_WIDTHS.get(ch, _DEFAULT_WIDTH)
    if bold:
        w *= _BOLD_SCALE
    return w


def text_width_pt(text: str, size: float, style: str = "normal") -> float:
    bold = _STYLES.get(style, _STYLES["normal"])[0] == "b"
    total = sum(_char_width(c, bold) for c in (text or ""))
    return total / 1000.0 * size


def _wrap_paragraph(text: str, size: float, bold: bool, max_width: float) -> list:
    """Quebra `text` em linhas que cabem em `max_width` pontos, respeitando
    quebras de linha já existentes (parágrafos vazios viram linhas em
    branco). Uma palavra sozinha maior do que a largura disponível nunca
    fica presa num ciclo infinito — é colocada na sua própria linha na
    mesma, mesmo que ultrapasse ligeiramente a margem (caso raríssimo)."""
    linhas = []
    for paragrafo in (text or "").split("\n"):
        if paragrafo == "":
            linhas.append("")
            continue
        atual = ""
        for palavra in paragrafo.split(" "):
            candidata = palavra if atual == "" else f"{atual} {palavra}"
            if atual == "" or _text_width_pt_bold(candidata, size, bold) <= max_width:
                atual = candidata
            else:
                linhas.append(atual)
                atual = palavra
        if atual:
            linhas.append(atual)
    return linhas


def _text_width_pt_bold(text: str, size: float, bold: bool) -> float:
    total = sum(_char_width(c, bold) for c in text)
    return total / 1000.0 * size


def wrap_lines(text: str, size: float, style: str, max_width: float) -> list:
    bold = _STYLES.get(style, _STYLES["normal"])[0] == "b"
    return _wrap_paragraph(text, size, bold, max_width)


def _pdf_escape(text: str) -> bytes:
    """Codifica em WinAnsi (~cp1252, cobre os acentos do português) e
    escapa os carateres especiais de uma string literal PDF `(...)`."""
    raw = (text or "").encode("cp1252", errors="replace")
    raw = raw.replace(b"\\", b"\\\\").replace(b"(", b"\\(").replace(b")", b"\\)")
    return raw


def _fmt(n: float) -> str:
    return f"{n:.2f}"


def image_size(png_bytes: bytes) -> tuple:
    """Devolve (largura, altura) em píxeis de um PNG — útil para calcular a
    altura certa a partir da largura desejada (ou vice-versa), mantendo a
    proporção original, antes de chamar `PdfWriter.image`."""
    img = _decode_png(png_bytes)
    return img["width"], img["height"]


# ---------------------------------------------------------------------------
# Descodificador de PNG (só `struct`/`zlib`, biblioteca padrão) — o
# suficiente para desenhar um logótipo num PDF sem nenhuma dependência
# externa (Pillow, etc.). Suporta os casos comuns de um logótipo exportado
# de qualquer editor de imagem: 8 bits por canal, sem entrelaçamento
# (interlace), cor de tipo 0 (cinza), 2 (RGB), 3 (paleta) 4 (cinza+alfa) ou
# 6 (RGBA). Não suporta 16 bits por canal nem PNG entrelaçado (Adobe 7)
# — nesses casos levanta `ValueError` com uma mensagem clara, em vez de
# desenhar uma imagem errada; quem chama (`documentos.py`) apanha o erro e
# simplesmente não desenha o logótipo, sem rebentar a geração do PDF.
def _paeth_predictor(a: int, b: int, c: int) -> int:
    p = a + b - c
    pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
    if pa <= pb and pa <= pc:
        return a
    if pb <= pc:
        return b
    return c


def _png_chunks(data: bytes):
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError("ficheiro não é um PNG válido (assinatura errada)")
    pos = 8
    while pos < len(data):
        (length,) = struct.unpack(">I", data[pos:pos + 4])
        tipo = data[pos + 4:pos + 8]
        conteudo = data[pos + 8:pos + 8 + length]
        yield tipo, conteudo
        pos += 8 + length + 4  # + 4 do CRC, que não é validado (só leitura)


def _decode_png(data: bytes) -> dict:
    """Descodifica um PNG para pixels RGB (+ alfa opcional) em bruto.
    Devolve {"width", "height", "rgb": bytes (W*H*3), "alpha": bytes
    (W*H) ou None}."""
    ihdr = None
    palette = None
    trns = None
    idat = bytearray()
    for tipo, conteudo in _png_chunks(data):
        if tipo == b"IHDR":
            ihdr = conteudo
        elif tipo == b"PLTE":
            palette = conteudo
        elif tipo == b"tRNS":
            trns = conteudo
        elif tipo == b"IDAT":
            idat += conteudo
        elif tipo == b"IEND":
            break
    if ihdr is None:
        raise ValueError("PNG sem IHDR")
    width, height, bitdepth, colortype, _compressao, _filtro, interlace = struct.unpack(">IIBBBBB", ihdr)
    if bitdepth != 8:
        raise ValueError(f"PNG com {bitdepth} bits/canal não suportado (só 8 bits)")
    if interlace != 0:
        raise ValueError("PNG entrelaçado (interlace) não suportado")

    canais_por_tipo = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}
    if colortype not in canais_por_tipo:
        raise ValueError(f"PNG com tipo de cor {colortype} não suportado")
    canais = canais_por_tipo[colortype]

    raw = zlib.decompress(bytes(idat))
    row_bytes = width * canais
    linhas = []
    anterior = bytes(row_bytes)
    pos = 0
    for _ in range(height):
        tipo_filtro = raw[pos]
        pos += 1
        atual = bytearray(raw[pos:pos + row_bytes])
        pos += row_bytes
        if tipo_filtro == 0:
            pass
        elif tipo_filtro == 1:  # Sub
            for i in range(row_bytes):
                a = atual[i - canais] if i >= canais else 0
                atual[i] = (atual[i] + a) & 0xFF
        elif tipo_filtro == 2:  # Up
            for i in range(row_bytes):
                atual[i] = (atual[i] + anterior[i]) & 0xFF
        elif tipo_filtro == 3:  # Average
            for i in range(row_bytes):
                a = atual[i - canais] if i >= canais else 0
                b = anterior[i]
                atual[i] = (atual[i] + (a + b) // 2) & 0xFF
        elif tipo_filtro == 4:  # Paeth
            for i in range(row_bytes):
                a = atual[i - canais] if i >= canais else 0
                b = anterior[i]
                c = anterior[i - canais] if i >= canais else 0
                atual[i] = (atual[i] + _paeth_predictor(a, b, c)) & 0xFF
        else:
            raise ValueError(f"PNG com tipo de filtro {tipo_filtro} desconhecido")
        linhas.append(bytes(atual))
        anterior = atual

    pixels = b"".join(linhas)
    rgb = bytearray(width * height * 3)
    alpha = None

    if colortype == 2:  # RGB
        rgb[:] = pixels
    elif colortype == 6:  # RGBA
        alpha = bytearray(width * height)
        for p in range(width * height):
            rgb[p * 3:p * 3 + 3] = pixels[p * 4:p * 4 + 3]
            alpha[p] = pixels[p * 4 + 3]
    elif colortype == 0:  # Cinza
        for p in range(width * height):
            v = pixels[p]
            rgb[p * 3:p * 3 + 3] = bytes((v, v, v))
    elif colortype == 4:  # Cinza + alfa
        alpha = bytearray(width * height)
        for p in range(width * height):
            v = pixels[p * 2]
            rgb[p * 3:p * 3 + 3] = bytes((v, v, v))
            alpha[p] = pixels[p * 2 + 1]
    elif colortype == 3:  # Paleta (índice -> PLTE)
        if palette is None:
            raise ValueError("PNG com paleta (colortype 3) sem chunk PLTE")
        if trns:
            alpha = bytearray(width * height)
        for p in range(width * height):
            idx = pixels[p]
            rgb[p * 3:p * 3 + 3] = palette[idx * 3:idx * 3 + 3]
            if trns:
                alpha[p] = trns[idx] if idx < len(trns) else 255

    return {"width": width, "height": height, "rgb": bytes(rgb), "alpha": bytes(alpha) if alpha else None}


class PdfWriter:
    """Gera um PDF com texto (normal/negrito/itálico, quebra automática),
    retângulos (com contorno e/ou preenchimento) e linhas — o suficiente
    para desenhar barras de secção, tabelas com grelha e checkboxes, sem
    nenhuma dependência externa."""

    def __init__(self, on_new_page=None, landscape: bool = False,
                 margin_l: float = None, margin_r: float = None,
                 margin_t: float = None, margin_b: float = None):
        """`on_new_page`, se indicado, é chamado como `on_new_page(self,
        pagina_idx)` logo após criar a página 0 e outra vez sempre que uma
        página nova é aberta (automaticamente por `ensure_space`/`add_text`,
        ou explicitamente por `new_page`) — usado para desenhar um
        cabeçalho repetido (marca + nº da ata) em todas as páginas.

        `landscape=True` (13/09/2026, pedido pela impressão da tabela
        "Centros Custo" — larga demais para caber em retrato) roda a página
        (troca largura/altura); `margin_l/r/t/b`, se indicados, substituem as
        margens por omissão (úteis para tabelas largas com muitas colunas).
        Estes parâmetros ficam guardados como atributos DE INSTÂNCIA
        (`self.page_w/page_h/margin_*/content_w`) — as constantes do módulo
        (PAGE_W, MARGIN_L, ...) nunca são alteradas, por isso o resto do
        módulo (Atas/Bams, que leem essas constantes diretamente) continua
        exatamente igual, mesmo depois desta alteração."""
        self.page_w, self.page_h = (PAGE_H, PAGE_W) if landscape else (PAGE_W, PAGE_H)
        self.margin_l = MARGIN_L if margin_l is None else margin_l
        self.margin_r = MARGIN_R if margin_r is None else margin_r
        self.margin_t = MARGIN_T if margin_t is None else margin_t
        self.margin_b = MARGIN_B if margin_b is None else margin_b
        self.content_w = self.page_w - self.margin_l - self.margin_r
        self.pages = [[]]  # cada página: lista de ops (tuplos, ver _op_*)
        self.y = self.page_h - self.margin_t
        self._on_new_page = on_new_page
        self._images = []  # imagens descodificadas (ver `image`/`output`)
        if on_new_page:
            on_new_page(self, 0)

    # -- gestão de páginas ---------------------------------------------
    def new_page(self):
        self.pages.append([])
        self.y = self.page_h - self.margin_t
        if self._on_new_page:
            self._on_new_page(self, len(self.pages) - 1)

    def ensure_space(self, altura: float):
        if self.y - altura < self.margin_b:
            self.new_page()

    @property
    def n_pages(self) -> int:
        return len(self.pages)

    # -- primitivas de baixo nível (posição absoluta) -------------------
    def text_at(self, x: float, y: float, text: str, size: float = 11, style: str = "normal", page: int = -1):
        _, fonte_pdf = _STYLES.get(style, _STYLES["normal"])
        self.pages[page].append(("text", x, y, fonte_pdf, size, text or ""))

    def rect(self, x: float, y: float, w: float, h: float, fill=None, stroke=_PRETO, line_width: float = 0.75, page: int = -1):
        """(x, y) = canto inferior esquerdo do retângulo (convenção PDF)."""
        self.pages[page].append(("rect", x, y, w, h, fill, stroke, line_width))

    def line(self, x1: float, y1: float, x2: float, y2: float, width: float = 0.75, color=_PRETO, page: int = -1):
        self.pages[page].append(("line", x1, y1, x2, y2, width, color))

    # -- primitivas de alto nível (fluxo automático, no cursor self.y) --
    def add_text(self, text: str, size: float = 11, style: str = "normal", space_after: float = 0.0):
        """Escreve `text` (quebrando automaticamente para caber na largura
        da página), avançando para a página seguinte sempre que necessário."""
        leading = size * 1.32
        if text:
            for linha in wrap_lines(text, size, style, self.content_w):
                self.ensure_space(leading)
                self.text_at(self.margin_l, self.y, linha, size, style)
                self.y -= leading
        if space_after:
            self.y -= space_after
            if self.y < self.margin_b:
                self.new_page()

    def paragraph_in_box(self, x: float, y: float, width: float, text: str, size: float, style: str, leading: float = None) -> float:
        """Escreve um parágrafo (com quebra) a partir de (x, y), avançando
        para novas páginas se preciso. Devolve o y final (depois da última
        linha) — não mexe em self.y (quem chama decide o que fazer com o
        espaço à volta, útil para caixas com várias colunas lado a lado)."""
        leading = leading or size * 1.32
        cursor_y = y
        for linha in wrap_lines(text, size, style, width):
            if cursor_y - leading < self.margin_b:
                self.new_page()
                cursor_y = self.y
            self.text_at(x, cursor_y, linha, size, style)
            cursor_y -= leading
        return cursor_y

    def secao_barra(self, titulo: str, altura: float = 20.0, size: float = 10.5):
        """Barra cinzenta de título de secção (ex.: "IDENTIFICAÇÃO DA OBRA E
        DOS INTERVENIENTES") — mesmo estilo visual do modelo de referência."""
        self.ensure_space(altura + 6)
        self.rect(self.margin_l, self.y - altura, self.content_w, altura, fill=_CINZA_HEADER, stroke=None)
        self.text_at(self.margin_l + 8, self.y - altura + (altura - size) / 2 + 2, titulo, size, "bold")
        self.y -= altura + 6

    def checkbox(self, x: float, y: float, marcado: bool, tam: float = 9.0):
        self.rect(x, y, tam, tam, fill=None, stroke=_PRETO, line_width=0.9)
        if marcado:
            self.line(x + 1.5, y + 1.5, x + tam - 1.5, y + tam - 1.5, width=1.1)
            self.line(x + 1.5, y + tam - 1.5, x + tam - 1.5, y + 1.5, width=1.1)

    def image(self, x: float, y: float, w: float, h: float, png_bytes: bytes, page: int = -1):
        """Desenha um PNG (x, y = canto inferior esquerdo, convenção PDF;
        w/h = largura/altura em pontos). Descodifica `png_bytes` (bytes do
        ficheiro .png, lidos do disco por quem chama) com `_decode_png` —
        sem nenhuma dependência externa — e embute os pixels em bruto no
        PDF (com máscara de transparência/SMask, se o PNG tiver canal
        alfa)."""
        img = _decode_png(png_bytes)
        self._images.append(img)
        img_idx = len(self._images) - 1
        self.pages[page].append(("image", x, y, w, h, img_idx))

    # -- geração final ---------------------------------------------------
    def output(self) -> bytes:
        n_paginas = len(self.pages)
        obj_pagina0 = 3
        obj_stream0 = obj_pagina0 + n_paginas
        obj_fonte0 = obj_stream0 + n_paginas
        obj_seguinte = obj_fonte0 + 4

        corpo = {}

        corpo[1] = b"<< /Type /Catalog /Pages 2 0 R >>"
        kids = " ".join(f"{obj_pagina0 + i} 0 R" for i in range(n_paginas))
        corpo[2] = f"<< /Type /Pages /Kids [{kids}] /Count {n_paginas} >>".encode("ascii")

        # Imagens (XObjects) — cada uma com um /SMask próprio (objeto
        # cinzento à parte) quando o PNG original tinha canal alfa, para a
        # transparência aparecer corretamente sobre o fundo branco da
        # página. Os dados são comprimidos aqui (FlateDecode) a partir dos
        # pixels já descodificados por `_decode_png` — nunca reaproveitamos
        # o IDAT original do PNG, porque já foi "des-filtrado" linha a linha.
        img_obj_nums = []
        for img in self._images:
            smask_num = None
            if img["alpha"] is not None:
                smask_num = obj_seguinte
                obj_seguinte += 1
                stream_smask = zlib.compress(img["alpha"])
                corpo[smask_num] = (
                    f"<< /Type /XObject /Subtype /Image /Width {img['width']} /Height {img['height']} "
                    f"/ColorSpace /DeviceGray /BitsPerComponent 8 /Filter /FlateDecode "
                    f"/Length {len(stream_smask)} >>\nstream\n".encode("ascii")
                    + stream_smask + b"\nendstream"
                )
            img_num = obj_seguinte
            obj_seguinte += 1
            stream_img = zlib.compress(img["rgb"])
            smask_ref = f" /SMask {smask_num} 0 R" if smask_num is not None else ""
            corpo[img_num] = (
                f"<< /Type /XObject /Subtype /Image /Width {img['width']} /Height {img['height']} "
                f"/ColorSpace /DeviceRGB /BitsPerComponent 8 /Filter /FlateDecode{smask_ref} "
                f"/Length {len(stream_img)} >>\nstream\n".encode("ascii")
                + stream_img + b"\nendstream"
            )
            img_obj_nums.append(img_num)

        xobject_res = ""
        if img_obj_nums:
            entradas = " ".join(f"/Im{i} {num} 0 R" for i, num in enumerate(img_obj_nums))
            xobject_res = f" /XObject << {entradas} >>"

        recursos = (
            f"<< /Font << /F1 {obj_fonte0} 0 R /F2 {obj_fonte0 + 1} 0 R "
            f"/F3 {obj_fonte0 + 2} 0 R /F4 {obj_fonte0 + 3} 0 R >>{xobject_res} >>"
        )
        for i in range(n_paginas):
            pagina_num = obj_pagina0 + i
            stream_num = obj_stream0 + i
            corpo[pagina_num] = (
                f"<< /Type /Page /Parent 2 0 R "
                f"/MediaBox [0 0 {self.page_w:.2f} {self.page_h:.2f}] "
                f"/Resources {recursos} /Contents {stream_num} 0 R >>"
            ).encode("ascii")

            partes = []
            for op in self.pages[i]:
                tipo = op[0]
                if tipo == "text":
                    _, x, y, fonte_pdf, size, texto = op
                    # A cor de preenchimento (rg) é um estado do content
                    # stream que persiste entre operações — sem repor a
                    # preto aqui, texto desenhado depois de uma barra
                    # cinzenta (secao_barra) herdava o cinzento do fill.
                    partes.append(b"0 0 0 rg")
                    partes.append(b"BT")
                    partes.append(f"{fonte_pdf} {size:g} Tf".encode("ascii"))
                    partes.append(f"1 0 0 1 {_fmt(x)} {_fmt(y)} Tm".encode("ascii"))
                    partes.append(b"(" + _pdf_escape(texto) + b") Tj")
                    partes.append(b"ET")
                elif tipo == "rect":
                    _, x, y, w, h, fill, stroke, lw = op
                    if fill is not None:
                        partes.append(f"{fill[0]:g} {fill[1]:g} {fill[2]:g} rg".encode("ascii"))
                        partes.append(f"{_fmt(x)} {_fmt(y)} {_fmt(w)} {_fmt(h)} re f".encode("ascii"))
                    if stroke is not None:
                        partes.append(f"{stroke[0]:g} {stroke[1]:g} {stroke[2]:g} RG".encode("ascii"))
                        partes.append(f"{lw:g} w".encode("ascii"))
                        partes.append(f"{_fmt(x)} {_fmt(y)} {_fmt(w)} {_fmt(h)} re S".encode("ascii"))
                elif tipo == "line":
                    _, x1, y1, x2, y2, lw, color = op
                    partes.append(f"{color[0]:g} {color[1]:g} {color[2]:g} RG".encode("ascii"))
                    partes.append(f"{lw:g} w".encode("ascii"))
                    partes.append(f"{_fmt(x1)} {_fmt(y1)} m {_fmt(x2)} {_fmt(y2)} l S".encode("ascii"))
                elif tipo == "image":
                    _, x, y, w, h, img_idx = op
                    partes.append(b"q")
                    partes.append(f"{_fmt(w)} 0 0 {_fmt(h)} {_fmt(x)} {_fmt(y)} cm".encode("ascii"))
                    partes.append(f"/Im{img_idx} Do".encode("ascii"))
                    partes.append(b"Q")
            conteudo = b"\n".join(partes)
            corpo[stream_num] = (
                f"<< /Length {len(conteudo)} >>\nstream\n".encode("ascii")
                + conteudo
                + b"\nendstream"
            )

        for idx, nome_base in enumerate(
            ["Helvetica", "Helvetica-Bold", "Helvetica-Oblique", "Helvetica-BoldOblique"]
        ):
            corpo[obj_fonte0 + idx] = (
                f"<< /Type /Font /Subtype /Type1 /BaseFont /{nome_base} "
                f"/Encoding /WinAnsiEncoding >>"
            ).encode("ascii")

        buf = bytearray()
        buf += b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n"
        offsets = {}
        for obj_num in sorted(corpo.keys()):
            offsets[obj_num] = len(buf)
            buf += f"{obj_num} 0 obj\n".encode("ascii")
            buf += corpo[obj_num]
            buf += b"\nendobj\n"

        xref_offset = len(buf)
        max_obj = max(offsets.keys())
        buf += f"xref\n0 {max_obj + 1}\n".encode("ascii")
        buf += b"0000000000 65535 f \n"
        for i in range(1, max_obj + 1):
            off = offsets.get(i, 0)
            buf += f"{off:010d} 00000 n \n".encode("ascii")
        buf += b"trailer\n"
        buf += f"<< /Size {max_obj + 1} /Root 1 0 R >>\n".encode("ascii")
        buf += b"startxref\n"
        buf += f"{xref_offset}\n".encode("ascii")
        buf += b"%%EOF"
        return bytes(buf)
