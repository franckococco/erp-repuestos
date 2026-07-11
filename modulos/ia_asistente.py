import os
import re
import unicodedata
import streamlit as st
from dotenv import load_dotenv

load_dotenv(override=True)

def normalizar_texto_basico(texto):
    if not texto:
        return ""
    texto = str(texto).lower()
    texto = unicodedata.normalize('NFD', texto)
    return ''.join(c for c in texto if unicodedata.category(c) != 'Mn')

def es_consulta_mayor_o_igual(texto):
    texto_norm = normalizar_texto_basico(texto)
    if re.search(r'\b(mas de|al menos|como minimo|mayores a|mayor a|superior a|por encima de|o mas|mayor o igual|mayor que|>=|\+)\b', texto_norm):
        if not re.search(r'\b(menos de|hasta|a lo sumo|como maximo|menor o igual|menor que|<=)\b', texto_norm):
            return True
    return False

def preprocesar_texto_usuario(texto):
    """
    Limpia texto dictado sin fusionar código de producto con cantidad (ej. 2105320 10).
    """
    texto_limpio = re.sub(
        r"\b(guion|guión)\b", "-", str(texto or ""), flags=re.IGNORECASE
    )

    def unir_si_dictado(match):
        fragmento = match.group(0)
        partes = fragmento.split()
        if len(partes) == 2 and partes[1].isdigit() and int(partes[1]) <= 9999:
            return fragmento
        if len(partes) == 2 and len(partes[0]) <= 3 and partes[0].isdigit():
            return fragmento
        return fragmento.replace(" ", "")

    return re.sub(r"(?:\d+\s+)+\d+", unir_si_dictado, texto_limpio)


def normalizar_orden_voz_deposito(texto):
    """Preprocesa dictado del asistente: códigos + lenguaje natural de depósito."""
    from modulos.voz_lenguaje_natural import aplicar_lenguaje_natural_deposito

    if not texto:
        return ""
    base = preprocesar_texto_usuario(str(texto).strip())
    return aplicar_lenguaje_natural_deposito(base)


def _limpiar_termino_busqueda(termino):
    from modulos.voz_repuestos import corregir_palabra_dictada
    from modulos.voz_lenguaje_natural import es_calificador_producto, quitar_muletillas_residuales

    t = quitar_muletillas_residuales(str(termino or ""))
    t = re.sub(r"^(?:el|la|los|las|de|del|al|un|una)\s+", "", t, flags=re.I)
    t = re.sub(r"\b(?:codigo|stock|repuesto|articulo|buscar)\b", " ", t, flags=re.I)
    t = re.sub(r"\s+para el\s+", " ", t)
    partes = []
    for palabra in t.split():
        if palabra == "de":
            partes.append(palabra)
        elif es_calificador_producto(palabra):
            partes.append(palabra)
        else:
            partes.append(corregir_palabra_dictada(palabra))
    t = " ".join(partes).strip(" ,.-")
    return t


def _limpiar_codigo_orden(termino):
    t = str(termino or "").strip().upper().replace("/", "-")
    return re.sub(r"\s+", "-", t).strip(",.;:")


def _extraer_ubicacion_orden(t):
    ubi = {}
    for key, pat in (
        ("pasillo", r"pasillo\s+(\d+)"),
        ("piso", r"piso\s+(\d+)"),
        ("modulo", r"modulo\s+(\d+)"),
        ("fila", r"fila\s+(\d+)"),
        ("fondo", r"fondo\s+(\d+)"),
    ):
        m = re.search(pat, t)
        if m:
            ubi[key] = int(m.group(1))
    return ubi


def _inferir_vehiculos_desde_texto(texto):
    t = normalizar_texto_basico(texto)
    claves = (
        ("volkswagen", "VOLKSWAGEN"),
        ("gol", "VOLKSWAGEN"),
        ("trend", "VOLKSWAGEN"),
        ("voyage", "VOLKSWAGEN"),
        ("suran", "VOLKSWAGEN"),
        ("polo", "VOLKSWAGEN"),
        ("vento", "VOLKSWAGEN"),
        ("amarok", "VOLKSWAGEN"),
        ("peugeot", "PEUGEOT"),
        ("208", "PEUGEOT"),
        ("207", "PEUGEOT"),
        ("308", "PEUGEOT"),
        ("citroen", "CITROEN"),
        ("c4", "CITROEN"),
        ("fiat", "FIAT"),
        ("cronos", "FIAT"),
        ("argo", "FIAT"),
        ("ford", "FORD"),
        ("ka", "FORD"),
        ("ranger", "FORD"),
        ("renault", "RENAULT"),
        ("clio", "RENAULT"),
        ("sandero", "RENAULT"),
        ("chevrolet", "CHEVROLET"),
        ("onix", "CHEVROLET"),
        ("corsa", "CHEVROLET"),
    )
    found = []
    for kw, marca in claves:
        if re.search(rf"\b{re.escape(kw)}\b", t) and marca not in found:
            found.append(marca)
    return found or ["UNIVERSAL"]


