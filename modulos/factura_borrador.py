"""Borradores de factura en Firestore — guardado paulatino (Ctrl+G)."""
from datetime import datetime, timezone

from modulos.db_firebase import get_db, invalidar_cache_datos
from modulos.util_fechas import formatear_fecha_ar, ahora_ar


def _ahora():
    return datetime.now(timezone.utc)


def guardar_borrador_factura(datos, borrador_id=None):
    """
    datos: dict con proveedor, cuit_proveedor, punto_venta, numero_comprobante,
           condicion_pago, articulos, etc.
    """
    cuit = "".join(filter(str.isdigit, str(datos.get("cuit_proveedor", ""))))
    if not cuit or len(cuit) != 11:
        return False, "CUIT inválido (11 dígitos) para guardar borrador.", None

    articulos = datos.get("articulos") or []
    if not articulos:
        return False, "No hay artículos para guardar.", None

    ahora = _ahora()
    db = get_db()

    if borrador_id:
        ref = db.collection("facturas_borrador").document(borrador_id)
        if not ref.get().exists:
            borrador_id = None

    if not borrador_id:
        borrador_id = f"BORRADOR_{cuit}_{ahora_ar().strftime('%Y%m%d_%H%M%S')}"
        ref = db.collection("facturas_borrador").document(borrador_id)

    payload = {
        "cuit_proveedor": cuit,
        "proveedor": str(datos.get("proveedor", "")).strip().upper(),
        "punto_venta": str(datos.get("punto_venta", "")).strip(),
        "numero_comprobante": str(datos.get("numero_comprobante", "")).strip(),
        "condicion_pago": str(datos.get("condicion_pago", "Contado")),
        "articulos": articulos,
        "item_count": len(articulos),
        "fecha_ultima_modificacion": ahora,
        "estado": "borrador",
    }
    archivo_origen = str(datos.get("archivo_origen", "")).strip()
    if archivo_origen:
        payload["archivo_origen"] = archivo_origen
    ref.set(payload, merge=True)
    return True, f"Borrador guardado ({formatear_fecha_ar(ahora)}).", borrador_id


def listar_borradores_factura(limite=50):
    docs = get_db().collection("facturas_borrador").limit(200).get()
    lista = []
    for doc in docs:
        data = doc.to_dict() or {}
        data["id"] = doc.id
        lista.append(data)
    lista.sort(
        key=lambda x: x.get("fecha_ultima_modificacion") or datetime.min.replace(tzinfo=timezone.utc),
        reverse=True,
    )
    return lista[:limite]


def obtener_borrador_factura(borrador_id):
    ref = get_db().collection("facturas_borrador").document(borrador_id)
    doc = ref.get()
    if not doc.exists:
        return None
    data = doc.to_dict() or {}
    data["id"] = doc.id
    return data


def eliminar_borrador_factura(borrador_id):
    if not borrador_id:
        return
    get_db().collection("facturas_borrador").document(borrador_id).delete()


def titulo_borrador(b):
    pv = str(b.get("punto_venta", "")).zfill(5) if b.get("punto_venta") else "—"
    num = str(b.get("numero_comprobante", "")).zfill(8) if b.get("numero_comprobante") else "—"
    fecha = formatear_fecha_ar(b.get("fecha_ultima_modificacion"))
    archivo = b.get("archivo_origen")
    sufijo_archivo = f" | 📎 {archivo}" if archivo else ""
    return (
        f"{b.get('proveedor', '—')} | {pv}-{num} | "
        f"{b.get('item_count', 0)} ítems | {fecha}{sufijo_archivo} | `{b.get('id', '')}`"
    )
