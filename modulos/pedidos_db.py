"""Persistencia de pedidos a proveedores en Firestore."""
from datetime import datetime, timezone

from modulos.db_firebase import (
    get_db,
    normalizar_codigo_proveedor,
    sanitizar_clave_marca,
    clave_linea_factura,
)


def _id_item_pedido(codigo, marca):
    k = clave_linea_factura(codigo, marca)
    return k.replace("|", "__").replace("/", "-")[:150]


def crear_pedido(cuit, nombre_proveedor, items, notas=""):
    cuit_l = "".join(filter(str.isdigit, str(cuit)))
    if len(cuit_l) != 11:
        return False, "CUIT inválido.", None

    lineas = []
    for art in items or []:
        if not isinstance(art, dict):
            continue
        cod = normalizar_codigo_proveedor(art.get("codigo", ""))
        if not cod:
            continue
        try:
            cant = int(art.get("cantidad", 0))
        except (TypeError, ValueError):
            cant = 0
        if cant <= 0:
            continue
        marca = sanitizar_clave_marca(art.get("marca", "GENERICO"))
        lineas.append({
            "codigo_proveedor": cod,
            "descripcion": str(art.get("descripcion", "")).strip(),
            "marca": marca,
            "cantidad_pedida": cant,
            "precio_estimado": float(art.get("precio_estimado", 0) or 0),
        })

    if not lineas:
        return False, "El pedido no tiene ítems válidos.", None

    ahora = datetime.now(timezone.utc)
    pedido_id = f"PED_{cuit_l}_{ahora.strftime('%Y%m%d_%H%M%S')}"
    db = get_db()
    ref_ped = db.collection("pedidos").document(pedido_id)

    ref_ped.set({
        "cuit_proveedor": cuit_l,
        "nombre_proveedor": str(nombre_proveedor or "").strip().upper(),
        "fecha_pedido": ahora,
        "estado": "abierto",
        "notas": str(notas or "").strip(),
        "item_count": len(lineas),
    })

    batch = db.batch()
    for ln in lineas:
        item_id = _id_item_pedido(ln["codigo_proveedor"], ln["marca"])
        batch.set(ref_ped.collection("items").document(item_id), ln)
    batch.commit()

    return True, f"Pedido {pedido_id} guardado ({len(lineas)} ítems).", pedido_id


def listar_pedidos(cuit=None, solo_abiertos=True):
    docs = get_db().collection("pedidos").limit(200).get()
    resultado = []
    cuit_f = "".join(filter(str.isdigit, str(cuit or "")))
    for doc in docs:
        data = doc.to_dict() or {}
        if cuit_f and data.get("cuit_proveedor") != cuit_f:
            continue
        if solo_abiertos and data.get("estado") not in ("abierto", "parcial"):
            continue
        data["id"] = doc.id
        resultado.append(data)
    resultado.sort(
        key=lambda x: x.get("fecha_pedido") or datetime.min.replace(tzinfo=timezone.utc),
        reverse=True,
    )
    return resultado[:100]


def obtener_items_pedido(pedido_id):
    docs = get_db().collection("pedidos").document(pedido_id).collection("items").get()
    return [d.to_dict() for d in docs if d.to_dict()]


def obtener_pedido_completo(pedido_id):
    ref = get_db().collection("pedidos").document(pedido_id)
    doc = ref.get()
    if not doc.exists:
        return None
    data = doc.to_dict() or {}
    data["id"] = doc.id
    data["items"] = obtener_items_pedido(pedido_id)
    return data


def cerrar_pedido(pedido_id):
    ref = get_db().collection("pedidos").document(pedido_id)
    if not ref.get().exists:
        return False, "Pedido no encontrado."
    ref.update({"estado": "cerrado", "fecha_cierre": datetime.now(timezone.utc)})
    return True, "Pedido cerrado."
