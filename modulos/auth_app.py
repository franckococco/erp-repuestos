"""Login simple de prueba (vendedor / administrador)."""
import os
import streamlit as st

from modulos.puntos_vendedor import asegurar_vendedor


def _clave_secrets(clave: str, default: str = "") -> str:
    try:
        return str(st.secrets.get(clave, default) or default)
    except Exception:
        return os.getenv(clave, default)


def usuarios_app():
    """Usuarios de prueba. Claves configurables en secrets."""
    admin_clave = _clave_secrets("ADMIN_PASSWORD", "111")
    return {
        "fernando": {
            "clave": _clave_secrets("FERNANDO_PASSWORD", "111"),
            "rol": "vendedor",
            "vendedor_id": "fernando",
            "nombre": "Fernando",
        },
        "admin": {
            "clave": admin_clave,
            "rol": "admin",
            "vendedor_id": None,
            "nombre": "Administrador",
        },
    }


def sesion_activa() -> bool:
    return bool(st.session_state.get("auth_usuario"))


def usuario_actual():
    return st.session_state.get("auth_usuario")


def rol_actual() -> str:
    return str(st.session_state.get("auth_rol", "") or "")


def vendedor_id_sesion() -> str:
    """ID de vendedor para mostrador y puntos."""
    if rol_actual() == "vendedor":
        return str(st.session_state.get("auth_vendedor_id") or "fernando")
    return str(st.session_state.get("vendedor_mostrador_sel") or "Caja Principal")


def es_admin() -> bool:
    return rol_actual() == "admin"


def cerrar_sesion():
    for k in (
        "auth_usuario", "auth_rol", "auth_nombre", "auth_vendedor_id",
        "vendedor_mostrador_sel",
    ):
        st.session_state.pop(k, None)


def validar_login(usuario: str, clave: str):
    u = str(usuario or "").strip().lower()
    users = usuarios_app()
    if u not in users:
        return False, "Usuario o clave incorrectos."
    if str(clave or "") != str(users[u]["clave"]):
        return False, "Usuario o clave incorrectos."
    return True, users[u]


def iniciar_sesion(usuario: str, clave: str):
    ok, data = validar_login(usuario, clave)
    if not ok:
        return False, data
    st.session_state.auth_usuario = str(usuario).strip().lower()
    st.session_state.auth_rol = data["rol"]
    st.session_state.auth_nombre = data["nombre"]
    st.session_state.auth_vendedor_id = data.get("vendedor_id")
    if data["rol"] == "vendedor" and data.get("vendedor_id"):
        asegurar_vendedor(data["vendedor_id"], nombre=data["nombre"], rol="vendedor")
    if data["rol"] == "admin":
        asegurar_vendedor("fernando", nombre="Fernando", rol="vendedor")
        asegurar_vendedor("caja_principal", nombre="Caja Principal", rol="vendedor")
    return True, f"Bienvenido, {data['nombre']}."


def render_login():
    st.markdown("## Hafid Repuestos — Ingreso")
    st.caption("Prueba: usuario **fernando** / clave **111** · admin / clave en secrets (default 111)")
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


def render_puntos_sidebar():
    if not sesion_activa():
        return
    if es_admin():
        return
    from modulos.puntos_vendedor import resumen_puntos_vendedor
    vid = vendedor_id_sesion()
    r = resumen_puntos_vendedor(vid)
    st.divider()
    st.markdown("**⭐ Mis puntos**")
    st.metric("Puntos", r["puntos"])
    st.caption(f"Próximo punto: faltan ${r['faltan_proximo']:,.0f}")


def render_panel_puntos_admin():
    from modulos.puntos_vendedor import listar_vendedores, UMBRAL_PUNTO
    st.subheader("Puntos por vendedor")
    st.caption(f"Regla: 1 punto cada ${UMBRAL_PUNTO:,.0f} facturados (solo facturas ARCA).")
    vendedores = listar_vendedores(activos_solo=False)
    if not vendedores:
        st.info("Aún no hay vendedores registrados. Se crean al facturar o al iniciar sesión.")
        return
    import pandas as pd
    filas = []
    for v in vendedores:
        if str(v.get("rol", "vendedor")) == "admin":
            continue
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
