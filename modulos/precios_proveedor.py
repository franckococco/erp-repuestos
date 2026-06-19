"""Motor de precios: márgenes por proveedor (IVA, rentabilidad, descuentos, recargos)."""
import math
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

IVA_PCT_DEFAULT = 21.0
RENTABILIDAD_PCT_DEFAULT = 40.0
REDONDEO_PESOS_DEFAULT = 10


def margenes_desde_proveedor(datos_prov: Optional[Dict[str, Any]]) -> Dict[str, float]:
    datos = datos_prov if isinstance(datos_prov, dict) else {}
    return {
        "descuento": float(datos.get("descuento", 0.0)),
        "iva_pct": float(datos.get("iva_pct", IVA_PCT_DEFAULT)),
        "rentabilidad_pct": float(datos.get("rentabilidad_pct", RENTABILIDAD_PCT_DEFAULT)),
    }


def recargo_desde_proveedor(datos_prov: Optional[Dict[str, Any]], condicion_pago: str = "Contado") -> float:
    condiciones = (datos_prov or {}).get("condiciones", {}) or {}
    if not isinstance(condiciones, dict):
        condiciones = {}
    return float(condiciones.get(str(condicion_pago), 0.0))


def calcular_cascada_precios(
    precio_base,
    recargo_financiero,
    descuento_proveedor=0.0,
    iva_pct=IVA_PCT_DEFAULT,
    rentabilidad_pct=RENTABILIDAD_PCT_DEFAULT,
    redondeo=REDONDEO_PESOS_DEFAULT,
) -> dict:
    base = float(precio_base or 0)
    desc = float(descuento_proveedor or 0)
    iva = float(iva_pct if iva_pct is not None else IVA_PCT_DEFAULT)
    rent = float(rentabilidad_pct if rentabilidad_pct is not None else RENTABILIDAD_PCT_DEFAULT)
    rec = float(recargo_financiero or 0)
    red = max(1.0, float(redondeo or REDONDEO_PESOS_DEFAULT))

    base_con_descuento = base * (1 - (desc / 100.0))
    costo_iva = base_con_descuento * (1 + (iva / 100.0))
    costo_final = costo_iva * (1 + (rec / 100.0))
    precio_interno = costo_final * (1 + (rent / 100.0))
    precio_venta = math.ceil(precio_interno / red) * red

    return {
        "costo_neto": round(base_con_descuento, 2),
        "costo_iva": round(costo_iva, 2),
        "costo_final": round(costo_final, 2),
        "precio_interno": round(precio_interno, 2),
        "precio_venta": int(precio_venta),
    }


def calcular_cascada_desde_proveedor(
    precio_base,
    datos_prov: Optional[Dict[str, Any]],
    condicion_pago: str = "Contado",
) -> dict:
    m = margenes_desde_proveedor(datos_prov)
    recargo = recargo_desde_proveedor(datos_prov, condicion_pago)
    return calcular_cascada_precios(
        precio_base,
        recargo,
        m["descuento"],
        m["iva_pct"],
        m["rentabilidad_pct"],
    )


def _proveedor_por_cuit(provs: Dict[str, Any], cuit: str) -> Dict[str, Any]:
    cuit_l = "".join(filter(str.isdigit, str(cuit or "")))
    if cuit_l and cuit_l in provs and isinstance(provs[cuit_l], dict):
        return provs[cuit_l]
    return {}


def _aplicar_recalculo_variante(
    batch,
    ref_prod,
    marca: str,
    costo_base: float,
    datos_prov: Dict[str, Any],
    condicion_pago: str,
    ahora,
    usa_variantes: bool,
):
    if costo_base <= 0:
        return batch, 0
    calculos = calcular_cascada_desde_proveedor(costo_base, datos_prov, condicion_pago)
    if usa_variantes:
        batch.update(ref_prod, {
            "ultima_actualizacion": ahora,
            f"variantes.{marca}.precio_interno": calculos["precio_interno"],
            f"variantes.{marca}.precio_venta": calculos["precio_venta"],
        })
    else:
        batch.update(ref_prod, {
            "ultima_actualizacion": ahora,
            "precio_interno": calculos["precio_interno"],
            "precio_venta": calculos["precio_venta"],
        })
    return batch, 1


