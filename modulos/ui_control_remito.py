import streamlit as st
import pandas as pd
from PIL import Image

from modulos.ia_vision import procesar_factura_con_ia, procesar_remito_con_ia
from modulos.control_remito import (
    comparar_factura_remito,
    sugerir_emparejamientos_huerfanos,
    resultado_a_tabla,
)
from modulos.db_firebase import (
    buscar_equivalencia,
    normalizar_codigo_proveedor,
    guardar_control_remito,
)


from modulos.ui_estilos import ayuda


def render_control_factura_remito():
    ayuda(
        "Ayuda — Control factura vs remito",
        "Compará lo **facturado** contra lo que **llegó** (remito). "
        "Usa las equivalencias guardadas para matchear códigos distintos del mismo producto. "
        "Podés usar la factura ya escaneada en la pestaña anterior o subir otra.",
    )

    if "control_factura" not in st.session_state:
        st.session_state.control_factura = None
    if "control_remito" not in st.session_state:
        st.session_state.control_remito = None
    if "control_resultado" not in st.session_state:
        st.session_state.control_resultado = None

    col_f, col_r = st.columns(2)

    with col_f:
        st.subheader("Factura")
        if st.session_state.get("temp_datos"):
            if st.button("Usar factura de la pestaña Carga", use_container_width=True):
                st.session_state.control_factura = dict(st.session_state.temp_datos)
                st.session_state.control_resultado = None
                st.rerun()
        foto_f = st.camera_input("Foto factura", key="cam_ctrl_fact")
        arch_f = st.file_uploader("Factura (PDF o imagen)", type=["png", "jpg", "jpeg", "pdf"], key="up_ctrl_fact")
        img_f = foto_f or arch_f
        if img_f and st.button("Leer factura", key="btn_leer_fact_ctrl", use_container_width=True):
            with st.spinner("Leyendo factura..."):
                try:
                    from modulos.util_imagen import imagen_desde_upload
                    datos = procesar_factura_con_ia(imagen_desde_upload(img_f))
                    for art in datos.get("articulos", []):
                        if isinstance(art, dict):
                            art["codigo_proveedor"] = art.get("codigo", "")
                            art.setdefault("marca", "GENERICO")
                    st.session_state.control_factura = datos
                    st.session_state.control_resultado = None
                    st.success(f"Factura leída: {len(datos.get('articulos', []))} ítems.")
                    st.rerun()
                except Exception as e:
                    st.error(str(e))

        fac = st.session_state.control_factura
        if fac:
            cuit_f = "".join(filter(str.isdigit, str(fac.get("cuit_proveedor", ""))))
            st.caption(
                f"{fac.get('proveedor', '—')} | CUIT {cuit_f or '—'} | "
                f"Comp. {fac.get('punto_venta', '')}-{fac.get('numero_comprobante', '')} | "
                f"{len(fac.get('articulos', []))} ítems"
            )

    with col_r:
        st.subheader("Remito")
        foto_r = st.camera_input("Foto remito", key="cam_ctrl_rem")
        arch_r = st.file_uploader("Remito (PDF o imagen)", type=["png", "jpg", "jpeg", "pdf"], key="up_ctrl_rem")
        img_r = foto_r or arch_r
        if img_r and st.button("Leer remito", key="btn_leer_rem_ctrl", use_container_width=True):
            with st.spinner("Leyendo remito..."):
                try:
                    from modulos.util_imagen import imagen_desde_upload
                    datos = procesar_remito_con_ia(imagen_desde_upload(img_r))
                    for art in datos.get("articulos", []):
                        if isinstance(art, dict):
                            art["codigo_proveedor"] = art.get("codigo", "")
                            art.setdefault("marca", "GENERICO")
                    st.session_state.control_remito = datos
                    st.session_state.control_resultado = None
                    st.success(f"Remito leído: {len(datos.get('articulos', []))} ítems.")
                    st.rerun()
                except Exception as e:
                    st.error(str(e))

        rem = st.session_state.control_remito
        if rem:
            cuit_r = "".join(filter(str.isdigit, str(rem.get("cuit_proveedor", ""))))
            st.caption(
                f"{rem.get('proveedor', '—')} | CUIT {cuit_r or '—'} | "
                f"Remito Nº {rem.get('numero_remito', '—')} | "
                f"{len(rem.get('articulos', []))} ítems"
            )

    st.divider()

    fac = st.session_state.control_factura
    rem = st.session_state.control_remito

    if fac and rem:
        cuit_f = "".join(filter(str.isdigit, str(fac.get("cuit_proveedor", ""))))
        cuit_r = "".join(filter(str.isdigit, str(rem.get("cuit_proveedor", ""))))
        if cuit_f and cuit_r and cuit_f != cuit_r:
            st.warning(f"⚠️ CUIT distinto: factura {cuit_f} vs remito {cuit_r}. Revisá que sean del mismo proveedor.")

        if st.button("🔍 Comparar factura vs remito", type="primary", use_container_width=True):
            cuit = cuit_f or cuit_r
            with st.spinner("Comparando mercadería..."):
                resultado = comparar_factura_remito(
                    fac.get("articulos", []),
                    rem.get("articulos", []),
                    cuit,
                    buscar_equivalencia,
                    normalizar_codigo_proveedor,
                )
                if resultado["resumen"]["faltan_en_remito"] or resultado["resumen"]["sobran_en_remito"]:
                    resultado["sugerencias_pares"] = sugerir_emparejamientos_huerfanos(
                        resultado["faltan_en_remito"],
                        resultado["sobran_en_remito"],
                    )
                else:
                    resultado["sugerencias_pares"] = []
                st.session_state.control_resultado = resultado
                st.rerun()

    resultado = st.session_state.control_resultado
    if resultado:
        res = resultado.get("resumen", {})
        c1, c2, c3, c4, c5 = st.columns(5)
        c1.metric("✅ Coinciden", res.get("coinciden", 0))
        c2.metric("⚠️ Dif. cantidad", res.get("diferencias", 0))
        c3.metric("❌ Falta remito", res.get("faltan_en_remito", 0))
        c4.metric("❌ Sobra remito", res.get("sobran_en_remito", 0))
        if res.get("ok"):
            c5.metric("Resultado", "OK")
        else:
            c5.metric("Resultado", "Revisar")

        if res.get("ok"):
            st.success("✅ Todo coincide: lo facturado es igual a lo que vino en el remito.")
        else:
            st.warning("Hay diferencias entre factura y remito. Revisá el detalle.")

        tabla = resultado_a_tabla(resultado)
        if tabla:
            st.dataframe(pd.DataFrame(tabla), hide_index=True, use_container_width=True)

        sugerencias = resultado.get("sugerencias_pares") or []
        if sugerencias:
            st.subheader("Sugerencias IA (posibles mismos productos con distinto código)")
            for i, par in enumerate(sugerencias):
                f, r = par.get("factura", {}), par.get("remito", {})
                st.info(
                    f"**{par.get('score', 0)}%** — Factura: {f.get('descripcion')} ({f.get('cant_factura')} u.) "
                    f"↔ Remito: {r.get('descripcion')} ({r.get('cant_remito')} u.) · {par.get('motivo', '')}"
                )

        if st.button("💾 Guardar control en historial", use_container_width=True):
            cuit = "".join(filter(str.isdigit, str((fac or {}).get("cuit_proveedor", ""))))
            prov = (fac or {}).get("proveedor") or (rem or {}).get("proveedor", "")
            num_f = f"{(fac or {}).get('punto_venta', '')}-{(fac or {}).get('numero_comprobante', '')}"
            num_r = (rem or {}).get("numero_remito", "")
            ctrl_id = guardar_control_remito(cuit, prov, num_f, num_r, resultado)
            st.success(f"Control guardado ({ctrl_id}).")

        if st.button("Limpiar comparación"):
            st.session_state.control_resultado = None
            st.rerun()
    elif not fac and not rem:
        st.info("Subí una factura y un remito para comparar.")
    elif not fac:
        st.info("Falta cargar la factura.")
    elif not rem:
        st.info("Falta cargar el remito.")
