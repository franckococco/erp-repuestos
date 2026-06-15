"""UI del mostrador: cliente, búsqueda de productos y facturación ARCA."""
import base64
from typing import Optional

import streamlit as st
import streamlit.components.v1 as components

from modulos.db_firebase import (
    obtener_clientes,
    configurar_cliente,
    cliente_consumidor_final,
    cliente_db_a_activo,
    obtener_carrito,
    vaciar_carrito,
    confirmar_venta,
    validar_carrito_para_venta,
    guardar_comprobante_arca,
    guardar_presupuesto,
    listar_presupuestos_guardados,
    reabrir_presupuesto_en_carrito,
    actualizar_estado_presupuesto,
    eliminar_presupuesto_guardado,
)
from modulos.factura_arca_client import generar_factura, cargar_datos_nube
from modulos.factura_arca_pdf import crear_ticket, crear_a4
from modulos.util_fechas import formatear_fecha_ar
from modulos.ia_mostrador import (
    FORMAS_PAGO,
    procesar_orden_mostrador,
    normalizar_forma_pago,
)


CONFIG_TICKET_DEFAULT = {
    "margen_x": 2.0,
    "margen_y": 2.0,
    "font_size": 8,
    "condicion_iva": "IVA Responsable Inscripto",
    "cuit_emisor": "",
    "iibb": "Ingresos Brutos: A-76154",
    "inicio_act": "Inicio de Actividades: 02/05/2023",
    "leyenda_extra": "¡Gracias por su compra!",
}


def normalizar_cliente_activo(cliente: Optional[dict]) -> dict:
    base = cliente_consumidor_final()
    if not isinstance(cliente, dict):
        return base
    cbte = str(cliente.get("tipo_comprobante", cliente.get("cbte_tipo", "6"))).strip()
    if cbte not in ("1", "6"):
        cbte = "6"
    cuit = "".join(filter(str.isdigit, str(cliente.get("cuit", "00000000000")))) or "00000000000"
    return {
        "nombre": str(cliente.get("nombre", base["nombre"])).upper(),
        "cuit": cuit,
        "descuento": float(cliente.get("descuento", 0.0)),
        "tipo_comprobante": cbte,
    }


def _defaults_desde_streamlit_secrets():
    cuit = ""
    clave = ""
    try:
        cuit = str(st.secrets.get("FACTURADOR_CUIT", "") or "")
        clave = str(st.secrets.get("FACTURADOR_CLAVE_SECRETA", "") or "")
        bloque = st.secrets.get("facturador", {})
        if isinstance(bloque, dict):
            cuit = cuit or str(bloque.get("cuit", "") or "")
            clave = clave or str(bloque.get("clave", "") or "")
    except Exception:
        pass
    return cuit.strip(), clave.strip()


def init_credenciales_arca_session():
    if st.session_state.get("_credenciales_arca_inited"):
        return
    cuit_def, clave_def = _defaults_desde_streamlit_secrets()
    if "facturador_cuit_ui" not in st.session_state:
        st.session_state.facturador_cuit_ui = cuit_def
    if "facturador_clave_ui" not in st.session_state:
        st.session_state.facturador_clave_ui = clave_def
    st.session_state._credenciales_arca_inited = True


def _leer_secrets_facturador():
    init_credenciales_arca_session()
    config_ticket = dict(CONFIG_TICKET_DEFAULT)
    cuit = str(st.session_state.get("facturador_cuit_ui", "") or "").strip()
    clave = str(st.session_state.get("facturador_clave_ui", "") or "").strip()
    if not cuit or not clave:
        cuit_sec, clave_sec = _defaults_desde_streamlit_secrets()
        cuit = cuit or cuit_sec
        clave = clave or clave_sec
    try:
        bloque = st.secrets.get("facturador", {})
        if isinstance(bloque, dict):
            cfg = bloque.get("config_ticket")
            if isinstance(cfg, dict):
                config_ticket.update(cfg)
        cfg_top = st.secrets.get("FACTURADOR_CONFIG_TICKET")
        if isinstance(cfg_top, dict):
            config_ticket.update(cfg_top)
    except Exception:
        pass
    if cuit and not config_ticket.get("cuit_emisor"):
        config_ticket["cuit_emisor"] = cuit
    return cuit, clave, config_ticket


