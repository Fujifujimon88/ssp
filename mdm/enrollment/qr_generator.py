"""QRコード生成（エンロールURL用）"""
import io

import qrcode
from qrcode.constants import ERROR_CORRECT_M


def generate_qr_png(url: str, box_size: int = 10, border: int = 4) -> bytes:
    """エンロールURLのQRコードをPNG bytesで返す"""
    qr = qrcode.QRCode(
        version=None,
        error_correction=ERROR_CORRECT_M,
        box_size=box_size,
        border=border,
    )
    qr.add_data(url)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    return buf.read()
