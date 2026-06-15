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


def _leer_secrets_facturador():
    cuit = ""
    clave = ""
    config_ticket = dict(CONFIG_TICKET_DEFAULT)
    try:
        cuit = st.secrets.get("FACTURADOR_CUIT", "") or ""
        clave = st.secrets.get("FACTURADOR_CLAVE_SECRETA", "") or ""
        bloque = st.secrets.get("facturador", {})
        if isinstance(bloque, dict):
            cuit = cuit or bloque.get("cuit", "")
            clave = clave or bloque.get("clave", "")
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
    return str(cuit).strip(), str(clave).strip(), config_ticket


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
        ["Contado", "Transferencia", "Tarjeta", "Cheque", "MercadoPago"],
        key=f"pago_arca_{vendedor}",
    )

    col_cob, col_fc, col_pdf, col_vac = st.columns(4)

    if col_cob.button("✅ Confirmar venta", type="primary", use_container_width=True):
        exito, msj = confirmar_venta(str(vendedor))
        if exito:
            _cerrar_presupuesto_cargado("vendido")
            st.success(msj)
            st.rerun()
        else:
            st.error(msj)

    cuit_fact, clave_fact, config_ticket = _leer_secrets_facturador()
    puede_facturar = bool(cuit_fact and clave_fact)

    if col_fc.button("🧾 Emitir factura ARCA", use_container_width=True, disabled=not puede_facturar):
        if not puede_facturar:
            st.error("Configurá FACTURADOR_CUIT y FACTURADOR_CLAVE_SECRETA en Streamlit Secrets.")
        else:
            ok_val, msg_val, _ = validar_carrito_para_venta(str(vendedor))
            if not ok_val:
                st.error(msg_val)
            else:
                cli = normalizar_cliente_activo(st.session_state.cliente_activo)
                datos_cliente = {
                    "cuit": cli["cuit"],
                    "nombre": cli["nombre"],
                    "cbte_tipo": cli["tipo_comprobante"],
                }
                items_fc = carrito_a_items_factura(carrito, desc_porc)
                if not items_fc or sum(i["precio"] for i in items_fc) <= 0:
                    st.error("El total a facturar debe ser mayor a cero.")
                else:
                    with st.spinner("Solicitando CAE a ARCA/AFIP…"):
                        resultado = generar_factura(
                            cuit_fact, clave_fact, datos_cliente, items_fc, forma_pago
                        )
                    if resultado.get("success"):
                        datos_resp = resultado["data"]
                        cfg = dict(config_ticket)
                        if cuit_fact:
                            cfg["cuit_emisor"] = cfg.get("cuit_emisor") or cuit_fact
                        pdf_ticket = crear_ticket(datos_resp, datos_cliente, items_fc, cfg)
                        pdf_a4 = crear_a4(datos_resp, datos_cliente, items_fc, cfg)

                        exito_stock, msj_stock = confirmar_venta(str(vendedor))
                        if not exito_stock:
                            st.error(
                                f"CAE obtenido pero falló el descuento de stock: {msj_stock}. "
                                "Revisá inventario manualmente."
                            )
                        else:
                            guardar_comprobante_arca(
                                vendedor, datos_cliente, datos_resp, items_fc, forma_pago, total_final
                            )
                            _cerrar_presupuesto_cargado("facturado")
                            st.session_state.factura_arca_reciente = {
                                "respuesta": datos_resp,
                                "pdf_ticket": pdf_ticket,
                                "pdf_a4": pdf_a4,
                            }
                            st.rerun()
                    else:
                        st.error(f"Error ARCA: {resultado.get('error', 'Desconocido')}")

    if not puede_facturar:
        col_fc.caption("Secrets: FACTURADOR_CUIT + FACTURADOR_CLAVE_SECRETA")

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