def _es_carga_producto_nuevo(t):
    """True si la orden describe un producto nuevo, no sumar stock."""
    if re.search(r"\b(?:carg\w*|registr\w*|ingres\w*)\b", t):
        if re.search(r"\b(pasillo|piso|modulo|fila|fondo)\b", t):
            return True
        if re.search(
            r"\b(?:carg\w*|registr\w*|ingres\w*)\s+(?:el\s+)?(?:codigo\s+)?"
            r"[\dA-Za-z]+(?:[-/][\dA-Za-z]+)?\s+[a-z]{4,}.+\d+\s*unidad",
            t,
        ):
            return True
    if re.search(r"\b(?:agreg\w*|sum\w*|aument\w*)\s+\d+\s*unidad", t):
        return False
    if re.search(r"\b(?:carg\w*)\s+\d+\s*unidad\w*\s+(?:del?\s+|al?\s+)?", t):
        return False
    if re.search(r"\bpasillo\b", t) and re.search(r"\bcodigo\s+[\dA-Za-z]", t):
        if re.search(r"\bunidades\b", t) and re.search(r"[a-z]{4,}", t):
            return True
    return False


def parse_cargar_producto_rapido(texto_usuario):
    """
    Detecta alta de producto nuevo sin Groq.
    Ej: cargame 111 embrague de gol trend 5 unidades pasillo 1 piso 0 fila 3
    """
    if not texto_usuario:
        return None
    t = str(texto_usuario).lower()

    if not re.search(r"\b(carg\w*|registr\w*|ingres\w*)\b", t):
        return None

    # Alta simple de stock existente: cargá 5 unidades del 1252 / sumá 10 al 1491
    if re.search(
        r"\b(?:agreg\w*|sum\w*|aument\w*)\s+\d+\s*unidad\w*\s+(?:del?\s+|al?\s+)?",
        t,
    ):
        return None
    if re.search(
        r"\b(?:carg\w*)\s+\d+\s*unidad\w*\s+(?:del?\s+|al?\s+)?(?:codigo\s+)?[\w/-]+\s*$",
        t,
    ):
        return None
    if not _es_carga_producto_nuevo(t):
        return None

    ubi = _extraer_ubicacion_orden(t)
    from modulos.normalizar_carga_producto import extraer_ubicacion_desde_texto, extraer_stock_desde_texto
    ubi = {**ubi, **extraer_ubicacion_desde_texto(t)}
    t_sin_ubi = t
    for pat in (
        r"\bpasillo\s+\d+",
        r"\bpiso\s+\d+",
        r"\bmodulo\s+\d+",
        r"\bfila\s+\d+",
        r"\bfondo\s+\d+",
    ):
        t_sin_ubi = re.sub(pat, " ", t_sin_ubi)
    t_sin_ubi = re.sub(r"\s+", " ", t_sin_ubi).strip()

    codigo = descripcion = None
    stock = 1

    num_pal = r"(?:\d+|cero|uno|una|dos|tres|cuatro|cinco|seis|siete|ocho|nueve|diez)"
    m_cant = re.search(rf"\bcantidad\s+({num_pal})\b", t_sin_ubi)
    if m_cant:
        from modulos.normalizar_carga_producto import _entero_ubi
        v = _entero_ubi(m_cant.group(1))
        if v is not None and v > 0:
            stock = v

    m = re.search(
        r"(?:carg\w*|registr\w*|ingres\w*)\s+(?:el\s+)?(?:codigo\s+)?"
        r"([\dA-Za-z]+(?:[-/][\dA-Za-z]+)?)\s+"
        r"(.+?)\s+"
        r"(\d{1,4})\s*unidad",
        t_sin_ubi,
    )
    if m:
        codigo, descripcion, stock = m.group(1), m.group(2).strip(), int(m.group(3))
    else:
        m2 = re.search(
            r"(?:carg\w*|registr\w*|ingres\w*)\s+(?:el\s+)?(?:codigo\s+)?"
            r"([\dA-Za-z]+(?:[-/][\dA-Za-z]+)?)\s+"
            r"(.++)",
            t_sin_ubi,
        )
        if not m2:
            return None
        codigo, descripcion = m2.group(1), m2.group(2).strip()
        if stock == 1:
            st_extra = extraer_stock_desde_texto(t_sin_ubi)
            if st_extra:
                stock = st_extra

    descripcion = re.sub(r"\s+", " ", descripcion).strip(" ,.-")
    if not descripcion or len(descripcion) < 3:
        return None

    codigo = str(codigo or "").strip().upper().replace("/", "-")
    if not codigo:
        return None

    out = {
        "accion": "cargar_producto",
        "codigo": codigo,
        "descripcion": descripcion.upper(),
        "vehiculos": _inferir_vehiculos_desde_texto(f"{descripcion} {t}"),
        "stock": max(1, int(stock)),
        "marca": "GENERICO",
    }
    for k, v in ubi.items():
        out[k] = v
    from modulos.normalizar_carga_producto import normalizar_orden_cargar_producto
    return normalizar_orden_cargar_producto(out, texto_usuario)


