"""Mostrador tipo asistente: chat + pantallas por estado de venta."""
import streamlit as st

from modulos.db_firebase import obtener_carrito
from modulos.mostrador_estado import (
    EstadoVenta,
    etiqueta_intent,
    guardar_mensaje_chat,
    obtener_estado_venta,
    obtener_intent_venta,
    obtener_mensaje_chat,
)
from modulos.mostrador_voz_flujo import (
    descartar_panels_operacion_anterior,
    ejecutar_flujo_factura_voz,
    extraer_items_orden_voz,
    inventario_cache_mostrador,
)
from modulos.ia_mostrador import procesar_orden_mostrador


def _procesar_orden_chat(
    vendedor,
    orden,
    obtener_inventario_completo,
    buscar_en_inventario,
    agregar_al_carrito,
):
    """Ejecuta una orden de voz/texto y guarda respuesta para el chat."""
    descartar_panels_operacion_anterior()
    from modulos.ui_mostrador import (
        calcular_totales_carrito,
        _carrito_para_presupuesto,
        _preparar_pdf_presupuesto_borrador,
        ejecutar_emitir_factura_arca,
    )

    resp = procesar_orden_mostrador(orden) or {}
    accion = resp.get("accion")
    inventario = inventario_cache_mostrador(obtener_inventario_completo)
    desc_porc = float(st.session_state.cliente_activo.get("descuento", 0))

    if accion == "flujo_factura":
        if not resp.get("items"):
            items_extra = extraer_items_orden_voz(orden)
            if items_extra:
                resp["items"] = items_extra
        ok, msj, ambiguos = ejecutar_flujo_factura_voz(
            vendedor,
            resp,
            inventario,
            buscar_en_inventario,
            agregar_al_carrito,
            ejecutar_emitir_factura_arca,
            texto_orden=orden,
        )
        if ok:
            st.session_state.resultados_ia_mostrador = None
            intent = resp.get("intent_sugerido")
            if intent == "presupuesto" and resp.get("ir_verificacion"):
                carrito_n = _carrito_para_presupuesto(vendedor)
                if carrito_n:
                    _, tb = calcular_totales_carrito(carrito_n, desc_porc)
                    _preparar_pdf_presupuesto_borrador(vendedor, carrito_n, tb)
                    msj = f"{msj} Revisá los ítems y usá **Imprimir presupuesto**."
            elif intent in ("factura_b", "factura_a") and resp.get("ir_verificacion"):
                msj = f"{msj} Revisá y pulsá **Facturar e imprimir**."
            guardar_mensaje_chat(orden, msj, "ok")
            return True
        if ambiguos:
            st.session_state.resultados_ia_mostrador = ambiguos
            st.session_state.msg_ia_mostrador = msj or "Elegí el producto exacto:"
            guardar_mensaje_chat(orden, msj, "warning")
            return False
        guardar_mensaje_chat(orden, msj or "No se pudo completar la orden.", "error")
        return False

    if accion == "listo_armado":
        from modulos.ui_mostrador import _carrito_para_presupuesto as _cp

        carrito_n = _cp(vendedor)
        if carrito_n:
            _, tb = calcular_totales_carrito(carrito_n, desc_porc)
            if resp.get("intent_sugerido") == "presupuesto":
                _preparar_pdf_presupuesto_borrador(vendedor, carrito_n, tb)
            guardar_mensaje_chat(orden, "Listo. Revisá el comprobante.", "ok")
            return True
        guardar_mensaje_chat(orden, "Carrito vacío.", "error")
        return False

    if accion == "cancelar_pendiente":
        st.session_state.mostrador_accion_pendiente = None
        guardar_mensaje_chat(orden, "Acción cancelada.", "info")
        return True

    texto = resp.get("respuesta") or "Orden procesada."
    guardar_mensaje_chat(orden, texto, "info" if accion != "error" else "error")
    return accion != "error"


def _render_header_venta(vendedor):
    cli = st.session_state.get("cliente_activo") or {}
    nombre = cli.get("nombre", "CONSUMIDOR FINAL")
    intent = etiqueta_intent()
    carrito = obtener_carrito(str(vendedor)) or []
    n_items = len(carrito)
    estado = obtener_estado_venta(vendedor)

    c1, c2, c3, c4 = st.columns([3, 2, 2, 2])
    c1.markdown(f"**Cliente:** {nombre}")
    c2.markdown(f"**Tipo:** {intent}")
    c3.markdown(f"**Ítems:** {n_items}")
    c4.markdown(f"**Estado:** {estado.replace('_', ' ').title()}")


def _render_chat_historial():
    orden, respuesta, tipo = obtener_mensaje_chat()
    if not orden:
        st.caption(
            "Dictá o escribí la orden completa. Ej: "
            "*presupuesto para Pablo, código 111 1, bielete para el 207 2 unidades*"
        )
        return
    with st.chat_message("user"):
        st.markdown(orden)
    with st.chat_message("assistant"):
        if tipo == "ok":
            st.success(respuesta)
        elif tipo == "error":
            st.error(respuesta)
        elif tipo == "warning":
            st.warning(respuesta)
        else:
            st.markdown(respuesta)


