"""Persistencia de pedidos a proveedores en Firestore."""
from datetime import datetime, timezone, date, time

from modulos.db_firebase import (
    get_db,
    normalizar_codigo_proveedor,
    sanitizar_clave_marca,
    clave_linea_factura,
)


def _id_item_pedido(codigo, marca):
    k = clave_linea_factura(codigo, marca)
    return k.replace("|", "__").replace("/", "-")[:150]


def _a_datetime(valor):
    """Normaliza fecha Firestore / datetime / date a datetime con timezone UTC."""
    if valor is None:
        return None
    if isinstance(valor, datetime):
        return valor if valor.tzinfo else valor.replace(tzinfo=timezone.utc)
    if isinstance(valor, date):
        return datetime.combine(valor, time.min, tzinfo=timezone.utc)
    if hasattr(valor, "timestamp"):
        return datetime.fromtimestamp(valor.timestamp(), tz=timezone.utc)
    return None


def formatear_fecha_pedido(valor, con_hora=True):
    dt = _a_datetime(valor)
    if not dt:
        return "—"
    try:
        local = dt.astimezone()
    except Exception:
        local = dt
    if con_hora:
        return local.strftime("%d/%m/%Y %H:%M")
    return local.strftime("%d/%m/%Y")


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
    cantidad_total = sum(ln["cantidad_pedida"] for ln in lineas)
    db = get_db()
    ref_ped = db.collection("pedidos").document(pedido_id)

    ref_ped.set({
        "cuit_proveedor": cuit_l,
        "nombre_proveedor": str(nombre_proveedor or "").strip().upper(),
        "fecha_pedido": ahora,
        "estado": "abierto",
        "notas": str(notas or "").strip(),
        "item_count": len(lineas),
        "cantidad_total": cantidad_total,
    })

    batch = db.batch()
    for ln in lineas:
        item_id = _id_item_pedido(ln["codigo_proveedor"], ln["marca"])
        batch.set(ref_ped.collection("items").document(item_id), ln)
    batch.commit()

    return True, f"Pedido {pedido_id} guardado ({len(lineas)} ítems, {cantidad_total} u.).", pedido_id


def listar_pedidos(cuit=None, estado=None, fecha_desde=None, fecha_hasta=None, limite=300):
    """
    estado: None = todos, 'abierto', 'cerrado'
    fecha_desde / fecha_hasta: date o datetime (inclusive)
    """
    docs = get_db().collection("pedidos").limit(limite).get()
    resultado = []
    cuit_f = "".join(filter(str.isdigit, str(cuit or "")))

    dt_desde = _a_datetime(fecha_desde)
    if dt_desde and isinstance(fecha_desde, date) and not isinstance(fecha_desde, datetime):
        dt_desde = datetime.combine(fecha_desde, time.min, tzinfo=timezone.utc)

    dt_hasta = _a_datetime(fecha_hasta)
    if dt_hasta:
        if isinstance(fecha_hasta, date) and not isinstance(fecha_hasta, datetime):
            dt_hasta = datetime.combine(fecha_hasta, time.max, tzinfo=timezone.utc)
        elif isinstance(fecha_hasta, datetime) and fecha_hasta.time() == time.min:
            dt_hasta = datetime.combine(fecha_hasta.date(), time.max, tzinfo=timezone.utc)

    for doc in docs:
        data = doc.to_dict() or {}
        if cuit_f and data.get("cuit_proveedor") != cuit_f:
            continue

        est = str(data.get("estado", "abierto")).lower()
        if estado == "abierto" and est not in ("abierto", "parcial"):
            continue
        if estado == "cerrado" and est != "cerrado":
            continue

        fp = _a_datetime(data.get("fecha_pedido"))
        if dt_desde and fp and fp < dt_desde:
            continue
        if dt_hasta and fp and fp > dt_hasta:
            continue

        data["id"] = doc.id
        data["fecha_pedido"] = fp
        resultado.append(data)

    resultado.sort(
        key=lambda x: x.get("fecha_pedido") or datetime.min.replace(tzinfo=timezone.utc),
        reverse=True,
    )
    return resultado


def agrupar_pedidos_por_proveedor(pedidos):
    grupos = {}
    for p in pedidos or []:
        cuit = p.get("cuit_proveedor", "")
        nombre = p.get("nombre_proveedor") or "SIN PROVEEDOR"
        key = cuit or nombre
        if key not in grupos:
            grupos[key] = {
                "cuit_proveedor": cuit,
                "nombre_proveedor": nombre,
                "pedidos": [],
            }
        grupos[key]["pedidos"].append(p)
    return dict(sorted(grupos.items(), key=lambda kv: kv[1]["nombre_proveedor"]))


def obtener_items_pedido(pedido_id):
    docs = get_db().collection("pedidos").document(pedido_id).collection("items").get()
    items = []
    for d in docs:
        data = d.to_dict()
        if data:
            items.append(data)
    items.sort(key=lambda x: str(x.get("codigo_proveedor", "")))
    return items


def obtener_pedido_completo(pedido_id):
    ref = get_db().collection("pedidos").document(pedido_id)
    doc = ref.get()
    if not doc.exists:
        return None
    data = doc.to_dict() or {}
    data["id"] = doc.id
    data["fecha_pedido"] = _a_datetime(data.get("fecha_pedido"))
    data["items"] = obtener_items_pedido(pedido_id)
    if "cantidad_total" not in data or not data.get("cantidad_total"):
        data["cantidad_total"] = sum(int(i.get("cantidad_pedida", 0)) for i in data["items"])
    return data


def cerrar_pedido(pedido_id):
    ref = get_db().collection("pedidos").document(pedido_id)
    if not ref.get().exists:
        return False, "Pedido no encontrado."
    ref.update({"estado": "cerrado", "fecha_cierre": datetime.now(timezone.utc)})
    return True, "Pedido cerrado."
