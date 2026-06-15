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


def texto_item_inventario(item):
    return (
        f"{item.get('codigo', '')} {item.get('descripcion', '')} {item.get('vehiculo', '')} "
        f"{item.get('vehiculos_busqueda', '')} {item.get('marca', '')} {item.get('id', '')} "
        f"{item.get('proveedor', '')}"
    )


def _normalizar_codigo_busqueda(codigo):
    return str(codigo or "").strip().upper().replace("/", "-")


def parece_codigo_producto(termino):
    """True si el término parece un código de repuesto (no búsqueda por palabras)."""
    t = _normalizar_codigo_busqueda(termino)
    if not t or " " in t:
        return False
    return bool(re.match(r"^[\dA-Z]+(?:[-/][\dA-Z]+)*$", t)) and len(t) <= 24


def buscar_codigo_exacto_inventario(items, codigo):
    """
    Coincidencia exacta por código maestro, campo codigo o ID variante (CODIGO_MARCA).
    Evita falsos positivos tipo N111VC al buscar «111».
    """
    cod = _normalizar_codigo_busqueda(codigo)
    if not cod:
        return []
    out = []
    for item in items or []:
        if not isinstance(item, dict):
            continue
        ic = _normalizar_codigo_busqueda(item.get("codigo", ""))
        im = _normalizar_codigo_busqueda(item.get("id_maestro", ""))
        iid = _normalizar_codigo_busqueda(item.get("id", ""))
        if ic == cod or im == cod:
            out.append(item)
            continue
        if iid == cod or iid.startswith(f"{cod}_"):
            out.append(item)
    return out


def filtrar_por_busqueda(items, termino_busqueda, extraer_texto):
    """Filtra items si todas las palabras del término coinciden (con flexión plural)."""
    if not termino_busqueda:
        return items

    term_limpio = str(termino_busqueda).strip()
    if parece_codigo_producto(term_limpio):
        exactos = buscar_codigo_exacto_inventario(items, term_limpio)
        if exactos:
            return exactos

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
