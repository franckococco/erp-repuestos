"""Interpretación de órdenes del mostrador: Groq primero, salida rígida, parser local de respaldo."""
from __future__ import annotations

import hashlib
import json
import os
import re
import time
from typing import Any, Optional

import streamlit as st
from dotenv import load_dotenv
from groq import Groq  # type: ignore

load_dotenv(override=True)

_MODELO_FLUJO = "llama-3.3-70b-versatile"
_MODELO_RAPIDO = "llama-3.1-8b-instant"

_ACCIONES_VALIDAS = frozenset({
    "flujo_factura", "agregar_carrito", "buscar", "set_cliente", "set_tipo_factura",
    "consumidor_final", "set_forma_pago", "imprimir_ticket", "presupuesto_pdf",
    "agregar_items", "listo_armado", "confirmar_venta", "facturar", "vaciar_carrito",
    "consulta", "cancelar_pendiente", "confirmar_pendiente", "error",
})


def _groq_api_key() -> Optional[str]:
    key = os.getenv("GROQ_API_KEY")
    if key:
        return key
    try:
        return st.secrets["GROQ_API_KEY"]
    except Exception:
        return None


def orden_compuesta_requiere_groq(texto: str) -> bool:
    """True si la frase parece venta/presupuesto compuesto (no comando corto)."""
    from modulos.ia_mostrador import _es_comando_corto, parece_orden_voz_mostrador

    raw = str(texto or "").strip()
    if len(raw) < 8:
        return False
    if _es_comando_corto(raw):
        return False
    if not parece_orden_voz_mostrador(raw):
        return False
    tl = raw.lower()
    if re.match(r"^[\dA-Za-z][\dA-Za-z_\-]*\s+\d{1,4}$", raw.strip()):
        return False
    if re.match(r"^(buscar|busca|codigo|código)\s+\S+$", tl):
        return False
    return True


def _prompt_groq_orden_mostrador(texto_procesado: str) -> str:
    from modulos.voz_lenguaje_natural import instrucciones_groq_lenguaje_natural

    return f"""
Sos el intérprete de órdenes del mostrador "Hafid Repuestos" (repuestos automotrices, Argentina).
La frase puede venir en CUALQUIER ORDEN de palabras; el significado no cambia.

ORDEN (ya preprocesada): "{texto_procesado}"

{instrucciones_groq_lenguaje_natural()}

REGLAS CRÍTICAS:
1. Separá SIEMPRE: cliente (persona) | productos/repuestos | vehículo (207, gol, etc.) | cantidad | acción.
2. Nombres de cliente pueden tener VARIAS palabras: "Carlos Alberto Poccia" → nombre_cliente completo.
   NUNCA uses parte del nombre como producto.
3. "de" antes de cantidad/repuesto NO es apellido: "para Juan de 2 bieletas" → cliente Juan, producto bieleta x2.
4. presupuesto/cotización → intent presupuesto. factura A → tipo 1. factura B → tipo 6.
5. listo/terminé al final → ir_verificacion true.
6. Códigos: 111, 3524150, CODIGO_MARCA. Repuestos: bieleta, buje, ruleman, etc.
7. vehiculo en cada ítem solo si aplica a ESE repuesto (ej. bieleta 207).

EJEMPLOS (misma salida, distinto orden):
- "presupuesto para Carlos Alberto Poccia de 2 bieletas suspension 207"
- "Carlos Alberto Poccia presupuesto 2 bieletas de suspension para el 207"
→ cliente Carlos Alberto Poccia, items [{{termino: bieleta suspension, cantidad: 2, vehiculo: 207}}], intent presupuesto

- "para el cliente Pablo Castellanos presupuesto codigo 111 3 unidades"
→ cliente Pablo Castellanos, items [{{termino: 111, cantidad: 3}}], intent presupuesto

- "che bueno dale haceme un presupuesto para juan guzman bieleta de suspension 207"
→ cliente Juan Guzman, items [{{termino: bieleta suspension, cantidad: 1, vehiculo: 207}}], intent presupuesto

- "necesito factura b para taller san martin dos bujes de directa para el gol contado listo"
→ cliente Taller San Martin, factura B, items buje directa x2 vehiculo gol, pago Contado, ir_verificacion true

Si hay cliente + productos o presupuesto/factura + productos → accion "flujo_factura".

JSON flujo_factura:
{{
  "accion": "flujo_factura",
  "vaciar_antes": true/false,
  "ir_verificacion": true/false,
  "intent_sugerido": "presupuesto" | "factura_a" | "factura_b",
  "tipo_comprobante": "6" o "1" o null,
  "nombre_cliente": "NOMBRE COMPLETO" o null,
  "consumidor_final": true/false,
  "items": [{{"termino": "DESC O CODIGO", "cantidad": N, "vehiculo": "207" o null}}],
  "forma_pago": "Contado|Transferencia|Tarjeta|Cheque|MercadoPago" o null,
  "imprimir_ticket": false
}}

Acciones simples (una sola cosa):
{{"accion": "agregar_carrito", "termino": "...", "cantidad": N}}
{{"accion": "agregar_items", "items": [...]}}
{{"accion": "buscar", "termino": "..."}}
{{"accion": "listo_armado", "intent_sugerido": "presupuesto"}}
{{"accion": "presupuesto_pdf"}}
{{"accion": "imprimir_ticket"}}
{{"accion": "set_cliente", "nombre_cliente": "...", "tipo_comprobante": "6"}}
{{"accion": "consumidor_final", "tipo_comprobante": "6"}}

Devolvé SOLO JSON válido, sin markdown.
"""