def render_credenciales_arca():
    init_credenciales_arca_session()
    cuit, clave, _ = _leer_secrets_facturador()
    configurado = bool(cuit and clave)

    with st.expander(
        "🔑 Facturación ARCA — CUIT emisor y clave secreta",
        expanded=not configurado,
    ):
        col_cuit, col_clave = st.columns(2)
        with col_cuit:
            st.text_input(
                "CUIT emisor (facturador)",
                key="facturador_cuit_ui",
                placeholder="30716713179",
            )
        with col_clave:
            st.text_input(
                "Clave secreta",
                key="facturador_clave_ui",
                type="password",
                placeholder="Clave del backend ARCA",
            )
        if configurado:
            mask = f"{cuit[:2]}…{cuit[-2:]}" if len(cuit) >= 4 else cuit
            st.caption(f"Listo para facturar · CUIT {mask}")
        else:
            st.warning("Completá ambos campos para habilitar «Emitir factura ARCA».")
        st.caption(
            "Quedan guardados mientras la app esté abierta. "
            "Para dejarlos fijos: Settings → Secrets en Streamlit Cloud."
        )


def _tipo_comprobante_label(cbte: str) -> str:
    return "Factura A" if str(cbte) == "1" else "Factura B"


def _cerrar_presupuesto_cargado(estado: str):
    pres_id = st.session_state.get("presupuesto_cargado_id")
    if pres_id:
        actualizar_estado_presupuesto(pres_id, estado)
        st.session_state.presupuesto_cargado_id = None


def render_presupuestos_guardados(vendedor, generar_pdf_presupuesto):
    with st.expander("📁 Presupuestos guardados", expanded=False):
        solo_abiertos = st.checkbox("Solo abiertos", value=True, key="pres_solo_abiertos")
        lista = listar_presupuestos_guardados(solo_abiertos=solo_abiertos, limite=30)

        if not lista:
            st.info("No hay presupuestos guardados.")
            return

        filas = []
        for p in lista:
            cli = p.get("cliente") or {}
            filas.append({
                "ID": p.get("id", "")[:8],
                "Fecha": formatear_fecha_ar(p.get("creado")),
                "Cliente": cli.get("nombre", "—"),
                "Total": f"${float(p.get('total_final', 0)):,.2f}",
                "Estado": p.get("estado", "abierto"),
                "Vendedor": p.get("vendedor", "—"),
            })
        st.dataframe(filas, use_container_width=True, hide_index=True)

        opciones = {p["id"]: p for p in lista}
        sel_id = st.selectbox(
            "Seleccionar presupuesto",
            options=list(opciones.keys()),
            format_func=lambda x: (
                f"{x[:8]}… · {(opciones[x].get('cliente') or {}).get('nombre', '')} · "
                f"${float(opciones[x].get('total_final', 0)):,.0f} · {opciones[x].get('estado', '')}"
            ),
            key="pres_sel_detalle",
        )
        pres = opciones.get(sel_id) or {}
        if pres.get("nota"):
            st.caption(f"Nota: {pres['nota']}")

        col_r, col_pdf, col_anu, col_del = st.columns(4)
        if col_r.button("↩️ Reabrir en carrito", use_container_width=True, key="pres_reabrir"):
            ok, msj, cliente = reabrir_presupuesto_en_carrito(str(vendedor), sel_id, reemplazar=True)
            if ok:
                st.session_state.cliente_activo = normalizar_cliente_activo(cliente)
                st.session_state.presupuesto_cargado_id = sel_id
                if "advertencias" in msj.lower() or "stock" in msj.lower():
                    st.warning(msj)
                else:
                    st.success(msj)
                st.rerun()
            else:
                st.error(msj)

        items_pres = pres.get("items") or []
        cli_pres = pres.get("cliente") or {}
        desc_pres = float(cli_pres.get("descuento", 0))
        total_bruto_pres = float(pres.get("total_bruto", 0))
        pdf_pres = generar_pdf_presupuesto(
            pres.get("vendedor", vendedor),
            items_pres,
            total_bruto_pres,
            cli_pres.get("nombre", "Particular"),
            desc_pres,
        )
        col_pdf.download_button(
            "📄 PDF",
            pdf_pres,
            f"Presupuesto_{sel_id[:8]}.pdf",
            "application/pdf",
            use_container_width=True,
            key="pres_dl_pdf",
        )

        if col_anu.button("Anular", use_container_width=True, key="pres_anular"):
            ok, msj = actualizar_estado_presupuesto(sel_id, "anulado")
            if ok:
                if st.session_state.get("presupuesto_cargado_id") == sel_id:
                    st.session_state.presupuesto_cargado_id = None
                st.success(msj)
                st.rerun()
            else:
                st.error(msj)

        if col_del.button("🗑️ Eliminar", use_container_width=True, key="pres_eliminar"):
            ok, msj = eliminar_presupuesto_guardado(sel_id)
            if ok:
                if st.session_state.get("presupuesto_cargado_id") == sel_id:
                    st.session_state.presupuesto_cargado_id = None
                st.success(msj)
                st.rerun()
            else:
                st.error(msj)


