"""
Capa de lenguaje natural para órdenes del mostrador (Argentina).

Quita muletillas y conectores irrelevantes, preserva calificadores de producto
(bieleta de suspension, buje de directa) y normaliza frases coloquiales a
tokens estables para el parser y Groq.
"""
from __future__ import annotations

import re
from typing import Dict, List, Tuple

from modulos.ia_asistente import normalizar_texto_basico

# --- Muletillas de comando (imperativo / pedido) ---
_MULETILLAS_COMANDO = (
  "haceme", "hacerme", "hacemelo", "hacela", "hacelo", "hacenos", "hacéme",
  "necesito", "necesitamos", "necesitaria", "necesitaría", "necesitaria",
  "quiero", "quisiera", "quisieramos", "quisiéramos",
  "dame", "damele", "damela", "damenos", "danos", "dános",
  "pasame", "pasáme", "pasale", "pasala", "pasenos", "pásenos",
  "agregame", "agregáme", "agregale", "agregále", "agreguen", "agregá",
  "meteme", "metéme", "metele", "metéle", "metelo", "metélo",
  "cargame", "cárgame", "cargale", "cárgale", "cargalo", "cárgalo",
  "armame", "ármame", "armeme", "ármelo", "armalo", "ármalo",
  "pongame", "poneme", "ponéme", "ponle", "ponéle", "ponelo", "ponélo",
  "sumame", "súmame", "sumale", "súmale", "sumalo",
  "buscame", "buscáme", "busca", "buscá", "buscalo", "buscálo",
  "fijate", "fijate si", "fijame", "fijá", "fijate si tenes", "fijate si tenés",
  "anotame", "anotá", "apuntame", "apuntá", "fichame", "fichá",
  "mandame", "mandá", "preparame", "prepará", "sacame", "sacá",
  "tirame", "tirá", "dejame", "dejá", "traeme", "traé", "traeme",
  "consigame", "consígueme", "conseguime",
  "che dame", "che pasame", "che fijate",
  "anda buscando", "a ver", "aver", "bueno", "bueno dale",
  "por favor", "porfa", "porfavor", "si podes", "si podés", "si puede ser",
  "me harías", "me harias", "me podés", "me podes", "me podria",
  "me podría", "podrias", "podrías", "podes", "podés",
  "rgame", "rgáme",
)

# Adverbios / relleno que no aportan entidades (se quitan sueltos)
_MULETILLAS_RELLENO = (
  "che", "bueno", "dale", "listo", "ok", "okey", "okay", "bárbaro", "barbaro",
  "genial", "perfecto", "joya", "joyita", "fijate", "mirá", "mira", "ves",
  "viste", "tipo", "digamos", "osea", "o sea", "basicamente", "básicamente",
  "literal", "literalmente", "ahora", "ahí", "ahi", "acá", "aca", "entonces",
  "igual", "bueno dale", "por favor", "porfa", "amigo", "loco", "hermano",
  "capo", "jefe", "maestro", "disculpa", "perdon", "perdón", "gracias",
)

# Separadores de ítems en una misma orden
_SEPARADORES_ITEMS = (
  "tambien", "también", "ademas", "además", "despues", "después", "y despues",
  "y después", "y tambien", "y también", "mas", "más", "otro mas", "otro más",
  "sumale", "sumále", "agregale", "agregále", "y agregame", "y agregáme",
)

# Calificadores que forman parte del nombre del repuesto (NO quitar el «de» anterior)
_CALIFICADORES_CON_DE = (
  "suspension", "suspensión", "direccion", "dirección", "freno", "frenos",
  "motor", "nafta", "combustible", "aceite", "aire", "polen", "habitaculo",
  "habitáculo", "agua", "distribucion", "distribución", "embrague", "escape",
  "directa", "trasera", "delantera", "delantero", "trasero", "inferior",
  "superior", "interno", "externo", "izquierdo", "derecho", "izq", "der",
  "auxiliar", "principal", "original", "generico", "genérico", "alternativo",
  "barra", "estabilizadora", "estab", "semieje", "rueda", "puerta", "capot",
  "parabrisas", "luneta", "tablero", "radiador", "calefaccion", "calefacción",
  "crique", "cardan", "cardán", "homocinetica", "homocinética",
)

# Marcas / condición frecuentes en dictado (se preservan en producto)
_MARCAS_CALIFICADORAS = (
  "original", "generico", "genérico", "alternativo", "importado", "nacional",
  "parellelo", "paralelo", "genuine", "genuino", "reconstruido", "usado",
  "nuevo", "nakata", "monroe", "skf", "gates", "dayco", "mahle", "mann",
  "knecht", "fram", "wix", "kreisen", "nakata", "original", "fremax",
)

