"""Estados de la venta en mostrador (UI tipo asistente)."""
from typing import Optional

import streamlit as st

from modulos.db_firebase import obtener_carrito


class EstadoVenta:
    LIBRE = "libre"
    ARMANDO = "armando"
    ELEGIR = "elegir"
    REVISAR = "revisar"
    LISTO = "listo"


def _vendedor_key(vendedor) -> str:
    return str(vendedor)


def obtener_estado_venta(vendedor) -> str:
    """Deriva el estado actual sin duplicar flags sueltos."""
    if st.session_state.get("resultados_ia_mostrador"):
        return EstadoVenta.ELEGIR
    if st.session_state.get("factura_arca_reciente"):
        return EstadoVenta.LISTO
    carrito = obtener_carrito(_vendedor_key(vendedor)) or []
    if st.session_state.get("mostrador_listo_para_ticket") and carrito:
        return EstadoVenta.REVISAR
    if carrito:
        return EstadoVenta.ARMANDO
    return EstadoVenta.LIBRE


def obtener_intent_venta() -> str:
    return str(st.session_state.get("mostrador_intent_sugerido") or "factura_b")


def etiqueta_intent(intent: Optional[str] = None) -> str:
    i = intent or obtener_intent_venta()
    return {
        "presupuesto": "Presupuesto",
        "factura_a": "Factura A",
        "factura_b": "Factura B",
    }.get(i, "Venta")


def guardar_mensaje_chat(orden: str, respuesta: str, tipo: str = "info"):
    historial = list(st.session_state.get("venta_chat_historial") or [])
    historial.append({"orden": orden, "respuesta": respuesta, "tipo": tipo})
    st.session_state.venta_chat_historial = historial[-10:]
    st.session_state.venta_chat_orden = orden
    st.session_state.venta_chat_respuesta = respuesta
    st.session_state.venta_chat_tipo = tipo


def obtener_historial_chat():
    return list(st.session_state.get("venta_chat_historial") or [])


def obtener_mensaje_chat():
    return (
        st.session_state.get("venta_chat_orden"),
        st.session_state.get("venta_chat_respuesta"),
        st.session_state.get("venta_chat_tipo", "info"),
    )


def limpiar_mensaje_chat():
    st.session_state.pop("venta_chat_historial", None)
    st.session_state.pop("venta_chat_orden", None)
    st.session_state.pop("venta_chat_respuesta", None)
    st.session_state.pop("venta_chat_tipo", None)