def render_seccion_cliente_mostrador():
    st.session_state.cliente_activo = normalizar_cliente_activo(
        st.session_state.get("cliente_activo")
    )
    cli = st.session_state.cliente_activo
    clientes_db = obtener_clientes() or {}

    col_info, col_cf, col_lim = st.columns([4, 1, 1])
    with col_info:
        st.markdown(f"**Cliente:** {cli['nombre']}")
        st.caption(
            f"CUIT/DNI: {cli['cuit']} · {_tipo_comprobante_label(cli['tipo_comprobante'])}"
            + (f" · Descuento: {cli['descuento']}%" if cli["descuento"] > 0 else "")
        )
    with col_cf:
        if st.button("Consumidor final", use_container_width=True):
            st.session_state.cliente_activo = cliente_consumidor_final()
            st.rerun()
    with col_lim:
        if st.button("Limpiar cliente", use_container_width=True):
            st.session_state.cliente_activo = cliente_consumidor_final()
            st.rerun()

    with st.expander("Buscar o cargar cliente", expanded=False):
        if clientes_db:
            opciones = [""] + list(clientes_db.keys())
            sel_id = st.selectbox(
                "Cliente registrado",
                options=opciones,
                format_func=lambda x: (
                    f"{(clientes_db.get(x) or {}).get('nombre', '')} ({x})"
                    if x else "— Elegir —"
                ),
                key="mostrador_sel_cliente",
            )
            if st.button("Usar cliente seleccionado", key="mostrador_usar_cliente"):
                if sel_id:
                    st.session_state.cliente_activo = cliente_db_a_activo(clientes_db.get(sel_id, {}))
                    st.rerun()
                else:
                    st.warning("Seleccioná un cliente de la lista.")

        with st.form("mostrador_alta_cliente_rapida"):
            c1, c2, c3, c4 = st.columns([3, 2, 1, 1])
            nombre_nuevo = c1.text_input("Nombre / Razón Social")
            cuit_nuevo = c2.text_input("DNI o CUIT")
            desc_nuevo = c3.number_input("% Desc.", min_value=0.0, step=1.0, value=0.0)
            tipo_nuevo = c4.selectbox(
                "Comprobante",
                options=["6", "1"],
                format_func=lambda x: _tipo_comprobante_label(x),
            )
            if st.form_submit_button("Guardar y usar"):
                if nombre_nuevo and cuit_nuevo:
                    ok, msj = configurar_cliente(
                        nombre_nuevo.upper(), cuit_nuevo, desc_nuevo, tipo_nuevo
                    )
                    if ok:
                        id_cli = "".join(filter(str.isdigit, str(cuit_nuevo)))
                        st.session_state.cliente_activo = {
                            "nombre": nombre_nuevo.upper(),
                            "cuit": id_cli,
                            "descuento": float(desc_nuevo),
                            "tipo_comprobante": tipo_nuevo,
                        }
                        st.success(msj)
                        st.rerun()
                    else:
                        st.error(msj)
                else:
                    st.error("Nombre y CUIT/DNI son obligatorios.")


