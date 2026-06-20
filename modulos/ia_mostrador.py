"""IA de voz/texto exclusiva del mostrador (ventas, cliente, facturación)."""
import json
import os
import re

import streamlit as st
from dotenv import load_dotenv
from groq import Groq  # type: ignore

from modulos.ia_asistente import normalizar_texto_basico

load_dotenv(override=True)

FORMAS_PAGO = ("Contado", "Transferencia", "Tarjeta", "Cheque", "MercadoPago")

_MODELO_RAPIDO = "llama-3.1-8b-instant"
_MODELO_FLUJO = "llama-3.3-70b-versatile"


def normalizar_forma_pago(texto):
    if not texto:
        return "Contado"
    t = str(texto).strip().lower()
    if "transfer" in t:
        return "Transferencia"
    if "tarjeta" in t:
        return "Tarjeta"
    if "cheque" in t:
        return "Cheque"
    if "mercado" in t or "mp" == t:
        return "MercadoPago"
    if "contado" in t or "efectivo" in t or "cash" in t:
        return "Contado"
    for fp in FORMAS_PAGO:
        if fp.lower() in t:
            return fp
    return "Contado"


def es_confirmacion_usuario(texto):
    t = str(texto or "").strip().lower()
    return bool(re.search(
        r"\b(si|sí|dale|confirmá|confirmar|confirmalo|ok|de acuerdo|hacelo|"
        r"procedé|procede|listo|avanzá|avanza)\b",
        t,
    ))


def es_cancelacion_usuario(texto):
    t = str(texto or "").strip().lower()
    return bool(re.search(r"\b(no|cancelá|cancelar|anulá|anular|detener|pará)\b", t))


def parece_orden_voz_mostrador(texto):
    """True si el texto es una orden compuesta (no búsqueda de producto)."""
    from modulos.mostrador_voz_flujo import extraer_items_orden_voz

    t = str(texto or "").strip()
    if len(t) < 5:
        return False
    if parse_flujo_rapido_voz(t):
        return True
    if extraer_items_orden_voz(t):
        return True
    tl = t.lower()
    if re.search(
        r"\b(factura|presupuesto|cliente|codigo|código|descripcion|descripción|desc|"
        r"agreg\w*|sum\w*|listo|consumidor)\b",
        tl,
    ):
        return True
    return False


def _orden_es_flujo_complejo(texto):
    t = str(texto or "").lower()
    señales = (
        "factura", "cliente", "agreg", "sumá", "poneme", "código", "codigo",
        "unidades", "contado", "imprimir", "ticket", "consumidor",
    )
    return sum(1 for s in señales if s in t) >= 2


def _es_comando_corto(texto):
    t = re.sub(r"\s+", " ", str(texto or "").strip().lower())
    return len(t.split()) <= 3


def parse_flujo_rapido_voz(texto_usuario):
    """Detecta órdenes compuestas sin Groq (cliente + varios ítems + listo)."""
    from modulos.mostrador_voz_flujo import extraer_items_orden_voz, extraer_cliente_orden_voz

    raw = str(texto_usuario or "").strip()
    t = normalizar_texto_basico(raw).lower()
    items = extraer_items_orden_voz(raw)
    cliente_info = extraer_cliente_orden_voz(raw)
    nombre_cliente = cliente_info.get("nombre_cliente")
    consumidor_final = cliente_info.get("consumidor_final")

    es_armado = bool(
        re.search(r"\b(carg\w*|hac\w*|arm\w*)\b", t)
        or re.search(r"(?:^|\s)(rgame|cargame|haceme|armeme|armame)\b", t)
    )
    es_presupuesto = bool(re.search(r"\bpresupuesto\b", t))
    tiene_factura = bool(re.search(r"\bfactura\b", t))
    orden_nueva = bool(
        (es_presupuesto or tiene_factura)
        and (nombre_cliente or consumidor_final)
        and items
    )
    ir_verificacion = bool(
        re.search(r"\b(listo|termine|terminé|fin)\b", t)
        or orden_nueva
    )

    if not items and not nombre_cliente and not consumidor_final:
        return None

    es_flujo = bool(
        es_armado or es_presupuesto or tiene_factura
        or (items and (nombre_cliente or consumidor_final))
        or (es_presupuesto and items)
        or (tiene_factura and items)
        or (es_presupuesto and nombre_cliente)
        or (tiene_factura and nombre_cliente)
    )
    if not es_flujo:
        return None

    flujo = {
        "accion": "flujo_factura",
        "vaciar_antes": es_armado or orden_nueva,
        "ir_verificacion": ir_verificacion,
        "imprimir_ticket": False,
    }

    if es_presupuesto:
        flujo["intent_sugerido"] = "presupuesto"
    elif re.search(r"factura\s+a\b", t):
        flujo["tipo_comprobante"] = "1"
        flujo["intent_sugerido"] = "factura_a"
    elif tiene_factura:
        flujo["tipo_comprobante"] = "6"
        flujo["intent_sugerido"] = "factura_b"

    if consumidor_final:
        flujo["consumidor_final"] = True
    elif nombre_cliente:
        flujo["nombre_cliente"] = nombre_cliente

    if items:
        flujo["items"] = items

    if re.search(r"contado|efectivo", t):
        flujo["forma_pago"] = "Contado"
    elif re.search(r"transfer", t):
        flujo["forma_pago"] = "Transferencia"
    elif re.search(r"tarjeta", t):
        flujo["forma_pago"] = "Tarjeta"
    elif re.search(r"cheque", t):
        flujo["forma_pago"] = "Cheque"
    elif re.search(r"mercado", t):
        flujo["forma_pago"] = "MercadoPago"

    return flujo


