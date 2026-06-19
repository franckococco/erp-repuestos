"""Pantalla de configuración: proveedores, márgenes y recálculo de precios."""
import streamlit as st
import pandas as pd

from modulos.db_firebase import (
    obtener_proveedores,
    configurar_proveedor,
    eliminar_proveedor,
    recalcular_precios_proveedor,
    recalcular_precios_todos,
    IVA_PCT_DEFAULT,
    RENTABILIDAD_PCT_DEFAULT,
)
from modulos.precios_proveedor import calcular_cascada_desde_proveedor


def _fila_cambio(fila, orig):
    return (
        float(fila.get("Descuento (%)", 0)) != float(orig.get("Descuento (%)", 0))
        or float(fila.get("IVA (%)", 0)) != float(orig.get("IVA (%)", 0))
        or float(fila.get("Rentabilidad (%)", 0)) != float(orig.get("Rentabilidad (%)", 0))
        or float(fila.get("Recargo Contado (%)", 0)) != float(orig.get("Recargo Contado (%)", 0))
        or float(fila.get("Recargo 30 Días (%)", 0)) != float(orig.get("Recargo 30 Días (%)", 0))
    )


def render_config_proveedores(auditoria_fn=None):
    st.subheader("Proveedores y márgenes de precio")
    st.caption(
        "Cada proveedor define descuento, IVA, rentabilidad y recargos. "
        "La fórmula: lista → −descuento → +IVA → +recargo (según pago) → +rentabilidad → redondeo a $10."
    )

    provs = obtener_proveedores() or {}

    if provs:
        datos_tabla = []
        for cuit, datos_prov in provs.items():
            if not isinstance(datos_prov, dict):
                datos_prov = {}
            condiciones = datos_prov.get("condiciones", {})
            if not isinstance(condiciones, dict):
                condiciones = {}

            datos_tabla.append({
                "Proveedor": datos_prov.get("nombre", ""),
                "CUIT": cuit,
                "Descuento (%)": float(datos_prov.get("descuento", 0)),
                "IVA (%)": float(datos_prov.get("iva_pct", IVA_PCT_DEFAULT)),
                "Rentabilidad (%)": float(datos_prov.get("rentabilidad_pct", RENTABILIDAD_PCT_DEFAULT)),
                "Recargo Contado (%)": float(condiciones.get("Contado", 0)),
                "Recargo 30 Días (%)": float(condiciones.get("30 Días", 0)),
            })

        df_prov_edit = st.data_editor(
            pd.DataFrame(datos_tabla),
            use_container_width=True,
            hide_index=True,
            column_config={
                "Proveedor": st.column_config.TextColumn("Proveedor", disabled=True),
                "CUIT": st.column_config.TextColumn("CUIT", disabled=True),
                "Descuento (%)": st.column_config.NumberColumn("Descuento (%)", min_value=0.0, step=1.0),
                "IVA (%)": st.column_config.NumberColumn("IVA (%)", min_value=0.0, step=0.5),
                "Rentabilidad (%)": st.column_config.NumberColumn("Rentabilidad (%)", min_value=0.0, step=1.0),
                "Recargo Contado (%)": st.column_config.NumberColumn("Rec. Contado (%)", min_value=0.0, step=1.0),
                "Recargo 30 Días (%)": st.column_config.NumberColumn("Rec. 30 Días (%)", min_value=0.0, step=1.0),
            },
            key="grilla_proveedores",
        )

        if st.button("💾 Guardar cambios de proveedores", type="primary", use_container_width=True):
            guardados = 0
            filas = df_prov_edit.to_dict("records")
            originales = {r["CUIT"]: r for r in datos_tabla}

            for fila in filas:
                cuit = str(fila.get("CUIT", ""))
                orig = originales.get(cuit, {})
                if _fila_cambio(fila, orig):
                    configurar_proveedor(
                        fila.get("Proveedor", orig.get("Proveedor", "")),
                        cuit,
                        float(fila.get("Recargo Contado (%)", 0)),
                        float(fila.get("Recargo 30 Días (%)", 0)),
                        float(fila.get("Descuento (%)", 0)),
                        float(fila.get("IVA (%)", IVA_PCT_DEFAULT)),
                        float(fila.get("Rentabilidad (%)", RENTABILIDAD_PCT_DEFAULT)),
                    )
                    guardados += 1

            if guardados:
                if auditoria_fn:
                    auditoria_fn(
                        "config", "guardar_proveedores",
                        f"Actualizados {guardados} proveedor(es)", exito=True,
                    )
                st.success(f"✅ {guardados} proveedor(es) actualizado(s).")
                st.rerun()
            else:
                st.info("No hubo cambios para guardar.")

        with st.expander("Vista previa de fórmula (ejemplo $10.000 lista)", expanded=False):
            prov_ej = st.selectbox(
                "Proveedor ejemplo",
                options=list(provs.keys()),
                format_func=lambda x: f"{(provs.get(x) or {}).get('nombre', x)} ({x})",
                key="prev_formula_prov",
            )
            pago_ej = st.radio("Condición de pago", ["Contado", "30 Días"], horizontal=True, key="prev_formula_pago")
            calc = calcular_cascada_desde_proveedor(10000, provs.get(prov_ej), pago_ej)
            st.write(
                f"Costo neto ${calc['costo_neto']:,.2f} → con IVA ${calc['costo_iva']:,.2f} → "
                f"costo final ${calc['costo_final']:,.2f} → **venta ${calc['precio_venta']:,.0f}**"
            )

        st.divider()
        st.markdown("#### Recálculo masivo de precios")
        st.caption(
            "Usa el **último costo base** guardado en cada variante y los márgenes actuales del proveedor. "
            "No modifica stock."
        )
        col_pago, col_sp = st.columns([2, 3])
        with col_pago:
            cond_recalc = st.selectbox(
                "Recargo a aplicar en el recálculo",
                options=["Contado", "30 Días"],
                key="recalc_condicion_pago",
            )
        with col_sp:
            prov_recalc = st.selectbox(
                "Proveedor",
                options=["— Todos —"] + list(provs.keys()),
                format_func=lambda x: (
                    "Todo el inventario"
                    if x == "— Todos —"
                    else f"{(provs.get(x) or {}).get('nombre', x)} ({x})"
                ),
                key="recalc_sel_prov",
            )

        col_r1, col_r2 = st.columns(2)
        confirmar_todo = prov_recalc == "— Todos —"
        if confirmar_todo:
            col_r2.checkbox("Confirmo recalcular TODO el inventario", key="recalc_confirm_todo")

        if col_r1.button("🔄 Recalcular precios", type="primary", use_container_width=True):
            if confirmar_todo and not st.session_state.get("recalc_confirm_todo"):
                st.error("Marcá la confirmación para recalcular todo el inventario.")
            else:
                with st.spinner("Recalculando precios…"):
                    if confirmar_todo:
                        ok, msg = recalcular_precios_todos(cond_recalc)
                    else:
                        ok, msg = recalcular_precios_proveedor(str(prov_recalc), cond_recalc)
                if ok:
                    if auditoria_fn:
                        auditoria_fn(
                            "config", "recalcular_precios",
                            msg,
                            detalle={"proveedor": prov_recalc, "condicion_pago": cond_recalc},
                            exito=True,
                        )
                    st.success(msg)
                    st.rerun()
                else:
                    st.error(msg)

        with st.expander("🗑️ Eliminar un proveedor"):
            prov_a_borrar = st.selectbox(
                "Seleccionar proveedor a eliminar:",
                options=list(provs.keys()),
                format_func=lambda x: f"{(provs.get(x) or {}).get('nombre', 'Desconocido')} (CUIT: {x})",
                key="prov_borrar_sel",
            )
            if st.button("Eliminar proveedor", type="primary", key="prov_borrar_btn"):
                eliminar_proveedor(str(prov_a_borrar))
                if auditoria_fn:
                    auditoria_fn("config", "eliminar_proveedor", f"CUIT {prov_a_borrar}", exito=True)
                st.success("Proveedor eliminado del sistema.")
                st.rerun()
    else:
        st.info("Aún no hay proveedores cargados.")

    st.divider()
    st.subheader("➕ Alta de proveedor nuevo")
    with st.form("conf_prov"):
        col1, col2 = st.columns(2)
        nombre_prov = col1.text_input("Nombre proveedor").upper()
        cuit_prov = col2.text_input("CUIT (solo números)")

        st.write("Márgenes y recargos (%)")
        col3, col4, col5, col6, col7 = st.columns(5)
        desc_prov = col3.number_input("Descuento factura", min_value=0.0, value=0.0, step=1.0)
        iva_prov = col4.number_input("IVA", min_value=0.0, value=float(IVA_PCT_DEFAULT), step=0.5)
        rent_prov = col5.number_input("Rentabilidad", min_value=0.0, value=float(RENTABILIDAD_PCT_DEFAULT), step=1.0)
        rec_contado = col6.number_input("Recargo contado", min_value=0.0, value=0.0, step=1.0)
        rec_30 = col7.number_input("Recargo 30 días", min_value=0.0, value=15.0, step=1.0)

        if st.form_submit_button("Guardar proveedor nuevo"):
            if nombre_prov and cuit_prov:
                configurar_proveedor(
                    nombre_prov, cuit_prov, rec_contado, rec_30, desc_prov, iva_prov, rent_prov,
                )
                if auditoria_fn:
                    auditoria_fn("config", "alta_proveedor", nombre_prov, exito=True, ref_id=cuit_prov)
                st.success(f"Proveedor {nombre_prov} guardado.")
                st.rerun()
            else:
                st.error("El nombre y el CUIT son obligatorios.")