def render_panel_coincidencias_mostrador(vendedor, agrupar_por_maestro, agregar_al_carrito):
    """Lista compacta de variantes encontradas (IA o búsqueda)."""
    resultados = st.session_state.get("resultados_ia_mostrador")
    if not resultados:
        return

    col_msg, col_x = st.columns([11, 1])
    with col_msg:
        st.caption(st.session_state.get("msg_ia_mostrador", "Coincidencias"))
    with col_x:
        if st.button("✕", key="cerrar_coinc_most", help="Cerrar coincidencias"):
            st.session_state.resultados_ia_mostrador = None
            st.session_state.msg_ia_mostrador = None
            st.rerun()

    grupos_most = agrupar_por_maestro(resultados)
    for gkey in sorted(grupos_most.keys(), key=lambda k: grupos_most[k]["descripcion"]):
        g = grupos_most[gkey]
        titulo = f"{g['descripcion'][:45]} · {g['codigo']}"
        if g.get("vehiculo"):
            titulo += f" · {str(g['vehiculo'])[:20]}"
        st.markdown(f"<p style='margin:0.2rem 0;font-size:0.85rem;font-weight:600'>{titulo}</p>", unsafe_allow_html=True)
        for res in g["variantes"]:
            marca_res = res.get("marca", res.get("condicion", ""))
            precio_f = float(res.get("precio_venta", 0))
            stock = res.get("stock", 0)
            rid = res.get("id", "N")
            c_txt, c_btn = st.columns([6, 1])
            with c_txt:
                st.markdown(
                    f"<span style='font-size:0.8rem;color:#555'>"
                    f"{marca_res} · {stock} u. · ${precio_f:,.0f}</span>",
                    unsafe_allow_html=True,
                )
            with c_btn:
                if st.button("➕", key=f"btn_add_most_{rid}", help="Agregar al carrito"):
                    exito, msj_db = agregar_al_carrito(str(vendedor), rid, 1)
                    if exito:
                        st.session_state.resultados_ia_mostrador = None
                        st.session_state.msg_ia_mostrador = None
                        st.rerun()
                    else:
                        st.error(msj_db)


def render_buscador_productos(vendedor, inv_completo, agregar_al_carrito, filtrar_inventario):
    busqueda = st.text_input(
        "Buscar por código, descripción, vehículo o marca",
        key=f"busq_most_{vendedor}",
        placeholder="Escribí al menos 2 caracteres…",
    )
    if not busqueda or len(busqueda.strip()) < 2:
        st.info("Escribí en el buscador para ver productos (no se lista todo el inventario).")
        return

    encontrados = filtrar_inventario(inv_completo, busqueda.strip())[:40]
    if not encontrados:
        st.warning("Sin coincidencias.")
        return

    opciones_desc = {}
    for item in encontrados:
        if isinstance(item, dict):
            marca_item = item.get("marca", item.get("condicion", ""))
            desc = (
                f"{item.get('codigo', '')} | {item.get('vehiculo', '')} - "
                f"{marca_item} | {item.get('descripcion', '')} - "
                f"${item.get('precio_venta', 0)} (stock {item.get('stock', 0)})"
            )
            opciones_desc[desc] = item.get("id")

    sel_prod = st.selectbox("Resultados:", options=[""] + list(opciones_desc.keys()))
    col_b1, col_b2 = st.columns([1, 3])
    cant_b = col_b1.number_input("Cantidad", min_value=1, step=1, key=f"cant_b_{vendedor}")

    if col_b2.button("➕ Agregar al Presupuesto", use_container_width=True, type="primary"):
        if sel_prod:
            id_real = opciones_desc[sel_prod]
            exito, msj = agregar_al_carrito(str(vendedor), id_real, int(cant_b))
            if exito:
                st.success(msj)
                st.rerun()
            else:
                st.error(msj)
        else:
            st.warning("Seleccioná un producto de la lista.")


def carrito_a_items_factura(carrito, descuento_pct):
    factor = 1.0 - float(descuento_pct) / 100.0
    items = []
    for item in carrito:
        if not isinstance(item, dict):
            continue
        cant = int(item.get("cantidad", 1))
        sub = float(item.get("subtotal", 0)) * factor
        items.append({
            "descripcion": str(item.get("descripcion", "Artículo"))[:120],
            "cantidad": cant,
            "precio": round(sub, 2),
        })
    return items


def _mostrar_boton_imprimir_pdf(pdf_bytes):
    base64_pdf = base64.b64encode(pdf_bytes).decode("utf-8")
    components.html(
        f"""
        <button onclick="imprimir()" style="
            background-color: #ff4b4b; color: white; padding: 10px;
            border-radius: 5px; width: 100%; border: none; cursor: pointer;
            font-weight: bold; font-family: sans-serif;
        ">🖨️ IMPRIMIR</button>
        <script>
        function imprimir() {{
            const b64 = "{base64_pdf}";
            const byteCharacters = atob(b64);
            const byteNumbers = new Array(byteCharacters.length);
            for (let i = 0; i < byteCharacters.length; i++) {{
                byteNumbers[i] = byteCharacters.charCodeAt(i);
            }}
            const blob = new Blob([new Uint8Array(byteNumbers)], {{type: 'application/pdf'}});
            const url = URL.createObjectURL(blob);
            const win = window.open(url, '_blank');
            if (win) {{ win.focus(); setTimeout(() => win.print(), 500); }}
        }}
        </script>
        """,
        height=60,
    )