_PLACEHOLDER_DE_CALIF = "\uE000de\uE001"

_NUMEROS_VOZ_EXT = {
  "cero": "0", "uno": "1", "un": "1", "una": "1",
  "dos": "2", "tres": "3", "cuatro": "4", "cinco": "5",
  "seis": "6", "siete": "7", "ocho": "8", "nueve": "9", "diez": "10",
  "once": "11", "doce": "12", "trece": "13", "catorce": "14", "quince": "15",
  "dieciseis": "16", "dieciséis": "16", "diecisiete": "17", "dieciocho": "18",
  "diecinueve": "19", "veinte": "20", "veintiuno": "21", "veintidos": "22",
  "veintidós": "22", "veintitres": "23", "veintitrés": "23", "treinta": "30",
  "cuarenta": "40", "cincuenta": "50", "sesenta": "60", "setenta": "70",
  "ochenta": "80", "noventa": "90", "cien": "100",
}


def patron_muletillas_comando() -> str:
    ordenadas = sorted(_MULETILLAS_COMANDO, key=len, reverse=True)
    return "|".join(re.escape(m) for m in ordenadas)


def patron_muletillas_relleno() -> str:
    ordenadas = sorted(_MULETILLAS_RELLENO, key=len, reverse=True)
    return "|".join(re.escape(m) for m in ordenadas)


def _proteger_de_calificador_producto(texto: str) -> str:
    """bieleta de suspension → bieleta __DE_CALIF__suspension (no se pierde el sentido)."""
    t = texto
    for cal in sorted(_CALIFICADORES_CON_DE, key=len, reverse=True):
        t = re.sub(
            rf"\bde\s+{re.escape(cal)}\b",
            f" {_PLACEHOLDER_DE_CALIF}{cal}",
            t,
            flags=re.I,
        )
        t = re.sub(
            rf"\bdel\s+{re.escape(cal)}\b",
            f" {_PLACEHOLDER_DE_CALIF}{cal}",
            t,
            flags=re.I,
        )
    return t


def _restaurar_de_calificador_producto(texto: str) -> str:
    t = texto
    for cal in _CALIFICADORES_CON_DE:
        t = t.replace(f"{_PLACEHOLDER_DE_CALIF}{cal}", f"de {cal}")
    return t


def _expandir_numeros_compuestos(texto: str) -> str:
    unidades = {
        "uno": 1, "dos": 2, "tres": 3, "cuatro": 4, "cinco": 5,
        "seis": 6, "siete": 7, "ocho": 8, "nueve": 9,
    }
    t = texto
    for decena, base in (
        ("treinta", 30), ("cuarenta", 40), ("cincuenta", 50),
        ("sesenta", 60), ("setenta", 70), ("ochenta", 80), ("noventa", 90),
    ):
        for nombre, val in unidades.items():
            if nombre == "uno":
                frase = f"{decena} y uno"
                num = base + 1
            else:
                frase = f"{decena} y {nombre}"
                num = base + val
            t = re.sub(rf"\b{frase}\s+unidades?\b", f"{num} unidades", t)
        t = re.sub(rf"\b{decena}\s+unidades?\b", f"{base} unidades", t)
    return t


def _unificar_comprobantes_y_acciones(t: str) -> str:
    t = re.sub(
        r"\b(cotizacion|cotización|cotiza|cotizame|cotizáme|presu|presupuestito|"
        r"presupuestame|presupuestáme|armame el presu|necesito un presu|"
        r"pasame un presu|haceme un presu)\b",
        "presupuesto",
        t,
    )
    t = re.sub(
        r"\b(facturame|facturáme|facturá|sacame la factura|sacá la factura|"
        r"ticket fiscal|factura fiscal|facturacion|facturación|"
        r"cerrá la venta|cierra la venta|cerrar venta)\b",
        "factura",
        t,
    )
    t = re.sub(r"\bfactura\s+be\b", "factura b", t)
    t = re.sub(r"\bfactura\s+ve\b", "factura a", t)
    t = re.sub(r"\bfactura\s+bee\b", "factura b", t)
    t = re.sub(r"\bfactura\s+abierta\b", "factura a", t)
    t = re.sub(r"\bfactura\s+ce\b", "factura c", t)
    t = re.sub(r"\bfactura\s+tipo\s+a\b", "factura a", t)
    t = re.sub(r"\bfactura\s+tipo\s+b\b", "factura b", t)
    t = re.sub(r"\bfactura\s+a\s+responsable\b", "factura a", t)
    return t


