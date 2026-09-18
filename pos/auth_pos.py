"""Autenticación y roles del POS, compatible con usuarios del ERP anterior."""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import time
from datetime import datetime, timezone
from typing import Any, Dict, Optional, Tuple

from inventory import _cred_path

PBKDF2_ITER = 120_000
CLAVE_INICIAL = "111"
USUARIOS = [
    {"usuario": "admin", "nombre": "Administrador", "rol": "admin", "vendedor_id": None},
    {"usuario": "fernando", "nombre": "Fernando", "rol": "vendedor", "vendedor_id": "fernando"},
    {"usuario": "emilio", "nombre": "Emilio", "rol": "vendedor", "vendedor_id": "emilio"},
    {"usuario": "facundo", "nombre": "Facundo", "rol": "vendedor", "vendedor_id": "facundo"},
    {"usuario": "gabriel", "nombre": "Gabriel", "rol": "vendedor", "vendedor_id": "gabriel"},
    {"usuario": "damian", "nombre": "Damian", "rol": "vendedor", "vendedor_id": "damian"},
]


def _db():
    import firebase_admin
    from firebase_admin import credentials, firestore

    ruta = _cred_path()
    if ruta is None:
        raise RuntimeError("Sin credenciales Firebase")
    if not firebase_admin._apps:  # type: ignore[attr-defined]
        firebase_admin.initialize_app(credentials.Certificate(str(ruta)))
    return firestore.client()


def _hash(clave: str, salt: Optional[str] = None) -> Tuple[str, str]:
    sal = salt or secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256", str(clave).encode("utf-8"), sal.encode("utf-8"), PBKDF2_ITER
    )
    return sal, digest.hex()


def inicializar_usuarios() -> None:
    db = _db()
    col = db.collection("usuarios_app")
    for usuario in USUARIOS:
        ref = col.document(usuario["usuario"])
        if not ref.get().exists:
            salt, clave_hash = _hash(CLAVE_INICIAL)
            ref.set(
                {
                    **usuario,
                    "clave_salt": salt,
                    "clave_hash": clave_hash,
                    "activo": True,
                    "creado": datetime.now(timezone.utc),
                    "actualizado": datetime.now(timezone.utc),
                }
            )
        if usuario["rol"] == "vendedor":
            vendedor_ref = db.collection("vendedores").document(usuario["vendedor_id"])
            if not vendedor_ref.get().exists:
                vendedor_ref.set(
                    {
                        "nombre": usuario["nombre"],
                        "rol": "vendedor",
                        "puntos": 0,
                        "ventas_acumuladas": 0.0,
                        "activo": True,
                        "creado": datetime.now(timezone.utc),
                    }
                )


def validar_credenciales(usuario: str, clave: str) -> Optional[Dict[str, Any]]:
    uid = str(usuario or "").strip().lower()[:40]
    if not uid or not clave:
        return None
    doc = _db().collection("usuarios_app").document(uid).get()
    if not doc.exists:
        return None
    data = doc.to_dict() or {}
    if not data.get("activo", True):
        return None
    _, calculado = _hash(clave, str(data.get("clave_salt") or ""))
    if not secrets.compare_digest(calculado, str(data.get("clave_hash") or "")):
        return None
    return {
        "usuario": uid,
        "nombre": str(data.get("nombre") or uid),
        "rol": str(data.get("rol") or "vendedor"),
        "vendedor_id": data.get("vendedor_id") or uid,
        "debe_cambiar_clave": str(clave) == CLAVE_INICIAL,
    }


def _session_secret() -> bytes:
    valor = (
        os.getenv("POS_SESSION_SECRET", "").strip()
        or os.getenv("POS_ACCESS_PASSWORD", "").strip()
        or "hafid-pos-local"
    )
    return valor.encode("utf-8")


def crear_token(usuario: Dict[str, Any]) -> str:
    payload = {
        **usuario,
        "exp": int(time.time()) + 12 * 60 * 60,
    }
    raw = base64.urlsafe_b64encode(
        json.dumps(payload, separators=(",", ":")).encode("utf-8")
    ).decode("ascii").rstrip("=")
    firma = hmac.new(_session_secret(), raw.encode("ascii"), hashlib.sha256).hexdigest()
    return f"{raw}.{firma}"


