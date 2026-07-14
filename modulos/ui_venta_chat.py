"""Mostrador tipo caja POS: teclado + voz, grilla e importes siempre visibles."""
import streamlit as st

from modulos.db_firebase import obtener_carrito
from modulos.mostrador_estado import (
    EstadoVenta,
    etiqueta_intent,
    guardar_mensaje_chat,
    limpiar_pantalla_mostrador,
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
    interpretar_orden_voz_mostrador,
    inventario_cache_mostrador,
    marcar_verificacion_mostrador,
)
from modulos.ia_mostrador import procesar_orden_mostrador
from modulos.ui_mostrador import render_mostrador_accion_pendiente, render_presupuesto_pdf_pendiente
from modulos.ui_voz_microfono import render_boton_dictado
from modulos.cliente_resolver import listar_clientes_frecuentes


def _chat_orden(orden, msj, tipo="info"):
    """Guarda respuesta del chat; en órdenes largas muestra qué entendió el parser."""
    key = "_mostrador_interp_mostrada"
    if (
        len(str(orden).split()) >= 5
        and tipo in ("ok", "warning", "error")
        and st.session_state.get(key) != orden
    ):
        resumen = interpretar_orden_voz_mostrador(orden).get("resumen")
        if resumen:
            msj = f"**Entendí:** {resumen}\n\n{msj}"
            st.session_state[key] = orden
    guardar_mensaje_chat(orden, msj, tipo)


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
        sincronizar_grilla_carrito_firebase,
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
                marcar_verificacion_mostrador("presupuesto")
                msj = (
                    f"{msj} Revisá cantidades y precios en la grilla; "
                    "después usá **Generar presupuesto PDF**."
                )
            elif intent in ("factura_b", "factura_a") and resp.get("ir_verificacion"):
                marcar_verificacion_mostrador(intent)
                msj = f"{msj} Revisá y pulsá **Facturar e imprimir**."
            _chat_orden(orden,msj, "ok")
            return True
        if ambiguos:
            st.session_state.resultados_ia_mostrador = ambiguos
            st.session_state.msg_ia_mostrador = msj or "Elegí el producto exacto:"
            _chat_orden(orden,msj, "warning")
            return False
        _chat_orden(orden,msj or "No se pudo completar la orden.", "error")
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
            _chat_orden(orden,f"🛒 {msj} · Total ${tf:,.2f}", "ok")
            return True
        if ambiguos:
            st.session_state.resultados_ia_mostrador = ambiguos
            st.session_state.msg_ia_mostrador = f"Elegí variante de '{termino}':"
            _chat_orden(orden,msj, "warning")
            return False
        _chat_orden(orden,msj, "error")
        return False

    if accion == "agregar_items":
        n, msj, ambiguos = _agregar_items_voz(
            vendedor, resp.get("items"), inventario, buscar_en_inventario, agregar_al_carrito
        )
        if ambiguos:
            st.session_state.resultados_ia_mostrador = ambiguos
            st.session_state.msg_ia_mostrador = "Elegí el producto exacto:"
            _chat_orden(orden,msj, "warning")
            return False
        if n:
            _, tf = calcular_totales_carrito(
                carrito_efectivo_mostrador(vendedor, obtener_carrito(str(vendedor)) or []),
                desc_porc,
            )
            _chat_orden(orden,f"{msj} · Total ${tf:,.2f}", "ok")
            return True
        _chat_orden(orden,msj, "error")
        return False

    if accion == "presupuesto_pdf":
        carrito_n = _carrito_para_presupuesto(vendedor)
        if not carrito_n:
            _chat_orden(orden,"El carrito está vacío.", "error")
            return False
        _, tb = calcular_totales_carrito(carrito_n, desc_porc)
        _, tf = calcular_totales_carrito(carrito_n, desc_porc)
        _preparar_pdf_presupuesto_borrador(vendedor, carrito_n, tb)
        _chat_orden(
            orden,
            f"Presupuesto emitido (${tf:,.2f}). Descargá el PDF arriba.",
            "ok",
        )
        return True

    if accion == "listo_armado":
        carrito_n = _carrito_para_presupuesto(vendedor)
        if not carrito_n:
            _chat_orden(orden,"Carrito vacío.", "error")
            return False
        _, tf = calcular_totales_carrito(carrito_n, desc_porc)
        intent = resp.get("intent_sugerido")
        _marcar_listo_para_ticket(vendedor, tf, intent)
        if intent == "presupuesto":
            _chat_orden(
                orden,
                f"Revisá el presupuesto (${tf:,.2f}). Editá cantidades o precios y generá el PDF.",
                "ok",
            )
        else:
            _chat_orden(orden,f"Listo (${tf:,.2f}). Revisá el comprobante.", "ok")
        return True

    if accion == "imprimir_ticket":
        if not carrito:
            _chat_orden(orden,"El carrito está vacío.", "error")
            return False
        ok_val, msg_val, _ = validar_carrito_para_venta(str(vendedor))
        if not ok_val:
            _chat_orden(orden,msg_val, "error")
            return False
        _marcar_listo_para_ticket(vendedor, total_final)
        _chat_orden(orden,f"Listo para cerrar · ${total_final:,.2f}", "ok")
        return True

    if accion == "set_cliente":
        cli = activar_cliente_voz(
            nombre_cliente=resp.get("nombre_cliente"),
            consumidor_final=resp.get("consumidor_final"),
            tipo_comprobante=resp.get("tipo_comprobante"),
        )
        if cli:
            _invalidar_pdf_presupuesto_mostrador()
            _chat_orden(
                orden, f"Cliente {cli.get('nombre', '')} activado.", "ok"
            )
            return True
        _chat_orden(orden,"No pude activar el cliente.", "error")
        return False

    if accion == "set_tipo_factura":
        tipo = resp.get("tipo_comprobante", "6")
        cli = dict(st.session_state.cliente_activo or cliente_consumidor_final())
        t = str(tipo).upper()
        cli["tipo_comprobante"] = "1" if t in ("1", "A") else "6"
        st.session_state.cliente_activo = cli
        _chat_orden(
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
        _chat_orden(orden,"Consumidor final activado.", "ok")
        return True

    if accion == "set_forma_pago":
        fp = _set_forma_pago(vendedor, resp.get("forma_pago", "Contado"))
        _chat_orden(orden,f"Forma de pago: {fp}.", "ok")
        return True

    if accion == "guardar_presupuesto":
        if not carrito:
            _chat_orden(orden,"El carrito está vacío.", "error")
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
        _chat_orden(orden,"Confirmá guardar el presupuesto.", "warning")
        return False

    if accion == "confirmar_venta":
        if not carrito:
            _chat_orden(orden,"El carrito está vacío.", "error")
            return False
        ok_val, msg_val, _ = validar_carrito_para_venta(str(vendedor))
        if not ok_val:
            _chat_orden(orden,msg_val, "error")
            return False
        st.session_state.mostrador_accion_pendiente = {
            "tipo": "confirmar_venta",
            "mensaje": (
                f"¿Confirmar venta por ${total_final:,.2f} "
                f"(sin factura fiscal) y descontar stock?"
            ),
        }
        _chat_orden(orden,"Confirmá la venta.", "warning")
        return False

    if accion == "facturar":
        if not carrito:
            _chat_orden(orden,"El carrito está vacío.", "error")
            return False
        sincronizar_grilla_carrito_firebase(vendedor)
        ok_val, msg_val, _ = validar_carrito_para_venta(str(vendedor))
        if not ok_val:
            _chat_orden(orden,msg_val, "error")
            return False
        _marcar_listo_para_ticket(vendedor, total_final, obtener_intent_venta())
        _chat_orden(
            orden, f"Listo para facturar · ${total_final:,.2f}", "ok"
        )
        return True

    if accion in ("buscar", "consulta"):
        termino = str(resp.get("termino", "") or orden)
        if not termino:
            _chat_orden(orden,"No detecté qué producto buscar.", "warning")
            return False
        encontrados = buscar_en_inventario(inventario, termino)
        if encontrados:
            st.session_state.resultados_ia_mostrador = encontrados[:25]
            st.session_state.msg_ia_mostrador = f"Encontré opciones para '{termino}':"
            _chat_orden(
                orden, f"{len(encontrados[:25])} opciones para '{termino}'. Elegí una.", "warning"
            )
            return False
        st.session_state[f"manual_add_ctx_{vendedor}"] = {
            "termino": termino,
            "vehiculo": None,
            "cantidad": 1,
        }
        _chat_orden(
            orden,
            f"No encontré coincidencias para '{termino}'. Podés agregarlo manual (fuera de stock).",
            "error",
        )
        return False

    if accion == "vaciar_carrito":
        limpiar_venta_mostrador(vendedor, reset_cliente=False)
        _chat_orden(orden,"Carrito vaciado.", "info")
        return True

    if accion == "confirmar_pendiente":
        pend = st.session_state.get("mostrador_accion_pendiente")
        if pend:
            ok, msj = _ejecutar_accion_pendiente(
                vendedor, pend, carrito_ui, total_final, desc_porc
            )
            _limpiar_accion_pendiente()
            _chat_orden(orden,msj, "ok" if ok else "error")
            return ok
        _chat_orden(orden,"No hay acción pendiente.", "info")
        return False

    if accion == "cancelar_pendiente":
        _limpiar_accion_pendiente()
        _chat_orden(orden,"Acción cancelada.", "info")
        return True

    texto = resp.get("respuesta") or "Orden procesada."
    _chat_orden(orden,texto, "info" if accion != "error" else "error")
    return accion != "error"


def _render_atajos_clientes(vendedor):
    """Botones rápidos con clientes frecuentes de Firebase (mecánicos / cuenta corriente)."""
    clientes = listar_clientes_frecuentes(8)
    if not clientes:
        return
    st.caption("Clientes frecuentes")
    n_cols = min(4, len(clientes))
    cols = st.columns(n_cols)
    for i, (nombre, _datos) in enumerate(clientes):
        with cols[i % n_cols]:
            etiqueta = nombre if len(nombre) <= 24 else nombre[:22] + "…"
            if st.button(
                etiqueta,
                key=f"cli_freq_{vendedor}_{i}",
                use_container_width=True,
                help=f"Activar cliente {nombre}",
            ):
                activar_cliente_voz(nombre_cliente=nombre)
                guardar_mensaje_chat(
                    f"cliente {nombre}",
                    f"Cliente activo: **{nombre}**.",
                    "ok",
                )
                st.rerun()


def _render_entrada_orden(
    vendedor,
    placeholder: str,
    key_suffix: str,
    obtener_inventario_completo,
    buscar_en_inventario,
    agregar_al_carrito,
    *,
    mostrar_atajos: bool = True,
):
    """Micrófono + chat: entrada unificada para dictado en celular."""
    if mostrar_atajos:
        _render_atajos_clientes(vendedor)
    dictado = render_boton_dictado(f"mic_{vendedor}_{key_suffix}")
    orden = st.chat_input(placeholder, key=f"chat_{key_suffix}_{vendedor}")
    entrada = (dictado or orden or "").strip()
    if not entrada:
        return
    with st.spinner("Procesando…"):
        _procesar_orden_chat(
            vendedor,
            entrada,
            obtener_inventario_completo,
            buscar_en_inventario,
            agregar_al_carrito,
        )
    st.rerun()


def _etiqueta_cancelar_operacion() -> str:
    intent = obtener_intent_venta()
    if intent == "presupuesto":
        return "❌ Cancelar presupuesto"
    if intent in ("factura_a", "factura_b"):
        return "❌ Cancelar factura"
    return "❌ Cancelar operación"


def _hay_operacion_activa_mostrador(vendedor, carrito_efectivo_mostrador) -> bool:
    """True si hay algo que cancelar (carrito, chat, cliente, coincidencias, PDF)."""
    estado = obtener_estado_venta(vendedor)
    if estado in (EstadoVenta.ARMANDO, EstadoVenta.REVISAR, EstadoVenta.ELEGIR):
        return True
    if st.session_state.get("presupuesto_emitido_ok"):
        return True
    if st.session_state.get("resultados_ia_mostrador"):
        return True
    if st.session_state.get("venta_chat_historial"):
        return True
    if st.session_state.get("mostrador_intent_sugerido"):
        return True
    if st.session_state.get("mostrador_listo_para_ticket"):
        return True
    cli = st.session_state.get("cliente_activo") or {}
    if str(cli.get("nombre", "CONSUMIDOR FINAL")).upper() not in ("", "CONSUMIDOR FINAL"):
        return True
    carrito = carrito_efectivo_mostrador(vendedor, obtener_carrito(str(vendedor)) or [])
    return bool(carrito)


def _render_barra_cancelar_mostrador(vendedor, carrito_efectivo_mostrador):
    """Barra visible con cancelar y limpiar (siempre en mostrador, salvo venta cerrada)."""
    if obtener_estado_venta(vendedor) == EstadoVenta.LISTO:
        return

    with st.container(border=True):
        c_cancel, c_limpiar = st.columns([3, 2])
        with c_cancel:
            if st.button(
                _etiqueta_cancelar_operacion(),
                key=f"cancelar_barra_{vendedor}",
                type="primary",
                use_container_width=True,
                help="Vacía carrito, cliente, chat y coincidencias. Empezar de cero.",
            ):
                from modulos.ui_mostrador import cancelar_operacion_mostrador

                cancelar_operacion_mostrador(vendedor, reset_cliente=True)
                st.rerun()
        with c_limpiar:
            if st.button(
                "🧹 Limpiar pantalla",
                key=f"limpiar_barra_{vendedor}",
                use_container_width=True,
                help="Borra solo el chat y las coincidencias (mantiene el carrito).",
            ):
                limpiar_pantalla_mostrador(vendedor)
                st.rerun()


def _render_header_venta(vendedor, carrito_efectivo_mostrador, calcular_totales_carrito):
    cli = st.session_state.get("cliente_activo") or {}
    nombre = cli.get("nombre", "CONSUMIDOR FINAL")
    if len(nombre) > 28:
        nombre = nombre[:26] + "…"
    intent = etiqueta_intent()
    carrito = carrito_efectivo_mostrador(vendedor, obtener_carrito(str(vendedor)) or [])
    n_items = len(carrito)
    estado = obtener_estado_venta(vendedor)
    desc_porc = float(cli.get("descuento", 0))
    _, total = calcular_totales_carrito(carrito, desc_porc)
    st.markdown(
        f"<div class='mostrador-resumen-chip'>"
        f"<span><b>Cliente</b> {nombre}</span>"
        f"<span><b>{intent}</b></span>"
        f"<span><b>{n_items}</b> ítems</span>"
        f"<span><b>${total:,.2f}</b></span>"
        f"<span>{estado.replace('_', ' ').title()}</span>"
        f"</div>",
        unsafe_allow_html=True,
    )

def _render_chat_historial(vendedor):
    historial = obtener_historial_chat()
    if not historial:
        st.caption(
            "Dictá o escribí la orden completa. Ej: "
            "*presupuesto para Pablo, código 111 1, bielete para el 207 2 unidades* · "
            "Decí **listo** para revisar."
        )
        return
    for i, entrada in enumerate(historial):
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
    if st.session_state.get(f"manual_add_ctx_{vendedor}"):
        from modulos.ui_mostrador import render_agregar_manual_mostrador

        render_agregar_manual_mostrador(vendedor)


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
    """UI principal del mostrador: caja POS (teclado + voz) sin romper flujos existentes."""
    from modulos.ui_mostrador import render_agregar_manual_mostrador
    from modulos.mostrador_estado import limpiar_mensaje_chat

    estado = obtener_estado_venta(vendedor)
    _render_header_venta(vendedor, carrito_efectivo_mostrador, calcular_totales_carrito)
    from modulos.ui_mostrador import render_panel_cliente_pendiente_confirmar

    render_panel_cliente_pendiente_confirmar()
    _render_barra_cancelar_mostrador(vendedor, carrito_efectivo_mostrador)
    render_mostrador_accion_pendiente(vendedor)
    render_presupuesto_pdf_pendiente(vendedor)

    if estado == EstadoVenta.LISTO:
        render_factura_arca_exitosa("top")
        if st.button("✅ Nueva venta", type="primary", key=f"nueva_venta_chat_{vendedor}"):
            limpiar_venta_mostrador(vendedor, reset_cliente=True)
            limpiar_mensaje_chat()
            st.rerun()
        return

    carrito = obtener_carrito(str(vendedor)) or []
    carrito_ui = carrito_efectivo_mostrador(vendedor, carrito)
    desc_porc = float(st.session_state.cliente_activo.get("descuento", 0))
    total_bruto, total_final = calcular_totales_carrito(carrito_ui, desc_porc)

    if any(isinstance(i, dict) and (i.get("fuera_stock") or i.get("manual")) for i in carrito_ui):
        st.warning(
            "Hay ítems **manuales fuera de stock** en el carrito. "
            "No descontarán inventario al facturar."
        )

    if st.session_state.get(f"manual_add_ctx_{vendedor}"):
        render_agregar_manual_mostrador(vendedor)

    # —— Zona superior: teclado (búsqueda) + voz (orden) ——
    st.markdown(
        '<div class="mostrador-pos-zona">1 · ⌨️ Teclado · 🎤 Voz</div>',
        unsafe_allow_html=True,
    )
    with st.container(border=True):
        col_teclado, col_voz = st.columns([1.35, 1], gap="medium")
        with col_teclado:
            if inv_mostrador:
                render_buscador_productos(
                    vendedor, inv_mostrador, agregar_al_carrito, filtrar_inventario
                )
            else:
                st.info("Inventario vacío.")
        with col_voz:
            st.caption(
                "Órdenes completas: cliente + ítems. Ej: "
                "*factura para Franco de una biela y un arranque gol trend*"
            )
            _render_entrada_orden(
                vendedor,
                "Dictá o escribí la orden…",
                "pos_voz",
                obtener_inventario_completo,
                buscar_en_inventario,
                agregar_al_carrito,
                mostrar_atajos=True,
            )
            with st.expander("Historial de órdenes", expanded=False):
                _render_chat_historial(vendedor)

    if estado == EstadoVenta.ELEGIR:
        st.markdown(
            '<div class="mostrador-pos-zona">Elegí coincidencia</div>',
            unsafe_allow_html=True,
        )
        with st.container(border=True):
            render_panel_coincidencias_mostrador(
                vendedor,
                agrupar_por_maestro,
                agregar_al_carrito,
                buscar_en_inventario=buscar_en_inventario,
                obtener_inventario=obtener_inventario_completo,
            )

    st.divider()
    # —— Artículos siempre visibles ——
    st.markdown(
        '<div class="mostrador-pos-zona">2 · Artículos</div>',
        unsafe_allow_html=True,
    )
    with st.container(border=True):
        if carrito_ui:
            render_carrito_grilla(vendedor, carrito_ui)
        else:
            st.caption("Sin ítems. Buscá por teclado o dictá la orden.")

    st.divider()
    # —— Pie fijo: Cliente | Facturación | Importes ——
    st.markdown(
        '<div class="mostrador-pos-zona">3 · Cliente · Facturación · Importes</div>',
        unsafe_allow_html=True,
    )
    st.markdown('<div class="mostrador-pos-pie">', unsafe_allow_html=True)
    pie_cli, pie_fact, pie_imp = st.columns([1.2, 1, 1], gap="medium")
    with pie_cli:
        with st.container(border=True):
            st.markdown("**Cliente**")
            render_seccion_cliente_mostrador()
    with pie_fact:
        with st.container(border=True):
            st.markdown(f"**Facturación** · {etiqueta_intent()}")
            render_credenciales_arca()
            intent_opts = ["factura_b", "factura_a", "presupuesto"]
            intent_actual = obtener_intent_venta()
            if intent_actual not in intent_opts:
                intent_actual = "factura_b"
            key_intent = f"pos_intent_{vendedor}"
            if key_intent not in st.session_state:
                st.session_state[key_intent] = intent_actual
            nuevo_intent = st.radio(
                "Comprobante",
                options=intent_opts,
                format_func=etiqueta_intent,
                horizontal=True,
                key=key_intent,
            )
            if nuevo_intent != st.session_state.get("mostrador_intent_sugerido"):
                st.session_state.mostrador_intent_sugerido = nuevo_intent
                if carrito_ui:
                    marcar_verificacion_mostrador(nuevo_intent)
    with pie_imp:
        render_panel_cobro_mostrador(
            vendedor, carrito_ui, total_bruto, total_final, desc_porc
        )
    st.markdown("</div>", unsafe_allow_html=True)

    with st.expander("Más herramientas", expanded=False):
        col_v, col_n = st.columns(2)
        with col_v:
            if st.button("🗑️ Vaciar carrito", key=f"vaciar_chat_{vendedor}", use_container_width=True):
                limpiar_venta_mostrador(vendedor, reset_cliente=False)
                limpiar_mensaje_chat()
                st.rerun()
        with col_n:
            if st.button("✅ Nueva venta", key=f"nueva_chat_{vendedor}", use_container_width=True):
                limpiar_venta_mostrador(vendedor, reset_cliente=True)
                limpiar_mensaje_chat()
                st.rerun()

        render_presupuestos_guardados(vendedor)
        tabs = ["⌨️ Pistola", "📷 QR"]
        t_manual, t_qr = st.tabs(tabs)
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
