"""Búsqueda flexible en inventario — plurales y variantes (bujes ↔ buje)."""
import unicodedata
import re


def normalizar_para_busqueda(texto):
    if not texto:
        return ""
    t = "".join(c for c in unicodedata.normalize("NFD", str(texto)) if unicodedata.category(c) != "Mn")
    return re.sub(r"[^a-z0-9\s]", "", t.lower())


def variantes_palabra(palabra):
    """Genera variantes singular/plural simples en español."""
    p = palabra.strip().lower()
    if len(p) < 2:
        return {p}
    out = {p}
    if p.endswith("es") and len(p) > 3:
        out.add(p[:-1])   # bujes → buje
        out.add(p[:-2])   # repuestos → repuesto
    if p.endswith("s") and not p.endswith("ss"):
        out.add(p[:-1])
    if not p.endswith("s"):
        out.add(p + "s")
        if p.endswith("e"):
            out.add(p + "s")
        else:
            out.add(p + "es")
    return out


def termino_en_texto(termino, texto_normalizado):
    for v in variantes_palabra(termino):
        if v and v in texto_normalizado:
            return True
    return False


def filtrar_por_busqueda(items, termino_busqueda, extraer_texto):
    """Filtra items si todas las palabras del término coinciden (con flexión plural)."""
    if not termino_busqueda:
        return items
    terminos = [t for t in normalizar_para_busqueda(termino_busqueda).split() if len(t) >= 2]
    if not terminos:
        return items
    resultado = []
    for item in items or []:
        if not isinstance(item, dict):
            continue
        texto_norm = normalizar_para_busqueda(extraer_texto(item))
        if all(termino_en_texto(t, texto_norm) for t in terminos):
            resultado.append(item)
    return resultado


def texto_item_inventario(item):
    return (
        f"{item.get('codigo', '')} {item.get('descripcion', '')} {item.get('vehiculo', '')} "
        f"{item.get('vehiculos_busqueda', '')} {item.get('marca', '')} {item.get('id', '')} "
        f"{item.get('proveedor', '')}"
    )
