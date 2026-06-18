"""Caja negra: registro de acciones (solo visible para admin)."""
import json
from datetime import date, datetime, time, timezone
from modulos.util_fechas import formatear_fecha_ar, rango_fechas_ar_a_utc, fecha_hoy_ar
from typing import Any, Dict, List, Optional

import pandas as pd
import streamlit as st

from modulos.db_firebase import get_db

MAX_DETALLE_CHARS = 8000


def _ctx_sesion() -> Dict[str, Any]:
    try:
        return {
            "usuario": st.session_state.get("auth_usuario"),
            "nombre": st.session_state.get("auth_nombre"),
            "vendedor_id": st.session_state.get("auth_vendedor_id"),
            "rol": st.session_state.get("auth_rol"),
        }
    except Exception:
        return {}


def _sanitizar_detalle(detalle: Any) -> Any:
    if detalle is None:
        return None
    if isinstance(detalle, (str, int, float, bool)):
        return detalle
    if isinstance(detalle, dict):
        out = {}
        for k, v in detalle.items():
            lk = str(k).lower()
            if any(x in lk for x in ("clave", "password", "token", "secret")):
                out[k] = "***"
            else:
                out[k] = _sanitizar_detalle(v)
        return out
    if isinstance(detalle, (list, tuple)):
        return [_sanitizar_detalle(x) for x in detalle[:50]]
    return str(detalle)[:500]


def registrar_auditoria(
    modulo: str,
    accion: str,
    resumen: str,
    detalle: Any = None,
    exito: bool = True,
    ref_id: Optional[str] = None,
    error_msg: Optional[str] = None,
    usuario: Optional[str] = None,
    nombre: Optional[str] = None,
    vendedor_id: Optional[str] = None,
):
    """Graba evento en Firestore. Nunca interrumpe la operación principal."""
    try:
        ctx = _ctx_sesion()
        det = _sanitizar_detalle(detalle)
        raw = json.dumps(det, ensure_ascii=False, default=str) if det is not None else ""
        if len(raw) > MAX_DETALLE_CHARS:
            raw = raw[:MAX_DETALLE_CHARS] + "…"

        get_db().collection("auditoria_app").add({
            "fecha": datetime.now(timezone.utc),
            "modulo": str(modulo or "app")[:40],
            "accion": str(accion or "accion")[:60],
            "resumen": str(resumen or "")[:500],
            "detalle": det,
            "detalle_json": raw or None,
            "exito": bool(exito),
            "error_msg": str(error_msg or "")[:400] if error_msg else None,
            "ref_id": str(ref_id)[:120] if ref_id else None,
            "usuario": usuario or ctx.get("usuario"),
            "nombre": nombre or ctx.get("nombre"),
            "vendedor_id": vendedor_id or ctx.get("vendedor_id"),
            "rol": ctx.get("rol"),
        })
    except Exception:
        pass


def _a_datetime(val):
    if val is None:
        return None
    if isinstance(val, datetime):
        return val if val.tzinfo else val.replace(tzinfo=timezone.utc)
    if isinstance(val, date):
        return datetime.combine(val, time.min, tzinfo=timezone.utc)
    if hasattr(val, "timestamp"):
        return datetime.fromtimestamp(val.timestamp(), tz=timezone.utc)
    return None


