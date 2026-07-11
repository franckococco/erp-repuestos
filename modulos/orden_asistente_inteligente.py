"""Interpretación de órdenes del asistente de depósito: Groq + normalización."""
from __future__ import annotations

import hashlib
import json
import os
import re
import time
from typing import Any, Optional

import streamlit as st
from dotenv import load_dotenv

load_dotenv(override=True)

_MODELO_FLUJO = "llama-3.3-70b-versatile"
_MODELO_RAPIDO = "llama-3.1-8b-instant"

_ACCIONES_VALIDAS = frozenset({
    "buscar", "reporte_stock", "actualizar_ubicacion", "alta", "baja",
    "cargar_producto", "filtrar_proveedor", "agregar_descripcion",
    "cambiar_marca", "cambiar_vehiculos", "set_cliente", "agregar_carrito", "error",
})

_GROQ_CACHE_TTL_SEG = 3600


def _groq_api_key() -> Optional[str]:
    key = os.getenv("GROQ_API_KEY")
    if key:
        return key
    try:
        return st.secrets["GROQ_API_KEY"]
    except Exception:
        return None


def _orden_requiere_modelo_grande(texto: str) -> bool:
    t = str(texto or "").lower()
    señales = (
        "pasillo", "piso", "modulo", "fila", "fondo", "descripcion", "vehiculo",
        "proveedor", "marca", "ubicacion", "cargar", "registr", "unidades",
    )
    return sum(1 for s in señales if s in t) >= 2 or len(t.split()) > 12


def _prompt_groq_orden_deposito(texto_procesado: str) -> str:
    from modulos.voz_lenguaje_natural import instrucciones_groq_deposito

    return f"""
Sos el intérprete del asistente de depósito "Hafid Repuestos" (repuestos automotrices, Argentina).
La frase puede venir en CUALQUIER ORDEN de palabras; el significado no cambia.

ORDEN (ya preprocesada): "{texto_procesado}"

{instrucciones_groq_deposito()}

REGLAS CRÍTICAS:
1. Separá SIEMPRE: intención | término/código | cantidad | ubicación | proveedor.
2. buscar: consultas de stock por palabra o código. termino limpio sin muletillas.
3. alta/baja: sumar o restar unidades a código existente (sin descripción de producto nuevo).
4. cargar_producto: código + descripción (repuesto nuevo o con datos completos).
5. actualizar_ubicacion: código + pasillo/piso/modulo/fila/fondo mencionados.
6. reporte_stock: operador exacto | menor_o_igual | mayor_o_igual + cantidad.
7. filtrar_proveedor: solo raíz del nombre (expoyer, filtrum).

EJEMPLOS:
- "buscar buje de directa para el gol" → buscar, termino: "buje directa gol"
- "sumar 3 codigo 1491" → alta, termino: "1491", cantidad: 3
- "cargar codigo 25412 buje amortiguador gol 4 unidades pasillo 2 piso 1" → cargar_producto
- "1491 pasillo 2 piso 1 modulo 3" → actualizar_ubicacion
- "reporte menos de 3" → reporte_stock, operador menor_o_igual, cantidad 3
- "proveedor expoyer" → filtrar_proveedor

Devolvé SOLO JSON válido con UNA acción, sin markdown.
"""


def _groq_cache_key(texto_procesado: str, modelo: str) -> str:
    digest = hashlib.sha256(f"{modelo}:{texto_procesado}".encode("utf-8")).hexdigest()[:20]
    return digest


def _groq_cache_get(texto_procesado: str, modelo: str) -> Optional[dict]:
    cache = st.session_state.get("_groq_asistente_cache") or {}
    key = _groq_cache_key(texto_procesado, modelo)
    entry = cache.get(key)
    if entry and time.time() - float(entry.get("ts", 0)) < _GROQ_CACHE_TTL_SEG:
        return entry.get("data")
    return None


