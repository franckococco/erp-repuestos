"""Preprocesado de fotos de documentos (facturas, remitos) antes de la IA."""
import io
import cv2
import numpy as np
from PIL import Image

_TIPOS_IMAGEN = frozenset({".png", ".jpg", ".jpeg", ".webp", ".bmp"})


def imagen_desde_upload(archivo, pagina_pdf: int = 0, escala_pdf: float = 2.0) -> Image.Image:
    """
    Convierte un upload de Streamlit (imagen o PDF) a PIL Image RGB.
    La primera página del PDF se rasteriza para la IA.
    """
    nombre = (getattr(archivo, "name", None) or "").lower()
    raw = archivo.getvalue() if hasattr(archivo, "getvalue") else archivo.read()

    if nombre.endswith(".pdf") or raw[:4] == b"%PDF":
        try:
            import fitz  # PyMuPDF
        except ImportError as e:
            raise ValueError(
                "Para subir PDF instalá PyMuPDF (pip install pymupdf) o usá una imagen JPG/PNG."
            ) from e
        doc = fitz.open(stream=raw, filetype="pdf")
        if doc.page_count == 0:
            raise ValueError("El PDF no tiene páginas.")
        idx = max(0, min(int(pagina_pdf), doc.page_count - 1))
        page = doc[idx]
        mat = fitz.Matrix(escala_pdf, escala_pdf)
        pix = page.get_pixmap(matrix=mat, alpha=False)
        return Image.frombytes("RGB", (pix.width, pix.height), pix.samples)

    suf = "." + nombre.rsplit(".", 1)[-1] if "." in nombre else ""
    if suf and suf not in _TIPOS_IMAGEN:
        raise ValueError(f"Formato no soportado ({nombre}). Usá PDF, PNG o JPG.")

    return Image.open(io.BytesIO(raw)).convert("RGB")


def mejorar_imagen_documento(imagen_pil, max_lado=2400):
    """
    Mejora contraste y nitidez de fotos de factura tomadas con celular.
    Retorna PIL Image RGB lista para Claude Vision.
    """
    img = np.array(imagen_pil.convert("RGB"))
    bgr = cv2.cvtColor(img, cv2.COLOR_RGB2BGR)

    alto, ancho = bgr.shape[:2]
    lado_max = max(alto, ancho)
    if lado_max > max_lado:
        escala = max_lado / lado_max
        bgr = cv2.resize(bgr, None, fx=escala, fy=escala, interpolation=cv2.INTER_AREA)

    lab = cv2.cvtColor(bgr, cv2.COLOR_BGR2LAB)
    canal_l, canal_a, canal_b = cv2.split(lab)
    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
    canal_l = clahe.apply(canal_l)
    bgr = cv2.cvtColor(cv2.merge([canal_l, canal_a, canal_b]), cv2.COLOR_LAB2BGR)

    # Nitidez suave (sin exagerar ruido)
    kernel = np.array([[0, -1, 0], [-1, 5, -1], [0, -1, 0]], dtype=np.float32)
    bgr = cv2.filter2D(bgr, -1, kernel)

    rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
    return Image.fromarray(rgb)
