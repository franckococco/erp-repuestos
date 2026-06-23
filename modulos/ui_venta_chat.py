"""Mostrador tipo asistente: chat + pantallas por estado de venta."""
import streamlit as st

from modulos.db_firebase import obtener_carrito
from modulos.mostrador_estado import (
    EstadoVenta,
    etiqueta_intent,
    guardar_mensaje_chat,
    obtener_estado_venta,
    obtener_intent_venta,
    obtener_historial_chat,
)
from modulos.mostrador_voz_flujo import (
    activar_cliente_voz,
    agregar_termino_voz,
    descartar_panels_operacion_anterior,
    ejecutar_flujo_factura_voz,
    extraer_items_orden_voz,
    inventario_cache_mostrador,
    marcar_verificacion_mostrador,
)
from modulos.ia_mostrador import procesar_orden_mostrador
from modulos.ui_mostrador import render_mostrador_accion_pendiente


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
        _agregar_items_voz,
        _carrito_para_presupuesto,
        _ejecutar_accion_pendiente,
        _invalidar_pdf_presupuesto_mostrador,
        _limpiar_accion_pendiente,
        _marcar_listo_para_ticket,
        _preparar_pdf_presupuesto_borrador,
        _set_forma_pago,
        _tipo_comprobante_label,
        calcular_totales_carrito,
        carrito_efectivo_mostrador,
        cliente_consumidor_final,
        ejecutar_emitir_factura_arca,
        limpiar_venta_mostrador,
        validar_carrito_para_venta,
    )
    from modulos.presupuesto_pdf import VALIDEZ_PRESUPUESTO_DIAS

    resp = procesar_orden_mostrador(orden) or {}
    accion = resp.get("accion")
    inventario = inventario_cache_mostrador(obtener_inventario_completo)
    carrito = obtener_carrito(str(vendedor)) or []
    carrito_ui = carrito_efectivo_mostrador(vendedor, carrito)
    desc_porc = float(st.session_state.cliente_activo.get("descuento", 0))
    total_bruto, total_final = calcular_totales_carrito(carrito_ui, desc_porc)

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

    if accion == "agregar_carrito":
        termino = str(resp.get("termino", ""))
        cant_raw = resp.get("cantidad")
        cant = int(cant_raw) if cant_raw is not None and str(cant_raw).isdigit() else 1
        ok, msj, ambiguos = agregar_termino_voz(
            vendedor, termino, cant, inventario, buscar_en_inventario, agregar_al_carrito
        )
        if ok:
            _, tf = calcular_totales_carrito(
                carrito_efectivo_mostrador(vendedor, obtener_carrito(str(vendedor)) or []),
                desc_porc,
            )
            guardar_mensaje_chat(orden, f"🛒 {msj} · Total ${tf:,.2f}", "ok")
            return True
        if ambiguos:
            st.session_state.resultados_ia_mostrador = ambiguos
            st.session_state.msg_ia_mostrador = f"Elegí variante de '{termino}':"
            guardar_mensaje_chat(orden, msj, "warning")
            return False
        guardar_mensaje_chat(orden, msj, "error")
        return False

    if accion == "agregar_items":
        n, msj, ambiguos = _agregar_items_voz(
            vendedor, resp.get("items"), inventario, buscar_en_inventario, agregar_al_carrito
        )
        if ambiguos:
            st.session_state.resultados_ia_mostrador = ambiguos
            st.session_state.msg_ia_mostrador = "Elegí el producto exacto:"
            guardar_mensaje_chat(orden, msj, "warning")
            return False
        if n:
            _, tf = calcular_totales_carrito(
                carrito_efectivo_mostrador(vendedor, obtener_carrito(str(vendedor)) or []),
                desc_porc,
            )
            guardar_mensaje_chat(orden, f"{msj} · Total ${tf:,.2f}", "ok")
            return True
        guardar_mensaje_chat(orden, msj, "error")
        return False

    if accion == "presupuesto_pdf":
        carrito_n = _carrito_para_presupuesto(vendedor)
        if not carrito_n:
            guardar_mensaje_chat(orden, "El carrito está vacío.", "error")
            return False
        _, tb = calcular_totales_carrito(carrito_n, desc_porc)
        _, tf = calcular_totales_carrito(carrito_n, desc_porc)
        _preparar_pdf_presupuesto_borrador(vendedor, carrito_n, tb)
        marcar_verificacion_mostrador("presupuesto")
        guardar_mensaje_chat(
            orden,
            f"Presupuesto BORRADOR (${tf:,.2f}). Validez {VALIDEZ_PRESUPUESTO_DIAS} días.",
            "ok",
        )
        return True

    if accion == "listo_armado":
        carrito_n = _carrito_para_presupuesto(vendedor)
        if not carrito_n:
            guardar_mensaje_chat(orden, "Carrito vacío.", "error")
            return False
        _, tf = calcular_totales_carrito(carrito_n, desc_porc)
        intent = resp.get("intent_sugerido")
        _marcar_listo_para_ticket(vendedor, tf, intent)
        if intent == "presupuesto":
            _, tb = calcular_totales_carrito(carrito_n, desc_porc)
            _preparar_pdf_presupuesto_borrador(vendedor, carrito_n, tb)
            guardar_mensaje_chat(
                orden, f"PDF listo (${tf:,.2f}). Revisá e imprimí.", "ok"
            )
        else:
            guardar_mensaje_chat(orden, f"Listo (${tf:,.2f}). Revisá el comprobante.", "ok")
        return True

    if accion == "imprimir_ticket":
        if not carrito:
            guardar_mensaje_chat(orden, "El carrito está vacío.", "error")
            return False
        ok_val, msg_val, _ = validar_carrito_para_venta(str(vendedor))
        if not ok_val:
            guardar_mensaje_chat(orden, msg_val, "error")
            return False
        _marcar_listo_para_ticket(vendedor, total_final)
        guardar_mensaje_chat(orden, f"Listo para cerrar · ${total_final:,.2f}", "ok")
        return True

    if accion == "set_cliente":
        cli = activar_cliente_voz(
            nombre_cliente=resp.get("nombre_cliente"),
            consumidor_final=resp.get("consumidor_final"),
            tipo_comprobante=resp.get("tipo_comprobante"),
        )
        if cli:
            _invalidar_pdf_presupuesto_mostrador()
            guardar_mensaje_chat(
                orden, f"Cliente {cli.get('nombre', '')} activado.", "ok"
            )
            return True
        guardar_mensaje_chat(orden, "No pude activar el cliente.", "error")
        return False

    if accion == "set_tipo_factura":
        tipo = resp.get("tipo_comprobante", "6")
        cli = dict(st.session_state.cliente_activo or cliente_consumidor_final())
        t = str(tipo).upper()
        cli["tipo_comprobante"] = "1" if t in ("1", "A") else "6"
        st.session_state.cliente_activo = cli
        guardar_mensaje_chat(
            orden,
            f"Factura {_tipo_comprobante_label(cli['tipo_comprobante'])}.",
            "ok",
        )
        return True

    if accion == "consumidor_final":
        tipo = resp.get("tipo_comprobante")
        cli = cliente_consumidor_final()
        if tipo in ("1", "6", "A", "B", "a", "b"):
            t = str(tipo).upper()
            cli["tipo_comprobante"] = "1" if t in ("1", "A") else "6"
        st.session_state.cliente_activo = cli
        _invalidar_pdf_presupuesto_mostrador()
        guardar_mensaje_chat(orden, "Consumidor final activado.", "ok")
        return True

    if accion == "set_forma_pago":
        fp = _set_forma_pago(vendedor, resp.get("forma_pago", "Contado"))
        guardar_mensaje_chat(orden, f"Forma de pago: {fp}.", "ok")
        return True

    if accion == "guardar_presupuesto":
        if not carrito:
            guardar_mensaje_chat(orden, "El carrito está vacío.", "error")
            return False
        nota = str(resp.get("nota", "") or "")
        st.session_state.mostrador_accion_pendiente = {
            "tipo": "guardar_presupuesto",
            "nota": nota,
            "mensaje": (
                f"¿Guardar presupuesto de ${total_final:,.2f} para "
                f"{st.session_state.cliente_activo.get('nombre', 'CONSUMIDOR FINAL')}?"
            ),
        }
        guardar_mensaje_chat(orden, "Confirmá guardar el presupuesto.", "warning")
        return False

    if accion == "confirmar_venta":
        if not carrito:
            guardar_mensaje_chat(orden, "El carrito está vacío.", "error")
            return False
        ok_val, msg_val, _ = validar_carrito_para_venta(str(vendedor))
        if not ok_val:
            guardar_mensaje_chat(orden, msg_val, "error")
            return False
        st.session_state.mostrador_accion_pendiente = {
            "tipo": "confirmar_venta",
            "mensaje": (
                f"¿Confirmar venta por ${total_final:,.2f} "
                f"(sin factura fiscal) y descontar stock?"
            ),
        }
        guardar_mensaje_chat(orden, "Confirmá la venta.", "warning")
        return False

    if accion == "facturar":
        if not carrito:
            guardar_mensaje_chat(orden, "El carrito está vacío.", "error")
            return False
        ok_val, msg_val, _ = validar_carrito_para_venta(str(vendedor))
        if not ok_val:
            guardar_mensaje_chat(orden, msg_val, "error")
            return False
        _marcar_listo_para_ticket(vendedor, total_final, obtener_intent_venta())
        guardar_mensaje_chat(
            orden, f"Listo para facturar · ${total_final:,.2f}", "ok"
        )
        return True

    if accion in ("buscar", "consulta"):
        termino = str(resp.get("termino", "") or orden)
        if not termino:
            guardar_mensaje_chat(orden, "No detecté qué producto buscar.", "warning")
            return False
        encontrados = buscar_en_inventario(inventario, termino)
        if encontrados:
            st.session_state.resultados_ia_mostrador = encontrados[:10]
            st.session_state.msg_ia_mostrador = f"Encontré opciones para '{termino}':"
            guardar_mensaje_chat(
                orden, f"{len(encontrados[:10])} opciones para '{termino}'. Elegí una.", "warning"
            )
            return False
        guardar_mensaje_chat(
            orden, f"No encontré coincidencias para '{termino}'.", "error"
        )
        return False

    if accion == "vaciar_carrito":
        limpiar_venta_mostrador(vendedor, reset_cliente=False)
        guardar_mensaje_chat(orden, "Carrito vaciado.", "info")
        return True

    if accion == "confirmar_pendiente":
        pend = st.session_state.get("mostrador_accion_pendiente")
        if pend:
            ok, msj = _ejecutar_accion_pendiente(
                vendedor, pend, carrito_ui, total_final, desc_porc
            )
            _limpiar_accion_pendiente()
            guardar_mensaje_chat(orden, msj, "ok" if ok else "error")
            return ok
        guardar_mensaje_chat(orden, "No hay acción pendiente.", "info")
        return False

    if accion == "cancelar_pendiente":
        _limpiar_accion_pendiente()
        guardar_mensaje_chat(orden, "Acción cancelada.", "info")
        return True

    texto = resp.get("respuesta") or "Orden procesada."
    guardar_mensaje_chat(orden, texto, "info" if accion != "error" else "error")
    return accion != "error"