def parse_alta_baja_rapido(texto_usuario):
    """Extrae alta/baja + código + cantidad sin llamar a Groq."""
    if not texto_usuario:
        return None
    t = str(texto_usuario).lower()

    if _es_carga_producto_nuevo(t):
        return None

    es_baja = bool(re.search(r"\b(baj\w*|rest\w*|descont\w*|sac\w*)\b", t))
    es_alta = bool(re.search(r"\b(agreg\w*|sum\w*|carg\w*|aument\w*|ingres\w*)\b", t))
    if not es_alta and not es_baja:
        return None

    accion = "baja" if es_baja and not es_alta else "alta"
    if es_baja and es_alta:
        accion = "baja" if t.find("baj") < t.find("agreg") and "baj" in t else "alta"

    cod_pat = r"([\dA-Za-z]+(?:[-/][\dA-Za-z]+)?)"
    patrones = [
        r"(?:sumar|agreg\w*|sum\w*|aument\w*)\s+(\d{1,4})\s*(?:unidad\w*)?\s+(?:al?\s+)?(?:codigo\s+)?" + cod_pat,
        r"(?:carg\w*)\s+(\d{1,4})\s*unidad\w*\s+(?:del?\s+|al?\s+)?(?:codigo\s+)?" + cod_pat,
        r"(?:sumar|bajar|agreg\w*|sum\w*|carg\w*|aument\w*|baj\w*|rest\w*)\s+(?:al?\s+)?(?:codigo\s+)?"
        + cod_pat + r"\s+(\d{1,4})\s*(?:unidad)?",
        r"(?:codigo\s+)" + cod_pat + r"\s+(\d{1,4})\s*(?:unidad)?",
        r"(\d{1,4})\s*(?:unidad)?\s+(?:al?\s+)?(?:codigo\s+)" + cod_pat,
        r"(?:sumar|bajar)\s+(\d{1,4})\s+(?:al?\s+)?(?:codigo\s+)?" + cod_pat,
    ]
    for i, patron in enumerate(patrones):
        m = re.search(patron, t)
        if not m:
            continue
        if i in (0, 1):
            cant, cod = m.group(1), m.group(2)
        elif i == 4:
            cant, cod = m.group(1), m.group(2)
        else:
            cod, cant = m.group(1), m.group(2)
        cod = _limpiar_codigo_orden(cod)
        if cod and "-" in cod and len(cod) > 20:
            continue
        if cod:
            return {"accion": accion, "termino": cod, "cantidad": int(cant)}
    return None


def parse_buscar_rapido(texto_nl):
    """Búsqueda de stock por palabra clave o código."""
    if not texto_nl:
        return None
    t = str(texto_nl).lower().strip()

    if re.search(r"\b(carg\w*|registr\w*|ingres\w*|sumar|bajar|ubicacion\s+\d|reporte\s+)\b", t):
        if not t.startswith("buscar "):
            return None

    if not re.search(r"\b(busc\w*|consult\w*|stock|buscar)\b", t):
        if re.search(
            r"\b(carg\w*|sumar|bajar|ubicacion|reporte|proveedor|registr\w*|ingres\w*)\b",
            t,
        ):
            return None
        if len(t.split()) >= 2 or re.search(r"\bcodigo\s+\S", t):
            termino = _limpiar_termino_busqueda(t)
            if len(termino) >= 2:
                return {"accion": "buscar", "termino": termino}
        return None

    termino = ""
    m = re.match(r"^buscar\s+(.+)$", t)
    if m:
        termino = m.group(1).strip()
    else:
        termino = re.sub(
            r"^.*?\b(?:busc\w*|consult\w*|stock)\s+(?:el\s+)?(?:codigo\s+)?",
            "",
            t,
            count=1,
        ).strip()

    termino = re.sub(r"\s+para el\s+", " ", termino)
    termino = _limpiar_termino_busqueda(termino)
    if len(termino) < 2:
        return None
    return {"accion": "buscar", "termino": termino}