def listar_auditoria(
    fecha_desde=None,
    fecha_hasta=None,
    usuario: Optional[str] = None,
    modulo: Optional[str] = None,
    accion: Optional[str] = None,
    solo_errores: bool = False,
    busqueda: str = "",
    limite: int = 400,
) -> List[Dict[str, Any]]:
    try:
        docs = list(
            get_db().collection("auditoria_app")
            .order_by("fecha", direction="DESCENDING")  # type: ignore
            .limit(min(limite * 3, 1200))
            .stream()
        )
    except Exception:
        docs = list(get_db().collection("auditoria_app").limit(1500).stream())
        docs.sort(
            key=lambda d: _a_datetime((d.to_dict() or {}).get("fecha"))
            or datetime.min.replace(tzinfo=timezone.utc),
            reverse=True,
        )
        docs = docs[: min(limite * 3, 1200)]
    dt_desde, dt_hasta = None, None
    if fecha_desde is not None:
        if isinstance(fecha_desde, date) and not isinstance(fecha_desde, datetime):
            if fecha_hasta is not None and isinstance(fecha_hasta, date) and not isinstance(fecha_hasta, datetime):
                dt_desde, dt_hasta = rango_fechas_ar_a_utc(fecha_desde, fecha_hasta)
            else:
                dt_desde, _ = rango_fechas_ar_a_utc(fecha_desde, fecha_desde)
        else:
            dt_desde = _a_datetime(fecha_desde)
    if fecha_hasta is not None and dt_hasta is None:
        if isinstance(fecha_hasta, date) and not isinstance(fecha_hasta, datetime):
            _, dt_hasta = rango_fechas_ar_a_utc(fecha_hasta, fecha_hasta)
        else:
            dt_hasta = _a_datetime(fecha_hasta)

    u_f = str(usuario or "").strip().lower()
    m_f = str(modulo or "").strip().lower()
    a_f = str(accion or "").strip().lower()
    q = str(busqueda or "").strip().lower()

    out = []
    for doc in docs:
        data = doc.to_dict() or {}
        f = _a_datetime(data.get("fecha"))
        if dt_desde and f and f < dt_desde:
            continue
        if dt_hasta and f and f > dt_hasta:
            continue
        if u_f and str(data.get("usuario", "")).lower() != u_f:
            continue
        if m_f and str(data.get("modulo", "")).lower() != m_f:
            continue
        if a_f and str(data.get("accion", "")).lower() != a_f:
            continue
        if solo_errores and data.get("exito", True):
            continue
        if q:
            blob = " ".join([
                str(data.get("resumen", "")),
                str(data.get("detalle_json", "")),
                str(data.get("usuario", "")),
                str(data.get("nombre", "")),
            ]).lower()
            if q not in blob:
                continue
        data["id"] = doc.id
        data["fecha"] = f
        out.append(data)
        if len(out) >= limite:
            break
    return out


def render_panel_auditoria_admin():
    from datetime import timedelta

    st.subheader("Caja negra — Auditoría")
    st.caption("Registro de acciones de todos los usuarios. Solo administrador.")

    usuarios = listar_auditoria(limite=1)
    usuarios_ids = sorted({
        str(x.get("usuario", ""))
        for x in listar_auditoria(limite=300)
        if x.get("usuario")
    })

    hoy = fecha_hoy_ar()
    c1, c2, c3, c4 = st.columns(4)
    f_desde = c1.date_input("Desde", value=hoy - timedelta(days=7), key="aud_desde")
    f_hasta = c2.date_input("Hasta", value=hoy, key="aud_hasta")
    filtro_u = c3.selectbox("Usuario", ["— Todos —"] + usuarios_ids, key="aud_usuario")
    filtro_m = c4.selectbox(
        "Módulo",
        ["— Todos —", "auth", "mostrador", "inventario", "carga", "pedidos", "asistente", "config"],
        key="aud_modulo",
    )
    c5, c6, c7 = st.columns([2, 1, 1])
    busq = c5.text_input("Buscar en resumen / detalle", key="aud_busq")
    solo_err = c6.checkbox("Solo errores", key="aud_solo_err")
    limite = c7.number_input("Máx. filas", min_value=50, max_value=500, value=200, step=50)

    items = listar_auditoria(
        fecha_desde=f_desde,
        fecha_hasta=f_hasta,
        usuario=None if filtro_u == "— Todos —" else filtro_u,
        modulo=None if filtro_m == "— Todos —" else filtro_m,
        solo_errores=solo_err,
        busqueda=busq,
        limite=int(limite),
    )

    if not items:
        st.info("No hay registros para esos filtros.")
        return

    filas = []
    for it in items:
        f = it.get("fecha")
        fs = formatear_fecha_ar(f) if f else "—"
        filas.append({
            "Fecha": fs,
            "Usuario": it.get("nombre") or it.get("usuario"),
            "Módulo": it.get("modulo"),
            "Acción": it.get("accion"),
            "Resumen": it.get("resumen"),
            "OK": "✅" if it.get("exito", True) else "❌",
            "Ref.": it.get("ref_id") or "",
        })
    st.caption(f"**{len(filas)}** evento(s)")
    st.dataframe(pd.DataFrame(filas), hide_index=True, use_container_width=True)

    with st.expander("Detalle del último evento filtrado", expanded=False):
        ult = items[0]
        st.json({
            "fecha": formatear_fecha_ar(ult.get("fecha")),
            "usuario": ult.get("usuario"),
            "vendedor_id": ult.get("vendedor_id"),
            "modulo": ult.get("modulo"),
            "accion": ult.get("accion"),
            "resumen": ult.get("resumen"),
            "exito": ult.get("exito"),
            "error": ult.get("error_msg"),
            "ref_id": ult.get("ref_id"),
            "detalle": ult.get("detalle"),
        })

    csv_buf = pd.DataFrame(filas).to_csv(index=False).encode("utf-8-sig")
    st.download_button(
        "⬇️ Exportar CSV",
        data=csv_buf,
        file_name=f"auditoria_{f_desde}_{f_hasta}.csv",
        mime="text/csv",
    )
