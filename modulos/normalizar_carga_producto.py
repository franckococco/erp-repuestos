"""Limpia órdenes cargar_producto: descripción solo repuesto, resto en sus campos."""
import re
from typing import Any, Dict, List, Optional, Tuple

from modulos.ia_asistente import normalizar_texto_basico, _inferir_vehiculos_desde_texto
from modulos.db_firebase import sanitizar_clave_marca

_NUM_PALABRA = {
    "cero": 0, "uno": 1, "una": 1, "dos": 2, "tres": 3, "cuatro": 4,
    "cinco": 5, "seis": 6, "siete": 7, "ocho": 8, "nueve": 9, "diez": 10,
}

_MARCAS_CONOCIDAS = (
    "KREISEN", "LUK", "SKF", "FRAM", "MANN", "MAHLE", "BOSCH", "VALEO",
    "MONROE", "GATES", "DAYCO", "NGK", "DENSO", "FEBI", "TRW", "FERODO",
    "ORIGINAL", "GENERICO", "GENÉRICO",
)


def _entero_ubi(val) -> Optional[int]:
    if val is None:
        return None
    if isinstance(val, (int, float)) and not isinstance(val, bool):
        try:
            return max(0, int(val))
        except (TypeError, ValueError):
            return None
    s = str(val).strip().lower()
    if not s:
        return None
    if s.isdigit():
        return int(s)
    return _NUM_PALABRA.get(s)


def extraer_ubicacion_desde_texto(texto: str) -> Dict[str, int]:
    """Pasillo/piso/módulo/fila con dígitos o palabras (cero, uno…)."""
    t = normalizar_texto_basico(str(texto or "")).lower()
    ubi: Dict[str, int] = {}
    num = r"(?:\d+|cero|uno|una|dos|tres|cuatro|cinco|seis|siete|ocho|nueve|diez)"
    for key, pat in (
        ("pasillo", rf"pasillo\s+({num})"),
        ("piso", rf"piso\s+({num})"),
        ("modulo", rf"modulo\s+({num})"),
        ("fila", rf"fila\s+({num})"),
    ):
        m = re.search(pat, t)
        if m:
            v = _entero_ubi(m.group(1))
            if v is not None:
                ubi[key] = v
    return ubi


def _patrones_quitar_ubicacion():
    num = r"(?:\d+|cero|uno|una|dos|tres|cuatro|cinco|seis|siete|ocho|nueve|diez)"
    return [
        rf"\bpasillo\s+{num}\b",
        rf"\bpiso\s+{num}\b",
        rf"\bmodulo\s+{num}\b",
        rf"\bfila\s+{num}\b",
    ]


def extraer_marca_desde_texto(texto: str, marca_actual: str = "") -> str:
    mar = sanitizar_clave_marca(marca_actual or "")
    if mar and mar != "GENERICO":
        return mar
    t = str(texto or "").upper()
    m = re.search(r"\bMARCA\s+([A-Z0-9][A-Z0-9\-]{1,20})\b", t)
    if m:
        return sanitizar_clave_marca(m.group(1))
    for mk in _MARCAS_CONOCIDAS:
        if re.search(rf"\b{re.escape(mk)}\b", t, re.IGNORECASE):
            cand = sanitizar_clave_marca(mk)
            if cand != "GENERICO":
                return cand
    return mar or "GENERICO"


def _quitar_ruido_descripcion(desc: str, vehiculos: List[str]) -> str:
    d = str(desc or "").upper()
    for pat in _patrones_quitar_ubicacion():
        d = re.sub(pat, " ", d, flags=re.IGNORECASE)
    d = re.sub(
        r"\b(?:cantidad|cant\.?|unidad(?:es)?|u\.?)\s*:?\s*\d+\b",
        " ",
        d,
        flags=re.IGNORECASE,
    )
    d = re.sub(r"\b\d+\s*(?:unidad(?:es)?|u\.?)\b", " ", d, flags=re.IGNORECASE)
    d = re.sub(r"\b(?:vehiculo|vehículo|auto)\s*:?\s*", " ", d, flags=re.IGNORECASE)
    d = re.sub(r"\bMARCA\s+[A-Z0-9][A-Z0-9\-]{1,20}\b", " ", d, flags=re.IGNORECASE)
    for mk in _MARCAS_CONOCIDAS:
        d = re.sub(rf"\b{re.escape(mk)}\b", " ", d, flags=re.IGNORECASE)
    for v in vehiculos or []:
        if v and str(v).upper() != "UNIVERSAL":
            d = re.sub(rf"\b{re.escape(str(v).upper())}\b", " ", d)
    d = re.sub(r"\s+", " ", d).strip(" ,.-;:")
    return d


def normalizar_orden_cargar_producto(
    datos: Dict[str, Any],
    texto_original: Optional[str] = None,
) -> Dict[str, Any]:
    """Unifica campos de cargar_producto; descripción solo con nombre del repuesto."""
    if not isinstance(datos, dict):
        return datos
    accion = datos.get("accion")
    if accion and accion != "cargar_producto":
        return datos

    out = dict(datos)
    if not accion:
        out["accion"] = "cargar_producto"
    blob = f"{texto_original or ''} {out.get('descripcion', '')}"

    ubi_txt = extraer_ubicacion_desde_texto(blob)
    for k in ("pasillo", "piso", "modulo", "fila"):
        if ubi_txt.get(k) is not None:
            out[k] = ubi_txt[k]
        elif out.get(k) is not None:
            v = _entero_ubi(out.get(k))
            if v is not None:
                out[k] = v

    veh_raw = out.get("vehiculos") or out.get("vehiculo") or []
    if isinstance(veh_raw, str):
        veh_raw = [veh_raw]
    vehiculos = _inferir_vehiculos_desde_texto(blob) if not veh_raw else list(veh_raw)
    out["vehiculos"] = vehiculos

    marca = extraer_marca_desde_texto(blob, str(out.get("marca", "") or ""))
    out["marca"] = marca

    stock_raw = out.get("stock")
    if stock_raw is None and texto_original:
        m = re.search(
            r"\b(\d{1,4})\s*(?:unidad(?:es)?|u\.?)\b",
            normalizar_texto_basico(texto_original),
        )
        if m:
            out["stock"] = int(m.group(1))

    desc_limpia = _quitar_ruido_descripcion(str(out.get("descripcion", "") or ""), vehiculos)
    if len(desc_limpia) < 3:
        desc_limpia = _quitar_ruido_descripcion(
            re.sub(
                r"(?i)^(?:carg\w*|registr\w*|ingres\w*)\s+(?:el\s+)?(?:codigo\s+)?[\w/-]+\s+",
                "",
                str(texto_original or ""),
            ),
            vehiculos,
        )
    out["descripcion"] = desc_limpia.upper() if desc_limpia else str(out.get("descripcion", "")).upper()

    return out
