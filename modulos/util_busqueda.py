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

    term_limpio = _limpiar_prefijo_busqueda(str(termino_busqueda).strip())
    if parece_codigo_producto(term_limpio):
        exactos = buscar_codigo_exacto_inventario(items, term_limpio)
        if exactos:
            return exactos

    terminos = [t for t in normalizar_para_busqueda(term_limpio).split() if len(t) >= 2]
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


def _limpiar_prefijo_busqueda(termino):
    t = str(termino or "").strip()
    tl = t.lower()
    for pref in (
        "codigo ", "código ", "descripcion ", "descripción ", "desc ",
        "buscar ", "busca ", "producto ", "articulo ", "artículo ",
    ):
        if tl.startswith(pref):
            return t[len(pref):].strip()
    return t


def filtrar_por_busqueda_flexible(items, termino_busqueda, extraer_texto, limite=25):
    """
    Búsqueda en capas: estricta → coincidencia parcial por palabras → código parcial.
    """
    term_limpio = _limpiar_prefijo_busqueda(str(termino_busqueda or "").strip())
    if not term_limpio:
        return []

    estrictos = filtrar_por_busqueda(items, term_limpio, extraer_texto)
    if estrictos:
        return estrictos[:limite]

    terminos = [t for t in normalizar_para_busqueda(term_limpio).split() if len(t) >= 2]
    if not terminos:
        return []

    scored = []
    for item in items or []:
        if not isinstance(item, dict):
            continue
        texto_norm = normalizar_para_busqueda(extraer_texto(item))
        hits = sum(1 for t in terminos if termino_en_texto(t, texto_norm))
        if hits:
            scored.append((hits, len(terminos), item))
    if scored:
        scored.sort(key=lambda x: (-x[0], x[1]))
        return [it for _, _, it in scored[:limite]]

    cod = _normalizar_codigo_busqueda(term_limpio)
    if cod and len(cod) >= 2:
        parciales = []
        for item in items or []:
            if not isinstance(item, dict):
                continue
            ic = _normalizar_codigo_busqueda(item.get("codigo", ""))
            iid = _normalizar_codigo_busqueda(item.get("id", ""))
            if cod in ic or cod in iid or ic.startswith(cod) or iid.startswith(f"{cod}_"):
                parciales.append(item)
        if parciales:
            return parciales[:limite]
    return []


def item_coincide_vehiculo(item, vehiculo: str) -> bool:
    """True si el ítem aplica al modelo (207 ≠ 206-207-20 en medidas)."""
    if not vehiculo:
        return True
    if not isinstance(item, dict):
        return False

    v_norm = normalizar_para_busqueda(str(vehiculo)).strip()
    if not v_norm:
        return True

    veh_text = normalizar_para_busqueda(
        f"{item.get('vehiculo', '')} {item.get('vehiculos_busqueda', '')}"
    )
    if veh_text and veh_text not in ("universal", ""):
        tokens = [t for t in v_norm.split() if len(t) >= 2] or [v_norm]
        if all(termino_en_texto(t, veh_text) for t in tokens):
            return True

    desc_raw = str(item.get("descripcion", "") or "")
    desc_norm = normalizar_para_busqueda(desc_raw)

    if re.match(r"^\d{3,4}$", v_norm):
        return _modelo_numerico_coincide(v_norm, desc_raw, desc_norm)

    tokens = [t for t in v_norm.split() if len(t) >= 2]
    if not tokens:
        tokens = [v_norm]
    return all(termino_en_texto(t, desc_norm) for t in tokens)


def _modelo_numerico_coincide(modelo: str, desc_raw: str, desc_norm: str) -> bool:
    """207 válido en «PEUGEOT 207»; inválido en «206-207-20» o listas 206/207/20."""
    if re.search(rf"\d-{re.escape(modelo)}(-\d|\d)", desc_raw, re.I):
        return False
    if re.search(rf"{re.escape(modelo)}-\d", desc_raw, re.I):
        return False

    if re.search(
        rf"(?:^|\s)(?:peugeot|citroen|fiat|ford|vw|volkswagen)\s+{re.escape(modelo)}(?:\s|$)",
        desc_norm,
        re.I,
    ):
        return True

    for m in re.finditer(rf"(?<!\d){re.escape(modelo)}(?!\d)", desc_norm):
        start, end = m.span()
        before = desc_norm[max(0, start - 6) : start]
        after = desc_norm[end : end + 6]
        if re.search(r"\d\s*$", before) and re.search(r"^\s*\d", after):
            continue
        return True
    return False


def buscar_en_inventario_con_vehiculo(items, termino, vehiculo=None, extraer_texto=None):
    """
    Búsqueda por repuesto + filtro opcional de vehículo (ej. bieleta + 207).
    """
    from modulos.voz_repuestos import corregir_termino_repuesto

    ext = extraer_texto or texto_item_inventario
    term = corregir_termino_repuesto(str(termino or "").strip())
    if not term:
        return []

    base = filtrar_por_busqueda(items, term, ext)
    if not base:
        base = filtrar_por_busqueda_flexible(items, term, ext, limite=25)

    if not vehiculo:
        return base

    con_veh = [i for i in base if item_coincide_vehiculo(i, vehiculo)]
    if con_veh:
        return con_veh

    palabras = [p for p in normalizar_para_busqueda(term).split() if len(p) >= 3]
    solo_veh = [i for i in (items or []) if item_coincide_vehiculo(i, vehiculo)]
    if palabras and solo_veh:
        scored = []
        for item in solo_veh:
            texto = normalizar_para_busqueda(ext(item))
            hits = sum(1 for p in palabras if termino_en_texto(p, texto))
            if hits:
                scored.append((hits, item))
        if scored:
            scored.sort(key=lambda x: -x[0])
            return [it for _, it in scored[:25]]

    return []