def leer_token(token: str) -> Optional[Dict[str, Any]]:
    try:
        raw, firma = str(token or "").split(".", 1)
        esperada = hmac.new(
            _session_secret(), raw.encode("ascii"), hashlib.sha256
        ).hexdigest()
        if not secrets.compare_digest(firma, esperada):
            return None
        padded = raw + "=" * (-len(raw) % 4)
        data = json.loads(base64.urlsafe_b64decode(padded).decode("utf-8"))
        if int(data.get("exp") or 0) < int(time.time()):
            return None
        return data
    except Exception:
        return None


def cambiar_clave(usuario: str, actual: str, nueva: str) -> Tuple[bool, str]:
    if len(str(nueva or "")) < 4:
        return False, "La nueva clave debe tener al menos 4 caracteres"
    datos = validar_credenciales(usuario, actual)
    if not datos:
        return False, "La clave actual no es correcta"
    salt, clave_hash = _hash(nueva)
    _db().collection("usuarios_app").document(datos["usuario"]).update(
        {
            "clave_salt": salt,
            "clave_hash": clave_hash,
            "actualizado": datetime.now(timezone.utc),
        }
    )
    return True, "Clave actualizada"


def crear_usuario(
    usuario: str,
    nombre: str,
    rol: str = "vendedor",
    clave: str = CLAVE_INICIAL,
) -> Tuple[bool, str]:
    uid = str(usuario or "").strip().lower()[:40]
    nom = str(nombre or "").strip()[:80] or uid.title()
    rol_n = str(rol or "vendedor").strip().lower()
    if rol_n not in ("admin", "vendedor"):
        return False, "Rol inválido"
    if not uid or not re.match(r"^[a-z0-9._-]{3,40}$", uid):
        return False, "Usuario inválido (3-40, letras/números/._-)"
    if len(str(clave or "")) < 4:
        return False, "La clave debe tener al menos 4 caracteres"
    ref = _db().collection("usuarios_app").document(uid)
    if ref.get().exists:
        return False, "Ese usuario ya existe"
    salt, clave_hash = _hash(clave)
    ahora = datetime.now(timezone.utc)
    vendedor_id = uid if rol_n == "vendedor" else None
    ref.set(
        {
            "usuario": uid,
            "nombre": nom,
            "rol": rol_n,
            "vendedor_id": vendedor_id,
            "clave_salt": salt,
            "clave_hash": clave_hash,
            "activo": True,
            "creado": ahora,
            "actualizado": ahora,
        }
    )
    if rol_n == "vendedor":
        _db().collection("vendedores").document(uid).set(
            {
                "nombre": nom,
                "rol": "vendedor",
                "puntos": 0,
                "ventas_acumuladas": 0.0,
                "activo": True,
                "creado": ahora,
            },
            merge=True,
        )
    return True, f"Usuario {uid} creado"


def listar_usuarios_admin() -> list[Dict[str, Any]]:
    docs = _db().collection("usuarios_app").stream()
    out = []
    for doc in docs:
        data = doc.to_dict() or {}
        if not data.get("activo", True):
            continue
        out.append(
            {
                "usuario": doc.id,
                "nombre": data.get("nombre") or doc.id,
                "rol": data.get("rol") or "vendedor",
                "vendedor_id": data.get("vendedor_id"),
            }
        )
    out.sort(key=lambda x: str(x["usuario"]))
    return out


def resumen_puntos_admin() -> list[Dict[str, Any]]:
    ids_validos = {
        str(u["vendedor_id"]) for u in USUARIOS if u["rol"] == "vendedor"
    }
    docs = _db().collection("vendedores").stream()
    out = []
    for doc in docs:
        data = doc.to_dict() or {}
        if doc.id not in ids_validos or not data.get("activo", True):
            continue
        acum = float(data.get("ventas_acumuladas") or 0)
        out.append(
            {
                "id": doc.id,
                "nombre": data.get("nombre") or doc.id,
                "puntos": int(data.get("puntos") or 0),
                "ventas_acumuladas": acum,
                "faltan_proximo": max(0.0, 100_000.0 - acum),
            }
        )
    out.sort(key=lambda x: str(x["nombre"]).upper())
    return out