def render_factura_arca_exitosa():
    rec = st.session_state.get("factura_arca_reciente")
    if not rec:
        return False

    st.success("✅ Factura ARCA emitida y stock descontado.")
    datos = rec.get("respuesta", {})
    nro = (
        f"{int(float(datos.get('punto_venta', 0))):04d}-"
        f"{int(float(datos.get('numero_factura', 0))):08d}"
    )
    st.caption(f"Comprobante {nro} · CAE {datos.get('cae', '')}")

    col_t, col_a = st.columns(2)
    with col_t:
        st.markdown("**Ticket (58mm)**")
        if rec.get("pdf_ticket"):
            _mostrar_boton_imprimir_pdf(rec["pdf_ticket"])
            st.download_button(
                "Descargar ticket",
                rec["pdf_ticket"],
                file_name=f"Ticket_{nro}.pdf",
                mime="application/pdf",
                use_container_width=True,
            )
    with col_a:
        st.markdown("**Formato A4**")
        if rec.get("pdf_a4"):
            _mostrar_boton_imprimir_pdf(rec["pdf_a4"])
            st.download_button(
                "Descargar A4",
                rec["pdf_a4"],
                file_name=f"Factura_{nro}.pdf",
                mime="application/pdf",
                use_container_width=True,
            )

    if st.button("Cerrar comprobante", key="cerrar_factura_arca"):
        st.session_state.factura_arca_reciente = None
        st.rerun()
    return True


def _forma_pago_actual(vendedor):
    key = f"mostrador_forma_pago_{vendedor}"
    if key not in st.session_state:
        st.session_state[key] = "Contado"
    return st.session_state[key]


def _set_forma_pago(vendedor, forma):
    fp = normalizar_forma_pago(forma)
    st.session_state[f"mostrador_forma_pago_{vendedor}"] = fp
    return fp


def ejecutar_emitir_factura_arca(vendedor, carrito, total_final, desc_porc, forma_pago):
    cuit_fact, clave_fact, config_ticket = _leer_secrets_facturador()
    if not cuit_fact or not clave_fact:
        return False, "Completá CUIT emisor y clave secreta en «Facturación ARCA» (arriba en Mostrador)."

    ok_val, msg_val, _ = validar_carrito_para_venta(str(vendedor))
    if not ok_val:
        return False, msg_val

    cli = normalizar_cliente_activo(st.session_state.cliente_activo)
    datos_cliente = {
        "cuit": cli["cuit"],
        "nombre": cli["nombre"],
        "cbte_tipo": cli["tipo_comprobante"],
    }
    items_fc = carrito_a_items_factura(carrito, desc_porc)
    if not items_fc or sum(i["precio"] for i in items_fc) <= 0:
        return False, "El total a facturar debe ser mayor a cero."

    resultado = generar_factura(cuit_fact, clave_fact, datos_cliente, items_fc, forma_pago)
    if not resultado.get("success"):
        return False, f"Error ARCA: {resultado.get('error', 'Desconocido')}"

    datos_resp = resultado["data"]
    cfg = dict(config_ticket)
    if cuit_fact:
        cfg["cuit_emisor"] = cfg.get("cuit_emisor") or cuit_fact
    pdf_ticket = crear_ticket(datos_resp, datos_cliente, items_fc, cfg)
    pdf_a4 = crear_a4(datos_resp, datos_cliente, items_fc, cfg)

    exito_stock, msj_stock = confirmar_venta(str(vendedor))
    if not exito_stock:
        return False, (
            f"CAE obtenido pero falló el descuento de stock: {msj_stock}. "
            "Revisá inventario manualmente."
        )

    guardar_comprobante_arca(
        vendedor, datos_cliente, datos_resp, items_fc, forma_pago, total_final
    )
    _cerrar_presupuesto_cargado("facturado")
    st.session_state.factura_arca_reciente = {
        "respuesta": datos_resp,
        "pdf_ticket": pdf_ticket,
        "pdf_a4": pdf_a4,
    }
    return True, "Factura ARCA emitida y stock descontado."


