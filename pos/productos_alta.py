"""Alta formal de productos desde el POS (mismo formato que depósito)."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict

from inventory import STOCK_CRITICO_DEFAULT, _cred_path, forzar_recarga, producto_por_id


def crear_producto_pos(
    *,
    codigo: str,
    descripcion: str,
    precio_venta: float,
    marca: str = "GENERICO",
    stock: int = 0,
    creado_por: str = "",
) -> Dict[str, Any]:
    """
    Alta en Firebase con el mismo esquema que depósito (`alta_manual_producto`).
    Rechaza código duplicado. Avisa al admin (alerta alta_pos).
    """
    codigo_base = str(codigo or "").strip().upper().replace("/", "-")
    desc = str(descripcion or "").strip().upper()
    marca_in = str(marca or "GENERICO").strip() or "GENERICO"
    precio = max(0.0, float(precio_venta or 0))
    stock_n = max(0, int(stock or 0))
    quien = str(creado_por or "").strip() or "POS"

    if not codigo_base or len(codigo_base) < 2:
        raise ValueError("Código inválido (mínimo 2 caracteres)")
    if not desc:
        raise ValueError("Descripción obligatoria")
    if precio <= 0:
        raise ValueError("El precio de venta debe ser mayor a cero")
    if _cred_path() is None:
        raise RuntimeError("Sin Firebase: no se puede dar de alta el producto")

    from modulos.db_firebase import alta_manual_producto, sanitizar_clave_marca

    marca_limpia = sanitizar_clave_marca(marca_in)

    import firebase_admin
    from firebase_admin import credentials, firestore

    ruta = _cred_path()
    if not firebase_admin._apps:  # type: ignore[attr-defined]
        firebase_admin.initialize_app(credentials.Certificate(str(ruta)))
    db = firestore.client()

    ref = db.collection("productos").document(codigo_base)
    if ref.get().exists:
        raise ValueError(f"El código {codigo_base} ya existe. Buscalo en el buscador.")
    existentes = (
        db.collection("productos").where("codigo", "==", codigo_base).limit(1).get()
    )
    if existentes:
        raise ValueError(f"El código {codigo_base} ya existe. Buscalo en el buscador.")

    ok, msj = alta_manual_producto(
        codigo_base,
        marca_limpia,
        "UNIVERSAL",
        desc,
        "0",
        precio,
        0.0,
        stock_n,
        0,
        0,
        0,
        0,
        0,
        STOCK_CRITICO_DEFAULT,
    )
    if not ok:
        raise ValueError(msj or "No se pudo dar de alta el producto")

    ahora = datetime.now(timezone.utc)
    ref.update(
        {
            "origen": "pos_caja",
            "creado_por": quien,
            "creado_en": ahora,
            "ultima_actualizacion": ahora,
            f"variantes.{marca_limpia}.precio_venta": precio,
            f"variantes.{marca_limpia}.precio_interno": precio,
            f"variantes.{marca_limpia}.ultimo_costo_base": precio,
            f"variantes.{marca_limpia}.proveedor": "POS",
            f"variantes.{marca_limpia}.cuit_proveedor": "0",
        }
    )

    forzar_recarga()
    item = producto_por_id(f"{codigo_base}_{marca_limpia}")
    if not item:
        item = {
            "id": f"{codigo_base}_{marca_limpia}",
            "id_maestro": codigo_base,
            "codigo": codigo_base,
            "descripcion": desc,
            "vehiculos": ["UNIVERSAL"],
            "vehiculo": "UNIVERSAL",
            "vehiculos_busqueda": "UNIVERSAL",
            "marca": marca_limpia,
            "stock": stock_n,
            "stock_critico": STOCK_CRITICO_DEFAULT,
            "precio_venta": precio,
            "ubicacion": {
                "pasillo": 0,
                "piso": 0,
                "modulo": 0,
                "fila": 0,
                "fondo": 0,
            },
        }

    try:
        from ventas import registrar_alerta_alta_producto

        registrar_alerta_alta_producto(
            codigo=codigo_base,
            descripcion=desc,
            marca=marca_limpia,
            precio=precio,
            stock=stock_n,
            vendedor=quien,
        )
    except Exception as exc:
        print(f"[POS] No se pudo registrar alerta de alta: {exc}", flush=True)

    return item
