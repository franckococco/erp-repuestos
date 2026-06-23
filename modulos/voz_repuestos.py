"""Normalización de repuestos dictados: sinónimos, vehículo y búsqueda compuesta."""
import re
from typing import List, Optional

from modulos.ia_asistente import normalizar_texto_basico
from modulos.util_busqueda import normalizar_para_busqueda

# Errores frecuentes de voz / B corta vs larga
_SINONIMOS_DICTADO = {
    "bielete": "bieleta",
    "bieletes": "bieletas",
    "bieletta": "bieleta",
    "bujete": "buje",
    "bujetes": "bujes",
    "rotula": "rótula",
    "rotulas": "rótulas",
    "amortiguador": "amortiguador",
    "cazoleta": "cazoleta",
    "cazoletas": "cazoletas",
    "crápodina": "crapodina",
    "crapodina": "crapodina",
    "homocinetica": "homocinética",
    "ruleman": "rulemán",
    "bujia": "bujía",
    "optica": "óptica",
    "lampara": "lámpara",
    "liquido": "líquido",
    "reten": "retén",
    "fuelle": "fuelle",
    "fuelles": "fuelles",
    "correa": "correa",
    "correas": "correas",
    "tensor": "tensor",
    "tensors": "tensor",
    "kit": "kit",
    "kits": "kit",
    "directa": "directa",
}

# Modelos / referencias frecuentes en descripción (orden largo → corto)
_MODELOS_VEHICULO = (
    "gol trend", "gol power", "gol country", "gol 1.6", "gol 1.4",
    "partner", "kangoo", "clio", "sandero", "logan", "corsa", "onix",
    "prisma", "cruze", "focus", "fiesta", "ecosport", "ranger",
    "amarok", "vento", "voyage", "suran", "polo", "gol",
    "207", "208", "206", "307", "308", "147", "408", "2008", "3008",
    "palio", "siena", "uno", "cronos", "argo", "toro", "strada",
    "duster", "fluence", "megane", "symbol", "ka", "ka plus",
)

_PAT_PARA_VEHICULO = re.compile(
    r"\b(?:para|del|de|en)\s+(?:el|la|los|las)?\s*"
    r"(\d{3,4}|gol(?:\s+trend|\s+power|\s+country)?|partner|kangoo|clio|"
    r"sandero|logan|corsa|onix|prisma|cruze|focus|fiesta|ecosport|ranger|"
    r"amarok|vento|voyage|suran|polo|palio|siena|uno|cronos|argo|toro|"
    r"strada|duster|fluence|megane|symbol|ka(?:\s+plus)?)\b",
    re.I,
)

_PAT_MODELO_SUELTO = re.compile(
    r"\b(207|208|206|307|308|147|408|2008|3008)\b"
)


def corregir_palabra_dictada(palabra: str) -> str:
    p = normalizar_para_busqueda(palabra)
    if not p:
        return palabra
    return _SINONIMOS_DICTADO.get(p, palabra)


def corregir_termino_repuesto(termino: str) -> str:
    """Aplica sinónimos palabra a palabra (bielete → bieleta)."""
    if not termino:
        return termino
    partes = str(termino).strip().split()
    out = [corregir_palabra_dictada(p) for p in partes]
    return " ".join(out)


def extraer_vehiculos_de_texto(texto: str) -> List[str]:
    """Lista de referencias de vehículo mencionadas (sin duplicados)."""
    if not texto:
        return []
    t = normalizar_texto_basico(texto).lower()
    found = []
    for m in _PAT_PARA_VEHICULO.finditer(t):
        v = m.group(1).strip().lower()
        if v and v not in found:
            found.append(v)
    for modelo in _MODELOS_VEHICULO:
        if re.search(rf"\b{re.escape(modelo)}\b", t) and modelo not in found:
            found.append(modelo)
    for m in _PAT_MODELO_SUELTO.finditer(t):
        v = m.group(1)
        if v not in found:
            found.append(v)
    return found


def extraer_vehiculo_global_orden(texto: str) -> Optional[str]:
    """Vehículo que aplica a toda la orden si se menciona una sola vez."""
    vehs = extraer_vehiculos_de_texto(texto)
    if len(vehs) == 1:
        return vehs[0]
    return None