def _ejecutar_accion_pendiente(vendedor, pendiente, carrito, total_final, desc_porc):
    tipo = pendiente.get("tipo")
    forma_pago = pendiente.get("forma_pago") or _forma_pago_actual(vendedor)

    if tipo == "confirmar_venta":
        exito, msj = confirmar_venta(str(vendedor))
        if exito:
            _cerrar_presupuesto_cargado("vendido")
        return exito, msj

    if tipo == "facturar":
        with st.spinner("Solicitando CAE a ARCA/AFIP…"):
            return ejecutar_emitir_factura_arca(
                vendedor, carrito, total_final, desc_porc, forma_pago
            )

    if tipo == "guardar_presupuesto":
        ok, msj, nuevo_id = guardar_presupuesto(
            str(vendedor), st.session_state.cliente_activo, pendiente.get("nota", "")
        )
        if ok:
            st.session_state.presupuesto_cargado_id = nuevo_id
        return ok, msj

    if tipo == "vaciar_carrito":
        vaciar_carrito(str(vendedor))
        return True, "Carrito vaciado."

    return False, "Acción pendiente desconocida."


def _limpiar_accion_pendiente():
    st.session_state.mostrador_accion_pendiente = None


def render_confirmacion_pendiente_mostrador(vendedor, carrito, total_final, desc_porc):
    pend = st.session_state.get("mostrador_accion_pendiente")
    if not pend:
        return

    st.warning(pend.get("mensaje", "¿Confirmás esta acción?"))
    col_ok, col_no = st.columns(2)
    if col_ok.button("✅ Confirmar", type="primary", use_container_width=True, key="most_pend_ok"):
        ok, msj = _ejecutar_accion_pendiente(vendedor, pend, carrito, total_final, desc_porc)
        _limpiar_accion_pendiente()
        if ok:
            st.success(msj)
            st.rerun()
        else:
            st.error(msj)
    if col_no.button("❌ Cancelar", use_container_width=True, key="most_pend_no"):
        _limpiar_accion_pendiente()
        st.info("Acción cancelada.")
        st.rerun()


