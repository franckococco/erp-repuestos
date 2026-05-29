"""Logo y rutas de assets de marca."""
import os


def ruta_logo_hafid():
    """Busca logo_hafid en modulos/ y en la raíz del proyecto."""
    modulos_dir = os.path.dirname(os.path.abspath(__file__))
    raiz = os.path.dirname(modulos_dir)
    for carpeta in (modulos_dir, raiz):
        for ext in ("png", "jpg", "jpeg", "webp"):
            path = os.path.join(carpeta, f"logo_hafid.{ext}")
            if os.path.isfile(path):
                return path
    return None
