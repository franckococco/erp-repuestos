"""Carga de inventario para el POS: Firebase si hay claves, si no muestra local."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Tuple

ROOT = Path(__file__).resolve().parent
REPO = ROOT.parent
SAMPLE = ROOT / "data" / "sample_inventory.json"

if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

STOCK_CRITICO_DEFAULT = 3

_MODO = "emulador"
_INVENTARIO: List[Dict[str, Any]] = []
_CARGADO = False
_ERROR_FIREBASE: str | None = None


def _cred_path() -> Path | None:
    env = os.getenv("FIREBASE_CREDENTIALS_PATH", "").strip()
    candidatos = []
    if env:
        candidatos.append(Path(env))
    candidatos.append(REPO / "firebase_claves.json")
    candidatos.append(ROOT / "firebase_claves.json")
    for p in candidatos:
        if p.is_file():
            return p
    return None


def _flatten_docs(docs) -> List[Dict[str, Any]]:
    from modulos.util_vehiculos import (
        normalizar_lista_vehiculos,
        vehiculos_a_texto,
        vehiculos_en_busqueda,
    )

    inventario: List[Dict[str, Any]] = []
    for d in docs:
        master = d.to_dict() or {}
        master_id = d.id
        vehs_master = normalizar_lista_vehiculos(master.get("vehiculos") or master.get("vehiculo"))
        veh_texto = vehiculos_a_texto(vehs_master)
        veh_busqueda = vehiculos_en_busqueda(vehs_master)

        if "variantes" not in master:
            marca_ant = master.get("marca", master.get("condicion", "GENERICO"))
            inventario.append(
                {
                    "id": f"{master_id}_{marca_ant}",
                    "id_maestro": master_id,
                    "codigo": master.get("codigo", master_id),
                    "descripcion": master.get("descripcion", ""),
                    "vehiculos": vehs_master,
                    "vehiculo": veh_texto,
                    "vehiculos_busqueda": veh_busqueda,
                    "marca": marca_ant,
                    "stock": master.get("stock", 0),
                    "stock_critico": int(master.get("stock_critico", STOCK_CRITICO_DEFAULT)),
                    "precio_venta": float(master.get("precio_venta", 0) or 0),
                    "ubicacion": master.get("ubicacion") or {},
                }
            )
            continue

        for marca, v_data in (master.get("variantes") or {}).items():
            inventario.append(
                {
                    "id": f"{master_id}_{marca}",
                    "id_maestro": master_id,
                    "codigo": master.get("codigo", master_id),
                    "descripcion": master.get("descripcion", ""),
                    "vehiculos": vehs_master,
                    "vehiculo": veh_texto,
                    "vehiculos_busqueda": veh_busqueda,
                    "marca": marca,
                    "stock": v_data.get("stock", 0),
                    "stock_critico": int(v_data.get("stock_critico", STOCK_CRITICO_DEFAULT)),
                    "precio_venta": float(v_data.get("precio_venta", 0) or 0),
                    "ubicacion": master.get("ubicacion") or {},
                }
            )
    return inventario


def _cargar_firebase(ruta: Path) -> List[Dict[str, Any]]:
    import firebase_admin
    from firebase_admin import credentials, firestore

    if not firebase_admin._apps:  # type: ignore[attr-defined]
        firebase_admin.initialize_app(credentials.Certificate(str(ruta)))
    db = firestore.client()
    docs = db.collection("productos").get()
    return _flatten_docs(docs)


def _cargar_sample() -> List[Dict[str, Any]]:
    data = json.loads(SAMPLE.read_text(encoding="utf-8"))
    for p in data:
        p.setdefault("id_maestro", str(p.get("codigo", "")))
        p.setdefault("vehiculos", [p.get("vehiculo")] if p.get("vehiculo") else [])
        p.setdefault("vehiculos_busqueda", " ".join(p.get("vehiculos") or []).upper())
    return data


def estado_conexion() -> Dict[str, Any]:
    cred = _cred_path()
    inv, modo = cargar_inventario()
    return {
        "modo": modo,
        "firebase": modo == "firebase",
        "tiene_claves": cred is not None,
        "error_firebase": _ERROR_FIREBASE,
        "ruta_claves": str(cred) if cred else None,
        "productos": len(inv),
        "donde_buscar": [
            str(REPO / "firebase_claves.json"),
            str(ROOT / "firebase_claves.json"),
            "o variable FIREBASE_CREDENTIALS_PATH",
        ],
        "instruccion": (
            "Inventario Firebase activo."
            if modo == "firebase"
            else (
                "Copiá firebase_claves.json a la carpeta erp-repuestos "
                "(junto a app.py) y tocá «Conectar Firebase»."
            )
        ),
    }


def cargar_inventario(force: bool = False) -> Tuple[List[Dict[str, Any]], str]:
    """Devuelve (items, modo) donde modo es 'firebase' o 'emulador'."""
    global _INVENTARIO, _MODO, _CARGADO, _ERROR_FIREBASE
    if _CARGADO and not force:
        return _INVENTARIO, _MODO

    cred = _cred_path()
    if cred is not None:
        try:
            _INVENTARIO = _cargar_firebase(cred)
            _MODO = "firebase"
            _CARGADO = True
            _ERROR_FIREBASE = None
            print(f"[POS] Firebase OK · {len(_INVENTARIO)} productos · {cred}", flush=True)
            return _INVENTARIO, _MODO
        except Exception as exc:
            print(f"[POS] Firebase falló ({exc}); uso inventario de muestra.", flush=True)
            _MODO = "emulador"
            _ERROR_FIREBASE = str(exc)
            # no marcar _CARGADO con firebase fallido permanente si force:
            # cargamos sample pero permitimos reintentar
    else:
        _ERROR_FIREBASE = None

    _INVENTARIO = _cargar_sample()
    _MODO = "emulador"
    _CARGADO = True
    return _INVENTARIO, _MODO


def forzar_recarga() -> Dict[str, Any]:
    """Vuelve a leer claves e inventario (útil apenas copiás firebase_claves.json)."""
    global _CARGADO, _INVENTARIO, _MODO, _ERROR_FIREBASE
    _CARGADO = False
    _INVENTARIO = []
    inv, modo = cargar_inventario(force=True)
    return estado_conexion() | {"ok": True, "modo": modo, "inventario": len(inv)}


def buscar(q: str, limite: int = 20) -> List[Dict[str, Any]]:
    from modulos.util_busqueda import (
        buscar_codigo_exacto_inventario,
        buscar_en_inventario_mostrador,
    )

    inv, _ = cargar_inventario()
    term = (q or "").strip()
    if not term:
        return []
    exactos = buscar_codigo_exacto_inventario(inv, term)
    if exactos:
        return exactos[:limite]
    return buscar_en_inventario_mostrador(inv, term)[:limite]


def producto_por_id(item_id: str) -> Dict[str, Any] | None:
    inv, _ = cargar_inventario()
    return next((p for p in inv if p.get("id") == item_id), None)


def descontar_stock_local(items: List[Dict[str, Any]]) -> None:
    """Actualiza el inventario en memoria sin volver a descargar todo Firebase."""
    descuentos: Dict[str, int] = {}
    for item in items:
        if item.get("manual"):
            continue
        item_id = str(item.get("id") or "")
        if item_id:
            descuentos[item_id] = descuentos.get(item_id, 0) + max(
                1, int(item.get("cantidad") or 1)
            )
    for producto in _INVENTARIO:
        item_id = str(producto.get("id") or "")
        if item_id in descuentos:
            producto["stock"] = max(
                0, int(producto.get("stock") or 0) - descuentos[item_id]
            )


def _sanitizar_marca(marca: str) -> str:
    import re

    m = str(marca or "GENERICO").strip().upper()
    m = re.sub(r"[.\/\[\]\*]", "_", m)
    m = re.sub(r"_+", "_", m).strip("_")
    if not m or m.startswith("__"):
        m = "GENERICO"
    return m[:60]


def crear_producto_pos(
    *,
    codigo: str,
    descripcion: str,
    precio_venta: float,
    marca: str = "GENERICO",
    stock: int = 0,
    creado_por: str = "",
) -> Dict[str, Any]:
    """Alta formal en Firebase desde el POS. Rechaza código duplicado."""
    global _INVENTARIO, _CARGADO, _MODO

    codigo_base = str(codigo or "").strip().upper().replace("/", "-")
    desc = str(descripcion or "").strip().upper()
    marca_limpia = _sanitizar_marca(marca)
    precio = max(0.0, float(precio_venta or 0))
    stock_n = max(0, int(stock or 0))

    if not codigo_base or len(codigo_base) < 2:
        raise ValueError("Código inválido (mínimo 2 caracteres)")
    if not desc:
        raise ValueError("Descripción obligatoria")
    if precio <= 0:
        raise ValueError("El precio de venta debe ser mayor a cero")

    ruta = _cred_path()
    if ruta is None:
        raise RuntimeError("Sin Firebase: no se puede dar de alta el producto")

    import firebase_admin
    from firebase_admin import credentials, firestore
    from datetime import datetime, timezone

    if not firebase_admin._apps:  # type: ignore[attr-defined]
        firebase_admin.initialize_app(credentials.Certificate(str(ruta)))
    db = firestore.client()

    ref = db.collection("productos").document(codigo_base)
    if ref.get().exists:
        raise ValueError(
            f"El código {codigo_base} ya existe. Buscalo en el buscador."
        )
    existentes = (
        db.collection("productos").where("codigo", "==", codigo_base).limit(1).get()
    )
    if existentes:
        raise ValueError(
            f"El código {codigo_base} ya existe. Buscalo en el buscador."
        )

    ahora = datetime.now(timezone.utc)
    payload = {
        "codigo": codigo_base,
        "descripcion": desc,
        "vehiculos": [],
        "vehiculo": "",
        "ubicacion": {
            "pasillo": 0,
            "piso": 0,
            "modulo": 0,
            "fila": 0,
            "fondo": 0,
        },
        "origen": "pos_caja",
        "creado_por": str(creado_por or "").strip() or "POS",
        "ultima_actualizacion": ahora,
        "variantes": {
            marca_limpia: {
                "stock": stock_n,
                "stock_critico": STOCK_CRITICO_DEFAULT,
                "ultimo_costo_base": 0.0,
                "precio_interno": precio,
                "precio_venta": precio,
                "proveedor": "POS",
                "cuit_proveedor": "0",
            }
        },
    }
    ref.set(payload)

    item = {
        "id": f"{codigo_base}_{marca_limpia}",
        "id_maestro": codigo_base,
        "codigo": codigo_base,
        "descripcion": desc,
        "vehiculos": [],
        "vehiculo": "",
        "vehiculos_busqueda": "",
        "marca": marca_limpia,
        "stock": stock_n,
        "stock_critico": STOCK_CRITICO_DEFAULT,
        "precio_venta": precio,
        "ubicacion": payload["ubicacion"],
    }
    # Actualizar caché local sin recargar todo
    _INVENTARIO = [p for p in _INVENTARIO if p.get("id") != item["id"]]
    _INVENTARIO.append(item)
    _CARGADO = True
    _MODO = "firebase"
    return item

