from datetime import date, timedelta

import streamlit as st
import pandas as pd
from PIL import Image

from modulos.pedidos_db import (
    crear_pedido,
    listar_pedidos,
    agrupar_pedidos_por_proveedor,
    obtener_pedido_completo,
    obtener_items_pedido,
    cerrar_pedido,
    formatear_fecha_pedido,
)
from modulos.comparar_pedido import comparar_pedido_con_documento, resultado_a_tabla
from modulos.ia_vision import procesar_factura_con_ia, procesar_remito_con_ia
from modulos.db_firebase import obtener_proveedores
from modulos.ui_estilos import ayuda


def _etiqueta_estado(estado):
    est = str(estado or "abierto").lower()
    if est == "cerrado":
        return "🔒 Cerrado"
    if est == "parcial":
        return "🟡 Parcial"
    return "🟢 Abierto"


def _titulo_pedido(p):
    fecha_s = formatear_fecha_pedido(p.get("fecha_pedido"))
    n_items = int(p.get("item_count", 0))
    n_u = int(p.get("cantidad_total", 0))
    u_txt = f" · {n_u} u." if n_u else ""
    return (
        f"📅 {fecha_s} | {_etiqueta_estado(p.get('estado'))} | "
        f"{n_items} ítems{u_txt} | `{p.get('id', '')}`"
    )


def _items_a_dataframe(items):
    if not items:
        return pd.DataFrame()
    filas = []
    for it in items:
        filas.append({
            "Código": it.get("codigo_proveedor", ""),
            "Descripción": it.get("descripcion", ""),
            "Marca": it.get("marca", ""),
            "Cantidad": int(it.get("cantidad_pedida", 0)),
            "Precio est.": float(it.get("precio_estimado", 0) or 0),
        })
    return pd.DataFrame(filas)


def _render_detalle_pedido(p, prefijo_key):
    pedido_id = p["id"]
    items = obtener_items_pedido(pedido_id)
    df_items = _items_a_dataframe(items)

    if p.get("notas"):
        st.caption(f"Notas: {p['notas']}")

    if not df_items.empty:
        st.dataframe(
            df_items,
            hide_index=True,
            use_container_width=True,
            column_config={
                "Precio est.": st.column_config.NumberColumn(format="$ %.2f"),
            },
        )
        total_u = int(df_items["Cantidad"].sum())
        st.caption(f"**Total unidades:** {total_u}")
    else:
        st.warning("Este pedido no tiene ítems cargados.")

    if str(p.get("estado", "")).lower() != "cerrado":
        if st.button("Cerrar pedido", key=f"{prefijo_key}_cerrar_{pedido_id}"):
            ok, msj = cerrar_pedido(pedido_id)
            if ok:
                st.success(msj)
                st.rerun()
            else:
                st.error(msj)


def _render_historial_pedidos(provs):
    st.subheader("Historial de pedidos")

    col_f1, col_f2, col_f3, col_f4 = st.columns(4)
    opciones_prov = {"— Todos los proveedores —": ""}
    for cuit, datos in (provs or {}).items():
        if isinstance(datos, dict):
            opciones_prov[f"{datos.get('nombre', '—')} (CUIT {cuit})"] = cuit

    sel_prov = col_f1.selectbox(
        "Proveedor",
        options=list(opciones_prov.keys()),
        key="hist_filtro_prov",
    )
    filtro_estado = col_f2.selectbox(
        "Estado",
        options=["Todos", "Abiertos", "Cerrados"],
        key="hist_filtro_estado",
    )
    hoy = date.today()
    fecha_desde = col_f3.date_input(
        "Desde",
        value=hoy - timedelta(days=90),
        key="hist_fecha_desde",
    )
    fecha_hasta = col_f4.date_input(
        "Hasta",
        value=hoy,
        key="hist_fecha_hasta",
    )

    if fecha_desde > fecha_hasta:
        st.error("La fecha «Desde» no puede ser posterior a «Hasta».")
        return

    map_estado = {"Todos": None, "Abiertos": "abierto", "Cerrados": "cerrado"}
    pedidos = listar_pedidos(
        cuit=opciones_prov.get(sel_prov),
        estado=map_estado[filtro_estado],
        fecha_desde=fecha_desde,
        fecha_hasta=fecha_hasta,
    )

    if not pedidos:
        st.info("No hay pedidos para los filtros seleccionados.")
        return

    st.caption(f"**{len(pedidos)}** pedido(s) encontrados · {fecha_desde.strftime('%d/%m/%Y')} — {fecha_hasta.strftime('%d/%m/%Y')}")

    grupos = agrupar_pedidos_por_proveedor(pedidos)
    for _key, grupo in grupos.items():
        nombre = grupo["nombre_proveedor"]
        cuit = grupo["cuit_proveedor"]
        lista = grupo["pedidos"]
        st.markdown(f"### 🏭 {nombre}" + (f" · CUIT {cuit}" if cuit else ""))

        for p in lista:
            with st.expander(_titulo_pedido(p), expanded=False):
                _render_detalle_pedido(p, prefijo_key=f"hist_{p['id']}")

        st.divider()


