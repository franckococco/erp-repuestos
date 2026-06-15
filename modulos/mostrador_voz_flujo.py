"""Flujo rápido de facturación por voz en mostrador (un solo paso)."""
import re
import time
from typing import Callable, Optional

import streamlit as st

from modulos.db_firebase import (
    obtener_carrito,
    vaciar_carrito,
    cliente_consumidor_final,
    cliente_db_a_activo,
    obtener_clientes,
)
from modulos.ia_mostrador import normalizar_forma_pago


def inventario_cache_mostrador(obtener_inventario_fn, ttl_seg=120):
    ahora = time.time()
    if (
        "_inv_cache_mostrador" in st.session_state
        and ahora - float(st.session_state.get("_inv_cache_mostrador_ts", 0)) < ttl_seg
    ):
        return st.session_state["_inv_cache_mostrador"]
    inv = obtener_inventario_fn() or []
    st.session_state["_inv_cache_mostrador"] = inv
    st.session_state["_inv_cache_mostrador_ts"] = ahora
    return inv


def invalidar_cache_inventario_mostrador():
    st.session_state.pop("_inv_cache_mostrador", None)
    st.session_state.pop("_inv_cache_mostrador_ts", None)


def _parece_codigo(termino: str) -> bool:
    t = str(termino or "").strip().upper().replace("/", "-")
    if not t:
        return False
    if "_" in t:
        return True
    return bool(re.match(r"^[A-Z0-9\-]{4,}$", t))


def agregar_termino_voz(
    vendedor,
    termino,
    cantidad,
    inventario,
    buscar_en_inventario,
    agregar_al_carrito,
):
    cant = max(1, int(cantidad or 1))
    termino = str(termino or "").strip()
    if not termino:
        return False, "Sin término de búsqueda.", None

    if _parece_codigo(termino):
        ok, msj = agregar_al_carrito(str(vendedor), termino.upper().replace("/", "-"), cant)
        if ok:
            return True, msj, None
        for p in inventario:
            if not isinstance(p, dict):
                continue
            cod = str(p.get("codigo", "")).upper()
            pid = str(p.get("id", "")).upper()
            t_up = termino.upper()
            if t_up in (cod, pid) or pid.startswith(t_up + "_"):
                ok2, msj2 = agregar_al_carrito(str(vendedor), p.get("id"), cant)
                return ok2, msj2, None

    encontrados = buscar_en_inventario(inventario, termino)
    if len(encontrados) == 1:
        ok, msj = agregar_al_carrito(str(vendedor), encontrados[0]["id"], cant)
        return ok, msj, None
    if len(encontrados) > 1:
        return False, f"Varias opciones para '{termino}'. Decí el código exacto.", encontrados[:10]
    return False, f"No encontré '{termino}'.", None


def activar_cliente_voz(nombre_cliente=None, consumidor_final=False, tipo_comprobante=None):
    if consumidor_final:
        cli = cliente_consumidor_final()
    elif nombre_cliente:
        nombre_up = str(nombre_cliente).upper()
        clientes_db = obtener_clientes() or {}
        encontrado = next(
            (c for c in clientes_db.values() if nombre_up in str(c.get("nombre", "")).upper()),
            None,
        )
        if encontrado:
            cli = cliente_db_a_activo(encontrado)
        else:
            cli = {
                "nombre": nombre_up,
                "cuit": "00000000000",
                "descuento": 0.0,
                "tipo_comprobante": "6",
            }
    else:
        return None

    if tipo_comprobante in ("1", "6", "A", "B", "a", "b"):
        t = str(tipo_comprobante).upper()
        cli["tipo_comprobante"] = "1" if t in ("1", "A") else "6"
    st.session_state.cliente_activo = cli
    return cli


def ejecutar_flujo_factura_voz(
    vendedor,
    flujo: dict,
    inventario,
    buscar_en_inventario,
    agregar_al_carrito,
    emitir_factura_fn: Callable,
):
    """
    Ejecuta en un solo paso: cliente, ítems, pago e impresión ticket.
    emitir_factura_fn(vendedor, carrito, total_final, desc_porc, forma_pago, solo_ticket)
    """
    pasos_ok = []
    errores = []

    if flujo.get("vaciar_antes", flujo.get("carrito_nuevo", False)):
        vaciar_carrito(str(vendedor))

    tipo = flujo.get("tipo_comprobante")
    if tipo in (None, "") and flujo.get("factura_b"):
        tipo = "6"
    if tipo in (None, "") and flujo.get("factura_a"):
        tipo = "1"

    if flujo.get("consumidor_final"):
        activar_cliente_voz(consumidor_final=True, tipo_comprobante=tipo)
        pasos_ok.append("Consumidor final")
    elif flujo.get("nombre_cliente"):
        activar_cliente_voz(
            nombre_cliente=flujo.get("nombre_cliente"),
            tipo_comprobante=tipo,
        )
        pasos_ok.append(f"Cliente {flujo.get('nombre_cliente')}")
    elif tipo:
        cli = dict(st.session_state.get("cliente_activo") or cliente_consumidor_final())
        t = str(tipo).upper()
        cli["tipo_comprobante"] = "1" if t in ("1", "A") else "6"
        st.session_state.cliente_activo = cli
        pasos_ok.append(f"Factura {'A' if cli['tipo_comprobante'] == '1' else 'B'}")

    items = flujo.get("items") or []
    if isinstance(items, dict):
        items = [items]
    for raw in items:
        if not isinstance(raw, dict):
            continue
        termino = raw.get("termino") or raw.get("codigo") or raw.get("descripcion")
        cant = raw.get("cantidad", 1)
        ok, msj, ambiguos = agregar_termino_voz(
            vendedor, termino, cant, inventario, buscar_en_inventario, agregar_al_carrito
        )
        if ok:
            pasos_ok.append(msj)
        elif ambiguos:
            return False, msj, ambiguos
        else:
            errores.append(msj)

    if flujo.get("forma_pago"):
        fp = normalizar_forma_pago(flujo.get("forma_pago"))
        st.session_state[f"mostrador_forma_pago_{vendedor}"] = fp
        pasos_ok.append(f"Pago {fp}")

    imprimir = bool(
        flujo.get("imprimir_ticket")
        or flujo.get("imprimir")
        or flujo.get("accion") == "imprimir_ticket"
    )
    if not imprimir:
        if errores:
            return False, "Flujo parcial:\n" + "\n".join(errores), None
        return True, " | ".join(pasos_ok) if pasos_ok else "Listo.", None

    carrito = obtener_carrito(str(vendedor)) or []
    if not carrito:
        return False, "No hay ítems para facturar.", None
    if errores:
        return False, "Corregí estos errores antes de imprimir:\n" + "\n".join(errores), None

    desc_porc = float(st.session_state.cliente_activo.get("descuento", 0))
    total_bruto = sum(float(i.get("subtotal", 0)) for i in carrito if isinstance(i, dict))
    total_final = total_bruto * (1 - desc_porc / 100)
    fp = st.session_state.get(f"mostrador_forma_pago_{vendedor}", "Contado")

    ok, msj = emitir_factura_fn(
        vendedor, carrito, total_final, desc_porc, fp, solo_ticket=True
    )
    if not ok:
        return False, msj, None

    resumen = " · ".join(pasos_ok) if pasos_ok else "Factura emitida"
    return True, f"{msj} {resumen}", None