def parse_armado_rapido_voz(texto_usuario):
    """Atajos para armado continuo de presupuesto/venta (sin Groq)."""
    from modulos.mostrador_voz_flujo import extraer_items_orden_voz, preprocesar_texto_mostrador

    raw = str(texto_usuario or "").strip()
    t = preprocesar_texto_mostrador(raw).strip().lower()
    if not t:
        return None

    if parse_flujo_rapido_voz(raw):
        return None

    items = extraer_items_orden_voz(raw)

    if re.match(r"^(presupuesto|imprimir presupuesto|pdf presupuesto)\.?$", t):
        return {"accion": "listo_armado", "intent_sugerido": "presupuesto"}
    if re.search(r"\bpresupuesto\b", t):
        if items:
            flujo = parse_flujo_rapido_voz(raw)
            if flujo:
                return flujo
            return {
                "accion": "flujo_factura",
                "intent_sugerido": "presupuesto",
                "items": items,
                "vaciar_antes": False,
                "ir_verificacion": bool(re.search(r"\b(listo|termine|terminé|fin)\b", t)),
            }
    if re.search(r"\bfactura\b", t) and items:
        flujo = parse_flujo_rapido_voz(raw)
        if flujo:
            return flujo
    if t in ("listo", "termine", "terminé", "fin"):
        return {"accion": "listo_armado"}

    if items:
        return {"accion": "agregar_items", "items": items}

    m = re.match(r"^([\dA-Za-z][\dA-Za-z_\-]*)\s+(\d{1,4})$", raw.strip())
    if m:
        return {
            "accion": "agregar_items",
            "items": [{"termino": m.group(1), "cantidad": int(m.group(2))}],
        }

    if re.match(r"^(agreg\w*|sum\w*)\s+", t):
        resto = re.sub(r"^(agreg\w*|sum\w*)\s+", "", raw, flags=re.I).strip()
        items2 = extraer_items_orden_voz(resto) or extraer_items_orden_voz(raw)
        if items2:
            return {"accion": "agregar_items", "items": items2}
        if resto:
            return {"accion": "agregar_carrito", "termino": resto, "cantidad": 1}

    resto_busq = re.sub(
        r"^(buscar|busca|codigo|código|descripcion|descripción|desc)\s+",
        "",
        raw.strip(),
        flags=re.I,
    ).strip()
    if resto_busq and len(resto_busq) >= 2 and not _orden_es_flujo_complejo(raw):
        return {"accion": "buscar", "termino": resto_busq}

    return None


def parse_rapido_voz(texto_usuario):
    """Atajos sin llamar a Groq (más rápido)."""
    from modulos.mostrador_voz_flujo import preprocesar_texto_mostrador

    t = preprocesar_texto_mostrador(texto_usuario).strip().lower()
    if re.match(r"^(imprimir|imprimí|imprime|ticket|facturá|factura)\.?$", t):
        return {"accion": "imprimir_ticket"}
    if re.search(r"\bfactura\s+b\b", t) and re.search(r"\bimprimir\b", t):
        return None
    if t in ("consumidor final", "particular"):
        return {"accion": "consumidor_final"}
    return None


def _fallback_orden_local(texto_usuario):
    """Último intento local antes de Groq o error."""
    from modulos.mostrador_voz_flujo import extraer_items_orden_voz

    raw = str(texto_usuario or "").strip()
    if not raw:
        return None

    items = extraer_items_orden_voz(raw)
    tl = raw.lower()
    if items:
        if re.search(r"\bpresupuesto\b", tl):
            return {
                "accion": "flujo_factura",
                "intent_sugerido": "presupuesto",
                "items": items,
                "vaciar_antes": False,
                "ir_verificacion": True,
            }
        if re.search(r"\bfactura\b", tl):
            flujo = parse_flujo_rapido_voz(raw)
            if flujo:
                return flujo
        return {"accion": "agregar_items", "items": items}

    termino = re.sub(
        r"^(buscar|busca|codigo|código|descripcion|descripción|desc|producto)\s+",
        "",
        raw,
        flags=re.I,
    ).strip()
    if len(termino) >= 2:
        return {"accion": "agregar_carrito", "termino": termino, "cantidad": 1}
    return None


