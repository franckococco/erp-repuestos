"""UI Streamlit — pestaña RELEVAMIENTOS (sección aislada del resto del ERP)."""
from __future__ import annotations

import pandas as pd
import streamlit as st

from modulos.db_firebase import obtener_inventario_completo
from modulos.puntos_vendedor import listar_vendedores
from modulos.relevamientos_db import (
    asignar_familia_a_vendedor,
    crear_familia,
    desvincular_articulo_familia,
    enriquecer_inventario_con_movimiento,
    familias_sin_asignar,
    guardar_conteo,
    guardar_tarjeta,
    listar_articulos_familia,
    listar_conteos,
    listar_familias,
    listar_tarjetas,
    obtener_familia,
    reporte_sin_movimiento,
    sembrar_tarjetas_iniciales,
    vincular_articulo_familia,
)
from modulos.util_fechas import formatear_fecha_ar


def render_relevamientos():
    st.caption(
        "Módulo nuevo e independiente: familias de repuestos, tarjetas por vendedor, "
        "conteo cíclico y stock sin movimiento. No modifica Mostrador ni Inventario."
    )
    tab_cat, tab_asig, tab_tarjeta, tab_mov = st.tabs([
        "📂 Catálogo de módulos",
        "👥 Asignación por vendedor",
        "🧾 Mi tarjeta (conteo)",
        "⏱ Sin movimiento",
    ])
    with tab_cat:
        _tab_catalogo()
    with tab_asig:
        _tab_asignacion()
    with tab_tarjeta:
        _tab_mi_tarjeta()
    with tab_mov:
        _tab_sin_movimiento()


def _inv_index():
    if "relev_inv_cache" not in st.session_state:
        st.session_state.relev_inv_cache = obtener_inventario_completo() or []
    return st.session_state.relev_inv_cache


def _refresh_inv():
    st.session_state.pop("relev_inv_cache", None)
    obtener_inventario_completo.clear()


# ── Catálogo ────────────────────────────────────────────────────────────────


def _tab_catalogo():
    st.subheader("Catálogo general de módulos / familias")
    c1, c2 = st.columns([2, 1])
    with c1:
        nombre = st.text_input("Nuevo módulo", placeholder="Ej: BOMBAS DE AGUA", key="rel_fam_nombre")
        desc = st.text_input("Descripción (opcional)", key="rel_fam_desc")
    with c2:
        st.write("")
        st.write("")
        if st.button("➕ Crear módulo", use_container_width=True, key="rel_fam_crear"):
            ok, msg, _ = crear_familia(nombre, desc)
            (st.success if ok else st.error)(msg)
            if ok:
                st.rerun()

    familias = listar_familias(solo_activas=True)
    if not familias:
        st.info("Todavía no hay módulos. Creá el primero arriba (ej. KIT DISTRIBUCIÓN).")
        return

    st.dataframe(
        pd.DataFrame([
            {
                "Módulo": f.get("nombre"),
                "Artículos": int(f.get("articulo_count") or 0),
                "ID": f.get("id"),
            }
            for f in familias
        ]),
        hide_index=True,
        use_container_width=True,
    )

    opciones = {f"{f['nombre']} ({f.get('articulo_count', 0)})": f["id"] for f in familias}
    elegido = st.selectbox("Ver / editar módulo", list(opciones.keys()), key="rel_fam_sel")
    fid = opciones[elegido]
    fam = obtener_familia(fid)
    if not fam:
        return

    st.markdown(f"### {fam.get('nombre')}")
    if fam.get("descripcion"):
        st.caption(fam["descripcion"])

    inv = _inv_index()
    arts = listar_articulos_familia(fid)
    filas = []
    for a in arts:
        mid = a.get("id_maestro")
        mrc = a.get("marca") or ""
        matches = [
            x for x in inv
            if str(x.get("id_maestro")) == str(mid)
            and (not mrc or str(x.get("marca", "")).upper() == str(mrc).upper())
        ]
        if not matches:
            filas.append({
                "Código": mid,
                "Marca": mrc or "—",
                "Descripción": a.get("descripcion") or "—",
                "Vehículo": "—",
                "Stock": 0,
                "Stock mín.": "—",
                "Costo": 0,
            })
        else:
            for x in matches:
                filas.append({
                    "Código": x.get("codigo") or mid,
                    "Marca": x.get("marca"),
                    "Descripción": x.get("descripcion"),
                    "Vehículo": x.get("vehiculo") or "",
                    "Stock": int(x.get("stock") or 0),
                    "Stock mín.": int(x.get("stock_critico") or 0),
                    "Costo": float(x.get("ultimo_costo_base") or 0),
                })

    if filas:
        st.dataframe(pd.DataFrame(filas), hide_index=True, use_container_width=True)
    else:
        st.warning("Este módulo aún no tiene artículos vinculados.")

    st.markdown("**Vincular artículo del inventario**")
    busq = st.text_input("Buscar por código o descripción", key="rel_vinc_busq")
    candidatos = inv
    if busq.strip():
        t = busq.strip().upper()
        candidatos = [
            x for x in inv
            if t in str(x.get("codigo", "")).upper()
            or t in str(x.get("descripcion", "")).upper()
            or t in str(x.get("id_maestro", "")).upper()
        ][:80]
    else:
        candidatos = inv[:80]

    labels = {
        f"{x.get('codigo')} · {x.get('marca')} · {x.get('descripcion', '')[:40]}": x
        for x in candidatos
    }
    if labels:
        pick = st.selectbox("Artículo", list(labels.keys()), key="rel_vinc_pick")
        if st.button("Vincular a este módulo", key="rel_vinc_ok"):
            x = labels[pick]
            ok, msg = vincular_articulo_familia(
                fid,
                str(x.get("id_maestro") or x.get("codigo")),
                str(x.get("marca") or ""),
                str(x.get("descripcion") or ""),
            )
            (st.success if ok else st.error)(msg)
            if ok:
                st.rerun()

    if arts:
        with st.expander("Quitar artículo del módulo"):
            quitar_opts = {
                f"{a.get('id_maestro')} · {a.get('marca') or 'TODAS'}": a for a in arts
            }
            q = st.selectbox("Artículo a quitar", list(quitar_opts.keys()), key="rel_quit_sel")
            if st.button("Quitar", key="rel_quit_ok"):
                a = quitar_opts[q]
                ok, msg = desvincular_articulo_familia(
                    fid, a.get("id_maestro"), a.get("marca") or ""
                )
                (st.success if ok else st.error)(msg)
                if ok:
                    st.rerun()