def extraer_vehiculo_cerca_termino(texto: str, termino: str) -> Optional[str]:
    """Vehículo asociado a un repuesto según proximidad en la frase."""
    if not texto or not termino:
        return None
    t = normalizar_texto_basico(texto).lower()
    term = normalizar_para_busqueda(corregir_termino_repuesto(termino))
    if not term:
        return None
    idx = t.find(term.split()[0]) if term.split() else -1
    if idx < 0:
        return extraer_vehiculo_global_orden(texto)

    ventana = t[max(0, idx - 40): idx + len(term) + 40]
    vehs = extraer_vehiculos_de_texto(ventana)
    if vehs:
        return vehs[-1]
    return extraer_vehiculo_global_orden(texto)


def _termino_es_solo_vehiculo(termino: str, vehiculos: List[str]) -> bool:
    t = normalizar_para_busqueda(str(termino or ""))
    if not t:
        return True
    veh_norm = {normalizar_para_busqueda(v) for v in (vehiculos or [])}
    return t in veh_norm


def _limpiar_termino_item_voz(termino: str, vehiculos: List[str], nombre_cliente: str = "") -> str:
    """Quita prefijo/sufijo de vehículo, cliente y corrige dictado."""
    from modulos.util_busqueda import parece_codigo_producto

    t = corregir_termino_repuesto(str(termino or "")).strip()
    if not t:
        return t
    if parece_codigo_producto(t):
        return t.upper()
    if nombre_cliente:
        nc = normalizar_texto_basico(nombre_cliente).upper()
        tu = normalizar_texto_basico(t).upper()
        if tu.startswith(nc + " "):
            t = t[len(nombre_cliente):].strip()
        tu = normalizar_texto_basico(t).upper()
        if tu == nc:
            return ""
    t_low = normalizar_texto_basico(t).lower()
    for v in vehiculos or []:
        vn = normalizar_texto_basico(v).lower()
        if t_low.startswith(vn + " "):
            t = t[len(vn):].strip()
            t_low = normalizar_texto_basico(t).lower()
        elif t_low.endswith(" " + vn):
            t = t[: -(len(vn) + 1)].strip()
            t_low = normalizar_texto_basico(t).lower()
        elif t_low == vn:
            return ""
    palabras = [p.upper() for p in t.split() if p]
    return " ".join(palabras)


def enriquecer_items_con_vehiculo(items: list, texto_completo: str) -> list:
    """Agrega vehiculo y limpia términos (orden de palabras no importa)."""
    if not items:
        return items
    vehs = extraer_vehiculos_de_texto(texto_completo)
    global_v = extraer_vehiculo_global_orden(texto_completo)
    try:
        from modulos.mostrador_voz_flujo import extraer_cliente_orden_voz

        nom_cli = (extraer_cliente_orden_voz(texto_completo) or {}).get("nombre_cliente", "")
    except Exception:
        nom_cli = ""
    out = []
    vistos = set()
    for item in items:
        if not isinstance(item, dict):
            continue
        term = item.get("termino") or item.get("descripcion") or ""
        v = item.get("vehiculo") or extraer_vehiculo_cerca_termino(texto_completo, str(term)) or global_v
        term_limpio = _limpiar_termino_item_voz(str(term), vehs, nombre_cliente=str(nom_cli or ""))
        if nom_cli and term_limpio:
            palabras = [
                p for p in term_limpio.split()
                if p.upper() != str(nom_cli).upper()
            ]
            term_limpio = " ".join(palabras).strip()
        if not term_limpio or _termino_es_solo_vehiculo(term_limpio, vehs):
            continue
        cant = int(item.get("cantidad", 1))
        if cant > 99 and not str(term_limpio).isdigit():
            continue
        if v and str(cant) == re.sub(r"\D", "", str(v)):
            continue
        clave = (term_limpio, int(item.get("cantidad", 1)), v or "")
        if clave in vistos:
            continue
        vistos.add(clave)
        nuevo = dict(item)
        nuevo["termino"] = term_limpio
        if v:
            nuevo["vehiculo"] = v
        out.append(nuevo)
    return out
