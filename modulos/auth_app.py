"""Login, sesión, puntos en sidebar y paneles admin."""
import streamlit as st

from modulos.puntos_vendedor import asegurar_vendedor, resumen_puntos_vendedor, listar_vendedores, UMBRAL_PUNTO
from modulos.usuarios_app_db import (
    validar_credenciales,
    cambiar_clave_usuario,
    resetear_clave_usuario,
    listar_usuarios_db,
    CLAVE_INICIAL,
)
from modulos.auditoria_app import registrar_auditoria, render_panel_auditoria_admin


def sesion_activa() -> bool:
    return bool(st.session_state.get("auth_usuario"))


def usuario_actual():
    return st.session_state.get("auth_usuario")


def rol_actual() -> str:
    return str(st.session_state.get("auth_rol", "") or "")


def vendedor_id_sesion() -> str:
    if rol_actual() == "vendedor":
        return str(st.session_state.get("auth_vendedor_id") or st.session_state.get("auth_usuario") or "fernando")
    return str(st.session_state.get("vendedor_mostrador_sel") or st.session_state.get("auth_vendedor_id") or "fernando")


def es_admin() -> bool:
    return rol_actual() == "admin"


def cerrar_sesion():
    if st.session_state.get("auth_usuario"):
        registrar_auditoria(
            "auth", "logout",
            f"Cierre de sesión: {st.session_state.get('auth_nombre', '')}",
            exito=True,
        )
    for k in (
        "auth_usuario", "auth_rol", "auth_nombre", "auth_vendedor_id",
        "vendedor_mostrador_sel",
    ):
        st.session_state.pop(k, None)


def iniciar_sesion(usuario: str, clave: str):
    u_try = str(usuario or "").strip().lower()
    ok, data = validar_credenciales(u_try, clave)
    if not ok:
        registrar_auditoria(
            "auth", "login_fallido",
            f"Intento fallido: {u_try or '(vacío)'}",
            detalle={"usuario": u_try},
            exito=False,
            error_msg=str(data),
            usuario=u_try,
        )
        return False, data

    st.session_state.auth_usuario = data["usuario"]
    st.session_state.auth_rol = data["rol"]
    st.session_state.auth_nombre = data["nombre"]
    st.session_state.auth_vendedor_id = data.get("vendedor_id")

    if data["rol"] == "vendedor" and data.get("vendedor_id"):
        asegurar_vendedor(data["vendedor_id"], nombre=data["nombre"], rol="vendedor")

    registrar_auditoria(
        "auth", "login",
        f"Ingreso: {data['nombre']} ({data['rol']})",
        detalle={"usuario": data["usuario"], "rol": data["rol"]},
        exito=True,
    )
    return True, f"Bienvenido, {data['nombre']}."


def render_login():
    st.markdown("## Hafid Repuestos — Ingreso")
    st.caption(
        "Usuarios: **admin**, **fernando**, **emilio**, **facundo**, **gabriel**, **damian** · "
        f"clave inicial **{CLAVE_INICIAL}** (podés cambiarla después del ingreso)"
    )
    with st.form("form_login_app"):
        user = st.text_input("Usuario")
        pwd = st.text_input("Clave", type="password")
        if st.form_submit_button("Entrar", type="primary", use_container_width=True):
            ok, msg = iniciar_sesion(user, pwd)
            if ok:
                st.success(msg)
                st.rerun()
            else:
                st.error(msg)


def render_cambiar_clave_sidebar():
    if not sesion_activa():
        return
    with st.expander("🔐 Cambiar mi clave", expanded=False):
        with st.form("form_cambiar_clave"):
            actual = st.text_input("Clave actual", type="password")
            nueva = st.text_input("Clave nueva", type="password")
            nueva2 = st.text_input("Repetir clave nueva", type="password")
            if st.form_submit_button("Guardar clave", use_container_width=True):
                if nueva != nueva2:
                    st.error("Las claves nuevas no coinciden.")
                else:
                    ok, msj = cambiar_clave_usuario(usuario_actual(), actual, nueva)
                    if ok:
                        registrar_auditoria("auth", "cambio_clave", "El usuario cambió su clave", exito=True)
                        st.success(msj)
                    else:
                        registrar_auditoria(
                            "auth", "cambio_clave",
                            "Intento fallido de cambio de clave",
                            exito=False, error_msg=msj,
                        )
                        st.error(msj)


def render_puntos_sidebar():
    if not sesion_activa() or es_admin():
        return
    vid = vendedor_id_sesion()
    r = resumen_puntos_vendedor(vid)
    st.divider()
    st.markdown("**⭐ Mis puntos**")
    st.metric("Puntos", r["puntos"])
    st.caption(f"Próximo punto: faltan ${r['faltan_proximo']:,.0f}")


def render_panel_puntos_admin():
    st.subheader("Puntos por vendedor")
    st.caption(f"Regla: 1 punto cada ${UMBRAL_PUNTO:,.0f} facturados (solo facturas ARCA).")
    vendedores = listar_vendedores(activos_solo=False)
    if not vendedores:
        st.info("Aún no hay vendedores registrados.")
        return
    import pandas as pd
    filas = []
    for v in vendedores:
        acum = float(v.get("ventas_acumuladas", 0) or 0)
        filas.append({
            "Vendedor": v.get("nombre", v.get("id")),
            "ID": v.get("id"),
            "Puntos": int(v.get("puntos", 0) or 0),
            "Acumulado ($)": acum,
            "Falta próximo ($)": max(0.0, UMBRAL_PUNTO - acum),
            "Activo": "Sí" if v.get("activo", True) else "No",
        })
    st.dataframe(pd.DataFrame(filas), hide_index=True, use_container_width=True)


def render_gestion_usuarios_admin():
    st.subheader("Usuarios de la app")
    import pandas as pd
    users = listar_usuarios_db()
    if not users:
        st.warning("No hay usuarios en Firestore.")
        return
    st.dataframe(
        pd.DataFrame([{
            "Usuario": u.get("usuario"),
            "Nombre": u.get("nombre"),
            "Rol": u.get("rol"),
            "Vendedor ID": u.get("vendedor_id") or "—",
            "Activo": "Sí" if u.get("activo", True) else "No",
        } for u in users]),
        hide_index=True,
        use_container_width=True,
    )
    vendedores_reset = [u for u in users if u.get("rol") == "vendedor"]
    if vendedores_reset:
        opciones = {f"{u.get('nombre')} ({u['id']})": u["id"] for u in vendedores_reset}
        sel = st.selectbox("Restablecer clave a 111", options=list(opciones.keys()), key="reset_clave_sel")
        if st.button("Restablecer clave del vendedor seleccionado", key="btn_reset_clave"):
            ok, msj = resetear_clave_usuario(opciones[sel])
            if ok:
                registrar_auditoria(
                    "config", "reset_clave",
                    msj,
                    detalle={"usuario_afectado": opciones[sel]},
                    exito=True,
                )
                st.success(msj)
            else:
                st.error(msj)


def render_admin_secciones():
    render_gestion_usuarios_admin()
    st.divider()
    render_panel_puntos_admin()
    st.divider()
    render_panel_auditoria_admin()