def _render_header_venta(vendedor, carrito_efectivo_mostrador, calcular_totales_carrito):
    cli = st.session_state.get("cliente_activo") or {}
    nombre = cli.get("nombre", "CONSUMIDOR FINAL")
    intent = etiqueta_intent()
    carrito = carrito_efectivo_mostrador(vendedor, obtener_carrito(str(vendedor)) or [])
    n_items = len(carrito)
    estado = obtener_estado_venta(vendedor)
    desc_porc = float(cli.get("descuento", 0))
    _, total = calcular_totales_carrito(carrito, desc_porc)

    c1, c2, c3, c4, c5 = st.columns([2.5, 1.5, 1.2, 1.2, 1.5])
    c1.markdown(f"**Cliente:** {nombre}")
    c2.markdown(f"**{intent}**")
    c3.markdown(f"**{n_items}** ítems")
    c4.markdown(f"**${total:,.2f}**")
    c5.caption(estado.replace("_", " ").title())


def _render_chat_historial():
    historial = obtener_historial_chat()
    if not historial:
        st.caption(
            "Dictá o escribí la orden completa. Ej: "
            "*presupuesto para Pablo, código 111 1, bielete para el 207 2 unidades* · "
            "Decí **listo** para revisar."
        )
        return
    for entrada in historial:
        orden = entrada.get("orden", "")
        respuesta = entrada.get("respuesta", "")
        tipo = entrada.get("tipo", "info")
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
    decodificar_qr_fn=None,
):
    """UI principal del mostrador (chat + vista por estado)."""
    estado = obtener_estado_venta(vendedor)
    _render_header_venta(vendedor, carrito_efectivo_mostrador, calcular_totales_carrito)
    render_mostrador_accion_pendiente(vendedor)

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
        orden = st.chat_input("Corrección o nueva búsqueda…", key=f"chat_elegir_{vendedor}")
        if orden:
            with st.spinner("Procesando…"):
                _procesar_orden_chat(
                    vendedor, orden, obtener_inventario_completo,
                    buscar_en_inventario, agregar_al_carrito,
                )
            st.rerun()
        return

    if estado == EstadoVenta.REVISAR:
        carrito = obtener_carrito(str(vendedor)) or []
        carrito_ui = carrito_efectivo_mostrador(vendedor, carrito)
        desc_porc = float(st.session_state.cliente_activo.get("descuento", 0))
        total_bruto, total_final = calcular_totales_carrito(carrito_ui, desc_porc)
        intent = obtener_intent_venta()

        st.markdown(f"### Revisar {etiqueta_intent(intent)}")
        render_carrito_grilla(vendedor, carrito_ui)
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
            f"🛒 Ver carrito · {len(carrito_ui)} ítem(s) · ${total_final:,.2f}",
            expanded=False,
        ):
            render_carrito_grilla(vendedor, carrito_ui)
        st.info("Seguí dictando ítems o decí **listo** para revisar e imprimir.")

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
        col_v, col_n = st.columns(2)
        with col_v:
            if st.button("🗑️ Vaciar carrito", key=f"vaciar_chat_{vendedor}", use_container_width=True):
                limpiar_venta_mostrador(vendedor, reset_cliente=False)
                from modulos.mostrador_estado import limpiar_mensaje_chat

                limpiar_mensaje_chat()
                st.rerun()
        with col_n:
            if st.button("✅ Nueva venta", key=f"nueva_chat_{vendedor}", use_container_width=True):
                limpiar_venta_mostrador(vendedor, reset_cliente=True)
                from modulos.mostrador_estado import limpiar_mensaje_chat

                limpiar_mensaje_chat()
                st.rerun()

        render_presupuestos_guardados(vendedor)
        tabs = ["🔍 Buscador", "⌨️ Pistola", "📷 QR"]
        t_buscar, t_manual, t_qr = st.tabs(tabs)
        with t_buscar:
            if inv_mostrador:
                render_buscador_productos(
                    vendedor, inv_mostrador, agregar_al_carrito, filtrar_inventario
                )
            else:
                st.info("Inventario vacío.")
        with t_manual:
            cod = st.text_input("Código variante (CODIGO_MARCA)", key=f"manual_chat_{vendedor}")
            if st.button("➕ Agregar", key=f"manual_add_{vendedor}") and cod:
                exito, msj = agregar_al_carrito(vendedor, cod)
                if exito:
                    st.success(msj)
                    st.rerun()
                else:
                    st.error(msj)
        with t_qr:
            if decodificar_qr_fn:
                foto_qr = st.camera_input("Escanear QR", key=f"cam_chat_{vendedor}")
                if foto_qr:
                    from PIL import Image

                    cod_detectado = decodificar_qr_fn(Image.open(foto_qr))
                    if cod_detectado:
                        id_limpio = cod_detectado.split("\n")[0].replace("COD:", "").strip()
                        exito, msj = agregar_al_carrito(vendedor, id_limpio)
                        if exito:
                            st.success(f"Añadido: {id_limpio}")
                            st.rerun()
                        else:
                            st.error(msj)
            else:
                st.caption("Escáner QR no disponible.")
