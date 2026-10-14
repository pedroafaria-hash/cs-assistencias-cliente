"""qr.py — gera a imagem QR (PNG) de um link, 10/10/2026, Fase 2 (QR code
para o cliente). Função única, pequena, só para não ficar código de
imagem espalhado pelo app.py."""
import io

import qrcode


def gerar_qr_png(link: str) -> bytes:
    imagem = qrcode.make(link)
    buffer = io.BytesIO()
    imagem.save(buffer, format="PNG")
    return buffer.getvalue()
