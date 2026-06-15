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
    formatear_id_variante,
)
from modulos.ia_asistente import normalizar_texto_basico


def preprocesar_texto_mostrador(texto):
    """Preprocesa sin fusionar código de producto con cantidad (ej. 3524150 5)."""
    texto_limpio = re.sub(
        r"\b(guion|guión)\b", "-", str(texto or ""), flags=re.IGNORECASE
    )

    def unir_si_dictado(match):
        fragmento = match.group(0)
        partes = fragmento.split()
        if len(partes) == 2 and partes[1].isdigit() and int(partes[1]) <= 999:
            return fragmento
        if len(partes) == 2 and len(partes[0]) <= 3 and partes[0].isdigit():
            return fragmento
        return fragmento.replace(" ", "")

    return re.sub(r"(?:\d+\s+)+\d+", unir_si_dictado, texto_limpio)


def _limpiar_termino_item(termino):
    t = str(termino or "").strip().upper().replace("/", "-")
    t = re.sub(r"\s+", "-", t)
    return t.strip(",.;:")


def _id_carrito_desde_item(item):
    if not isinstance(item, dict):
        return None
    id_m = item.get("id_maestro") or item.get("codigo")
    marca = item.get("marca")
    if id_m and marca:
        return formatear_id_variante(id_m, marca)
    return item.get("id")


def _normalizar_codigo_con_inventario(termino, inventario):
    """Ajusta códigos dictados (1273 BH → 1273-BH) según inventario."""
    directo = _limpiar_termino_item(termino)
    if not directo:
        return directo
    coincidencias = _buscar_variantes_por_codigo(inventario, directo)
    if coincidencias:
        cod = _limpiar_termino_item(coincidencias[0].get("codigo", ""))
        return cod or directo
    compacto = directo.replace("-", "")
    for p in inventario or []:
        if not isinstance(p, dict):
            continue
        cod = _limpiar_termino_item(p.get("codigo", ""))
        if cod.replace("-", "") == compacto:
            return cod
    return directo


def extraer_items_orden_voz(texto):
    """Extrae código + cantidad desde la orden hablada/escrita."""
    if not texto:
        return []
    t = normalizar_texto_basico(texto).lower()
    items = []
    vistos = set()

    def agregar(termino, cantidad):
        term = _limpiar_termino_item(termino)
        try:
            cant = max(1, int(cantidad))
        except (TypeError, ValueError):
            return
        if not term or not re.search(r"[A-Z0-9]", term):
            return
        clave = (term, cant)
        if clave in vistos:
            return
        vistos.add(clave)
        items.append({"termino": term, "cantidad": cant})

    patrones = [
        (r"(?:codigo)\s+([\dA-Za-z]+(?:[\s-][\dA-Za-z]+)*)\s+(\d{1,4})\s*(?:unidades?|u\.?|uds?)?\b", False),
        (r"(?:agreg\w*|sum\w*|pon\w*)\s+(?:codigo\s+)?([\dA-Za-z]+(?:[\s-][\dA-Za-z]+)*)\s+(\d{1,4})\s*(?:unidades?|u\.?)?\b", False),
        (r"(?:codigo)\s+([\dA-Za-z]+(?:[\s-][\dA-Za-z]+)*)\s*(?:por|x|\*|con)\s*(\d{1,4})\b", False),
        (r"(\d{1,4})\s*(?:unidades?|u\.?)\s+(?:del?\s+)?(?:codigo\s+)?([\dA-Za-z]+(?:[\s-][\dA-Za-z]+)*)", True),
    ]
    for patron, invertido in patrones:
        for m in re.finditer(patron, t):
            if invertido:
                agregar(m.group(2), m.group(1))
            else:
                agregar(m.group(1), m.group(2))
    return items


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


def _buscar_variantes_por_codigo(inventario, termino):
    t = _limpiar_termino_item(termino)
    if not t:
        return []
    exactos = []
    for p in inventario:
        if not isinstance(p, dict):
            continue
        cod = _limpiar_termino_item(p.get("codigo", ""))
        pid = _limpiar_termino_item(p.get("id", ""))
        id_m = _limpiar_termino_item(p.get("id_maestro", ""))
        if t in (cod, pid, id_m):
            exactos.append(p)
        elif pid.startswith(f"{t}_") or id_m.startswith(f"{t}_"):
            exactos.append(p)
    return exactos


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

    id_limpio = _normalizar_codigo_con_inventario(termino, inventario)

    if _parece_codigo(id_limpio):
        ok, msj = agregar_al_carrito(str(vendedor), id_limpio, cant)
        if ok:
            return True, msj, None
        coincidencias = _buscar_variantes_por_codigo(inventario, id_limpio)
        if len(coincidencias) == 1:
            id_cart = _id_carrito_desde_item(coincidencias[0])
            ok2, msj2 = agregar_al_carrito(str(vendedor), id_cart, cant)
            return ok2, msj2, None
        if len(coincidencias) > 1:
            return (
                False,
                f"Hay {len(coincidencias)} variantes para '{id_limpio}'. Decí el código con marca.",
                coincidencias[:10],
            )

    coincidencias_cod = _buscar_variantes_por_codigo(inventario, id_limpio)
    if len(coincidencias_cod) == 1:
        id_cart = _id_carrito_desde_item(coincidencias_cod[0])
        ok, msj = agregar_al_carrito(str(vendedor), id_cart, cant)
        return ok, msj, None
    if len(coincidencias_cod) > 1:
        return (
            False,
            f"Hay {len(coincidencias_cod)} variantes para '{id_limpio}'. Decí el código exacto.",
            coincidencias_cod[:10],
        )

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
    texto_orden=None,
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
    if not items and texto_orden:
        items = extraer_items_orden_voz(texto_orden)
    if not items and texto_orden and flujo.get("termino"):
        items = [{"termino": flujo.get("termino"), "cantidad": flujo.get("cantidad", 1)}]

    errores_items = []
    items_agregados = 0
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
            items_agregados += 1
        elif ambiguos:
            return False, msj, ambiguos
        else:
            errores.append(msj)
            errores_items.append(msj)

    if items and items_agregados == 0 and errores_items:
        if len(errores_items) == 1:
            return False, errores_items[0], None
        return False, "No se pudo agregar ningún producto:\n" + "\n".join(errores_items), None

    if flujo.get("forma_pago"):
        from modulos.ia_mostrador import normalizar_forma_pago

        fp = normalizar_forma_pago(flujo.get("forma_pago"))
        st.session_state[f"mostrador_forma_pago_{vendedor}"] = fp
        pasos_ok.append(f"Pago {fp}")

    imprimir = bool(
        flujo.get("imprimir_ticket")
        or flujo.get("imprimir")
        or flujo.get("accion") == "imprimir_ticket"
    )
    if errores:
        return False, "Flujo parcial:\n" + "\n".join(errores), None

    if imprimir:
        carrito = obtener_carrito(str(vendedor)) or []
        if not carrito:
            if not items:
                return (
                    False,
                    "No detecté código ni cantidad. Ejemplo: «código 3524150 5 unidades».",
                    None,
                )
            return False, "No hay ítems en el carrito. Revisá el código.", None
        st.session_state.mostrador_listo_para_ticket = True
        pasos_ok.append("Revisá la grilla y confirmá el ticket abajo")

    resumen = " · ".join(pasos_ok) if pasos_ok else "Listo."
    if imprimir:
        return True, f"{resumen} (sin imprimir hasta que confirmes)", None
    return True, resumen, None