def _normalizar_items(items: Any) -> list[dict]:
    from modulos.util_busqueda import parece_codigo_producto
    from modulos.voz_repuestos import corregir_termino_repuesto

    if not isinstance(items, list):
        return []
    out = []
    for raw in items:
        if not isinstance(raw, dict):
            continue
        term = str(raw.get("termino") or raw.get("producto") or "").strip()
        if not term:
            continue
        term = corregir_termino_repuesto(term).upper()
        try:
            cant = max(1, int(raw.get("cantidad", 1)))
        except (TypeError, ValueError):
            cant = 1
        modo = str(raw.get("modo") or "").lower()
        if modo not in ("codigo", "descripcion"):
            modo = "codigo" if parece_codigo_producto(term) else "descripcion"
        item = {"termino": term, "cantidad": cant, "modo": modo}
        veh = raw.get("vehiculo")
        if veh:
            item["vehiculo"] = str(veh).strip()
        out.append(item)
    return out


def _elegir_mejor_nombre_cliente(groq_nombre, local_nombre) -> Optional[str]:
    from modulos.cliente_resolver import corregir_nombre_con_clientes

    g = str(groq_nombre or "").strip().upper()
    l = str(local_nombre or "").strip().upper()
    if not l:
        candidato = g or None
    elif not g:
        candidato = l
    elif len(l.split()) > len(g.split()):
        candidato = l
    elif l.startswith(g) or g in l.split():
        candidato = l
    else:
        candidato = g
    if candidato:
        return corregir_nombre_con_clientes(candidato)
    return None


def _limpiar_termino_de_cliente(termino: str, nombre_cliente: str) -> str:
    if not nombre_cliente or not termino:
        return termino
    tokens_cli = set(nombre_cliente.upper().split())
    tokens_term = [t for t in str(termino).upper().split() if t not in tokens_cli]
    limpio = " ".join(tokens_term).strip()
    return limpio if len(limpio) >= 2 else termino


def _items_parecen_contaminados(items, nombre_cliente: str) -> bool:
    if not nombre_cliente or not items:
        return False
    tokens = nombre_cliente.upper().split()
    if len(tokens) < 2:
        return False
    apellido = tokens[-1]
    for it in items:
        if not isinstance(it, dict):
            continue
        partes = str(it.get("termino", "")).upper().split()
        if apellido in partes and not all(p in tokens for p in partes):
            return True
    return False


def _limpiar_items_de_cliente(items, nombre_cliente: str) -> list[dict]:
    out = []
    for raw in items or []:
        if not isinstance(raw, dict):
            continue
        it = dict(raw)
        term = _limpiar_termino_de_cliente(str(it.get("termino", "")), nombre_cliente)
        it["termino"] = term
        out.append(it)
    return out


