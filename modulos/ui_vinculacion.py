import streamlit as st

from modulos.db_firebase import (
    obtener_proveedores,
    buscar_equivalencia,
    guardar_equivalencia,
    listar_maestros_para_busqueda,
    obtener_inventario_completo,
)
from modulos.ia_vinculacion import sugerir_articulo_con_groq
from modulos.ui_estilos import ayuda


def render_vinculacion_inventario():
    ayuda(
        "Ayuda — Vinculación",
        "Asociá el **código del proveedor** (el de la factura) con un **artículo maestro** interno. "
        "Podés hacerlo de a poco; no es necesario al cargar cada factura.",
    )

    provs = obtener_proveedores() or {}
    if not provs:
        st.warning("Registrá proveedores en Configuración primero.")
        return

    opciones = {f"{d.get('nombre', '—')} (CUIT {c})": c for c, d in provs.items() if isinstance(d, dict)}
    sel = st.selectbox("Proveedor", options=list(opciones.keys()), key="vinc_prov")
    cuit = opciones.get(sel, "")

    col1, col2 = st.columns(2)
    cod_prov = col1.text_input("Código proveedor", placeholder="Ej: 1252T")
    marca_prov = col2.text_input("Marca en factura", value="GENERICO")

    if cod_prov:
        eq = buscar_equivalencia(cuit, cod_prov)
        if eq:
            st.success(
                f"Ya vinculado → `{eq.get('id_maestro')}` / {eq.get('marca_variante', '')} "
                f"({eq.get('descripcion_maestro', '')})"
            )

        art_demo = {
            "codigo": cod_prov,
            "codigo_proveedor": cod_prov,
            "descripcion": "",
            "marca": marca_prov,
        }

        if st.button("🤖 Sugerir con IA", key="vinc_inv_groq"):
            inventario = obtener_inventario_completo() or []
            sugerido = sugerir_articulo_con_groq(dict(art_demo), inventario)
            st.session_state.vinc_sugerencias = sugerido.get("sugerencias", [])

        sugerencias = st.session_state.get("vinc_sugerencias") or []
        for j, sug in enumerate(sugerencias):
            sc1, sc2 = st.columns([4, 1])
            sc1.caption(
                f"**{sug.get('score', 0)}%** — {sug.get('descripcion', '')} "
                f"→ `{sug.get('id_maestro')}_{sug.get('marca')}` · {sug.get('motivo', '')}"
            )
            if sc2.button("✅ Vincular", key=f"vinc_inv_sug_{j}"):
                guardar_equivalencia(
                    cuit, cod_prov,
                    sug.get("id_maestro"), sug.get("marca", "GENERICO"),
                    descripcion_proveedor="", marca_proveedor=marca_prov,
                    origen="sugerencia_ia",
                )
                st.success("Equivalencia guardada.")
                st.session_state.vinc_sugerencias = []
                st.rerun()

        busq = st.text_input("Buscar artículo maestro", placeholder="Código, descripción, marca…", key="vinc_inv_busq")
        if busq:
            for m in listar_maestros_para_busqueda(busq):
                for marca_m in m.get("marcas", ["GENERICO"]):
                    lbl = f"{m['descripcion']} | {m['vehiculo']} | {marca_m} | Cód. {m['codigo']}"
                    if st.button(f"🔗 {lbl}", key=f"vinc_inv_man_{m['id_maestro']}_{marca_m}"):
                        guardar_equivalencia(
                            cuit, cod_prov,
                            m["id_maestro"], marca_m,
                            descripcion_proveedor="", marca_proveedor=marca_prov,
                            origen="manual",
                        )
                        st.success(f"Vinculado {cod_prov} → {m['id_maestro']} / {marca_m}")
                        st.rerun()