def _unificar_codigo_y_articulo(t: str) -> str:
    t = re.sub(
        r"\b(codi|cod\.|articulo|artículo|art\.|item|ítem|repuesto numero|"
        r"numero de parte|nro de parte|número de parte|codigo de pieza|"
        r"la pieza|el repuesto|numero|número|nro|num)\b",
        "codigo",
        t,
    )
    t = re.sub(r"\bdel\s+codigo\b", "codigo", t)
    t = re.sub(r"\bde\s+el\s+codigo\b", "codigo", t)
    t = re.sub(r"\bcon\s+codigo\b", "codigo", t)
    return t


def _unificar_cliente(t: str) -> str:
    t = re.sub(r"\b(?:a|al)\s+nombre\s+de\s+", "para ", t)
    t = re.sub(r"\bpara\s+el\s+cliente\s+", "para ", t)
    t = re.sub(r"\bdel\s+cliente\s+", "para ", t)
    t = re.sub(r"\bcliente\s+(?!final\b)", "para ", t)
    t = re.sub(r"\bpara\s+el\s+senor\s+", "para ", t)
    t = re.sub(r"\bpara\s+el\s+señor\s+", "para ", t)
    t = re.sub(r"\bpara\s+la\s+senora\s+", "para ", t)
    t = re.sub(r"\bpara\s+la\s+señora\s+", "para ", t)
    t = re.sub(r"\bpara\s+el\s+taller\s+", "para taller ", t)
    t = re.sub(
        r"\b(sin nombre|sin cliente|venta de mostrador|mostrador|"
        r"consumidor final|particular|cf|consumidor)\b",
        "consumidor final",
        t,
    )
    return t


def _unificar_vehiculo(t: str) -> str:
    t = re.sub(
        r"\b(?:del auto|del vehiculo|del vehículo|del coche|del vehiculo|"
        r"modelo|año|ano|año modelo)\s+",
        "para el ",
        t,
    )
    t = re.sub(r"\bpara\s+un\s+", "para el ", t)
    t = re.sub(r"\bcompatible\s+con\s+", "para el ", t)
    t = re.sub(r"\bque\s+va\s+en\s+", "para el ", t)
    t = re.sub(r"\bque\s+es\s+de\s+", "para el ", t)
    return t


def _unificar_cierre(t: str) -> str:
    t = re.sub(
        r"\b(ya esta|ya está|eso es todo|nada mas|nada más|basta|"
        r"cerrá|cierra|cerrar|dale listo|listo dale|termina|terminá|"
        r"con eso|eso nomas|eso nomás|fin de la orden|fin de orden)\b",
        "listo",
        t,
    )
    return t


def _unificar_unidades_y_cantidades(t: str) -> str:
    t = re.sub(r"\b(ud|uds|u\.d\.|piezas?|pzas?|pza|unid|unidad|unidades)\b", "unidades", t)
    t = re.sub(r"\bmedia docena\b", "6 unidades", t)
    t = re.sub(r"\buna docena\b", "12 unidades", t)
    t = re.sub(r"\bdocena\b", "12 unidades", t)
    t = re.sub(r"\bun par\b", "2 unidades", t)
    t = re.sub(r"\bpar de\b", "", t)
    t = re.sub(r"\bjuego de\b", "", t)
    t = re.sub(r"\bkit de\b", "kit ", t)
    t = re.sub(r"\bx\s*(\d{1,2})\b", r"\1 unidades", t)
    t = re.sub(r"\bpor\s+(\d{1,2})\b(?!\s*%|\s*ciento)", r"\1 unidades", t)
    t = re.sub(r"\bcantidad\s+", "", t)
    t = re.sub(r"\bcant\s+", "", t)
    return t


def _quitar_muletillas_comando(t: str) -> str:
    pat = patron_muletillas_comando()
    t = re.sub(rf"\b(?:{pat})\s+(?:un|una|el|la|me|le|nos|por favor|porfa)?\s*", " ", t)
    t = re.sub(rf"\b(?:{pat})\b", " ", t)
    return t


def _quitar_relleno_suelto(t: str) -> str:
    pat = patron_muletillas_relleno()
    t = re.sub(rf"(?:^|\s)(?:{pat})(?:\s|$)", " ", t)
    return t


def _unificar_separadores_items(t: str) -> str:
    for sep in sorted(_SEPARADORES_ITEMS, key=len, reverse=True):
        t = re.sub(rf"\b{re.escape(sep)}\b", " y ", t)
    return t


