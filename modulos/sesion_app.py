"""Sesiones persistentes (Firestore + cookie) con timeout por inactividad."""
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional, Tuple

INACTIVIDAD_MINUTOS = 30
COOKIE_SESION = "hr_auth_token"
COOKIE_DIAS = 30


def _ahora_utc():
    return datetime.now(timezone.utc)


def _a_utc(val):
    if val is None:
        return None
    if isinstance(val, datetime):
        return val if val.tzinfo else val.replace(tzinfo=timezone.utc)
    if hasattr(val, "timestamp"):
        return datetime.fromtimestamp(val.timestamp(), tz=timezone.utc)
    return None


def crear_sesion(usuario: str, rol: str, nombre: str, vendedor_id: Optional[str] = None) -> str:
    from modulos.db_firebase import get_db

    token = secrets.token_urlsafe(32)
    ahora = _ahora_utc()
    get_db().collection("sesiones_app").document(token).set({
        "usuario": str(usuario),
        "rol": str(rol),
        "nombre": str(nombre),
        "vendedor_id": vendedor_id,
        "creado": ahora,
        "ultima_actividad": ahora,
        "activa": True,
    })
    return token


def _datos_desde_doc(data: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "usuario": data.get("usuario"),
        "rol": data.get("rol"),
        "nombre": data.get("nombre"),
        "vendedor_id": data.get("vendedor_id"),
    }


def validar_y_renovar_sesion(token: str) -> Tuple[bool, Optional[Dict[str, Any]]]:
    from modulos.db_firebase import get_db

    if not token:
        return False, None
    ref = get_db().collection("sesiones_app").document(str(token))
    doc = ref.get()
    if not doc.exists:
        return False, None
    data = doc.to_dict() or {}
    if not data.get("activa", True):
        return False, None

    ultima = _a_utc(data.get("ultima_actividad"))
    if not ultima:
        return False, None
    if (_ahora_utc() - ultima) > timedelta(minutes=INACTIVIDAD_MINUTOS):
        ref.update({"activa": False})
        return False, None

    ahora = _ahora_utc()
    ref.update({"ultima_actividad": ahora})
    return True, _datos_desde_doc(data)


def cerrar_sesion_firestore(token: Optional[str]):
    if not token:
        return
    try:
        from modulos.db_firebase import get_db
        ref = get_db().collection("sesiones_app").document(str(token))
        if ref.get().exists:
            ref.update({"activa": False, "cerrada": _ahora_utc()})
    except Exception:
        pass