def procesar_orden_mostrador(texto_usuario):
    if es_cancelacion_usuario(texto_usuario) and _es_comando_corto(texto_usuario):
        return {"accion": "cancelar_pendiente"}

    flujo_rapido = parse_flujo_rapido_voz(texto_usuario)
    if flujo_rapido:
        return flujo_rapido

    if es_confirmacion_usuario(texto_usuario) and _es_comando_corto(texto_usuario):
        if st.session_state.get("mostrador_accion_pendiente"):
            return {"accion": "confirmar_pendiente"}
        return {"accion": "listo_armado"}

    armado = parse_armado_rapido_voz(texto_usuario)
    if armado:
        return armado

    rapido = parse_rapido_voz(texto_usuario)
    if rapido:
        return rapido

    fallback = _fallback_orden_local(texto_usuario)
    if fallback:
        return fallback

    api_key = os.getenv("GROQ_API_KEY")
    if not api_key:
        try:
            api_key = st.secrets["GROQ_API_KEY"]
        except Exception:
            api_key = None

    if not api_key:
        fb = _fallback_orden_local(texto_usuario)
        if fb:
            return fb
        return {
            "accion": "error",
            "respuesta": "Orden no reconocida. Ejemplo: «presupuesto buje directa 3 unidades» o «código 111 2 unidades».",
        }

    client = Groq(api_key=api_key)
    from modulos.mostrador_voz_flujo import preprocesar_texto_mostrador

    texto_procesado = preprocesar_texto_mostrador(texto_usuario)
    es_flujo = _orden_es_flujo_complejo(texto_procesado)
    modelo = _MODELO_FLUJO if es_flujo else _MODELO_RAPIDO

    prompt = f"""
    Eres el asistente de MOSTRADOR / CAJA de "Hafid Repuestos".
    Interpretás órdenes de venta y facturación fiscal ARCA/AFIP.

    ORDEN: "{texto_procesado}"

    REGLAS:
    - Si la orden tiene VARIAS partes (cliente + productos + pago + imprimir), usá "flujo_factura".
    - "Factura B" -> tipo_comprobante "6". "Factura A" -> "1".
    - "Imprimir" / "ticket" al final -> imprimir_ticket true (arma carrito; NO factura sola: el usuario revisa la grilla y confirma).
    - Códigos de producto: números o CODIGO_MARCA (ej. 3524150, F00099C125_GENERICO).
    - "Agregá código X 5 unidades" -> items con termino X y cantidad 5.
    - "Cargame factura B para [cliente]" -> nombre_cliente + tipo 6 + vaciar_antes true si dice cargar/cargame.
    - Una sola acción simple -> acciones individuales abajo.

    FLUJO COMPLETO (preferido si hay varias instrucciones en una frase):
    {{
      "accion": "flujo_factura",
      "vaciar_antes": true/false,
      "tipo_comprobante": "6" o "1",
      "nombre_cliente": "NOMBRE o null",
      "consumidor_final": true/false,
      "items": [{{"termino": "CODIGO_O_DESC", "cantidad": N}}],
      "forma_pago": "Contado|Transferencia|Tarjeta|Cheque|MercadoPago",
      "imprimir_ticket": true/false
    }}

    ACCIONES SIMPLES (una sola):
    {{"accion": "agregar_carrito", "termino": "RAIZ", "cantidad": N}}
    {{"accion": "buscar", "termino": "RAIZ"}}
    {{"accion": "set_cliente", "nombre_cliente": "NOMBRE", "tipo_comprobante": "6" o "1" o null}}
    {{"accion": "set_tipo_factura", "tipo_comprobante": "6" o "1"}}
    {{"accion": "consumidor_final", "tipo_comprobante": "6"}}
    {{"accion": "set_forma_pago", "forma_pago": "Contado"}}
    {{"accion": "imprimir_ticket"}}
    {{"accion": "presupuesto_pdf"}}
    {{"accion": "agregar_items", "items": [{{"termino": "CODIGO_O_DESC", "cantidad": N}}]}}
    {{"accion": "listo_armado"}}
    {{"accion": "confirmar_venta"}}
    {{"accion": "facturar"}}
    {{"accion": "vaciar_carrito"}}
    {{"accion": "consulta", "respuesta": "..."}}

    Devolvé SOLO JSON válido.
    """

    try:
        chat_completion = client.chat.completions.create(
            messages=[{"role": "user", "content": prompt}],
            model=modelo,
            temperature=0.0,
            response_format={"type": "json_object"},
        )
        texto = chat_completion.choices[0].message.content.strip()  # type: ignore
        texto = texto.replace("```json", "").replace("```", "").strip()
        return json.loads(texto)
    except Exception as e:
        return {"accion": "error", "respuesta": f"Error en lectura de IA: {str(e)}"}
