"""Preprocesado de fotos de documentos (facturas, remitos) antes de la IA."""
import cv2
import numpy as np
from PIL import Image


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