# ── Asignación ──────────────────────────────────────────────────────────────


def _tab_asignacion():
    st.subheader("Asignación de módulos por vendedor")
    c1, c2 = st.columns([1, 1])
    with c1:
        if st.button("Inicializar tarjetas A–D (Facundo/Emilio/Damián/Fernando)", key="rel_seed"):
            ok, msg = sembrar_tarjetas_iniciales()
            (st.success if ok else st.error)(msg)
            st.rerun()
    with c2:
        st.caption("Podés agregar vendedores nuevos abajo con total libertad.")

    # Alta libre de vendedor/tarjeta
    with st.expander("➕ Agregar / editar vendedor y su tarjeta", expanded=False):
        vend_exist = listar_vendedores(activos_solo=False) or []
        sugeridos = [v.get("id") for v in vend_exist]
        col_a, col_b, col_c = st.columns(3)
        with col_a:
            vid = st.text_input("ID vendedor", placeholder="ej: gabriel", key="rel_tv_id")
            if sugeridos:
                st.caption("Existentes: " + ", ".join(sugeridos[:8]))
        with col_b:
            nom = st.text_input("Nombre", key="rel_tv_nom")
        with col_c:
            etq = st.text_input("Etiqueta tarjeta", placeholder="E", key="rel_tv_etq")
        fams = listar_familias()
        fam_map = {f["nombre"]: f["id"] for f in fams}
        sel = st.multiselect(
            "Módulos asignados",
            list(fam_map.keys()),
            key="rel_tv_fams",
        )
        if st.button("Guardar tarjeta", key="rel_tv_save"):
            ok, msg = guardar_tarjeta(
                vid or nom,
                nombre=nom or vid,
                etiqueta=etq,
                familia_ids=[fam_map[n] for n in sel],
            )
            (st.success if ok else st.error)(msg)
            if ok:
                st.rerun()

    tarjetas = listar_tarjetas(solo_activas=True)
    fams_all = {f["id"]: f for f in listar_familias(solo_activas=False)}

    if not tarjetas:
        st.info("No hay tarjetas. Usá «Inicializar tarjetas A–D» o agregá un vendedor.")
    else:
        cols = st.columns(min(2, len(tarjetas)) or 1)
        for i, t in enumerate(tarjetas):
            with cols[i % len(cols)]:
                etq = t.get("etiqueta") or "?"
                st.markdown(f"#### Tarjeta {etq} — {t.get('nombre', t['id'])}")
                ids = t.get("familia_ids") or []
                if not ids:
                    st.caption("Sin módulos asignados.")
                else:
                    for fid in ids:
                        f = fams_all.get(fid, {})
                        st.write(
                            f"• **{f.get('nombre', fid)}** "
                            f"({int(f.get('articulo_count') or 0)} art.)"
                        )

    st.markdown("---")
    st.markdown("### ⚠️ Módulos sin asignar")
    huerfanas = familias_sin_asignar()
    if not huerfanas:
        st.success("Todos los módulos están asignados a un vendedor.")
        return

    st.markdown(
        '<div style="background:#ffe8d6;border:1px solid #e67e22;padding:12px;'
        'border-radius:8px;margin-bottom:8px;">'
        "<b>Familias huérfanas</b> — asignalas a una tarjeta para que no queden sin auditar."
        "</div>",
        unsafe_allow_html=True,
    )
    for f in huerfanas:
        c1, c2, c3 = st.columns([3, 2, 1])
        with c1:
            st.write(
                f"**{f.get('nombre')}** · {int(f.get('articulo_count') or 0)} artículos"
            )
        with c2:
            opts = {
                f"{t.get('etiqueta', '?')} — {t.get('nombre', t['id'])}": t["id"]
                for t in tarjetas
            }
            if not opts:
                st.caption("Creá una tarjeta primero.")
                continue
            dest = st.selectbox(
                "Asignar a",
                list(opts.keys()),
                key=f"rel_asig_{f['id']}",
                label_visibility="collapsed",
            )
        with c3:
            if st.button("Asignar", key=f"rel_asig_btn_{f['id']}", use_container_width=True):
                ok, msg = asignar_familia_a_vendedor(f["id"], opts[dest])
                (st.success if ok else st.error)(msg)
                if ok:
                    st.rerun()


