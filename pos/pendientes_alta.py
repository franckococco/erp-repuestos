"""Cola compartida de ítems manuales pendientes de alta definitiva."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List


def _firebase_ok() -> bool:
    try:
        from inventory import _cred_path

        return _cred_path() is not None
    except Exception:
        return False


def crear_pendiente_desde_item(
    item: Dict[str, Any],
    *,
    vendedor: str,
) -> str:
    """Crea pendiente en Firebase si el ítem es manual. No rompe la venta si falla."""
    if not item:
        return ""
    existente = str(item.get("pendiente_alta_id") or "").strip()
    if existente:
        return existente
    es_manual = bool(item.get("manual")) or str(item.get("id") or "").upper().startswith(
        "MANUAL_"
    )
    if not es_manual:
        return ""
    if not _firebase_ok():
        return ""
    try:
        from modulos.db_firebase import crear_pendiente_alta_manual

        pid = crear_pendiente_alta_manual(
            vendedor,
            descripcion=str(item.get("descripcion") or ""),
            precio_unitario=float(item.get("precio_unitario") or 0),
            cantidad=max(1, int(item.get("cantidad") or 1)),
            codigo=str(item.get("codigo") or ""),
            marca=str(item.get("marca") or "MANUAL"),
            item_carrito_id=str(item.get("id") or ""),
        )
        if pid:
            item["pendiente_alta_id"] = pid
        return str(pid or "")
    except Exception as exc:
        print(f"[POS] pendiente alta no creada: {exc}", flush=True)
        return ""


def asegurar_pendientes_carrito(
    carrito: List[Dict[str, Any]],
    *,
    vendedor: str,
) -> int:
    n = 0
    for it in carrito or []:
        if not isinstance(it, dict):
            continue
        if crear_pendiente_desde_item(it, vendedor=vendedor):
            n += 1
    return n


def vincular_pendientes_venta(
    carrito: List[Dict[str, Any]],
    *,
    comprobante_id: str,
    nro: str,
) -> None:
    if not _firebase_ok():
        return
    try:
        from modulos.db_firebase import vincular_pendientes_alta_a_factura

        vincular_pendientes_alta_a_factura(carrito, comprobante_id, nro)
    except Exception as exc:
        print(f"[POS] vincular pendientes: {exc}", flush=True)


def listar_pendientes(solo_abiertos: bool = True, limite: int = 80) -> List[Dict[str, Any]]:
    if not _firebase_ok():
        return []
    try:
        from modulos.db_firebase import listar_pendientes_alta_manual

        rows = listar_pendientes_alta_manual(solo_abiertos=solo_abiertos, limite=limite)
    except Exception as exc:
        print(f"[POS] listar pendientes: {exc}", flush=True)
        return []
    out: List[Dict[str, Any]] = []
    for r in rows or []:
        creado = r.get("creado")
        if hasattr(creado, "isoformat"):
            creado_txt = creado.isoformat()
        else:
            creado_txt = str(creado or "")
        out.append(
            {
                "id": str(r.get("id") or ""),
                "descripcion": str(r.get("descripcion") or ""),
                "marca": str(r.get("marca") or ""),
                "codigo": str(r.get("codigo") or ""),
                "precio_unitario": float(r.get("precio_unitario") or 0),
                "cantidad": int(r.get("cantidad") or 1),
                "vendedor": str(r.get("vendedor") or ""),
                "estado": str(r.get("estado") or "pendiente"),
                "nro_factura": str(r.get("nro_factura") or ""),
                "comprobante_id": str(r.get("comprobante_id") or ""),
                "creado": creado_txt,
            }
        )
    return out


def cargar_definitivo(
    pendiente_id: str,
    *,
    codigo: str,
    descripcion: str = "",
    precio_venta: float,
    marca: str = "GENERICO",
    stock: int = 0,
    resuelto_por: str = "",
) -> Dict[str, Any]:
    """Alta formal en inventario + marca el pendiente como resuelto."""
    pid = str(pendiente_id or "").strip()
    if not pid:
        raise ValueError("Pendiente inválido")
    if not _firebase_ok():
        raise RuntimeError("Sin Firebase: no se puede cargar el producto")

    from modulos.db_firebase import get_db, resolver_pendiente_alta_manual
    from productos_alta import crear_producto_pos

    doc = get_db().collection("pendientes_alta_manual").document(pid).get()
    if not doc.exists:
        raise ValueError("Pendiente no encontrado")
    pendiente = doc.to_dict() or {}
    estado = str(pendiente.get("estado") or "pendiente").lower()
    if estado not in ("pendiente", "abierto"):
        raise ValueError("Ese pendiente ya fue resuelto")

    desc = (descripcion or pendiente.get("descripcion") or "").strip()
    marca_in = (marca or pendiente.get("marca") or "GENERICO").strip() or "GENERICO"
    precio = float(precio_venta if precio_venta is not None else pendiente.get("precio_unitario") or 0)
    if precio <= 0:
        precio = float(pendiente.get("precio_unitario") or 0)
    if precio <= 0:
        raise ValueError("Precio de venta inválido")

    prod = crear_producto_pos(
        codigo=codigo,
        descripcion=desc,
        precio_venta=precio,
        marca=marca_in,
        stock=max(0, int(stock or 0)),
        creado_por=resuelto_por or str(pendiente.get("vendedor") or "POS"),
    )
    ok, msj = resolver_pendiente_alta_manual(pid, resuelto_por=resuelto_por)
    if not ok:
        raise RuntimeError(msj or "No se pudo marcar el pendiente como resuelto")
    return {
        "ok": True,
        "mensaje": f"Producto {prod.get('codigo')} cargado. Pendiente cerrado.",
        "producto": prod,
        "pendiente_id": pid,
        "resuelto_en": datetime.now(timezone.utc).isoformat(),
    }
