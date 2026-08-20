"""Mostrador caja POS: búsqueda rápida, grilla y facturación (sin chat/voz)."""
import streamlit as st

from modulos.db_firebase import obtener_carrito
from modulos.mostrador_estado import EstadoVenta, obtener_estado_venta
from modulos.util_busqueda import (
    buscar_codigo_exacto_inventario,
    buscar_en_inventario_mostrador,
)


def _procesar_busqueda_caja(vendedor, termino, cant, inv, agregar_al_carrito):
    """Código exacto → carrito directo; texto → panel de coincidencias."""
    from modulos.mostrador_voz_flujo import descartar_panels_operacion_anterior

    termino = str(termino or "").strip()
    if len(termino) < 1:
        return False, "Escribí un código o descripción."

    cant = max(1, int(cant or 1))
    descartar_panels_operacion_anterior()

    exactos = buscar_codigo_exacto_inventario(inv, termino)
    if len(exactos) == 1:
        exito, msj = agregar_al_carrito(vendedor, exactos[0]["id"], cant)
        if exito:
            st.session_state.pop(f"manual_add_ctx_{vendedor}", None)
        return exito, msj
    if len(exactos) > 1:
        st.session_state.resultados_ia_mostrador = exactos[:25]
        st.session_state.msg_ia_mostrador = f"Varias variantes para «{termino}» — elegí una:"
        return True, ""

    encontrados = buscar_en_inventario_mostrador(inv, termino)[:25]
    if not encontrados:
        st.session_state[f"manual_add_ctx_{vendedor}"] = {
            "termino": termino,
            "vehiculo": None,
            "cantidad": cant,
        }
        return False, f"Sin coincidencias para «{termino}». Podés agregar manual abajo."

    st.session_state.resultados_ia_mostrador = encontrados
    st.session_state.msg_ia_mostrador = f"Elegí el producto para «{termino}»:"
    return True, ""


def render_barra_cliente_caja():
    """Cliente activo compacto: CF por defecto, Factura A/B según cliente."""
    from modulos.cliente_resolver import clientes_cache_mostrador
    from modulos.ui_mostrador import (
        _filtrar_clientes,
        _label_cliente_listado,
        _tipo_comprobante_label_largo,
        cliente_consumidor_final,
        configurar_cliente,
        establecer_cliente_mostrador,
        normalizar_cliente_activo,
    )

    st.session_state.cliente_activo = normalizar_cliente_activo(
        st.session_state.get("cliente_activo")
    )
    cli = st.session_state.cliente_activo
    clientes_db = clientes_cache_mostrador() or {}

    c_nom, c_tipo, c_cf = st.columns([4, 2.2, 1.2])
    with c_nom:
        st.markdown(f"**{cli['nombre']}**")
        extra = f"CUIT {cli['cuit']}"
        if cli["descuento"] > 0:
            extra += f" · Dto {cli['descuento']}%"
        st.caption(extra)
    with c_tipo:
        st.caption(_tipo_comprobante_label_largo(cli["tipo_comprobante"]))
    with c_cf:
        if st.button("CF", key="caja_cli_cf", use_container_width=True, help="Consumidor final"):
            establecer_cliente_mostrador(cliente_consumidor_final())
            st.rerun()

    with st.expander("Cliente — buscar o cargar", expanded=False):
        if clientes_db:
            buscar_cli = st.text_input(
                "Nombre, CUIT o DNI",
                key="caja_buscar_cliente",
                placeholder="Ej: García, 30716…",
            )
            termino = (buscar_cli or "").strip()
            if len(termino) >= 2:
                encontrados = _filtrar_clientes(clientes_db, termino)
                if encontrados:
                    ids = [x[0] for x in encontrados]
                    sel_id = st.selectbox(
                        "Resultados",
                        options=ids,
                        format_func=lambda x: _label_cliente_listado(x, clientes_db.get(x, {})),
                        key="caja_sel_cliente",
                        label_visibility="collapsed",
                    )
                    if st.button(
                        "Usar cliente",
                        type="primary",
                        key="caja_usar_cliente",
                        use_container_width=True,
                    ):
                        establecer_cliente_mostrador(clientes_db.get(sel_id, {}))
                        st.rerun()
                else:
                    st.warning("No hay clientes que coincidan.")

        with st.form("caja_alta_cliente"):
            c1, c2 = st.columns(2)
            nombre_nuevo = c1.text_input("Nombre / Razón Social")
            cuit_nuevo = c2.text_input("DNI o CUIT")
            desc_nuevo = st.number_input("% Desc.", min_value=0.0, step=1.0, value=0.0)
            tipo_nuevo = st.radio(
                "Tipo de factura",
                options=["6", "1"],
                format_func=_tipo_comprobante_label_largo,
                horizontal=True,
                label_visibility="collapsed",
            )
            if st.form_submit_button("Guardar y usar", use_container_width=True):
                if nombre_nuevo and cuit_nuevo:
                    ok, msj = configurar_cliente(
                        nombre_nuevo.upper(),
                        cuit_nuevo,
                        desc_nuevo,
                        tipo_nuevo,
                    )
                    if ok:
                        id_cli = "".join(filter(str.isdigit, str(cuit_nuevo)))
                        establecer_cliente_mostrador(clientes_db.get(id_cli, {}) or {
                            "nombre": nombre_nuevo.upper(),
                            "cuit_dni": id_cli,
                            "descuento": desc_nuevo,
                            "tipo_comprobante": tipo_nuevo,
                        })
                        st.success(msj)
                        st.rerun()
                    else:
                        st.error(msj)
                else:
                    st.warning("Completá nombre y CUIT/DNI.")


