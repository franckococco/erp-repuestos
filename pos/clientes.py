"""Clientes para el POS: Firebase si hay claves, si no muestra local."""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict, List

from inventory import _cred_path

ROOT = Path(__file__).resolve().parent
SAMPLE = ROOT / "data" / "sample_clientes.json"


def firebase_disponible() -> bool:
    return _cred_path() is not None


def _db():
    import firebase_admin
    from firebase_admin import credentials, firestore

    ruta = _cred_path()
    if ruta is None:
        raise RuntimeError("Sin credenciales Firebase")
    if not firebase_admin._apps:  # type: ignore[attr-defined]
        firebase_admin.initialize_app(credentials.Certificate(str(ruta)))
    return firestore.client()


def _activo(cuit: str, data: Dict[str, Any]) -> Dict[str, Any]:
    dig = re.sub(r"\D", "", str(cuit or data.get("cuit") or data.get("cuit_dni") or "")) or "00000000000"
    return {
        "nombre": str(data.get("nombre") or "CONSUMIDOR FINAL").upper(),
        "cuit": dig,
        "descuento": float(data.get("descuento") or 0),
        "tipo_comprobante": "6",
        "etiqueta_descuento": str(data.get("etiqueta_descuento") or ""),
        "tipo_cliente": str(data.get("tipo_cliente") or "ocasional"),
        "telefono": str(data.get("telefono") or ""),
        "condicion_iva": str(data.get("condicion_iva") or ""),
    }


def _sample() -> List[Dict[str, Any]]:
    if not SAMPLE.exists():
        return []
    raw = json.loads(SAMPLE.read_text(encoding="utf-8"))
    return [_activo(c.get("cuit", ""), c) for c in raw]


def buscar(termino: str, limite: int = 12) -> List[Dict[str, Any]]:
    t = (termino or "").strip()
    if not t:
        return []
    dig = re.sub(r"\D", "", t)
    up = t.upper()
    hits: List[Dict[str, Any]] = []

    fuente: List[Dict[str, Any]] = []
    if firebase_disponible():
        try:
            for d in _db().collection("clientes").limit(500).stream():
                fuente.append(_activo(d.id, d.to_dict() or {}))
        except Exception:
            fuente = _sample()
    else:
        fuente = _sample()

    for c in fuente:
        nombre = c["nombre"]
        cuit = c["cuit"]
        if dig and dig in cuit:
            hits.append(c)
        elif up and up in nombre:
            hits.append(c)
        if len(hits) >= limite:
            break
    return hits