def _recalcular_documento_producto(
    doc,
    provs: Dict[str, Any],
    condicion_pago: str,
    cuit_filtro: Optional[str],
    batch,
    operaciones: int,
    ahora,
) -> Tuple[Any, int, int]:
    from modulos.db_firebase import _commit_batch_si_lleno, get_db

    master = doc.to_dict() or {}
    master_id = doc.id
    actualizados = 0

    if "variantes" not in master:
        cuit = str(master.get("cuit_proveedor", "") or "")
        if cuit_filtro and cuit != cuit_filtro:
            return batch, operaciones, 0
        costo = float(master.get("ultimo_costo_base", 0) or 0)
        if costo <= 0:
            return batch, operaciones, 0
        marca = str(master.get("marca", master.get("condicion", "GENERICO")))
        ref = get_db().collection("productos").document(master_id)
        datos_prov = _proveedor_por_cuit(provs, cuit)
        batch, n = _aplicar_recalculo_variante(
            batch, ref, marca, costo, datos_prov, condicion_pago, ahora, False,
        )
        if n:
            actualizados += n
            operaciones += 1
            batch = _commit_batch_si_lleno(batch, operaciones)
        return batch, operaciones, actualizados

    for marca, v_data in (master.get("variantes") or {}).items():
        if not isinstance(v_data, dict):
            continue
        cuit = str(v_data.get("cuit_proveedor", "") or "")
        if cuit_filtro and cuit != cuit_filtro:
            continue
        costo = float(v_data.get("ultimo_costo_base", 0) or 0)
        if costo <= 0:
            continue
        ref = get_db().collection("productos").document(master_id)
        datos_prov = _proveedor_por_cuit(provs, cuit)
        batch, n = _aplicar_recalculo_variante(
            batch, ref, marca, costo, datos_prov, condicion_pago, ahora, True,
        )
        if n:
            actualizados += n
            operaciones += 1
            batch = _commit_batch_si_lleno(batch, operaciones)

    return batch, operaciones, actualizados


def recalcular_precios_proveedor(cuit: str, condicion_pago: str = "Contado") -> Tuple[bool, str]:
    from modulos.db_firebase import get_db, invalidar_cache_datos, obtener_proveedores, _BATCH_LIMIT

    cuit_l = "".join(filter(str.isdigit, str(cuit or "")))
    if len(cuit_l) != 11:
        return False, "CUIT inválido."

    provs = obtener_proveedores() or {}
    if cuit_l not in provs:
        return False, "Proveedor no encontrado."

    docs = list(get_db().collection("productos").stream())
    batch = get_db().batch()
    operaciones = 0
    actualizados = 0
    ahora = datetime.now(timezone.utc)

    for doc in docs:
        batch, operaciones, n = _recalcular_documento_producto(
            doc, provs, condicion_pago, cuit_l, batch, operaciones, ahora,
        )
        actualizados += n

    if operaciones % _BATCH_LIMIT != 0 and operaciones > 0:
        batch.commit()
    invalidar_cache_datos()

    nombre = (provs.get(cuit_l) or {}).get("nombre", cuit_l)
    if not actualizados:
        return True, f"No hay variantes con costo base para recalcular ({nombre})."
    return True, f"Recalculados {actualizados} precio(s) de {nombre} (pago: {condicion_pago})."


def recalcular_precios_todos(condicion_pago: str = "Contado") -> Tuple[bool, str]:
    from modulos.db_firebase import get_db, invalidar_cache_datos, obtener_proveedores, _BATCH_LIMIT

    provs = obtener_proveedores() or {}
    docs = list(get_db().collection("productos").stream())
    batch = get_db().batch()
    operaciones = 0
    actualizados = 0
    ahora = datetime.now(timezone.utc)

    for doc in docs:
        batch, operaciones, n = _recalcular_documento_producto(
            doc, provs, condicion_pago, None, batch, operaciones, ahora,
        )
        actualizados += n

    if operaciones % _BATCH_LIMIT != 0 and operaciones > 0:
        batch.commit()
    invalidar_cache_datos()

    if not actualizados:
        return True, "No hay variantes con costo base para recalcular."
    return True, f"Recalculados {actualizados} precio(s) en todo el inventario (pago: {condicion_pago})."


def recalcular_precios_items(
    items: List[Dict[str, Any]],
    condicion_pago: str = "Contado",
) -> Tuple[bool, str]:
    from modulos.db_firebase import get_db, invalidar_cache_datos, obtener_proveedores, _BATCH_LIMIT, _commit_batch_si_lleno

    if not items:
        return False, "No hay ítems para recalcular."

    provs = obtener_proveedores() or {}
    batch = get_db().batch()
    operaciones = 0
    actualizados = 0
    ahora = datetime.now(timezone.utc)
    vistos = set()

    for it in items:
        if not isinstance(it, dict):
            continue
        id_maestro = str(it.get("id_maestro") or it.get("codigo") or "").strip()
        marca = str(it.get("marca") or "GENERICO").strip()
        if not id_maestro:
            continue
        clave = (id_maestro, marca)
        if clave in vistos:
            continue
        vistos.add(clave)

        costo = float(it.get("ultimo_costo_base", 0) or 0)
        if costo <= 0:
            continue

        cuit = str(it.get("cuit_proveedor", "") or "")
        datos_prov = _proveedor_por_cuit(provs, cuit)
        ref = get_db().collection("productos").document(id_maestro)
        usa_var = bool(it.get("usa_variantes_fs", True))
        batch, n = _aplicar_recalculo_variante(
            batch, ref, marca, costo, datos_prov, condicion_pago, ahora, usa_var,
        )
        if n:
            actualizados += n
            operaciones += 1
            batch = _commit_batch_si_lleno(batch, operaciones)

    if operaciones % _BATCH_LIMIT != 0 and operaciones > 0:
        batch.commit()
    invalidar_cache_datos()

    if not actualizados:
        return True, "Ningún ítem del filtro tenía costo base para recalcular."
    return True, f"Recalculados {actualizados} precio(s) del filtro (pago: {condicion_pago})."