def render_ia_mostrador(
    vendedor,
    obtener_inventario_completo,
    buscar_en_inventario,
    agrupar_por_maestro,
    agregar_al_carrito,
):
    st.caption(
        "Ejemplos: *agregá 2 bujes gol*, *cliente García*, *consumidor final*, "
        "*forma de pago transferencia*, *guardá el presupuesto*, *facturá* (pide confirmación)."
    )

    with st.form("form_ia_mostrador", clear_on_submit=True):
        col_ia1, col_ia2 = st.columns([4, 1])
        orden = col_ia1.text_input("Dicte o escriba su orden:", key=f"ia_most_{vendedor}")
        submit_ia = col_ia2.form_submit_button("🤖 Ejecutar", use_container_width=True)

        if submit_ia and orden:
            with st.spinner("Hafid IA procesando..."):
                resp = procesar_orden_mostrador(orden) or {}
                accion = resp.get("accion")
                inventario = obtener_inventario_completo() or []
                carrito = obtener_carrito(str(vendedor)) or []
                total_bruto = sum(item.get("subtotal", 0) for item in carrito if isinstance(item, dict))
                desc_porc = float(st.session_state.cliente_activo.get("descuento", 0))
                total_final = total_bruto * (1 - desc_porc / 100)

                if accion == "confirmar_pendiente":
                    pend = st.session_state.get("mostrador_accion_pendiente")
                    if pend:
                        ok, msj = _ejecutar_accion_pendiente(
                            vendedor, pend, carrito, total_final, desc_porc
                        )
                        _limpiar_accion_pendiente()
                        if ok:
                            st.success(msj)
                            st.session_state.resultados_ia_mostrador = None
                            st.rerun()
                        else:
                            st.error(msj)
                    else:
                        st.info("No hay ninguna acción pendiente de confirmación.")

                elif accion == "cancelar_pendiente":
                    _limpiar_accion_pendiente()
                    st.info("Acción cancelada.")
                    st.session_state.resultados_ia_mostrador = None
                    st.rerun()

                elif accion == "agregar_carrito":
                    termino = str(resp.get("termino", ""))
                    cant_raw = resp.get("cantidad")
                    cant = int(cant_raw) if cant_raw is not None and str(cant_raw).isdigit() else 1
                    encontrados = buscar_en_inventario(inventario, termino)

                    if len(encontrados) == 1:
                        exito, msj_db = agregar_al_carrito(str(vendedor), encontrados[0]["id"], cant)
                        if exito:
                            st.success(f"🛒 {msj_db}")
                            st.session_state.resultados_ia_mostrador = None
                            st.rerun()
                        else:
                            st.error(f"❌ {msj_db}")
                    elif len(encontrados) > 1:
                        st.warning(f"Encontré {len(encontrados)} alternativas para '{termino}'.")
                        st.session_state.resultados_ia_mostrador = encontrados
                        st.session_state.msg_ia_mostrador = (
                            f"Elegí qué variante de '{termino}' querés agregar:"
                        )
                    else:
                        st.error(f"❌ No encontré ningún producto asociado a '{termino}'.")

                elif accion == "set_cliente":
                    nombre_det = str(resp.get("nombre_cliente", "")).upper()
                    clientes_db = obtener_clientes() or {}
                    cliente_encontrado = next(
                        (c for c in clientes_db.values()
                         if nombre_det in str(c.get("nombre", "")).upper()),
                        None,
                    )
                    if cliente_encontrado:
                        st.session_state.cliente_activo = cliente_db_a_activo(cliente_encontrado)
                        st.success(f"✅ Cliente {cliente_encontrado['nombre']} activado.")
                        st.session_state.resultados_ia_mostrador = None
                        st.rerun()
                    else:
                        st.warning(f"⚠️ '{nombre_det}' no está en la base de datos.")

                elif accion == "consumidor_final":
                    st.session_state.cliente_activo = cliente_consumidor_final()
                    st.success("✅ Consumidor final activado.")
                    st.session_state.resultados_ia_mostrador = None
                    st.rerun()

                elif accion == "set_forma_pago":
                    fp = _set_forma_pago(vendedor, resp.get("forma_pago", "Contado"))
                    st.success(f"✅ Forma de pago: {fp}")
                    st.session_state.resultados_ia_mostrador = None
                    st.rerun()

                elif accion == "guardar_presupuesto":
                    if not carrito:
                        st.error("El carrito está vacío.")
                    else:
                        nota = str(resp.get("nota", "") or "")
                        st.session_state.mostrador_accion_pendiente = {
                            "tipo": "guardar_presupuesto",
                            "nota": nota,
                            "mensaje": f"¿Guardar presupuesto de ${total_final:,.2f} para "
                            f"{st.session_state.cliente_activo.get('nombre', 'CONSUMIDOR FINAL')}?",
                        }
                        st.rerun()

                elif accion == "confirmar_venta":
                    if not carrito:
                        st.error("El carrito está vacío.")
                    else:
                        ok_val, msg_val, _ = validar_carrito_para_venta(str(vendedor))
                        if not ok_val:
                            st.error(msg_val)
                        else:
                            st.session_state.mostrador_accion_pendiente = {
                                "tipo": "confirmar_venta",
                                "mensaje": (
                                    f"¿Confirmar venta por ${total_final:,.2f} "
                                    f"(sin factura fiscal) y descontar stock?"
                                ),
                            }
                            st.rerun()

                elif accion == "facturar":
                    if not carrito:
                        st.error("El carrito está vacío.")
                    else:
                        cuit_fact, clave_fact, _ = _leer_secrets_facturador()
                        if not cuit_fact or not clave_fact:
                            st.error("Completá CUIT emisor y clave en «Facturación ARCA» arriba.")
                        else:
                            ok_val, msg_val, _ = validar_carrito_para_venta(str(vendedor))
                            if not ok_val:
                                st.error(msg_val)
                            else:
                                fp = _forma_pago_actual(vendedor)
                                st.session_state.mostrador_accion_pendiente = {
                                    "tipo": "facturar",
                                    "forma_pago": fp,
                                    "mensaje": (
                                        f"¿Emitir factura ARCA ({_tipo_comprobante_label(st.session_state.cliente_activo.get('tipo_comprobante', '6'))}) "
                                        f"por ${total_final:,.2f} — pago {fp}?"
                                    ),
                                }
                                st.rerun()

                elif accion == "vaciar_carrito":
                    if not carrito:
                        st.info("El carrito ya está vacío.")
                    else:
                        st.session_state.mostrador_accion_pendiente = {
                            "tipo": "vaciar_carrito",
                            "mensaje": "¿Vaciar el carrito actual?",
                        }
                        st.rerun()

                elif accion == "buscar" or accion == "consulta":
                    termino = str(resp.get("termino", "") or orden)
                    if termino:
                        encontrados = buscar_en_inventario(inventario, termino)
                        if encontrados:
                            st.session_state.resultados_ia_mostrador = encontrados[:10]
                            st.session_state.msg_ia_mostrador = (
                                f"🔍 Encontré estas opciones para '{termino}':"
                            )
                        else:
                            st.warning(f"No encontré coincidencias para '{termino}'.")
                            st.session_state.resultados_ia_mostrador = None
                    else:
                        st.warning("No detecté qué producto querés buscar.")
                        st.session_state.resultados_ia_mostrador = None

                elif accion == "error":
                    st.error(resp.get("respuesta", "Error de IA."))
                    st.session_state.resultados_ia_mostrador = None

                else:
                    msg = resp.get("respuesta") or "Orden no reconocida para el mostrador."
                    st.info(msg)
                    st.session_state.resultados_ia_mostrador = None

    render_panel_coincidencias_mostrador(vendedor, agrupar_por_maestro, agregar_al_carrito)


