import streamlit as st
import pandas as pd
from PIL import Image

from modulos.pedidos_db import (
    crear_pedido,
    listar_pedidos,
    obtener_pedido_completo,
    cerrar_pedido,
)
from modulos.comparar_pedido import comparar_pedido_con_documento, resultado_a_tabla
from modulos.ia_vision import procesar_factura_con_ia, procesar_remito_con_ia
from modulos.db_firebase import obtener_proveedores
from modulos.ui_estilos import ayuda


def _fmt_pedido(p):
    fecha = p.get("fecha_pedido")
    fecha_s = fecha.strftime("%d/%m/%Y %H:%M") if fecha else "—"
    return f"{p.get('id', '')} | {p.get('nombre_proveedor', '')} | {fecha_s} | {p.get('item_count', 0)} ítems"


def render_pedidos():
    ayuda(
        "Ayuda — Pedidos",
        "Registrá pedidos con **código del proveedor**. Después compará contra la **factura** "
        "(lo que te cobraron) o el **remito** (lo que llegó). El cruce es por código + marca.",
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
        st.subheader("Pedidos abiertos")
        pedidos = listar_pedidos(solo_abiertos=True)
        if pedidos:
            for p in pedidos:
                c1, c2 = st.columns([4, 1])
                c1.caption(_fmt_pedido(p))
                if c2.button("Cerrar", key=f"cerrar_{p['id']}"):
                    ok, msj = cerrar_pedido(p["id"])
                    st.success(msj) if ok else st.error(msj)
                    st.rerun()
        else:
            st.info("No hay pedidos abiertos.")

    pedidos_all = listar_pedidos(solo_abiertos=False)
    if not pedidos_all:
        with tab_vs_fact:
            st.info("Creá un pedido primero.")
        with tab_vs_rem:
            st.info("Creá un pedido primero.")
        return

    opciones_ped = {_fmt_pedido(p): p["id"] for p in pedidos_all}

    def _render_comparacion(tipo_doc, session_doc_key, session_res_key, leer_fn, titulo_doc):
        st.subheader(titulo_doc)
        pedido_sel = st.selectbox("Pedido", options=list(opciones_ped.keys()), key=f"sel_ped_{tipo_doc}")
        pedido_id = opciones_ped[pedido_sel]
        pedido = obtener_pedido_completo(pedido_id)

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