def render_pedidos():
    ayuda(
        "Ayuda — Pedidos",
        "Registrá pedidos con **código del proveedor**. El **historial** queda agrupado por proveedor "
        "y podés filtrar por **fecha** y estado. Compará después contra **factura** o **remito**.",
    )

    tab_gestion, tab_vs_fact, tab_vs_rem = st.tabs([
        "📋 Gestionar pedidos",
        "📄 Pedido vs Factura",
        "📦 Pedido vs Remito",
    ])

    provs = obtener_proveedores() or {}

    with tab_gestion:
        st.subheader("Nuevo pedido")
        if not provs:
            st.warning("Registrá proveedores en Configuración antes de crear pedidos.")
        else:
            opciones_prov = {
                f"{d.get('nombre', '—')} (CUIT {c})": c
                for c, d in provs.items() if isinstance(d, dict)
            }
            sel = st.selectbox("Proveedor", options=list(opciones_prov.keys()), key="ped_prov_sel")
            cuit_sel = opciones_prov.get(sel, "")
            notas = st.text_input("Notas (opcional)", key="ped_notas")

            st.caption("Grilla de ítems — usá el **código del proveedor**.")
            df_nuevo = st.data_editor(
                pd.DataFrame([{
                    "codigo": "",
                    "descripcion": "",
                    "marca": "GENERICO",
                    "cantidad": 1,
                    "precio_estimado": 0.0,
                }]),
                num_rows="dynamic",
                use_container_width=True,
                column_config={
                    "codigo": st.column_config.TextColumn("Cód. proveedor", required=True),
                    "descripcion": st.column_config.TextColumn("Descripción"),
                    "marca": st.column_config.TextColumn("Marca"),
                    "cantidad": st.column_config.NumberColumn("Cantidad", min_value=1, step=1),
                    "precio_estimado": st.column_config.NumberColumn("Precio est.", min_value=0.0, format="$ %.2f"),
                },
                key="grilla_nuevo_pedido",
            )

            if st.button("💾 Guardar pedido", type="primary", key="btn_guardar_pedido"):
                items = df_nuevo.to_dict("records") if not df_nuevo.empty else []
                nombre = (provs.get(cuit_sel) or {}).get("nombre", sel)
                exito, msg, _ = crear_pedido(cuit_sel, nombre, items, notas)
                if exito:
                    st.success(msg)
                    st.rerun()
                else:
                    st.error(msg)

        st.divider()
        _render_historial_pedidos(provs)

    pedidos_all = listar_pedidos(estado=None)
    if not pedidos_all:
        with tab_vs_fact:
            st.info("Creá un pedido primero.")
        with tab_vs_rem:
            st.info("Creá un pedido primero.")
        return

    def _opciones_pedido_comparacion():
        """Selector agrupado por proveedor con fecha visible."""
        opciones = {}
        grupos = agrupar_pedidos_por_proveedor(pedidos_all)
        for _k, g in grupos.items():
            for p in g["pedidos"]:
                lbl = (
                    f"{g['nombre_proveedor']} | {formatear_fecha_pedido(p.get('fecha_pedido'))} | "
                    f"{p.get('item_count', 0)} ítems | {p.get('id', '')}"
                )
                opciones[lbl] = p["id"]
        return opciones

    opciones_ped = _opciones_pedido_comparacion()

    def _render_comparacion(tipo_doc, session_doc_key, session_res_key, leer_fn, titulo_doc):
        st.subheader(titulo_doc)

        col_pf, col_pd1, col_pd2 = st.columns([2, 1, 1])
        filtro_cmp_prov = col_pf.selectbox(
            "Filtrar proveedor",
            options=["— Todos —"] + sorted({lbl.split(" | ")[0] for lbl in opciones_ped.keys()}),
            key=f"cmp_prov_{tipo_doc}",
        )
        cmp_desde = col_pd1.date_input("Desde", value=date.today() - timedelta(days=90), key=f"cmp_desde_{tipo_doc}")
        cmp_hasta = col_pd2.date_input("Hasta", value=date.today(), key=f"cmp_hasta_{tipo_doc}")

        opts_filtradas = {}
        for lbl, pid in opciones_ped.items():
            prov_nombre = lbl.split(" | ")[0]
            if filtro_cmp_prov != "— Todos —" and prov_nombre != filtro_cmp_prov:
                continue
            ped = next((p for p in pedidos_all if p["id"] == pid), None)
            if ped:
                fp = ped.get("fecha_pedido")
                if fp:
                    try:
                        fd = fp.date() if hasattr(fp, "date") else fp
                        if fd < cmp_desde or fd > cmp_hasta:
                            continue
                    except Exception:
                        pass
            opts_filtradas[lbl] = pid

        if not opts_filtradas:
            st.info("No hay pedidos en ese rango de fechas / proveedor.")
            return

        pedido_sel = st.selectbox(
            "Pedido",
            options=list(opts_filtradas.keys()),
            key=f"sel_ped_{tipo_doc}",
        )
        pedido_id = opts_filtradas[pedido_sel]
        pedido = obtener_pedido_completo(pedido_id)

        if pedido and pedido.get("items"):
            with st.expander("Ver contenido del pedido seleccionado", expanded=False):
                st.dataframe(_items_a_dataframe(pedido["items"]), hide_index=True, use_container_width=True)

        if session_doc_key not in st.session_state:
            st.session_state[session_doc_key] = None
        if session_res_key not in st.session_state:
            st.session_state[session_res_key] = None

        col_cam, col_up = st.columns(2)
        foto = col_cam.camera_input(f"Foto {tipo_doc}", key=f"cam_ped_{tipo_doc}")
        arch = col_up.file_uploader(f"Imagen {tipo_doc}", type=["png", "jpg", "jpeg"], key=f"up_ped_{tipo_doc}")
        img = foto or arch
        if img and st.button(f"Leer {tipo_doc}", key=f"leer_{tipo_doc}"):
            with st.spinner(f"Leyendo {tipo_doc}..."):
                try:
                    datos = leer_fn(Image.open(img))
                    for art in datos.get("articulos", []):
                        if isinstance(art, dict):
                            art.setdefault("codigo_proveedor", art.get("codigo", ""))
                            art.setdefault("marca", "GENERICO")
                    st.session_state[session_doc_key] = datos
                    st.session_state[session_res_key] = None
                    st.success(f"{titulo_doc} leído: {len(datos.get('articulos', []))} ítems.")
                    st.rerun()
                except Exception as e:
                    st.error(str(e))

        doc = st.session_state.get(session_doc_key)
        if doc:
            st.caption(f"{len(doc.get('articulos', []))} ítems cargados en {tipo_doc}.")

        if pedido and doc and st.button(f"🔍 Comparar pedido vs {tipo_doc}", type="primary", key=f"cmp_{tipo_doc}"):
            resultado = comparar_pedido_con_documento(
                pedido.get("items", []),
                doc.get("articulos", []),
                tipo_documento=tipo_doc,
            )
            st.session_state[session_res_key] = resultado
            st.rerun()

        resultado = st.session_state.get(session_res_key)
        if resultado:
            res = resultado.get("resumen", {})
            c1, c2, c3, c4, c5 = st.columns(5)
            c1.metric("✅ OK", res.get("coinciden", 0))
            c2.metric("⚠️ Dif.", res.get("diferencias", 0))
            c3.metric("❌ Falta doc.", res.get("faltan_en_documento", 0))
            c4.metric("❌ Sobra doc.", res.get("sobran_en_documento", 0))
            c5.metric("Resultado", "OK" if res.get("ok") else "Revisar")

            tabla = resultado_a_tabla(resultado, tipo_documento=tipo_doc)
            if tabla:
                st.dataframe(pd.DataFrame(tabla), hide_index=True, use_container_width=True)

            if st.button("Limpiar", key=f"limpiar_{tipo_doc}"):
                st.session_state[session_res_key] = None
                st.rerun()

    with tab_vs_fact:
        _render_comparacion(
            "factura",
            "pedido_doc_factura",
            "pedido_res_factura",
            procesar_factura_con_ia,
            "Factura del proveedor",
        )

    with tab_vs_rem:
        _render_comparacion(
            "remito",
            "pedido_doc_remito",
            "pedido_res_remito",
            procesar_remito_con_ia,
            "Remito de entrega",
        )
