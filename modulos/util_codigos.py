"""Limpieza de códigos leídos por OCR / IA."""
import re


def normalizar_codigo_ocr(codigo):
    c = str(codigo or "").strip().upper()
    c = c.replace("/", "-").replace(" ", "")
    c = re.sub(r"[^A-Z0-9\-_\.]", "", c)
    return c


def normalizar_codigos_en_articulos(articulos):
    """Aplica limpieza a la lista de artículos (in-place). Retorna cantidad modificada."""
    cambios = 0
    for art in articulos or []:
        if not isinstance(art, dict):
            continue
        for campo in ("codigo", "codigo_proveedor"):
            if campo not in art:
                continue
            orig = str(art.get(campo, "")).strip()
            nuevo = normalizar_codigo_ocr(orig)
            if nuevo and nuevo != orig:
                art[campo] = nuevo
                if campo == "codigo":
                    art["codigo_proveedor"] = nuevo
                cambios += 1
    return cambios
