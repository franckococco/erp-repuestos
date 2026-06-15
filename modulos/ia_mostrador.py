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


def _orden_es_flujo_complejo(texto):
    t = str(texto or "").lower()
    señales = (
        "factura", "cliente", "agreg", "sumá", "poneme", "código", "codigo",
        "unidades", "contado", "imprimir", "ticket", "consumidor",
    )
    return sum(1 for s in señales if s in t) >= 2


def parse_flujo_rapido_voz(texto_usuario):
    """Detecta órdenes compuestas sin Groq (más rápido)."""
    from modulos.mostrador_voz_flujo import extraer_items_orden_voz

    t = normalizar_texto_basico(texto_usuario).lower()
    imprimir = bool(re.search(r"\bimprimir\b|\bticket\b|\bfactur", t))
    if not imprimir:
        return None
    if sum(
        1 for s in ("factura", "cliente", "agreg", "codigo", "unidad", "contado", "para ")
        if s in t
    ) < 2:
        return None

    flujo = {
        "accion": "flujo_factura",
        "vaciar_antes": bool(re.search(r"\bcarg", t)),
        "imprimir_ticket": True,
    }
    if re.search(r"factura\s+b\b", t):
        flujo["tipo_comprobante"] = "6"
    elif re.search(r"factura\s+a\b", t):
        flujo["tipo_comprobante"] = "1"

    if re.search(r"consumidor\s+final|particular", t):
        flujo["consumidor_final"] = True
    else:
        m_cli = re.search(
            r"(?:para|cliente)\s+(.+?)(?:,|\s+(?:agreg|codigo|forma|pago|imprimir|factura)\b)",
            t,
        )
        if m_cli:
            flujo["nombre_cliente"] = m_cli.group(1).strip().upper()

    items = extraer_items_orden_voz(texto_usuario)
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

    if flujo.get("items") or flujo.get("nombre_cliente") or flujo.get("consumidor_final"):
        return flujo
    return None


def parse_armado_rapido_voz(texto_usuario):
    """Atajos para armado continuo de presupuesto/venta (sin Groq)."""
    from modulos.mostrador_voz_flujo import extraer_items_orden_voz, preprocesar_texto_mostrador

    raw = str(texto_usuario or "").strip()
    t = preprocesar_texto_mostrador(raw).strip().lower()
    if not t:
        return None

    if re.match(r"^(presupuesto|imprimir presupuesto|pdf presupuesto)\.?$", t):
        return {"accion": "presupuesto_pdf"}
    if t in ("listo", "termine", "terminé", "fin"):
        return {"accion": "listo_armado"}

    items = extraer_items_orden_voz(raw)
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


def procesar_orden_mostrador(texto_usuario):
    api_key = os.getenv("GROQ_API_KEY")
    if not api_key:
        try:
            api_key = st.secrets["GROQ_API_KEY"]
        except Exception:
            api_key = None

    if not api_key:
        return {"accion": "error", "respuesta": "Falta configurar la GROQ_API_KEY en los secretos."}

    if es_confirmacion_usuario(texto_usuario):
        if st.session_state.get("mostrador_accion_pendiente"):
            return {"accion": "confirmar_pendiente"}
        return {"accion": "listo_armado"}
    if es_cancelacion_usuario(texto_usuario):
        return {"accion": "cancelar_pendiente"}

    armado = parse_armado_rapido_voz(texto_usuario)
    if armado:
        return armado

    rapido = parse_rapido_voz(texto_usuario)
    if rapido:
        return rapido

    flujo_rapido = parse_flujo_rapido_voz(texto_usuario)
    if flujo_rapido:
        return flujo_rapido

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