def normalizar_accion_mostrador(data: dict, texto_original: str = "") -> dict:
    """Convierte respuesta Groq/local a contrato rígido del mostrador."""
    from modulos.mostrador_voz_flujo import (
        extraer_cliente_orden_voz,
        extraer_items_orden_voz,
        normalizar_orden_voz_mostrador,
    )

    if not isinstance(data, dict):
        return {"accion": "error", "respuesta": "Respuesta inválida del intérprete."}

    accion = str(data.get("accion") or "").strip()
    if accion not in _ACCIONES_VALIDAS:
        return {"accion": "error", "respuesta": f"Acción no reconocida: {accion or '?'}"}

    texto = str(texto_original or "").strip()
    norm = normalizar_orden_voz_mostrador(texto).lower() if texto else ""

    if accion == "flujo_factura":
        flujo = dict(data)
        flujo["accion"] = "flujo_factura"
        flujo["imprimir_ticket"] = bool(flujo.get("imprimir_ticket"))

        nombre = flujo.get("nombre_cliente")
        if nombre:
            flujo["nombre_cliente"] = str(nombre).strip().upper()
        elif flujo.get("consumidor_final"):
            flujo.pop("nombre_cliente", None)
        elif texto:
            cli_local = extraer_cliente_orden_voz(texto)
            if cli_local.get("nombre_cliente"):
                flujo["nombre_cliente"] = cli_local["nombre_cliente"]
            elif cli_local.get("consumidor_final"):
                flujo["consumidor_final"] = True

        items = _normalizar_items(flujo.get("items"))
        if not items and texto:
            items = extraer_items_orden_voz(texto)
        nombre_cli = flujo.get("nombre_cliente")
        if texto and nombre_cli:
            cli_local = extraer_cliente_orden_voz(texto).get("nombre_cliente")
            nombre_cli = _elegir_mejor_nombre_cliente(nombre_cli, cli_local) or nombre_cli
            flujo["nombre_cliente"] = nombre_cli
        if items and nombre_cli and _items_parecen_contaminados(items, nombre_cli):
            items = extraer_items_orden_voz(texto) if texto else items
        if items and nombre_cli:
            items = _limpiar_items_de_cliente(items, nombre_cli)
            items = _normalizar_items(items)
        flujo["items"] = items

        intent = flujo.get("intent_sugerido")
        if not intent:
            if re.search(r"\bpresupuesto\b", norm):
                intent = "presupuesto"
            elif re.search(r"factura\s+a\b", norm):
                intent = "factura_a"
            elif re.search(r"\bfactura\b", norm):
                intent = "factura_b"
        if intent in ("presupuesto", "factura_a", "factura_b"):
            flujo["intent_sugerido"] = intent
        if intent == "presupuesto":
            flujo.pop("tipo_comprobante", None)
        elif intent == "factura_a":
            flujo["tipo_comprobante"] = "1"
        elif intent == "factura_b":
            flujo["tipo_comprobante"] = "6"

        tc = flujo.get("tipo_comprobante")
        if tc in ("1", "6") and not flujo.get("intent_sugerido"):
            flujo["intent_sugerido"] = "factura_a" if tc == "1" else "factura_b"

        if flujo.get("forma_pago"):
            from modulos.ia_mostrador import normalizar_forma_pago
            flujo["forma_pago"] = normalizar_forma_pago(flujo["forma_pago"])

        if flujo.get("ir_verificacion") is None:
            flujo["ir_verificacion"] = bool(
                re.search(r"\b(listo|termine|terminé|fin|dale)\b", norm)
                or (flujo.get("items") and (flujo.get("nombre_cliente") or flujo.get("consumidor_final")))
            )

        if flujo.get("vaciar_antes") is None:
            flujo["vaciar_antes"] = bool(
                re.search(r"\b(carg\w*|hac\w*|arm\w*|met\w*|necesito|quiero)\b", norm)
                and (flujo.get("intent_sugerido") or flujo.get("items"))
            )

        return flujo

    if accion == "agregar_items":
        return {"accion": "agregar_items", "items": _normalizar_items(data.get("items"))}

    if accion == "agregar_carrito":
        term = data.get("termino")
        if term:
            from modulos.voz_repuestos import corregir_termino_repuesto
            term = corregir_termino_repuesto(str(term)).upper()
        try:
            cant = max(1, int(data.get("cantidad", 1)))
        except (TypeError, ValueError):
            cant = 1
        return {"accion": "agregar_carrito", "termino": term, "cantidad": cant}

    if accion == "set_cliente" and data.get("nombre_cliente"):
        out = dict(data)
        out["nombre_cliente"] = str(out["nombre_cliente"]).strip().upper()
        return out

    return data