def parse_ubicacion_rapido(texto_nl):
    """Actualizar ubicación de un código."""
    if not texto_nl:
        return None
    t = str(texto_nl).lower()
    ubi = _extraer_ubicacion_orden(t)
    if not ubi:
        return None

    cod_pat = r"([\dA-Za-z]+(?:[-/][\dA-Za-z]+)?)"
    codigo = None
    m_cod = re.search(rf"\bcodigo\s+(\d[\dA-Za-z/-]*)", t)
    if m_cod:
        codigo = _limpiar_codigo_orden(m_cod.group(1))
    if not codigo:
        m_num = re.search(r"\b(\d{2,})\b", t)
        if m_num:
            codigo = m_num.group(1)
    if not codigo:
        for patron in (
            rf"{cod_pat}\s+(?:va en|ubicacion)",
            rf"ubicacion\s+(?:codigo\s+)?{cod_pat}",
        ):
            m = re.search(patron, t)
            if m:
                candidato = _limpiar_codigo_orden(m.group(1))
                if candidato.upper() not in ("EL", "LA", "LOS", "LAS", "UN", "UNA"):
                    codigo = candidato
                    break

    if not codigo:
        return None

    out = {
        "accion": "actualizar_ubicacion",
        "termino": codigo,
        "pasillo": ubi.get("pasillo"),
        "piso": ubi.get("piso"),
        "modulo": ubi.get("modulo"),
        "fila": ubi.get("fila"),
        "fondo": ubi.get("fondo"),
    }
    return out


def parse_reporte_rapido(texto_nl):
    """Reporte de stock por cantidad."""
    if not texto_nl:
        return None
    t = str(texto_nl).lower()
    if not re.search(
        r"\b(reporte|menos de|mas de|más de|al menos|faltantes|punto|"
        r"tienen\s+\d+|stock bajo|critico|crítico)\b",
        t,
    ):
        return None

    cant = 3
    m = re.search(r"(\d{1,4})", t)
    if m:
        cant = int(m.group(1))

    operador = "menor_o_igual"
    if re.search(r"\b(exacto|exactamente|tienen\s+\d+|con\s+\d+)\b", t):
        operador = "exacto"
    elif es_consulta_mayor_o_igual(t) or re.search(r"\b(mas de|más de|al menos|mayor)\b", t):
        operador = "mayor_o_igual"

    return {"accion": "reporte_stock", "operador": operador, "cantidad": cant}


def parse_proveedor_rapido(texto_nl):
    """Filtrar inventario por proveedor."""
    if not texto_nl:
        return None
    t = str(texto_nl).lower().strip()
    m = re.search(r"\bproveedor\s+(.+)$", t)
    if not m:
        m = re.search(r"\b(?:buscar|listar|mostrar)\s+(?:todo\s+)?(?:lo\s+)?(?:de\s+)?(\w[\w\s]{1,40})$", t)
    if not m:
        return None
    prov = _limpiar_termino_busqueda(m.group(1).strip())
    if len(prov) < 2:
        return None
    return {"accion": "filtrar_proveedor", "proveedor": prov}


def _parsers_locales_deposito():
    return (
        parse_cargar_producto_rapido,
        parse_alta_baja_rapido,
        parse_ubicacion_rapido,
        parse_reporte_rapido,
        parse_proveedor_rapido,
        parse_buscar_rapido,
    )


def procesar_orden_voz(texto_usuario, inventario_actual=None):
    from modulos.orden_asistente_inteligente import (
        interpretar_orden_groq_deposito,
        normalizar_accion_asistente,
    )

    texto_nl = normalizar_orden_voz_deposito(texto_usuario)

    for parser in _parsers_locales_deposito():
        resultado = parser(texto_nl)
        if resultado:
            return normalizar_accion_asistente(resultado, texto_usuario)

    groq = interpretar_orden_groq_deposito(texto_usuario)
    if groq:
        return groq

    api_key = os.getenv("GROQ_API_KEY")
    if not api_key:
        try:
            api_key = st.secrets["GROQ_API_KEY"]
        except Exception:
            api_key = None

    if not api_key:
        return {"accion": "error", "respuesta": "Falta configurar la GROQ_API_KEY en los secretos."}

    return {
        "accion": "error",
        "respuesta": (
            "Orden no reconocida. Ejemplos: «fijate si tenés buje de directa para el gol», "
            "«sumá 3 al código 1491», «cargá el 25412 buje amortiguador gol 4 unidades pasillo 2»."
        ),
    }