def _reordenar_cantidad_repuesto(t: str) -> str:
    """«de 2 bieletas de suspension 207» → «bieletas de suspension 2 unidades 207»."""
    t = re.sub(
        r"\bde\s+(\d{1,2})\s+((?:[a-záéíóúñ]+\s+)*[a-záéíóúñ]+)\s+(?=\d{3,4}\s*$)",
        r"\2 \1 unidades ",
        t,
    )
    t = re.sub(
        r"\bde\s+(\d{1,2})\s+((?:[a-záéíóúñ]+\s+)*[a-záéíóúñ]+)\s+(?=para\s+el\s+)",
        r"\2 \1 unidades ",
        t,
    )
    t = re.sub(
        r"\bde\s+(\d{1,2})\s+((?:[a-záéíóúñ]+\s+)*[a-záéíóúñ]+)\s+(?=(?:codigo|código)\b)",
        r"\2 \1 unidades ",
        t,
    )
    t = re.sub(
        r"\b(\d{1,2})\s+((?:[a-záéíóúñ]+\s+)*[a-záéíóúñ]+)\s+(?=para\s+el\s+)",
        r"\2 \1 unidades ",
        t,
    )
    return t


def _expandir_numeros_en_palabras(t: str) -> str:
    t = _expandir_numeros_compuestos(t)
    for palabra, num in sorted(_NUMEROS_VOZ_EXT.items(), key=lambda x: -len(x[0])):
        if palabra in ("un", "una", "uno", "cero"):
            continue
        t = re.sub(rf"\b{palabra}\s+unidades?\b", f"{num} unidades", t)
    return t


def _cantidad_repuesto_en_palabras(t: str) -> str:
    from modulos.voz_repuestos import corregir_palabra_dictada, palabras_cantidad_repuesto_voz

    _palabras_cant = palabras_cantidad_repuesto_voz()
    prod_pat = "|".join(re.escape(p) for p in _palabras_cant)
    nums = "|".join(
        p for p in _NUMEROS_VOZ_EXT if p not in ("un", "una", "uno", "cero")
    )

    def _cantidad_repuesto(m):
        num = _NUMEROS_VOZ_EXT.get(m.group(1), m.group(1))
        rep = m.group(2)
        if rep.endswith("s") and len(rep) > 4:
            rep = rep[:-1]
        rep = corregir_palabra_dictada(rep)
        return f"{rep} {num} unidades"

    return re.sub(rf"\b({nums})\s+({prod_pat})\w*\b", _cantidad_repuesto, t)


def _unificar_pago(t: str) -> str:
    t = re.sub(r"\b(en efectivo|al contado|en cash|con plata)\b", "contado", t)
    t = re.sub(r"\b(con transferencia|por transferencia|transfe)\b", "transferencia", t)
    t = re.sub(r"\b(con tarjeta|tarjeta de credito|tarjeta crédito)\b", "tarjeta", t)
    t = re.sub(r"\b(mercado pago|mercadopago|con mp)\b", "mercadopago", t)
    t = re.sub(r"\b(cuenta corriente|cta cte|ctacte)\b", "cuenta corriente", t)
    return t


def aplicar_lenguaje_natural_mostrador(texto: str) -> str:
    """
    Pipeline completo: muletillas → sinónimos de acción → cantidades →
    preservación de calificadores de producto.
    """
    if not texto:
        return ""
    t = normalizar_texto_basico(str(texto).strip()).lower()
    t = re.sub(r"\bguion\b", "-", t)
    t = re.sub(r"\bguión\b", "-", t)

    t = _proteger_de_calificador_producto(t)
    t = _unificar_comprobantes_y_acciones(t)
    t = _unificar_codigo_y_articulo(t)
    t = _unificar_cliente(t)
    t = _unificar_vehiculo(t)
    t = _unificar_pago(t)
    t = _unificar_cierre(t)
    t = _unificar_unidades_y_cantidades(t)
    t = _quitar_muletillas_comando(t)
    t = _expandir_numeros_en_palabras(t)
    t = _cantidad_repuesto_en_palabras(t)
    t = _reordenar_cantidad_repuesto(t)
    t = _unificar_separadores_items(t)
    t = _quitar_relleno_suelto(t)
    t = _restaurar_de_calificador_producto(t)
    t = re.sub(r"\s+", " ", t).strip()
    return t


