"""Relevamientos cíclicos: familias, asignación a vendedores y movimiento de stock.

Colecciones nuevas (no altera el esquema operativo de inventario/mostrador):
  - familias_relevamiento
  - tarjetas_relevamiento
  - conteos_relevamiento

Campos opcionales en variantes de producto (solo se agregan al vender/ingresar):
  - last_sale_at, last_ingreso_at
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from modulos.db_firebase import get_db, sanitizar_clave_marca
from modulos.util_fechas import ahora_ar, ahora_utc, _a_utc

COL_FAMILIAS = "familias_relevamiento"
COL_TARJETAS = "tarjetas_relevamiento"
COL_CONTEOS = "conteos_relevamiento"


def _slug(texto: str, max_len: int = 80) -> str:
    t = str(texto or "").strip().upper()
    out = []
    for ch in t:
        if ch.isalnum():
            out.append(ch)
        elif ch in (" ", "-", "_", "/"):
            out.append("_")
    s = "".join(out).strip("_")
    while "__" in s:
        s = s.replace("__", "_")
    return (s or "FAMILIA")[:max_len]


def _id_articulo_familia(id_maestro: str, marca: str = "") -> str:
    mid = str(id_maestro or "").strip().replace("/", "-")
    m = sanitizar_clave_marca(marca) if marca else ""
    if m:
        return f"{mid}__{m}"[:150]
    return mid[:150]


# ── Familias / módulos ──────────────────────────────────────────────────────


def crear_familia(nombre: str, descripcion: str = "") -> Tuple[bool, str, Optional[str]]:
    nom = str(nombre or "").strip().upper()
    if not nom:
        return False, "Indicá el nombre del módulo/familia.", None
    fid = _slug(nom)
    ref = get_db().collection(COL_FAMILIAS).document(fid)
    if ref.get().exists:
        return False, f"Ya existe el módulo «{nom}».", None
    ahora = ahora_utc()
    ref.set({
        "nombre": nom,
        "descripcion": str(descripcion or "").strip(),
        "activo": True,
        "articulo_count": 0,
        "creado": ahora,
        "actualizado": ahora,
    })
    return True, f"Módulo «{nom}» creado.", fid


def listar_familias(solo_activas: bool = True) -> List[Dict[str, Any]]:
    docs = get_db().collection(COL_FAMILIAS).stream()
    out = []
    for d in docs:
        data = d.to_dict() or {}
        if solo_activas and not data.get("activo", True):
            continue
        data["id"] = d.id
        out.append(data)
    out.sort(key=lambda x: str(x.get("nombre", "")).upper())
    return out


def obtener_familia(familia_id: str) -> Optional[Dict[str, Any]]:
    if not familia_id:
        return None
    doc = get_db().collection(COL_FAMILIAS).document(familia_id).get()
    if not doc.exists:
        return None
    data = doc.to_dict() or {}
    data["id"] = doc.id
    return data


def listar_articulos_familia(familia_id: str) -> List[Dict[str, Any]]:
    docs = (
        get_db().collection(COL_FAMILIAS)
        .document(familia_id)
        .collection("articulos")
        .stream()
    )
    out = []
    for d in docs:
        data = d.to_dict() or {}
        data["id"] = d.id
        out.append(data)
    out.sort(key=lambda x: (str(x.get("id_maestro", "")), str(x.get("marca", ""))))
    return out


def _quitar_articulo_de_otras_familias(id_maestro: str, marca: str, excepto_fid: str):
    """Garantiza 1 artículo → 1 familia."""
    art_id = _id_articulo_familia(id_maestro, marca)
    for fam in listar_familias(solo_activas=False):
        fid = fam["id"]
        if fid == excepto_fid:
            continue
        ref = (
            get_db().collection(COL_FAMILIAS)
            .document(fid)
            .collection("articulos")
            .document(art_id)
        )
        if ref.get().exists:
            ref.delete()
            _recontar_articulos_familia(fid)


def _recontar_articulos_familia(familia_id: str):
    n = len(list(
        get_db().collection(COL_FAMILIAS)
        .document(familia_id)
        .collection("articulos")
        .stream()
    ))
    get_db().collection(COL_FAMILIAS).document(familia_id).update({
        "articulo_count": n,
        "actualizado": ahora_utc(),
    })


def vincular_articulo_familia(
    familia_id: str, id_maestro: str, marca: str = "", descripcion: str = ""
) -> Tuple[bool, str]:
    if not familia_id or not id_maestro:
        return False, "Falta módulo o código de artículo."
    fam = obtener_familia(familia_id)
    if not fam:
        return False, "Módulo no encontrado."
    mid = str(id_maestro).strip()
    mrc = sanitizar_clave_marca(marca) if marca else ""
    art_id = _id_articulo_familia(mid, mrc)
    _quitar_articulo_de_otras_familias(mid, mrc, familia_id)
    ref = (
        get_db().collection(COL_FAMILIAS)
        .document(familia_id)
        .collection("articulos")
        .document(art_id)
    )
    ref.set({
        "id_maestro": mid,
        "marca": mrc,
        "descripcion": str(descripcion or "").strip().upper(),
        "agregado": ahora_utc(),
    })
    _recontar_articulos_familia(familia_id)
    return True, f"Artículo {mid} vinculado a «{fam.get('nombre')}»."


def desvincular_articulo_familia(
    familia_id: str, id_maestro: str, marca: str = ""
) -> Tuple[bool, str]:
    art_id = _id_articulo_familia(id_maestro, marca)
    ref = (
        get_db().collection(COL_FAMILIAS)
        .document(familia_id)
        .collection("articulos")
        .document(art_id)
    )
    if not ref.get().exists:
        return False, "El artículo no está en ese módulo."
    ref.delete()
    _recontar_articulos_familia(familia_id)
    return True, "Artículo quitado del módulo."


def mapa_familia_por_articulo() -> Dict[str, str]:
    """Clave id_maestro o id_maestro__MARCA → familia_id."""
    mapa = {}
    for fam in listar_familias(solo_activas=False):
        for art in listar_articulos_familia(fam["id"]):
            mid = str(art.get("id_maestro", ""))
            mrc = str(art.get("marca", "") or "")
            if mid:
                mapa[_id_articulo_familia(mid, mrc)] = fam["id"]
                if not mrc:
                    mapa[mid] = fam["id"]
    return mapa


# ── Tarjetas / asignación a vendedores ──────────────────────────────────────


def asegurar_tarjeta(
    vendedor_id: str, nombre: str = "", etiqueta: str = ""
) -> Tuple[bool, str]:
    vid = str(vendedor_id or "").strip().lower().replace(" ", "_")[:80]
    if not vid:
        return False, "Vendedor inválido."
    ref = get_db().collection(COL_TARJETAS).document(vid)
    doc = ref.get()
    if doc.exists:
        return True, vid
    ref.set({
        "vendedor_id": vid,
        "nombre": (nombre or vendedor_id).strip(),
        "etiqueta": str(etiqueta or "").strip().upper()[:8],
        "familia_ids": [],
        "activo": True,
        "creado": ahora_utc(),
        "actualizado": ahora_utc(),
    })
    return True, vid


def listar_tarjetas(solo_activas: bool = True) -> List[Dict[str, Any]]:
    docs = get_db().collection(COL_TARJETAS).stream()
    out = []
    for d in docs:
        data = d.to_dict() or {}
        if solo_activas and not data.get("activo", True):
            continue
        data["id"] = d.id
        data["familia_ids"] = list(data.get("familia_ids") or [])
        out.append(data)
    out.sort(key=lambda x: (
        str(x.get("etiqueta", "")),
        str(x.get("nombre", x.get("id", ""))).upper(),
    ))
    return out


def guardar_tarjeta(
    vendedor_id: str,
    nombre: str = "",
    etiqueta: str = "",
    familia_ids: Optional[List[str]] = None,
    activo: bool = True,
) -> Tuple[bool, str]:
    vid = str(vendedor_id or "").strip().lower().replace(" ", "_")[:80]
    if not vid:
        return False, "Indicá un ID/nombre de vendedor."
    ref = get_db().collection(COL_TARJETAS).document(vid)
    payload = {
        "vendedor_id": vid,
        "nombre": (nombre or vendedor_id).strip(),
        "etiqueta": str(etiqueta or "").strip().upper()[:8],
        "activo": bool(activo),
        "actualizado": ahora_utc(),
    }
    if familia_ids is not None:
        # Un módulo solo en una tarjeta
        limpios = []
        for fid in familia_ids:
            fid = str(fid or "").strip()
            if fid and fid not in limpios:
                limpios.append(fid)
        _liberar_familias_de_otras_tarjetas(limpios, excepto_vid=vid)
        payload["familia_ids"] = limpios
    if not ref.get().exists:
        payload["creado"] = ahora_utc()
        if "familia_ids" not in payload:
            payload["familia_ids"] = []
    ref.set(payload, merge=True)
    return True, f"Tarjeta de {payload['nombre']} guardada."


def _liberar_familias_de_otras_tarjetas(familia_ids: List[str], excepto_vid: str):
    if not familia_ids:
        return
    wanted = set(familia_ids)
    for t in listar_tarjetas(solo_activas=False):
        if t["id"] == excepto_vid:
            continue
        actuales = list(t.get("familia_ids") or [])
        nuevos = [f for f in actuales if f not in wanted]
        if len(nuevos) != len(actuales):
            get_db().collection(COL_TARJETAS).document(t["id"]).update({
                "familia_ids": nuevos,
                "actualizado": ahora_utc(),
            })


def asignar_familia_a_vendedor(familia_id: str, vendedor_id: str) -> Tuple[bool, str]:
    fid = str(familia_id or "").strip()
    vid = str(vendedor_id or "").strip().lower().replace(" ", "_")[:80]
    if not fid or not vid:
        return False, "Falta módulo o vendedor."
    fam = obtener_familia(fid)
    if not fam:
        return False, "Módulo no encontrado."
    ok, _ = asegurar_tarjeta(vid, nombre=vid)
    if not ok:
        return False, "No se pudo crear la tarjeta."
    tarjetas = {t["id"]: t for t in listar_tarjetas(solo_activas=False)}
    t = tarjetas.get(vid) or {"familia_ids": []}
    ids = list(t.get("familia_ids") or [])
    if fid not in ids:
        ids.append(fid)
    return guardar_tarjeta(
        vid,
        nombre=t.get("nombre", vid),
        etiqueta=t.get("etiqueta", ""),
        familia_ids=ids,
        activo=t.get("activo", True),
    )


def familias_sin_asignar() -> List[Dict[str, Any]]:
    asignadas = set()
    for t in listar_tarjetas(solo_activas=True):
        for fid in t.get("familia_ids") or []:
            asignadas.add(fid)
    return [f for f in listar_familias(solo_activas=True) if f["id"] not in asignadas]


def sembrar_tarjetas_iniciales() -> Tuple[bool, str]:
    """Crea las 4 tarjetas base si no existen (no pisa asignaciones)."""
    base = [
        ("facundo", "Facundo Balcarce", "A"),
        ("emilio", "Emilio Real", "B"),
        ("damian", "Damian Reynaga", "C"),
        ("fernando", "Fernando Blanco", "D"),
    ]
    creadas = 0
    for vid, nom, etq in base:
        ref = get_db().collection(COL_TARJETAS).document(vid)
        if not ref.get().exists:
            ok, _ = asegurar_tarjeta(vid, nombre=nom, etiqueta=etq)
            if ok:
                get_db().collection(COL_TARJETAS).document(vid).update({"etiqueta": etq, "nombre": nom})
                creadas += 1
    return True, f"Tarjetas base listas ({creadas} nuevas)."


# ── Movimiento: last_sale_at / last_ingreso_at ──────────────────────────────


def marcar_venta_variante(id_maestro: str, marca: str, cuando: Optional[datetime] = None):
    mid = str(id_maestro or "").strip()
    if not mid:
        return
    mrc = sanitizar_clave_marca(marca or "GENERICO")
    cuando = cuando or ahora_utc()
    ref = get_db().collection("productos").document(mid)
    doc = ref.get()
    if not doc.exists:
        return
    data = doc.to_dict() or {}
    if "variantes" in data:
        if mrc not in (data.get("variantes") or {}):
            # si solo hay una variante, usarla
            vars_ = data.get("variantes") or {}
            if len(vars_) == 1:
                mrc = list(vars_.keys())[0]
            else:
                return
        ref.update({
            f"variantes.{mrc}.last_sale_at": cuando,
            "ultima_actualizacion": cuando,
        })
    else:
        ref.update({"last_sale_at": cuando, "ultima_actualizacion": cuando})


def marcar_ingreso_variante(id_maestro: str, marca: str, cuando: Optional[datetime] = None):
    mid = str(id_maestro or "").strip()
    if not mid:
        return
    mrc = sanitizar_clave_marca(marca or "GENERICO")
    cuando = cuando or ahora_utc()
    ref = get_db().collection("productos").document(mid)
    doc = ref.get()
    if not doc.exists:
        return
    data = doc.to_dict() or {}
    if "variantes" in data:
        if mrc not in (data.get("variantes") or {}):
            vars_ = data.get("variantes") or {}
            if len(vars_) == 1:
                mrc = list(vars_.keys())[0]
            else:
                return
        ref.update({
            f"variantes.{mrc}.last_ingreso_at": cuando,
            "ultima_actualizacion": cuando,
        })
    else:
        ref.update({"last_ingreso_at": cuando, "ultima_actualizacion": cuando})


def registrar_ventas_desde_lineas_carrito(lineas_ok: List[Dict[str, Any]]):
    """Hook post-venta: no debe romper el flujo principal."""
    ahora = ahora_utc()
    for linea in lineas_ok or []:
        if linea.get("fuera_stock"):
            continue
        item = linea.get("item") or {}
        mid = str(item.get("id_maestro") or item.get("codigo") or "").strip()
        marca = str(linea.get("marca") or item.get("marca") or "GENERICO")
        if mid:
            try:
                marcar_venta_variante(mid, marca, ahora)
            except Exception:
                continue


# ── Reportes / conteo ───────────────────────────────────────────────────────


def _dias_desde(valor) -> Optional[int]:
    dt = _a_utc(valor)
    if not dt:
        return None
    hoy = ahora_ar().date()
    dia = dt.astimezone(ahora_ar().tzinfo).date() if dt.tzinfo else dt.date()
    return max(0, (hoy - dia).days)


def enriquecer_inventario_con_movimiento(inventario: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    try:
        mapa_fam = mapa_familia_por_articulo()
        familias = {f["id"]: f for f in listar_familias(solo_activas=False)}
    except Exception:
        mapa_fam, familias = {}, {}
    out = []
    for it in inventario or []:
        row = dict(it)
        mid = str(row.get("id_maestro") or row.get("codigo") or "")
        mrc = str(row.get("marca") or "")
        fid = mapa_fam.get(_id_articulo_familia(mid, mrc)) or mapa_fam.get(mid)
        fam = familias.get(fid or "", {})
        row["familia_id"] = fid or ""
        row["familia_nombre"] = fam.get("nombre", "")
        row["dias_sin_venta"] = _dias_desde(row.get("last_sale_at"))
        row["dias_sin_ingreso"] = _dias_desde(row.get("last_ingreso_at"))
        try:
            stock = float(row.get("stock") or 0)
            costo = float(row.get("ultimo_costo_base") or 0)
        except (TypeError, ValueError):
            stock, costo = 0.0, 0.0
        row["costo_inmovilizado"] = round(stock * costo, 2)
        out.append(row)
    return out


def reporte_sin_movimiento(
    inventario: List[Dict[str, Any]],
    dias_venta_min: int = 5,
    dias_ingreso_min: Optional[int] = None,
    solo_con_stock: bool = True,
) -> List[Dict[str, Any]]:
    enriq = enriquecer_inventario_con_movimiento(inventario)
    out = []
    for row in enriq:
        if solo_con_stock and float(row.get("stock") or 0) <= 0:
            continue
        dvs = row.get("dias_sin_venta")
        din = row.get("dias_sin_ingreso")
        # Sin venta: nunca vendido o >= umbral
        ok_venta = dvs is None or dvs >= int(dias_venta_min)
        if not ok_venta:
            continue
        if dias_ingreso_min is not None:
            ok_ing = din is None or din >= int(dias_ingreso_min)
            if not ok_ing:
                continue
        out.append(row)
    out.sort(key=lambda x: float(x.get("costo_inmovilizado") or 0), reverse=True)
    return out


def guardar_conteo(
    vendedor_id: str,
    familia_id: str,
    items: List[Dict[str, Any]],
    notas: str = "",
) -> Tuple[bool, str, Optional[str]]:
    vid = str(vendedor_id or "").strip()
    fid = str(familia_id or "").strip()
    if not vid or not fid:
        return False, "Falta vendedor o módulo.", None
    ahora = ahora_utc()
    ref = get_db().collection(COL_CONTEOS).document()
    limpios = []
    for it in items or []:
        try:
            sist = int(it.get("stock_sistema") or 0)
            fis = int(it.get("stock_fisico") or 0)
        except (TypeError, ValueError):
            continue
        limpios.append({
            "id_maestro": str(it.get("id_maestro") or ""),
            "marca": str(it.get("marca") or ""),
            "descripcion": str(it.get("descripcion") or ""),
            "stock_sistema": sist,
            "stock_fisico": fis,
            "diferencia": fis - sist,
            "observacion": str(it.get("observacion") or "").strip(),
        })
    ref.set({
        "vendedor_id": vid,
        "familia_id": fid,
        "fecha": ahora,
        "notas": str(notas or "").strip(),
        "items": limpios,
        "item_count": len(limpios),
        "diffs": sum(1 for x in limpios if x["diferencia"] != 0),
    })
    return True, f"Conteo guardado ({len(limpios)} ítems).", ref.id


def listar_conteos(vendedor_id: str = "", limite: int = 30) -> List[Dict[str, Any]]:
    docs = list(get_db().collection(COL_CONTEOS).limit(max(limite * 2, 40)).stream())
    out = []
    vid = str(vendedor_id or "").strip().lower()
    for d in docs:
        data = d.to_dict() or {}
        data["id"] = d.id
        if vid and str(data.get("vendedor_id", "")).lower() != vid:
            continue
        out.append(data)
    out.sort(
        key=lambda x: _a_utc(x.get("fecha")) or datetime.min.replace(tzinfo=timezone.utc),
        reverse=True,
    )
    return out[:limite]
