"""Mostrador caja POS: búsqueda rápida, grilla y facturación (sin chat/voz)."""
import streamlit as st

from modulos.db_firebase import obtener_carrito
from modulos.mostrador_estado import EstadoVenta, obtener_estado_venta
from modulos.util_busqueda import (
    buscar_codigo_exacto_inventario,
    buscar_en_inventario_mostrador,
)


def _digitos(valor) -> str:
    return "".join(filter(str.isdigit, str(valor or "")))


def _cuit_vacio(cuit) -> bool:
    dig = _digitos(cuit)
    return (not dig) or set(dig) <= {"0"}


def _cuit_ok_factura_a(cuit) -> bool:
    dig = _digitos(cuit)
    return len(dig) == 11 and not set(dig) <= {"0"}


def _cuit_visible(cuit) -> str:
    if _cuit_vacio(cuit):
        return "—"
    return _digitos(cuit)


def _es_consumidor_final(cli: dict) -> bool:
    nombre = str((cli or {}).get("nombre") or "").strip().upper()
    return nombre in ("", "CONSUMIDOR FINAL") and _cuit_vacio((cli or {}).get("cuit"))


def sincronizar_cliente_desde_caja():
    """Expone sync para usar antes de facturar."""
    _sync_cliente_caja_desde_inputs()


def _sync_cliente_caja_desde_inputs():
    from modulos.ui_mostrador import normalizar_cliente_activo

    nombre = str(st.session_state.get("caja_nombre_cli") or "").strip()
    cuit = _digitos(st.session_state.get("caja_cuit_cli"))
    tipo = str(st.session_state.get("caja_tipo_fc") or "6")
    if tipo not in ("1", "6"):
        tipo = "6"
    try:
        desc = float(st.session_state.get("caja_desc_cli") or 0)
    except (TypeError, ValueError):
        desc = 0.0
    telefono = str(st.session_state.get("caja_celular_cli") or "").strip()
    condicion_iva = str(st.session_state.get("caja_condicion_iva") or "").strip()
    actual = st.session_state.get("cliente_activo") or {}
    st.session_state.cliente_activo = normalizar_cliente_activo({
        **actual,
        "nombre": nombre.upper() if nombre else "CONSUMIDOR FINAL",
        "cuit": cuit or "00000000000",
        "tipo_comprobante": tipo,
        "descuento": desc,
        "telefono": telefono,
        "condicion_iva": condicion_iva,
    })
    st.session_state.mostrador_intent_sugerido = (
        "factura_a" if tipo == "1" else "factura_b"
    )


def _cargar_inputs_cliente(cli: dict):
    cf = _es_consumidor_final(cli)
    st.session_state.caja_nombre_cli = "" if cf else str(cli.get("nombre") or "")
    st.session_state.caja_cuit_cli = "" if _cuit_vacio(cli.get("cuit")) else _digitos(cli.get("cuit"))
    st.session_state.caja_tipo_fc = str(cli.get("tipo_comprobante") or "6")
    st.session_state.caja_desc_cli = float(cli.get("descuento") or 0)
    st.session_state.caja_celular_cli = str(cli.get("telefono") or cli.get("celular") or "")
    st.session_state.caja_condicion_iva = str(cli.get("condicion_iva") or "")


def _usar_cliente_encontrado(datos: dict):
    from modulos.ui_mostrador import establecer_cliente_mostrador

    establecer_cliente_mostrador(datos)
    _cargar_inputs_cliente(st.session_state.cliente_activo)
    st.session_state.pop("caja_msg_cli", None)


def _volver_consumidor_final():
    from modulos.ui_mostrador import cliente_consumidor_final, establecer_cliente_mostrador

    establecer_cliente_mostrador(cliente_consumidor_final())
    _cargar_inputs_cliente(st.session_state.cliente_activo)
    st.session_state.pop("caja_msg_cli", None)


def _aplicar_tipo_factura(tipo: str):
    from modulos.ui_mostrador import configurar_cliente, establecer_cliente_mostrador

    st.session_state.caja_tipo_fc = tipo
    nombre = str(st.session_state.get("caja_nombre_cli") or "").strip()
    cuit = _digitos(st.session_state.get("caja_cuit_cli"))
    _sync_cliente_caja_desde_inputs()
    if tipo == "1" and not _cuit_ok_factura_a(cuit):
        st.session_state.caja_msg_cli = (
            "Factura A: escribí el CUIT de 11 dígitos. Todavía no se puede facturar."
        )
        return
    if nombre and cuit:
        ok, msj = configurar_cliente(
            nombre.upper(),
            cuit,
            float(st.session_state.get("caja_desc_cli") or 0),
            tipo,
            telefono=str(st.session_state.get("caja_celular_cli") or "").strip(),
            condicion_iva=str(st.session_state.get("caja_condicion_iva") or "").strip(),
        )
        if not ok:
            st.session_state.caja_msg_cli = msj
            return
        establecer_cliente_mostrador({
            "nombre": nombre.upper(),
            "cuit": cuit,
            "descuento": float(st.session_state.get("caja_desc_cli") or 0),
            "tipo_comprobante": tipo,
            "telefono": str(st.session_state.get("caja_celular_cli") or "").strip(),
            "condicion_iva": str(st.session_state.get("caja_condicion_iva") or "").strip(),
        })
        _cargar_inputs_cliente(st.session_state.cliente_activo)
    st.session_state.pop("caja_msg_cli", None)