# ── Mi tarjeta ──────────────────────────────────────────────────────────────


def _tab_mi_tarjeta():
    st.subheader("Conteo físico — mi tarjeta")
    tarjetas = listar_tarjetas(solo_activas=True)
    if not tarjetas:
        st.warning("No hay tarjetas. Creá asignaciones en la pestaña anterior.")
        return

    opts = {
        f"{t.get('etiqueta', '?')} — {t.get('nombre', t['id'])}": t for t in tarjetas
    }
    pick = st.selectbox("Vendedor / tarjeta", list(opts.keys()), key="rel_count_vend")
    tarjeta = opts[pick]
    fam_ids = tarjeta.get("familia_ids") or []
    if not fam_ids:
        st.info("Esta tarjeta no tiene módulos asignados.")
        return

    fams = {f["id"]: f for f in listar_familias()}
    fam_opts = {
        fams[fid]["nombre"]: fid for fid in fam_ids if fid in fams
    }
    if not fam_opts:
        st.info("Los módulos asignados no están activos o no existen.")
        return

    fam_nom = st.selectbox("Módulo a relevar hoy", list(fam_opts.keys()), key="rel_count_fam")
    fid = fam_opts[fam_nom]
    inv = enriquecer_inventario_con_movimiento(_inv_index())
    arts = listar_articulos_familia(fid)

    filas_editor = []
    for a in arts:
        mid = a.get("id_maestro")
        mrc = a.get("marca") or ""
        matches = [
            x for x in inv
            if str(x.get("id_maestro")) == str(mid)
            and (not mrc or str(x.get("marca", "")).upper() == str(mrc).upper())
        ]
        if not matches:
            filas_editor.append({
                "id_maestro": mid,
                "marca": mrc,
                "descripcion": a.get("descripcion") or "",
                "stock_sistema": 0,
                "stock_fisico": 0,
                "observacion": "",
            })
        else:
            for x in matches:
                filas_editor.append({
                    "id_maestro": x.get("id_maestro"),
                    "marca": x.get("marca"),
                    "descripcion": x.get("descripcion"),
                    "stock_sistema": int(x.get("stock") or 0),
                    "stock_fisico": int(x.get("stock") or 0),
                    "observacion": "",
                })

    if not filas_editor:
        st.warning("El módulo no tiene artículos para contar.")
        return

    st.caption("Editá «stock_fisico» según el conteo en depósito. La diferencia se calcula al guardar.")
    df = st.data_editor(
        pd.DataFrame(filas_editor),
        hide_index=True,
        use_container_width=True,
        disabled=["id_maestro", "marca", "descripcion", "stock_sistema"],
        key="rel_count_editor",
        column_config={
            "stock_sistema": st.column_config.NumberColumn("Stock sistema"),
            "stock_fisico": st.column_config.NumberColumn("Stock físico"),
            "observacion": st.column_config.TextColumn("Observación"),
        },
    )
    notas = st.text_input("Notas del conteo (opcional)", key="rel_count_notas")
    if st.button("💾 Guardar conteo", type="primary", key="rel_count_save"):
        items = df.to_dict("records")
        ok, msg, _ = guardar_conteo(tarjeta["id"], fid, items, notas)
        (st.success if ok else st.error)(msg)

    hist = listar_conteos(vendedor_id=tarjeta["id"], limite=8)
    if hist:
        st.markdown("**Últimos conteos**")
        st.dataframe(
            pd.DataFrame([
                {
                    "Fecha": formatear_fecha_ar(h.get("fecha")),
                    "Módulo": (fams.get(h.get("familia_id"), {}) or {}).get("nombre", h.get("familia_id")),
                    "Ítems": h.get("item_count"),
                    "Con diferencia": h.get("diffs"),
                }
                for h in hist
            ]),
            hide_index=True,
            use_container_width=True,
        )


