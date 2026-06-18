"""Usuarios de la app (login, claves hasheadas) en Firestore."""
import hashlib
import secrets
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from modulos.db_firebase import get_db
from modulos.puntos_vendedor import asegurar_vendedor

CLAVE_INICIAL = "111"
_PBKDF2_ITER = 120_000

USUARIOS_PREDEFINIDOS: List[Dict[str, Any]] = [
    {"usuario": "admin", "nombre": "Administrador", "rol": "admin", "vendedor_id": None},
    {"usuario": "fernando", "nombre": "Fernando", "rol": "vendedor", "vendedor_id": "fernando"},
    {"usuario": "emilio", "nombre": "Emilio", "rol": "vendedor", "vendedor_id": "emilio"},
    {"usuario": "facundo", "nombre": "Facundo", "rol": "vendedor", "vendedor_id": "facundo"},
    {"usuario": "gabriel", "nombre": "Gabriel", "rol": "vendedor", "vendedor_id": "gabriel"},
    {"usuario": "damian", "nombre": "Damian", "rol": "vendedor", "vendedor_id": "damian"},
]


def _slug_usuario(usuario: str) -> str:
    return str(usuario or "").strip().lower()[:40]


def hash_clave(clave: str, salt: Optional[str] = None) -> Tuple[str, str]:
    sal = salt or secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256",
        str(clave or "").encode("utf-8"),
        sal.encode("utf-8"),
        _PBKDF2_ITER,
    )
    return sal, digest.hex()


def verificar_clave(clave: str, salt: str, hash_hex: str) -> bool:
    if not salt or not hash_hex:
        return False
    _, calc = hash_clave(clave, salt)
    return secrets.compare_digest(calc, str(hash_hex))


def inicializar_usuarios_predeterminados():
    """Crea usuarios faltantes con clave inicial 111 (no pisa claves existentes)."""
    col = get_db().collection("usuarios_app")
    for u in USUARIOS_PREDEFINIDOS:
        uid = _slug_usuario(u["usuario"])
        ref = col.document(uid)
        if ref.get().exists:
            continue
        sal, h = hash_clave(CLAVE_INICIAL)
        ref.set({
            "usuario": uid,
            "nombre": u["nombre"],
            "rol": u["rol"],
            "vendedor_id": u.get("vendedor_id"),
            "clave_salt": sal,
            "clave_hash": h,
            "activo": True,
            "creado": datetime.now(timezone.utc),
            "actualizado": datetime.now(timezone.utc),
        })
        if u["rol"] == "vendedor" and u.get("vendedor_id"):
            asegurar_vendedor(u["vendedor_id"], nombre=u["nombre"], rol="vendedor")


def obtener_usuario_db(usuario: str) -> Optional[Dict[str, Any]]:
    uid = _slug_usuario(usuario)
    if not uid:
        return None
    doc = get_db().collection("usuarios_app").document(uid).get()
    if not doc.exists:
        return None
    data = doc.to_dict() or {}
    if not data.get("activo", True):
        return None
    return {"id": doc.id, **data}


def listar_usuarios_db():
    items = []
    for doc in get_db().collection("usuarios_app").stream():
        data = doc.to_dict() or {}
        items.append({"id": doc.id, **data})
    items.sort(key=lambda x: (0 if x.get("rol") == "admin" else 1, str(x.get("nombre", ""))))
    return items


def validar_credenciales(usuario: str, clave: str) -> Tuple[bool, Any]:
    u = obtener_usuario_db(usuario)
    if not u:
        return False, "Usuario o clave incorrectos."
    if not verificar_clave(clave, u.get("clave_salt", ""), u.get("clave_hash", "")):
        return False, "Usuario o clave incorrectos."
    return True, {
        "usuario": u["id"],
        "nombre": u.get("nombre", u["id"]),
        "rol": u.get("rol", "vendedor"),
        "vendedor_id": u.get("vendedor_id"),
    }


def cambiar_clave_usuario(usuario: str, clave_actual: str, clave_nueva: str) -> Tuple[bool, str]:
    uid = _slug_usuario(usuario)
    if len(str(clave_nueva or "")) < 4:
        return False, "La clave nueva debe tener al menos 4 caracteres."
    u = obtener_usuario_db(uid)
    if not u:
        return False, "Usuario no encontrado."
    if not verificar_clave(clave_actual, u.get("clave_salt", ""), u.get("clave_hash", "")):
        return False, "La clave actual no es correcta."
    sal, h = hash_clave(clave_nueva)
    get_db().collection("usuarios_app").document(uid).update({
        "clave_salt": sal,
        "clave_hash": h,
        "actualizado": datetime.now(timezone.utc),
    })
    return True, "Clave actualizada."


def resetear_clave_usuario(usuario: str) -> Tuple[bool, str]:
    """Solo para uso desde panel admin."""
    uid = _slug_usuario(usuario)
    if uid == "admin":
        return False, "No se puede resetear el admin desde aquí."
    u = obtener_usuario_db(uid)
    if not u:
        return False, "Usuario no encontrado."
    sal, h = hash_clave(CLAVE_INICIAL)
    get_db().collection("usuarios_app").document(uid).update({
        "clave_salt": sal,
        "clave_hash": h,
        "actualizado": datetime.now(timezone.utc),
    })
    return True, f"Clave de {u.get('nombre', uid)} restablecida a {CLAVE_INICIAL}."
