"""Puntos por vendedor: $100.000 facturados = 1 punto."""
from datetime import datetime, timezone

from modulos.db_firebase import get_db

UMBRAL_PUNTO = 100_000.0


def _slug_vendedor(vendedor_id: str) -> str:
    return str(vendedor_id or "").strip().lower().replace(" ", "_")[:80]


def asegurar_vendedor(vendedor_id: str, nombre: str = "", rol: str = "vendedor"):
    """Crea el documento del vendedor si no existe."""
    vid = _slug_vendedor(vendedor_id)
    if not vid:
        return False, "Vendedor inválido."
    ref = get_db().collection("vendedores").document(vid)
    if ref.get().exists:
        return True, vid
    ref.set({
        "nombre": (nombre or vendedor_id).strip(),
        "rol": rol,
        "puntos": 0,
        "ventas_acumuladas": 0.0,
        "activo": True,
        "creado": datetime.now(timezone.utc),
    })
    return True, vid


def obtener_vendedor(vendedor_id: str):
    vid = _slug_vendedor(vendedor_id)
    if not vid:
        return None
    doc = get_db().collection("vendedores").document(vid).get()
    if not doc.exists:
        return None
    return {"id": doc.id, **(doc.to_dict() or {})}


def listar_vendedores(activos_solo=True):
    docs = get_db().collection("vendedores").stream()
    items = []
    for d in docs:
        data = d.to_dict() or {}
        if activos_solo and not data.get("activo", True):
            continue
        items.append({"id": d.id, **data})
    items.sort(key=lambda x: str(x.get("nombre", x.get("id", ""))).upper())
    return items


def _movimiento_ya_registrado(ref_id: str) -> bool:
    if not ref_id:
        return False
    q = (
        get_db().collection("puntos_movimientos")
        .where("ref_id", "==", str(ref_id))
        .limit(1)
    )
    return bool(list(q.stream()))


def registrar_venta_puntos(vendedor_id: str, monto: float, ref_id: str, origen: str = "comprobante_arca"):
    """
    Suma venta al acumulador del vendedor. Idempotente por ref_id.
    Retorna (ok, mensaje, puntos_ganados).
    """
    vid = _slug_vendedor(vendedor_id)
    monto_f = max(0.0, float(monto or 0))
    if not vid:
        return False, "Vendedor inválido.", 0
    if monto_f <= 0:
        return True, "Monto cero, sin puntos.", 0
    if _movimiento_ya_registrado(ref_id):
        return True, "Puntos ya registrados para este comprobante.", 0

    asegurar_vendedor(vid, nombre=vendedor_id)
    ref_v = get_db().collection("vendedores").document(vid)
    data = ref_v.get().to_dict() or {}
    acum = float(data.get("ventas_acumuladas", 0) or 0) + monto_f
    puntos_tot = int(data.get("puntos", 0) or 0)
    puntos_ganados = 0
    while acum >= UMBRAL_PUNTO:
        acum -= UMBRAL_PUNTO
        puntos_ganados += 1
    puntos_tot += puntos_ganados

    ref_v.update({
        "ventas_acumuladas": round(acum, 2),
        "puntos": puntos_tot,
        "ultima_venta": datetime.now(timezone.utc),
    })

    get_db().collection("puntos_movimientos").add({
        "vendedor_id": vid,
        "monto": monto_f,
        "puntos_ganados": puntos_ganados,
        "puntos_total_despues": puntos_tot,
        "ventas_acumuladas_despues": round(acum, 2),
        "origen": str(origen),
        "ref_id": str(ref_id),
        "fecha": datetime.now(timezone.utc),
    })

    if puntos_ganados:
        msg = f"+{puntos_ganados} punto(s). Total: {puntos_tot}."
    else:
        faltan = UMBRAL_PUNTO - acum
        msg = f"Acumulado ${acum:,.0f}. Faltan ${faltan:,.0f} para el próximo punto."
    return True, msg, puntos_ganados


def resumen_puntos_vendedor(vendedor_id: str):
    v = obtener_vendedor(vendedor_id)
    if not v:
        return {"puntos": 0, "ventas_acumuladas": 0.0, "faltan_proximo": UMBRAL_PUNTO, "nombre": vendedor_id}
    acum = float(v.get("ventas_acumuladas", 0) or 0)
    return {
        "puntos": int(v.get("puntos", 0) or 0),
        "ventas_acumuladas": acum,
        "faltan_proximo": max(0.0, UMBRAL_PUNTO - acum),
        "nombre": v.get("nombre", vendedor_id),
    }