# ── Sin movimiento ──────────────────────────────────────────────────────────


def _tab_sin_movimiento():
    st.subheader("Artículos sin movimiento")
    st.caption(
        "Muestra stock parado: días sin venta y días sin ingreso de mercadería, "
        "más el costo inmovilizado (stock × costo)."
    )
    c1, c2, c3 = st.columns(3)
    with c1:
        dias_v = st.number_input("Mín. días sin venta", min_value=0, value=5, step=1, key="rel_d_v")
    with c2:
        filtrar_ing = st.checkbox("Filtrar también por días sin ingreso", value=False, key="rel_f_ing")
        dias_i = st.number_input(
            "Mín. días sin ingreso",
            min_value=0,
            value=5,
            step=1,
            key="rel_d_i",
            disabled=not filtrar_ing,
        )
    with c3:
        solo_stock = st.checkbox("Solo con stock > 0", value=True, key="rel_solo_stock")
        if st.button("Actualizar inventario", key="rel_mov_ref"):
            _refresh_inv()
            st.rerun()

    inv = enriquecer_inventario_con_movimiento(_inv_index())
    rep = reporte_sin_movimiento(
        inv,
        dias_venta_min=int(dias_v),
        dias_ingreso_min=int(dias_i) if filtrar_ing else None,
        solo_con_stock=solo_stock,
    )

    total_inm = sum(float(r.get("costo_inmovilizado") or 0) for r in rep)
    m1, m2, m3 = st.columns(3)
    m1.metric("Artículos en reporte", len(rep))
    m2.metric("Costo inmovilizado", f"${total_inm:,.2f}")
    m3.metric("Umbral sin venta", f"{int(dias_v)} días")

    if not rep:
        st.success("No hay artículos que cumplan el filtro.")
        return

    df = pd.DataFrame([
        {
            "Código": r.get("codigo"),
            "Marca": r.get("marca"),
            "Descripción": r.get("descripcion"),
            "Familia": r.get("familia_nombre") or "—",
            "Stock": int(r.get("stock") or 0),
            "Costo u.": float(r.get("ultimo_costo_base") or 0),
            "$ Inmovilizado": float(r.get("costo_inmovilizado") or 0),
            "Días sin venta": r.get("dias_sin_venta") if r.get("dias_sin_venta") is not None else "Nunca",
            "Días sin ingreso": r.get("dias_sin_ingreso") if r.get("dias_sin_ingreso") is not None else "Nunca",
            "Última venta": formatear_fecha_ar(r.get("last_sale_at")) or "—",
            "Último ingreso": formatear_fecha_ar(r.get("last_ingreso_at")) or "—",
        }
        for r in rep
    ])
    st.dataframe(df, hide_index=True, use_container_width=True)
    st.caption(
        "Si dice «Nunca», aún no hay fecha registrada. A partir de ahora, "
        "cada venta e ingreso de mercadería actualiza esas fechas automáticamente."
    )