def _groq_cache_set(texto_procesado: str, modelo: str, data: dict):
    cache = dict(st.session_state.get("_groq_asistente_cache") or {})
    key = _groq_cache_key(texto_procesado, modelo)
    cache[key] = {"ts": time.time(), "data": data}
    if len(cache) > 80:
        ordenado = sorted(cache.items(), key=lambda x: x[1]["ts"], reverse=True)
        cache = dict(ordenado[:60])
    st.session_state["_groq_asistente_cache"] = cache


def normalizar_accion_asistente(data: dict, texto_original: str = "") -> dict:
    """Convierte respuesta Groq/local al contrato del asistente."""
    from modulos.ia_asistente import (
        _limpiar_termino_busqueda,
        es_consulta_mayor_o_igual,
    )
    from modulos.voz_repuestos import corregir_termino_repuesto

    if not isinstance(data, dict):
        return {"accion": "error", "respuesta": "Respuesta inválida del intérprete."}

    accion = str(data.get("accion") or "").strip()
    if accion not in _ACCIONES_VALIDAS:
        return {"accion": "error", "respuesta": f"Acción no reconocida: {accion or '?'}"}

    out = dict(data)

    for campo in ("termino",):
        if campo in out and out[campo]:
            out[campo] = _limpiar_termino_busqueda(str(out[campo]))

    if accion in ("agregar_descripcion", "cambiar_marca", "cambiar_vehiculos") and out.get("codigo"):
        out["codigo"] = str(out["codigo"]).strip().upper().replace("/", "-")

    if accion == "filtrar_proveedor" and out.get("proveedor"):
        out["proveedor"] = corregir_termino_repuesto(str(out["proveedor"]).strip())

    if accion == "reporte_stock":
        operador = str(out.get("operador", "") or "").strip().lower()
        if operador not in {"exacto", "menor_o_igual", "mayor_o_igual"}:
            operador = "menor_o_igual"
        if operador != "exacto" and es_consulta_mayor_o_igual(texto_original):
            operador = "mayor_o_igual"
        out["operador"] = operador
        try:
            out["cantidad"] = int(out.get("cantidad", 3) or 3)
        except (TypeError, ValueError):
            out["cantidad"] = 3

    if accion == "cargar_producto":
        from modulos.normalizar_carga_producto import normalizar_orden_cargar_producto
        out = normalizar_orden_cargar_producto(out, texto_original)

    if accion in ("alta", "baja"):
        try:
            out["cantidad"] = max(1, int(out.get("cantidad", 1) or 1))
        except (TypeError, ValueError):
            out["cantidad"] = 1

    return out


def interpretar_orden_groq_deposito(texto_usuario: str) -> Optional[dict]:
    """Llama a Groq y devuelve acción normalizada, o None si no hay API/falla."""
    api_key = _groq_api_key()
    if not api_key:
        return None

    from modulos.ia_asistente import normalizar_orden_voz_deposito

    texto_procesado = normalizar_orden_voz_deposito(texto_usuario)
    modelo = _MODELO_FLUJO if _orden_requiere_modelo_grande(texto_procesado) else _MODELO_RAPIDO

    cached = _groq_cache_get(texto_procesado, modelo)
    if cached is not None:
        return cached

    try:
        from groq import Groq  # type: ignore

        client = Groq(api_key=api_key)
        chat_completion = client.chat.completions.create(
            messages=[{"role": "user", "content": _prompt_groq_orden_deposito(texto_procesado)}],
            model=modelo,
            temperature=0.0,
            response_format={"type": "json_object"},
        )
        texto = chat_completion.choices[0].message.content.strip()  # type: ignore
        texto = texto.replace("```json", "").replace("```", "").strip()
        data = json.loads(texto)
    except Exception:
        return None

    result = normalizar_accion_asistente(data, texto_usuario)
    if result and result.get("accion") != "error":
        _groq_cache_set(texto_procesado, modelo, result)
    return result