def _procesar_busqueda_caja(vendedor, termino, cant, inv, agregar_al_carrito):
    """Código exacto → carrito directo; texto → panel de coincidencias."""
    from modulos.mostrador_voz_flujo import descartar_panels_operacion_anterior

    termino = str(termino or "").strip()
    if len(termino) < 1:
        return False, "Escribí un código o descripción."

    cant = max(1, int(cant or 1))
    st.session_state.caja_cant_agregar = cant
    descartar_panels_operacion_anterior()

    exactos = buscar_codigo_exacto_inventario(inv, termino)
    if len(exactos) == 1:
        exito, msj = agregar_al_carrito(vendedor, exactos[0]["id"], cant)
        if exito:
            st.session_state.pop(f"manual_add_ctx_{vendedor}", None)
        return exito, msj
    if len(exactos) > 1:
        st.session_state.resultados_ia_mostrador = exactos[:25]
        st.session_state.msg_ia_mostrador = f"Varias variantes para «{termino}» — tocá una:"
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
    st.session_state.msg_ia_mostrador = f"Tocá el producto para «{termino}»:"
    return True, ""


def render_barra_cliente_caja():
    """Cliente siempre visible: cuadros, sin menús desplegables."""
    from modulos.cliente_resolver import clientes_cache_mostrador
    from modulos.ui_mostrador import _filtrar_clientes, normalizar_cliente_activo

    pendiente = st.session_state.pop("caja_cliente_pendiente", None)
    if pendiente == "cf":
        _volver_consumidor_final()
    elif isinstance(pendiente, tuple) and pendiente and pendiente[0] == "tipo":
        _aplicar_tipo_factura(pendiente[1])
    elif isinstance(pendiente, dict):
        _usar_cliente_encontrado(pendiente)

    st.session_state.cliente_activo = normalizar_cliente_activo(
        st.session_state.get("cliente_activo")
    )
    cli = st.session_state.cliente_activo
    if "caja_nombre_cli" not in st.session_state:
        _cargar_inputs_cliente(cli)

    clientes_db = clientes_cache_mostrador() or {}
    tipo = str(st.session_state.get("caja_tipo_fc") or cli.get("tipo_comprobante") or "6")

    st.markdown("**Cliente**")
    c_b, c_a, c_cf = st.columns(3)
    with c_b:
        if st.button(
            "Factura B",
            type="primary" if tipo != "1" else "secondary",
            use_container_width=True,
            key="caja_btn_fb",
        ):
            st.session_state.caja_cliente_pendiente = ("tipo", "6")
            st.rerun()
    with c_a:
        if st.button(
            "Factura A",
            type="primary" if tipo == "1" else "secondary",
            use_container_width=True,
            key="caja_btn_fa",
        ):
            st.session_state.caja_cliente_pendiente = ("tipo", "1")
            st.rerun()
    with c_cf:
        if st.button(
            "Consumidor final",
            use_container_width=True,
            key="caja_btn_cf",
        ):
            st.session_state.caja_cliente_pendiente = "cf"
            st.rerun()

    msg = st.session_state.get("caja_msg_cli")
    if msg:
        st.warning(msg)

    c_nom, c_cuit, c_tel, c_dto = st.columns([3, 2, 2, 1])
    c_nom.text_input(
        "Nombre",
        key="caja_nombre_cli",
        placeholder="Razón social o nombre",
        on_change=_sync_cliente_caja_desde_inputs,
    )
    c_cuit.text_input(
        "CUIT / DNI",
        key="caja_cuit_cli",
        placeholder="CUIT, DNI o CUIL",
        on_change=_sync_cliente_caja_desde_inputs,
    )
    c_tel.text_input(
        "Celular",
        key="caja_celular_cli",
        placeholder="11 1234-5678",
        on_change=_sync_cliente_caja_desde_inputs,
    )
    c_dto.number_input(
        "% Dto",
        min_value=0.0,
        step=1.0,
        key="caja_desc_cli",
        on_change=_sync_cliente_caja_desde_inputs,
    )
    st.text_input(
        "Condición IVA (opcional)",
        key="caja_condicion_iva",
        placeholder="Ej: IVA Exento, Monotributo, Responsable inscripto…",
        on_change=_sync_cliente_caja_desde_inputs,
    )

    nombre_mostrar = str(st.session_state.get("caja_nombre_cli") or "").strip() or "CONSUMIDOR FINAL"
    cuit_mostrar = _cuit_visible(st.session_state.get("caja_cuit_cli"))
    cel_mostrar = str(st.session_state.get("caja_celular_cli") or "").strip()
    resumen = f"Se factura a **{nombre_mostrar}** · CUIT {cuit_mostrar}"
    if cel_mostrar:
        resumen += f" · Cel {cel_mostrar}"
    st.caption(resumen)
    if tipo == "1" and not _cuit_ok_factura_a(st.session_state.get("caja_cuit_cli")):
        st.warning("Factura A: el CUIT tiene que tener 11 dígitos. Si no, usá Factura B.")

    buscar_cli = st.text_input(
        "Buscar cliente guardado",
        key="caja_buscar_cliente",
        placeholder="Nombre o CUIT…",
    )
    termino = (buscar_cli or "").strip()
    if len(termino) >= 2:
        encontrados = _filtrar_clientes(clientes_db, termino)
        if not encontrados:
            st.caption("No hay clientes guardados con eso. Completá nombre y CUIT y tocá Factura A o B.")
        else:
            for i in range(0, min(len(encontrados), 8), 2):
                cols = st.columns(2)
                for col, par in zip(cols, encontrados[i:i + 2]):
                    id_cli, datos = par
                    nom = str((datos or {}).get("nombre") or "—")
                    tipo_c = "A" if str((datos or {}).get("tipo_comprobante")) == "1" else "B"
                    etiqueta = f"{nom} · {id_cli} · {tipo_c}"
                    if col.button(etiqueta, key=f"caja_cli_{id_cli}", use_container_width=True):
                        st.session_state.caja_cliente_pendiente = {
                            **(datos or {}),
                            "cuit": id_cli,
                            "cuit_dni": id_cli,
                        }
                        st.rerun()


