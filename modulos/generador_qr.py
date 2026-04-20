import qrcode
from io import BytesIO

def generar_qr_producto(codigo: str, descripcion: str, precio: float, tamano_caja: int = 10) -> bytes:
    """
    Genera un código QR con los datos y el tamaño especificado.
    """
    texto_qr = f"COD: {codigo}\nDESC: {descripcion}\nPRECIO: ${precio:,.2f}"
    
    qr = qrcode.QRCode(
        version=1,
        error_correction=qrcode.constants.ERROR_CORRECT_L, # type: ignore
        box_size=tamano_caja,
        border=4,
    )
    qr.add_data(texto_qr)
    qr.make(fit=True)

    img = qr.make_image(fill_color="black", back_color="white")
    
    buffer = BytesIO()
    img.save(buffer, format="PNG") # type: ignore
    
    return buffer.getvalue()