def fusionar_con_parser_local(groq_data: dict, texto_original: str) -> dict:
    """Completa huecos del JSON de Groq con el parser local (sin contradecir Groq)."""
    from modulos.mostrador_voz_flujo import interpretar_orden_voz_mostrador

    local = interpretar_orden_voz_mostrador(texto_original)
    out = dict(groq_data)
    if out.get("accion") != "flujo_factura":
        return out

    cli = local.get("cliente") or {}
    local_cli = cli.get("nombre_cliente")
    mejor_cli = _elegir_mejor_nombre_cliente(out.get("nombre_cliente"), local_cli)
    if mejor_cli:
        out["nombre_cliente"] = mejor_cli
    elif cli.get("consumidor_final"):
        out["consumidor_final"] = True

    local_items = local.get("items") or []
    groq_items = out.get("items") or []
    if local_items and (
        not groq_items or _items_parecen_contaminados(groq_items, mejor_cli or local_cli or "")
    ):
        out["items"] = local_items
    elif groq_items:
        out["items"] = _limpiar_items_de_cliente(groq_items, mejor_cli or local_cli or "")
    elif local_items:
        out["items"] = local_items

    if not out.get("intent_sugerido") and local.get("intent"):
        out["intent_sugerido"] = local["intent"]

    if not out.get("forma_pago") and local.get("forma_pago"):
        out["forma_pago"] = local["forma_pago"]

    if local.get("listo"):
        out["ir_verificacion"] = True

    return out


_GROQ_CACHE_TTL_SEG = 3600


def _groq_cache_key(texto_procesado: str, modelo: str) -> str:
    digest = hashlib.sha256(f"{modelo}:{texto_procesado}".encode("utf-8")).hexdigest()[:20]
    return digest


def _groq_cache_get(texto_procesado: str, modelo: str) -> Optional[dict]:
    cache = st.session_state.get("_groq_orden_cache") or {}
    key = _groq_cache_key(texto_procesado, modelo)
    entry = cache.get(key)
    if entry and time.time() - float(entry.get("ts", 0)) < _GROQ_CACHE_TTL_SEG:
        return entry.get("data")
    return None


def _groq_cache_set(texto_procesado: str, modelo: str, data: dict):
    cache = dict(st.session_state.get("_groq_orden_cache") or {})
    key = _groq_cache_key(texto_procesado, modelo)
    cache[key] = {"ts": time.time(), "data": data}
    if len(cache) > 80:
        ordenado = sorted(cache.items(), key=lambda x: x[1]["ts"], reverse=True)
        cache = dict(ordenado[:60])
    st.session_state["_groq_orden_cache"] = cache


def interpretar_orden_groq(texto_usuario: str) -> Optional[dict]:
    """Llama a Groq y devuelve acción normalizada, o None si no hay API/falla."""
    api_key = _groq_api_key()
    if not api_key:
        return None

    from modulos.mostrador_voz_flujo import preprocesar_texto_mostrador
    from modulos.ia_mostrador import _orden_es_flujo_complejo

    texto_procesado = preprocesar_texto_mostrador(texto_usuario)
    modelo = _MODELO_FLUJO if _orden_es_flujo_complejo(texto_procesado) else _MODELO_RAPIDO

    cached = _groq_cache_get(texto_procesado, modelo)
    if cached is not None:
        return cached

    try:
        client = Groq(api_key=api_key)
        chat_completion = client.chat.completions.create(
            messages=[{"role": "user", "content": _prompt_groq_orden_mostrador(texto_procesado)}],
            model=modelo,
            temperature=0.0,
            response_format={"type": "json_object"},
        )
        texto = chat_completion.choices[0].message.content.strip()  # type: ignore
        texto = texto.replace("```json", "").replace("```", "").strip()
        data = json.loads(texto)
    except Exception:
        return None

    data = fusionar_con_parser_local(data, texto_usuario)
    result = normalizar_accion_mostrador(data, texto_usuario)
    if result:
        _groq_cache_set(texto_procesado, modelo, result)
    return result
