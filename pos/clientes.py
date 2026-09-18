"""Clientes para el POS: Firebase si hay claves, si no muestra local."""
from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

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
        "tipo_comprobante": str(data.get("tipo_comprobante") or "6"),
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


def validar_cuit_digitos(cuit: str) -> str:
    dig = re.sub(r"\D", "", str(cuit or ""))
    if len(dig) != 11:
        raise ValueError("CUIT debe tener 11 dígitos")
    if set(dig) <= {"0"}:
        raise ValueError("CUIT inválido")
    pesos = [5, 4, 3, 2, 7, 6, 5, 4, 3, 2]
    suma = sum(int(dig[i]) * pesos[i] for i in range(10))
    resto = 11 - (suma % 11)
    if resto == 11:
        verif = 0
    elif resto == 10:
        verif = 9
    else:
        verif = resto
    if int(dig[10]) != verif:
        raise ValueError("CUIT inválido (dígito verificador)")
    return dig


def _tipo_fc_por_iva(condicion_iva: str) -> str:
    c = str(condicion_iva or "").strip().lower()
    if "responsable inscripto" in c or c in ("ri", "iva ri", "inscripto"):
        return "1"
    return "6"


def _buscar_firebase_exacto(cuit: str) -> Optional[Dict[str, Any]]:
    if not firebase_disponible():
        return None
    try:
        snap = _db().collection("clientes").document(cuit).get()
        if snap.exists:
            return _activo(cuit, snap.to_dict() or {})
        # por si está indexado con otro id
        for d in (
            _db()
            .collection("clientes")
            .where("cuit_dni", "==", cuit)
            .limit(1)
            .stream()
        ):
            return _activo(d.id, d.to_dict() or {})
    except Exception:
        return None
    return None


def _guardar_cliente_firebase(cli: Dict[str, Any]) -> None:
    if not firebase_disponible():
        return
    dig = cli["cuit"]
    payload = {
        "nombre": cli["nombre"],
        "cuit_dni": dig,
        "descuento": float(cli.get("descuento") or 0),
        "tipo_comprobante": str(cli.get("tipo_comprobante") or "6"),
        "tipo_cliente": str(cli.get("tipo_cliente") or "ocasional"),
        "etiqueta_descuento": str(cli.get("etiqueta_descuento") or ""),
        "telefono": str(cli.get("telefono") or ""),
        "condicion_iva": str(cli.get("condicion_iva") or ""),
        "actualizado": datetime.now(timezone.utc),
        "origen": "pos_cuit",
    }
    _db().collection("clientes").document(dig).set(payload, merge=True)


def resolver_cuit(cuit: str) -> Dict[str, Any]:
    """Firebase primero; si no está, padrón ARCA/AFIP. Autocompleta nombre."""
    dig = validar_cuit_digitos(cuit)

    local = _buscar_firebase_exacto(dig)
    if local:
        if local.get("tipo_comprobante") not in ("1", "6"):
            local["tipo_comprobante"] = _tipo_fc_por_iva(local.get("condicion_iva") or "")
        return {
            "ok": True,
            "fuente": "firebase",
            "cliente": local,
            "mensaje": f"Cliente encontrado: {local['nombre']}",
        }

    from modulos.factura_arca_client import consultar_cuit

    cuit_emisor = os.getenv("POS_ARCA_CUIT", "20265010505").strip()
    clave_emisor = os.getenv("POS_ARCA_CLAVE", "111").strip()
    r = consultar_cuit(cuit_emisor, clave_emisor, dig)
    if not r.get("success"):
        raise RuntimeError(
            str(r.get("error") or "No se pudo consultar el padrón AFIP/ARCA")
        )
    data = r.get("data") or {}
    nombre = str(
        data.get("nombre")
        or data.get("razon_social")
        or data.get("denominacion")
        or ""
    ).strip().upper()
    if not nombre:
        raise RuntimeError("AFIP no devolvió el nombre del contribuyente")
    condicion = str(
        data.get("condicion_iva")
        or data.get("condicionIva")
        or data.get("iva")
        or ""
    ).strip()
    tipo_fc = str(data.get("tipo_comprobante") or _tipo_fc_por_iva(condicion))
    if tipo_fc not in ("1", "6"):
        tipo_fc = _tipo_fc_por_iva(condicion)

    cli = {
        "nombre": nombre,
        "cuit": dig,
        "descuento": 0.0,
        "tipo_comprobante": tipo_fc,
        "etiqueta_descuento": "",
        "tipo_cliente": "ocasional",
        "telefono": str(data.get("telefono") or ""),
        "condicion_iva": condicion,
    }
    try:
        _guardar_cliente_firebase(cli)
    except Exception as exc:
        print(f"[POS] No se pudo guardar cliente {dig}: {exc}", flush=True)

    return {
        "ok": True,
        "fuente": "arca",
        "cliente": cli,
        "mensaje": f"Cliente AFIP: {nombre}",
    }