def render_venta_chat(
    vendedor,
    obtener_inventario_completo,
    buscar_en_inventario,
    agrupar_por_maestro,
    agregar_al_carrito,
    *,
    render_carrito_grilla,
    render_panel_coincidencias_mostrador,
    render_descarga_presupuesto_prominente,
    render_panel_cobro_mostrador,
    render_factura_arca_exitosa,
    render_credenciales_arca,
    render_seccion_cliente_mostrador,
    render_buscador_productos,
    render_presupuestos_guardados,
    filtrar_inventario,
    carrito_efectivo_mostrador,
    calcular_totales_carrito,
    limpiar_venta_mostrador,
    inv_mostrador,
):
    """UI principal del mostrador (chat + vista por estado)."""
    estado = obtener_estado_venta(vendedor)
    _render_header_venta(vendedor)

    if estado == EstadoVenta.LISTO:
        render_factura_arca_exitosa("top")
        if st.button("✅ Nueva venta", type="primary", key=f"nueva_venta_chat_{vendedor}"):
            limpiar_venta_mostrador(vendedor, reset_cliente=True)
            from modulos.mostrador_estado import limpiar_mensaje_chat

            limpiar_mensaje_chat()
            st.rerun()
        return

    with st.expander("Cliente y ARCA", expanded=False):
        c1, c2 = st.columns(2)
        with c1:
            render_seccion_cliente_mostrador()
        with c2:
            render_credenciales_arca()

    if estado == EstadoVenta.ELEGIR:
        render_panel_coincidencias_mostrador(
            vendedor,
            agrupar_por_maestro,
            agregar_al_carrito,
            buscar_en_inventario=buscar_en_inventario,
            obtener_inventario=obtener_inventario_completo,
        )
        _render_chat_historial()
        return

    if estado == EstadoVenta.REVISAR:
        carrito = obtener_carrito(str(vendedor)) or []
        carrito_ui = carrito_efectivo_mostrador(vendedor, carrito)
        desc_porc = float(st.session_state.cliente_activo.get("descuento", 0))
        total_bruto, total_final = calcular_totales_carrito(carrito_ui, desc_porc)
        intent = obtener_intent_venta()

        st.markdown(f"### Revisar {etiqueta_intent(intent)}")
        render_carrito_grilla(vendedor, carrito_ui)
        if intent == "presupuesto" and st.session_state.get("presupuesto_pdf_descarga"):
            render_descarga_presupuesto_prominente(vendedor)
        render_panel_cobro_mostrador(
            vendedor, carrito_ui, total_bruto, total_final, desc_porc
        )
        _render_chat_historial()
        orden = st.chat_input("Otra orden o corrección…", key=f"chat_revisar_{vendedor}")
        if orden:
            with st.spinner("Procesando…"):
                _procesar_orden_chat(
                    vendedor, orden, obtener_inventario_completo,
                    buscar_en_inventario, agregar_al_carrito,
                )
            st.rerun()
        return

    if estado == EstadoVenta.ARMANDO:
        carrito = obtener_carrito(str(vendedor)) or []
        carrito_ui = carrito_efectivo_mostrador(vendedor, carrito)
        desc_porc = float(st.session_state.cliente_activo.get("descuento", 0))
        _, total_final = calcular_totales_carrito(carrito_ui, desc_porc)
        with st.expander(
            f"🛒 Carrito · {len(carrito_ui)} ítem(s) · ${total_final:,.2f}",
            expanded=False,
        ):
            render_carrito_grilla(vendedor, carrito_ui)

    _render_chat_historial()

    orden = st.chat_input("Dicte o escriba la orden de venta…", key=f"chat_venta_{vendedor}")
    if orden:
        with st.spinner("Procesando orden…"):
            _procesar_orden_chat(
                vendedor,
                orden,
                obtener_inventario_completo,
                buscar_en_inventario,
                agregar_al_carrito,
            )
        st.rerun()

    with st.expander("Más herramientas", expanded=False):
        render_presupuestos_guardados(vendedor)
        t_buscar, t_manual = st.tabs(["🔍 Buscador", "⌨️ Pistola / Manual"])
        with t_buscar:
            if inv_mostrador:
                render_buscador_productos(
                    vendedor, inv_mostrador, agregar_al_carrito, filtrar_inventario
                )
            else:
                st.info("Inventario vacío.")
        with t_manual:
            cod = st.text_input("Código variante", key=f"manual_chat_{vendedor}")
            if st.button("➕ Agregar", key=f"manual_add_{vendedor}") and cod:
                exito, msj = agregar_al_carrito(vendedor, cod)
                if exito:
                    st.success(msj)
                    st.rerun()
                else:
                    st.error(msj)