def render_coincidencias_caja(vendedor, agrupar_por_maestro, agregar_al_carrito):
    """Resultados de producto: un toque agrega, sin listas desplegables."""
    resultados = st.session_state.get("resultados_ia_mostrador")
    if not resultados:
        return

    vid = str(vendedor)
    col_msg, col_x = st.columns([11, 1])
    with col_msg:
        st.markdown(f"**{st.session_state.get('msg_ia_mostrador', 'Elegí el producto')}**")
    with col_x:
        if st.button("✕", key=f"cerrar_coinc_caja_{vid}"):
            st.session_state.resultados_ia_mostrador = None
            st.session_state.msg_ia_mostrador = None
            st.rerun()

    cant = max(1, int(st.session_state.get("caja_cant_agregar") or 1))
    grupos = agrupar_por_maestro(resultados)
    for gkey in sorted(grupos.keys(), key=lambda k: grupos[k]["descripcion"]):
        g = grupos[gkey]
        for res in g["variantes"]:
            rid = str(res.get("id", ""))
            if not rid:
                continue
            marca_res = res.get("marca", res.get("condicion", ""))
            precio_f = float(res.get("precio_venta", 0))
            stock = res.get("stock", 0)
            label = (
                f"{g.get('codigo', '')} · {g.get('descripcion', '')[:42]} · "
                f"{marca_res} · {stock} u. · ${precio_f:,.0f}"
            )
            if st.button(label, key=f"caja_prod_{rid}", use_container_width=True):
                exito, msj_db = agregar_al_carrito(vid, rid, cant)
                if exito:
                    st.session_state.resultados_ia_mostrador = None
                    st.session_state.msg_ia_mostrador = None
                    st.rerun()
                else:
                    st.error(msj_db)


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
    carrito = carrito_efectivo_mostrador(vendedor, obtener_carrito(str(vendedor)) or [])
    n_items = len(carrito)
    cli = st.session_state.get("cliente_activo") or {}
    desc_porc = float(cli.get("descuento", 0))
    _, total = calcular_totales_carrito(carrito, desc_porc)
    st.markdown(
        f"<div class='mostrador-resumen-chip'>"
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
            for k in (
                "caja_nombre_cli", "caja_cuit_cli", "caja_tipo_fc", "caja_desc_cli",
                "caja_celular_cli", "caja_condicion_iva",
            ):
                st.session_state.pop(k, None)
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
            for k in (
                "caja_nombre_cli", "caja_cuit_cli", "caja_tipo_fc", "caja_desc_cli",
                "caja_celular_cli", "caja_condicion_iva",
            ):
                st.session_state.pop(k, None)
            st.rerun()

    if inv_mostrador:
        render_buscador_caja(vendedor, inv_mostrador, agregar_al_carrito)
    else:
        st.info("Inventario vacío.")

    render_barra_cliente_caja()
    render_coincidencias_caja(vendedor, agrupar_por_maestro, agregar_al_carrito)

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

    with st.expander("Presupuestos guardados", expanded=False):
        render_presupuestos_guardados(vendedor)
        render_credenciales_arca()