def render_acciones_carrito(vendedor, carrito, total_bruto, total_final, desc_porc, generar_pdf_presupuesto):
    if render_factura_arca_exitosa():
        st.divider()

    st.table(carrito)
    st.write(f"### Subtotal: ${total_bruto:,.2f}")
    if desc_porc > 0:
        st.write(f"### Descuento ({desc_porc}%): -${(total_bruto * desc_porc / 100):,.2f}")
    st.write(f"## TOTAL: ${total_final:,.2f}")

    pres_id = st.session_state.get("presupuesto_cargado_id")
    if pres_id:
        st.caption(f"Presupuesto cargado: `{pres_id[:8]}…`")

    nota_pres = st.text_input("Nota interna (opcional, al guardar)", key=f"nota_pres_{vendedor}")
    if st.button("💾 Guardar presupuesto", key=f"guardar_pres_{vendedor}"):
        ok, msj, nuevo_id = guardar_presupuesto(
            str(vendedor), st.session_state.cliente_activo, nota_pres
        )
        if ok:
            st.session_state.presupuesto_cargado_id = nuevo_id
            st.success(msj)
        else:
            st.error(msj)

    forma_pago = st.selectbox(
        "Forma de pago (factura ARCA)",
        list(FORMAS_PAGO),
        index=list(FORMAS_PAGO).index(_forma_pago_actual(vendedor)),
        key=f"pago_arca_{vendedor}",
    )
    _set_forma_pago(vendedor, forma_pago)

    col_cob, col_fc, col_pdf, col_vac = st.columns(4)

    if col_cob.button("✅ Confirmar venta", type="primary", use_container_width=True):
        exito, msj = confirmar_venta(str(vendedor))
        if exito:
            _cerrar_presupuesto_cargado("vendido")
            st.success(msj)
            st.rerun()
        else:
            st.error(msj)

    cuit_fact, clave_fact, _ = _leer_secrets_facturador()
    puede_facturar = bool(cuit_fact and clave_fact)

    if col_fc.button("🧾 Emitir factura ARCA", use_container_width=True, disabled=not puede_facturar):
        if not puede_facturar:
            st.error("Completá CUIT emisor y clave en «Facturación ARCA» arriba.")
        else:
            with st.spinner("Solicitando CAE a ARCA/AFIP…"):
                ok, msj = ejecutar_emitir_factura_arca(
                    vendedor, carrito, total_final, desc_porc, forma_pago
                )
            if ok:
                st.rerun()
            else:
                st.error(msj)

    if not puede_facturar:
        col_fc.caption("Completá CUIT y clave en «Facturación ARCA» arriba.")

    pdf_bytes = generar_pdf_presupuesto(
        str(vendedor), carrito, total_bruto,
        st.session_state.cliente_activo["nombre"], desc_porc,
    )
    col_pdf.download_button(
        "📄 Presupuesto PDF",
        pdf_bytes,
        f"Presupuesto_{vendedor}.pdf",
        "application/pdf",
        use_container_width=True,
    )

    if col_vac.button("🗑️ Vaciar", use_container_width=True):
        vaciar_carrito(str(vendedor))
        st.rerun()


def sincronizar_config_ticket_desde_nube():
    """Opcional: cargar config del ticket desde el backend (sidebar)."""
    cuit, clave, config_local = _leer_secrets_facturador()
    if not cuit or not clave:
        return config_local
    res = cargar_datos_nube(cuit, clave)
    if res.get("success"):
        data = res.get("data") or {}
        cfg = data.get("configuracion")
        if isinstance(cfg, dict):
            config_local.update(cfg)
    return config_local