def quitar_muletillas_residuales(texto: str) -> str:
    """Limpieza ligera en fragmentos de producto (solo muletillas, no «para el 207»)."""
    t = normalizar_texto_basico(str(texto or "")).lower()
    t = _quitar_muletillas_comando(t)
    t = _quitar_relleno_suelto(t)
    t = re.sub(r"\s+", " ", t).strip()
    return t


def es_calificador_producto(palabra: str) -> bool:
    p = normalizar_texto_basico(str(palabra or "")).lower()
    return p in _CALIFICADORES_CON_DE or p in _MARCAS_CALIFICADORAS


def segmentar_orden_natural(texto: str) -> Dict:
    """
    Segmenta una orden en bloques semánticos (sin inventario).
    Usado por interpretar_orden_voz_mostrador y validación Groq.
    """
    from modulos.mostrador_voz_flujo import (
        extraer_cliente_orden_voz,
        extraer_items_orden_voz,
    )

    raw = str(texto or "").strip()
    from modulos.mostrador_voz_flujo import normalizar_orden_voz_mostrador

    norm_full = normalizar_orden_voz_mostrador(raw)
    raw_low = raw.lower()

    cliente = extraer_cliente_orden_voz(raw)
    items = extraer_items_orden_voz(raw)
    t = norm_full.lower()

    intent = None
    if re.search(r"\bpresupuesto\b", t):
        intent = "presupuesto"
    elif re.search(r"factura\s+a\b", t):
        intent = "factura_a"
    elif re.search(r"\bfactura\b", t):
        intent = "factura_b"

    forma_pago = None
    if re.search(r"contado|efectivo|cash|plata", t):
        forma_pago = "Contado"
    elif re.search(r"transfer", t):
        forma_pago = "Transferencia"
    elif re.search(r"tarjeta", t):
        forma_pago = "Tarjeta"
    elif re.search(r"cheque", t):
        forma_pago = "Cheque"
    elif re.search(r"mercado", t):
        forma_pago = "MercadoPago"
    elif re.search(r"cuenta\s+corriente", t):
        forma_pago = "Cuenta corriente"

    return {
        "texto_original": raw,
        "texto_normalizado": norm_full,
        "texto_lenguaje_natural": norm_full,
        "cliente": cliente,
        "items": items,
        "intent": intent,
        "forma_pago": forma_pago,
        "listo": bool(
            re.search(r"\b(listo|termine|terminé|fin|dale)\b", raw_low)
            or re.search(r"\b(listo|termine|terminé|fin|dale)\b", t)
        ),
    }


def resumen_orden_natural(segmento: Dict) -> str:
    partes = []
    cliente = segmento.get("cliente") or {}
    if cliente.get("consumidor_final"):
        partes.append("cliente: consumidor final")
    elif cliente.get("nombre_cliente"):
        partes.append(f"cliente: {cliente['nombre_cliente']}")
    for it in segmento.get("items") or []:
        frag = f"{it.get('termino', '?')} x{it.get('cantidad', 1)}"
        if it.get("vehiculo"):
            frag += f" ({it['vehiculo']})"
        partes.append(frag)
    if segmento.get("intent"):
        partes.append(f"acción: {str(segmento['intent']).replace('_', ' ')}")
    if segmento.get("forma_pago"):
        partes.append(f"pago: {segmento['forma_pago']}")
    return " · ".join(partes)


def instrucciones_groq_lenguaje_natural() -> str:
    """Bloque de prompt: qué ignorar y qué preservar."""
    return """
PALABRAS QUE DEBES IGNORAR (no son cliente ni producto):
- Muletillas: haceme, dame, necesito, quiero, fijate, che, bueno, dale, por favor, a ver
- Artículos sueltos: un, una, el, la, los, las
- Conectores entre bloques: para, de (solo si separa cliente de cantidad), del, al
- Adverbios: también, además, después, ahora, igual

PALABRAS QUE DEBES PRESERVAR EN EL PRODUCTO:
- «de» + calificador: bieleta DE suspension, buje DE directa, filtro DE aceite
- Marca/condición: original, generico, nakata, skf
- Vehículo: 207, gol, partner, corsa (campo vehiculo, no cantidad)

REGLAS DE CANTIDAD:
- «dos bieletas», «2 unidades», «un par» → cantidad numérica
- «207» después de repuesto = vehículo Peugeot 207, NO cantidad 207
- «de 2 bieletas» después del nombre del cliente = separador, no apellido

NOMBRES DE CLIENTE:
- Pueden tener 2, 3 o 4 palabras: Juan Guzmán, Carlos Alberto Poccia, Taller San Martín
- Todo el bloque va en nombre_cliente; ninguna palabra del nombre en items
"""