def render_buscador_caja(vendedor, inv, agregar_al_carrito):
    """Campo grande de código/producto arriba de la grilla."""
    st.markdown(
        '<div class="mostrador-caja-busqueda"><strong>Agregar producto</strong></div>',
        unsafe_allow_html=True,
    )
    with st.form(f"busq_caja_{vendedor}", clear_on_submit=True):
        c_busq, c_cant, c_btn = st.columns([5.5, 1, 1.5])
        with c_busq:
            termino = st.text_input(
                "Código o producto",
                placeholder="Código, descripción, vehículo…",
                label_visibility="collapsed",
            )
        with c_cant:
            cant = st.number_input(
                "Cant.",
                min_value=1,
                step=1,
                value=1,
                label_visibility="collapsed",
            )
        with c_btn:
            enviado = st.form_submit_button(
                "➕ Agregar",
                type="primary",
                use_container_width=True,
            )
        if enviado:
            ok, msj = _procesar_busqueda_caja(
                vendedor, termino, cant, inv, agregar_al_carrito
            )
            if msj:
                if ok:
                    st.toast(msj)
                elif "manual" in msj.lower():
                    st.warning(msj)
                else:
                    st.error(msj)
            st.rerun()


def _render_resumen_caja(vendedor, carrito_efectivo_mostrador, calcular_totales_carrito):
    from modulos.mostrador_estado import etiqueta_intent

    cli = st.session_state.get("cliente_activo") or {}
    nombre = cli.get("nombre", "CONSUMIDOR FINAL")
    if len(nombre) > 32:
        nombre = nombre[:30] + "…"
    carrito = carrito_efectivo_mostrador(vendedor, obtener_carrito(str(vendedor)) or [])
    n_items = len(carrito)
    desc_porc = float(cli.get("descuento", 0))
    _, total = calcular_totales_carrito(carrito, desc_porc)
    st.markdown(
        f"<div class='mostrador-resumen-chip'>"
        f"<span><b>Cliente</b> {nombre}</span>"
        f"<span><b>{etiqueta_intent()}</b></span>"
        f"<span><b>{n_items}</b> ítems</span>"
        f"<span><b>${total:,.2f}</b></span>"
        f"</div>",
        unsafe_allow_html=True,
    )


def render_mostrador_caja(
    vendedor,
    inv_mostrador,
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
    render_presupuestos_guardados,
    carrito_efectivo_mostrador,
    calcular_totales_carrito,
    limpiar_venta_mostrador,
):
    """UI caja POS: búsqueda + grilla + cobro (sin chat ni voz)."""
    from modulos.ui_mostrador import (
        cancelar_operacion_mostrador,
        normalizar_cliente_activo,
        render_agregar_manual_mostrador,
        render_mostrador_accion_pendiente,
        render_panel_cliente_pendiente_confirmar,
        render_presupuesto_pdf_pendiente,
    )

    st.session_state.mostrador_modo_caja = True
    cli = normalizar_cliente_activo(st.session_state.get("cliente_activo"))
    st.session_state.cliente_activo = cli
    cbte = str(cli.get("tipo_comprobante", "6"))
    st.session_state.mostrador_intent_sugerido = (
        "factura_a" if cbte == "1" else "factura_b"
    )

    estado = obtener_estado_venta(vendedor)
    _render_resumen_caja(vendedor, carrito_efectivo_mostrador, calcular_totales_carrito)
    render_panel_cliente_pendiente_confirmar()
    render_mostrador_accion_pendiente(vendedor)
    render_presupuesto_pdf_pendiente(vendedor)

    if estado == EstadoVenta.LISTO:
        render_factura_arca_exitosa("caja")
        if st.button("✅ Nueva venta", type="primary", key=f"nueva_venta_caja_{vendedor}"):
            limpiar_venta_mostrador(vendedor, reset_cliente=True)
            st.rerun()
        return

    carrito = obtener_carrito(str(vendedor)) or []
    carrito_ui = carrito_efectivo_mostrador(vendedor, carrito)
    desc_porc = float(st.session_state.cliente_activo.get("descuento", 0))
    total_bruto, total_final = calcular_totales_carrito(carrito_ui, desc_porc)

    c_cancel, _ = st.columns([1, 4])
    with c_cancel:
        if st.button(
            "❌ Cancelar venta",
            key=f"caja_cancelar_{vendedor}",
            use_container_width=True,
            help="Vacía carrito y vuelve a consumidor final.",
        ):
            cancelar_operacion_mostrador(vendedor, reset_cliente=True)
            st.rerun()

    if inv_mostrador:
        render_buscador_caja(vendedor, inv_mostrador, agregar_al_carrito)
    else:
        st.info("Inventario vacío.")

    render_barra_cliente_caja()

    render_panel_coincidencias_mostrador(
        vendedor,
        agrupar_por_maestro,
        agregar_al_carrito,
        buscar_en_inventario=buscar_en_inventario,
        obtener_inventario=obtener_inventario_completo,
    )

    if st.session_state.get(f"manual_add_ctx_{vendedor}"):
        render_agregar_manual_mostrador(vendedor)

    if any(
        isinstance(i, dict) and (i.get("fuera_stock") or i.get("manual"))
        for i in carrito_ui
    ):
        st.warning(
            "Hay ítems **manuales fuera de stock** en el carrito. "
            "No descontarán inventario al facturar."
        )

    if carrito_ui:
        render_carrito_grilla(vendedor, carrito_ui)
        st.markdown('<div class="mostrador-pos-pie">Cobro</div>', unsafe_allow_html=True)
        render_panel_cobro_mostrador(
            vendedor, carrito_ui, total_bruto, total_final, desc_porc
        )
    else:
        st.caption("Carrito vacío — escaneá o buscá un producto arriba.")

    with st.expander("Más opciones", expanded=False):
        render_presupuestos_guardados(vendedor)
        render_credenciales_arca()
