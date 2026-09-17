"""API local del Mostrador POS — foco en presupuestos (ARCA después)."""
from __future__ import annotations

import asyncio
import os
import sys
import uuid
from pathlib import Path
from typing import Any, Dict, List

from fastapi import BackgroundTasks, FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

ROOT = Path(__file__).resolve().parent
REPO = ROOT.parent
STATIC = ROOT / "static"

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from inventory import cargar_inventario, buscar, producto_por_id, forzar_recarga, estado_conexion  # noqa: E402
from presupuestos import (  # noqa: E402
    anular as anular_presupuesto,
    cargar_en_carrito,
    emitir as emitir_presupuesto,
    firebase_disponible,
    listar as listar_presupuestos,
    regenerar_pdf,
)
from clientes import buscar as buscar_clientes  # noqa: E402
from auth_pos import (  # noqa: E402
    cambiar_clave,
    crear_token,
    inicializar_usuarios,
    leer_token,
    resumen_puntos_admin,
    validar_credenciales,
)
from ventas import (  # noqa: E402
    emitir_factura,
    listar_facturas,
    registrar_puntos_factura,
    regenerar_ticket_factura,
)

app = FastAPI(title="HAFID POS", version="0.4.0")


@app.middleware("http")
async def evitar_cache_pos(request, call_next):
    response = await call_next(request)
    if request.url.path == "/" or request.url.path.startswith("/static/"):
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate"
        response.headers["Pragma"] = "no-cache"
    return response


_CARRITO: List[Dict[str, Any]] = []
_CLIENTE: Dict[str, Any] = {
    "nombre": "CONSUMIDOR FINAL",
    "cuit": "",
    "tipo_comprobante": "6",
    "descuento": 0.0,
}
_NOTA = ""
_VENDEDOR = "CAJA"
_PRESUPUESTO_CARGADO: str | None = None
_MODO = "emulador"
_ESPERA: List[Dict[str, Any]] = []
_UNDO_SNAP: Dict[str, Any] | None = None
_SESSION_STATES: Dict[str, Dict[str, Any]] = {}
_SESSION_LOCK = asyncio.Lock()


def _estado_actual() -> Dict[str, Any]:
    return {
        "carrito": [dict(i) for i in _CARRITO],
        "cliente": dict(_CLIENTE),
        "nota": _NOTA,
        "vendedor": _VENDEDOR,
        "presupuesto_cargado": _PRESUPUESTO_CARGADO,
        "espera": [dict(i) for i in _ESPERA],
        "undo": dict(_UNDO_SNAP) if _UNDO_SNAP else None,
    }


def _cargar_estado(estado: Dict[str, Any] | None) -> None:
    global _CARRITO, _CLIENTE, _NOTA, _VENDEDOR
    global _PRESUPUESTO_CARGADO, _ESPERA, _UNDO_SNAP
    data = estado or {}
    _CARRITO = [dict(i) for i in data.get("carrito") or []]
    _CLIENTE = dict(
        data.get("cliente")
        or {
            "nombre": "CONSUMIDOR FINAL",
            "cuit": "",
            "tipo_comprobante": "6",
            "descuento": 0.0,
        }
    )
    _NOTA = str(data.get("nota") or "")
    _VENDEDOR = str(data.get("vendedor") or "CAJA")
    _PRESUPUESTO_CARGADO = data.get("presupuesto_cargado")
    _ESPERA = [dict(i) for i in data.get("espera") or []]
    _UNDO_SNAP = dict(data["undo"]) if data.get("undo") else None


@app.middleware("http")
async def proteger_y_separar_cajas(request: Request, call_next):
    publicas = {"/", "/healthz", "/api/auth/login"}
    es_publica = request.url.path in publicas or request.url.path.startswith("/static/")
    usuario = leer_token(request.cookies.get("pos_auth", ""))
    request.state.usuario = usuario
    if not es_publica and not usuario:
        return JSONResponse({"detail": "Iniciá sesión"}, status_code=401)
    if (
        usuario
        and usuario.get("debe_cambiar_clave")
        and request.url.path
        not in {"/api/auth/me", "/api/auth/logout", "/api/auth/cambiar-clave"}
    ):
        return JSONResponse(
            {"detail": "Debés cambiar la clave inicial antes de continuar"},
            status_code=403,
        )
    if not request.url.path.startswith("/api/") or request.url.path == "/api/health":
        return await call_next(request)

    session_id = request.cookies.get("pos_session") or uuid.uuid4().hex
    async with _SESSION_LOCK:
        _cargar_estado(_SESSION_STATES.get(session_id))
        if usuario and usuario.get("rol") == "vendedor":
            global _VENDEDOR
            _VENDEDOR = str(usuario.get("vendedor_id") or usuario["usuario"]).upper()
        response = await call_next(request)
        _SESSION_STATES[session_id] = _estado_actual()
    response.set_cookie(
        "pos_session",
        session_id,
        max_age=60 * 60 * 24 * 30,
        httponly=True,
        samesite="lax",
        secure=request.url.scheme == "https",
    )
    return response


def _fmt_money(val: float) -> str:
    return f"${val:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def _snapshot_estado() -> Dict[str, Any]:
    return {
        "items": [dict(i) for i in _CARRITO],
        "cliente": dict(_CLIENTE),
        "nota": _NOTA,
        "vendedor": _VENDEDOR,
        "presupuesto_cargado_id": _PRESUPUESTO_CARGADO,
    }


def _guardar_undo():
    global _UNDO_SNAP
    _UNDO_SNAP = _snapshot_estado()


def _totales_de(items: List[Dict[str, Any]], cliente: Dict[str, Any]):
    bruto = sum(float(i["precio_unitario"]) * int(i["cantidad"]) for i in items)
    desc = float(cliente.get("descuento") or 0)
    monto_desc = round(bruto * desc / 100.0, 2)
    final = round(bruto - monto_desc, 2)
    return {
        "bruto": round(bruto, 2),
        "descuento_pct": desc,
        "descuento_monto": monto_desc,
        "total": final,
        "bruto_txt": _fmt_money(bruto),
        "descuento_txt": _fmt_money(monto_desc),
        "total_txt": _fmt_money(final),
        "items": len(items),
    }


def _totales():
    return _totales_de(_CARRITO, _CLIENTE)


class AddItem(BaseModel):
    id: str
    cantidad: int = Field(1, ge=1)


class ManualItem(BaseModel):
    descripcion: str
    precio_unitario: float = Field(0, ge=0)
    cantidad: int = Field(1, ge=1)
    codigo: str = ""
    marca: str = "MANUAL"


class QtyUpdate(BaseModel):
    cantidad: int | None = Field(None, ge=0)
    precio_unitario: float | None = Field(None, ge=0)
    descripcion: str | None = None


class ClienteIn(BaseModel):
    nombre: str = "CONSUMIDOR FINAL"
    cuit: str = ""
    tipo_comprobante: str = "6"
    descuento: float = 0.0
    telefono: str = ""
    condicion_iva: str = ""


class PresupuestoIn(BaseModel):
    nota: str = ""
    vendedor: str = ""
    actualizar: bool = False


class FacturaIn(BaseModel):
    forma_pago: str = "Contado"
    observacion: str = ""
    cuotas: int = Field(1, ge=1, le=48)
    interes_pct: float = Field(0, ge=0, le=100)
    cliente: ClienteIn | None = None
    vendedor: str = ""
    confirmar: bool = False


class VendedorIn(BaseModel):
    vendedor: str = "CAJA"


class RestaurarCarrito(BaseModel):
    items: List[Dict[str, Any]] = []
    cliente: ClienteIn | None = None
    nota: str = ""
    vendedor: str = ""


class EsperarIn(BaseModel):
    etiqueta: str = ""


class LoginIn(BaseModel):
    usuario: str
    clave: str


class CambiarClaveIn(BaseModel):
    actual: str
    nueva: str


@app.on_event("startup")
def startup():
    global _MODO
    inv, _MODO = cargar_inventario(force=True)
    print(
        f"[POS] modo={_MODO} productos={len(inv)} firebase={firebase_disponible()}",
        flush=True,
    )
    if _MODO == "firebase":
        try:
            inicializar_usuarios()
        except Exception as exc:
            # Una cuota temporal de Firestore no debe impedir que Render arranque.
            # Las operaciones que necesiten Firebase informarán su propio error.
            print(
                f"[POS] No se pudieron inicializar usuarios ({exc}); "
                "el servicio continúa activo.",
                flush=True,
            )


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


@app.get("/healthz")
def healthz():
    est = estado_conexion()
    try:
        prueba_busqueda = buscar("filtro", limite=1)
        busqueda_ok = True
        error_busqueda = None
    except Exception as exc:
        prueba_busqueda = []
        busqueda_ok = False
        error_busqueda = f"{type(exc).__name__}: {exc}"
    return {
        "ok": True,
        "firebase": est["firebase"],
        "inventario": est["productos"],
        "modo": est["modo"],
        "busqueda_ok": busqueda_ok,
        "resultados_prueba": len(prueba_busqueda),
        "error_busqueda": error_busqueda,
        "revision": os.getenv("RENDER_GIT_COMMIT", "local")[:8],
    }


@app.post("/api/auth/login")
def auth_login(body: LoginIn, request: Request):
    usuario = validar_credenciales(body.usuario, body.clave)
    if not usuario:
        raise HTTPException(401, "Usuario o clave incorrectos")
    response = JSONResponse({"ok": True, **usuario})
    response.set_cookie(
        "pos_auth",
        crear_token(usuario),
        max_age=12 * 60 * 60,
        httponly=True,
        samesite="lax",
        secure=request.url.scheme == "https",
    )
    response.set_cookie(
        "pos_session",
        uuid.uuid4().hex,
        max_age=30 * 24 * 60 * 60,
        httponly=True,
        samesite="lax",
        secure=request.url.scheme == "https",
    )
    return response


@app.get("/api/auth/me")
def auth_me(request: Request):
    return {"ok": True, **request.state.usuario}


@app.post("/api/auth/logout")
def auth_logout():
    response = JSONResponse({"ok": True})
    response.delete_cookie("pos_auth")
    return response


@app.post("/api/auth/cambiar-clave")
def auth_cambiar_clave(body: CambiarClaveIn, request: Request):
    ok, mensaje = cambiar_clave(
        request.state.usuario["usuario"], body.actual, body.nueva
    )
    if not ok:
        raise HTTPException(400, mensaje)
    usuario = {**request.state.usuario, "debe_cambiar_clave": False}
    response = JSONResponse({"ok": True, "mensaje": mensaje})
    response.set_cookie(
        "pos_auth",
        crear_token(usuario),
        max_age=12 * 60 * 60,
        httponly=True,
        samesite="lax",
        secure=request.url.scheme == "https",
    )
    return response


@app.get("/api/admin/puntos")
def api_puntos_admin(request: Request):
    if request.state.usuario.get("rol") != "admin":
        raise HTTPException(403, "Solo el administrador puede ver los puntos")
    return {"resultados": resumen_puntos_admin()}


@app.get("/api/health")
def health():
    est = estado_conexion()
    return {
        "ok": True,
        "modo": est["modo"],
        "inventario": est["productos"],
        "firebase": est["firebase"],
        "tiene_claves": est["tiene_claves"],
        "error_firebase": est["error_firebase"],
        "ruta_claves": est["ruta_claves"],
        "instruccion": est["instruccion"],
        "donde_buscar": est["donde_buscar"],
        "etiqueta": "Firebase real" if est["firebase"] else "inventario de muestra",
        "fase": "presupuestos",
    }


@app.post("/api/inventario/recargar")
def api_recargar_inventario():
    """Relee firebase_claves.json si apareció y refresca productos."""
    return forzar_recarga()


@app.get("/api/productos")
def buscar_productos(q: str = Query("", min_length=0), limite: int = 25):
    try:
        hits = buscar(q, limite=limite)
    except Exception as exc:
        print(f"[POS] Error buscando productos: {exc}", flush=True)
        raise HTTPException(
            500, f"No se pudo buscar en el inventario: {type(exc).__name__}: {exc}"
        ) from exc
    from collections import Counter

    codigos = Counter(str(p.get("codigo") or "").upper() for p in hits)
    out = []
    for p in hits:
        cod = str(p.get("codigo") or "").upper()
        nvar = int(codigos.get(cod) or 1)
        out.append(
            {
                "id": p.get("id"),
                "id_maestro": p.get("id_maestro") or p.get("codigo"),
                "codigo": p.get("codigo"),
                "marca": p.get("marca"),
                "descripcion": p.get("descripcion"),
                "vehiculo": p.get("vehiculo") or "",
                "precio_venta": float(p.get("precio_venta") or 0),
                "stock": int(p.get("stock") or 0),
                "variantes_mismo_codigo": nvar,
            }
        )
    return {"resultados": out, "total": len(out)}


@app.get("/api/clientes")
def api_clientes(q: str = Query("", min_length=0)):
    return {"resultados": buscar_clientes(q)}


@app.get("/api/carrito")
def get_carrito():
    return {
        "items": _CARRITO,
        "totales": _totales(),
        "cliente": _CLIENTE,
        "nota": _NOTA,
        "vendedor": _VENDEDOR,
        "presupuesto_cargado_id": _PRESUPUESTO_CARGADO,
        "presupuesto_cargado_nro": _nro_presupuesto_cargado(),
    }


def _nro_presupuesto_cargado():
    if not _PRESUPUESTO_CARGADO:
        return None
    from presupuestos import obtener

    p = obtener(_PRESUPUESTO_CARGADO)
    return (p or {}).get("numero_presupuesto")


@app.post("/api/carrito/items")
def add_item(body: AddItem):
    prod = producto_por_id(body.id)
    if not prod:
        raise HTTPException(404, "Producto no encontrado")
    _guardar_undo()
    stock = int(prod.get("stock") or 0)
    for item in _CARRITO:
        if item["id"] == body.id:
            item["cantidad"] += body.cantidad
            item["subtotal"] = round(item["cantidad"] * item["precio_unitario"], 2)
            item["stock"] = stock
            return {"ok": True, "items": _CARRITO, "totales": _totales()}
    _CARRITO.append(
        {
            "id": prod["id"],
            "id_maestro": prod.get("id_maestro") or prod.get("codigo"),
            "codigo": prod["codigo"],
            "descripcion": prod["descripcion"],
            "marca": prod["marca"],
            "precio_unitario": float(prod["precio_venta"]),
            "cantidad": body.cantidad,
            "subtotal": round(float(prod["precio_venta"]) * body.cantidad, 2),
            "stock": stock,
        }
    )
    return {"ok": True, "items": _CARRITO, "totales": _totales()}


@app.post("/api/carrito/manual")
def add_manual(body: ManualItem):
    desc = (body.descripcion or "").strip().upper()
    if not desc:
        raise HTTPException(400, "Descripción obligatoria")
    import time

    _guardar_undo()
    item_id = f"MANUAL_{int(time.time() * 1000)}"
    precio = float(body.precio_unitario or 0)
    cant = int(body.cantidad or 1)
    _CARRITO.append(
        {
            "id": item_id,
            "id_maestro": (body.codigo or item_id).strip().upper(),
            "codigo": (body.codigo or "").strip().upper(),
            "descripcion": desc,
            "marca": (body.marca or "MANUAL").strip().upper() or "MANUAL",
            "precio_unitario": precio,
            "cantidad": cant,
            "subtotal": round(precio * cant, 2),
            "manual": True,
            "stock": None,
        }
    )
    return {"ok": True, "items": _CARRITO, "totales": _totales()}


@app.patch("/api/carrito/items/{item_id}")
def patch_item(item_id: str, body: QtyUpdate):
    for i, item in enumerate(_CARRITO):
        if item["id"] == item_id:
            if body.cantidad is not None:
                if body.cantidad <= 0:
                    _CARRITO.pop(i)
                    return {"ok": True, "items": _CARRITO, "totales": _totales()}
                item["cantidad"] = body.cantidad
            if body.precio_unitario is not None:
                item["precio_unitario"] = float(body.precio_unitario)
            if body.descripcion is not None:
                d = str(body.descripcion).strip()
                if d:
                    item["descripcion"] = d.upper()
            item["subtotal"] = round(item["cantidad"] * item["precio_unitario"], 2)
            return {"ok": True, "items": _CARRITO, "totales": _totales()}
    raise HTTPException(404, "Ítem no está en el carrito")


@app.delete("/api/carrito/items/{item_id}")
def del_item(item_id: str):
    global _CARRITO
    _CARRITO = [i for i in _CARRITO if i["id"] != item_id]
    return {"ok": True, "items": _CARRITO, "totales": _totales()}


@app.post("/api/carrito/vaciar")
def vaciar():
    global _PRESUPUESTO_CARGADO, _NOTA, _UNDO_SNAP
    _guardar_undo()
    _CARRITO.clear()
    _PRESUPUESTO_CARGADO = None
    _NOTA = ""
    return {"ok": True, "items": _CARRITO, "totales": _totales()}


@app.post("/api/carrito/deshacer")
def deshacer():
    global _CARRITO, _CLIENTE, _NOTA, _VENDEDOR, _PRESUPUESTO_CARGADO, _UNDO_SNAP
    if not _UNDO_SNAP:
        raise HTTPException(400, "Nada para deshacer")
    snap = _UNDO_SNAP
    _UNDO_SNAP = None
    _CARRITO = [dict(i) for i in snap.get("items") or []]
    if snap.get("cliente"):
        _CLIENTE.update(snap["cliente"])
    _NOTA = str(snap.get("nota") or "")
    if snap.get("vendedor"):
        _VENDEDOR = str(snap["vendedor"])
    _PRESUPUESTO_CARGADO = snap.get("presupuesto_cargado_id")
    return {
        "ok": True,
        "mensaje": "Deshecho",
        "items": _CARRITO,
        "totales": _totales(),
        "cliente": _CLIENTE,
        "nota": _NOTA,
        "vendedor": _VENDEDOR,
        "presupuesto_cargado_id": _PRESUPUESTO_CARGADO,
        "presupuesto_cargado_nro": _nro_presupuesto_cargado(),
    }


@app.get("/api/carrito/espera")
def listar_espera():
    out = []
    for e in _ESPERA:
        tot = _totales_de(e.get("items") or [], e.get("cliente") or _CLIENTE)
        out.append(
            {
                "id": e["id"],
                "etiqueta": e.get("etiqueta") or e.get("cliente", {}).get("nombre") or "En espera",
                "creado": e.get("creado"),
                "items": tot["items"],
                "total_txt": tot["total_txt"],
            }
        )
    return {"resultados": out}


@app.post("/api/carrito/esperar")
def poner_en_espera(body: EsperarIn = EsperarIn()):
    global _CARRITO, _PRESUPUESTO_CARGADO, _NOTA, _UNDO_SNAP
    if not _CARRITO:
        raise HTTPException(400, "Carrito vacío")
    import uuid
    from datetime import datetime

    snap = _snapshot_estado()
    etq = (body.etiqueta or "").strip()
    if not etq:
        etq = str((_CLIENTE.get("nombre") or "CLIENTE")).upper()
    entrada = {
        "id": f"esp_{uuid.uuid4().hex[:8]}",
        "etiqueta": etq,
        "creado": datetime.now().strftime("%H:%M"),
        **snap,
    }
    _ESPERA.insert(0, entrada)
    del _ESPERA[12:]
    _CARRITO.clear()
    _PRESUPUESTO_CARGADO = None
    _NOTA = ""
    _UNDO_SNAP = None
    return {
        "ok": True,
        "mensaje": f"En espera: {etq}",
        "espera_id": entrada["id"],
        "items": _CARRITO,
        "totales": _totales(),
        "espera": listar_espera()["resultados"],
    }


@app.post("/api/carrito/espera/{esp_id}/retomar")
def retomar_espera(esp_id: str):
    global _CARRITO, _CLIENTE, _NOTA, _VENDEDOR, _PRESUPUESTO_CARGADO, _ESPERA, _UNDO_SNAP
    e = next((x for x in _ESPERA if x["id"] == esp_id), None)
    if e is None:
        raise HTTPException(404, "Carrito en espera no encontrado")
    if _CARRITO:
        poner_en_espera(EsperarIn(etiqueta=str(_CLIENTE.get("nombre") or "Actual")))
    _ESPERA = [x for x in _ESPERA if x["id"] != esp_id]
    _UNDO_SNAP = None
    _CARRITO = [dict(i) for i in e.get("items") or []]
    if e.get("cliente"):
        _CLIENTE.update(e["cliente"])
    _NOTA = str(e.get("nota") or "")
    if e.get("vendedor"):
        _VENDEDOR = str(e["vendedor"])
    _PRESUPUESTO_CARGADO = e.get("presupuesto_cargado_id")
    return {
        "ok": True,
        "mensaje": f"Retomado: {e.get('etiqueta')}",
        "items": _CARRITO,
        "totales": _totales(),
        "cliente": _CLIENTE,
        "nota": _NOTA,
        "vendedor": _VENDEDOR,
        "presupuesto_cargado_id": _PRESUPUESTO_CARGADO,
        "presupuesto_cargado_nro": _nro_presupuesto_cargado(),
        "espera": listar_espera()["resultados"],
    }


@app.delete("/api/carrito/espera/{esp_id}")
def borrar_espera(esp_id: str):
    global _ESPERA
    antes = len(_ESPERA)
    _ESPERA = [e for e in _ESPERA if e["id"] != esp_id]
    if len(_ESPERA) == antes:
        raise HTTPException(404, "No encontrado")
    return {"ok": True, "espera": listar_espera()["resultados"]}


@app.post("/api/carrito/restaurar")
def restaurar(body: RestaurarCarrito, request: Request):
    """Restaura carrito desde el navegador (tras F5)."""
    global _CARRITO, _CLIENTE, _NOTA, _PRESUPUESTO_CARGADO, _VENDEDOR
    items = []
    for it in body.items or []:
        if not isinstance(it, dict) or not it.get("id"):
            continue
        cant = max(1, int(it.get("cantidad") or 1))
        precio = float(it.get("precio_unitario") or 0)
        items.append(
            {
                "id": str(it.get("id")),
                "id_maestro": it.get("id_maestro") or it.get("codigo") or "",
                "codigo": it.get("codigo") or "",
                "descripcion": it.get("descripcion") or "",
                "marca": it.get("marca") or "",
                "precio_unitario": precio,
                "cantidad": cant,
                "subtotal": round(precio * cant, 2),
                "manual": bool(it.get("manual")),
            }
        )
    _CARRITO = items
    if body.cliente:
        _CLIENTE.update(body.cliente.model_dump())
        _CLIENTE["descuento"] = max(0.0, min(100.0, float(_CLIENTE.get("descuento") or 0)))
        if str(_CLIENTE.get("tipo_comprobante")) not in ("1", "6"):
            _CLIENTE["tipo_comprobante"] = "6"
    _NOTA = str(body.nota or "")
    if body.vendedor and request.state.usuario.get("rol") == "admin":
        _VENDEDOR = str(body.vendedor).strip() or _VENDEDOR
    _PRESUPUESTO_CARGADO = None
    return {
        "ok": True,
        "items": _CARRITO,
        "totales": _totales(),
        "cliente": _CLIENTE,
        "nota": _NOTA,
        "vendedor": _VENDEDOR,
    }


@app.get("/api/cliente")
def get_cliente():
    return _CLIENTE


@app.put("/api/cliente")
def put_cliente(body: ClienteIn):
    _CLIENTE.update(body.model_dump())
    _CLIENTE["descuento"] = max(0.0, min(100.0, float(_CLIENTE.get("descuento") or 0)))
    if str(_CLIENTE.get("tipo_comprobante")) not in ("1", "6"):
        _CLIENTE["tipo_comprobante"] = "6"
    return _CLIENTE


@app.get("/api/vendedor")
def get_vendedor():
    return {"vendedor": _VENDEDOR}


@app.put("/api/vendedor")
def put_vendedor(body: VendedorIn, request: Request):
    global _VENDEDOR
    usuario = request.state.usuario
    if usuario.get("rol") == "vendedor":
        _VENDEDOR = str(usuario.get("vendedor_id") or usuario["usuario"]).upper()
    else:
        _VENDEDOR = (body.vendedor or "CAJA").strip().upper() or "CAJA"
    return {"vendedor": _VENDEDOR}


@app.get("/api/presupuestos")
def api_listar_presupuestos(
    limite: int = 30,
    q: str = Query(""),
    incluir_anulados: bool = False,
    fecha_desde: str = Query(""),
    fecha_hasta: str = Query(""),
):
    return {
        "resultados": listar_presupuestos(
            limite=limite,
            q=q,
            incluir_anulados=incluir_anulados,
            fecha_desde=fecha_desde,
            fecha_hasta=fecha_hasta,
        )
    }


@app.post("/api/presupuestos/{pres_id}/anular")
def api_anular_presupuesto(pres_id: str):
    try:
        return anular_presupuesto(pres_id)
    except ValueError as e:
        raise HTTPException(404, str(e)) from e


@app.post("/api/presupuestos/emitir")
def api_emitir_presupuesto(body: PresupuestoIn = PresupuestoIn()):
    global _CARRITO, _PRESUPUESTO_CARGADO, _NOTA, _VENDEDOR
    if not _CARRITO:
        raise HTTPException(400, "Carrito vacío")
    if body.vendedor:
        _VENDEDOR = body.vendedor.strip().upper() or _VENDEDOR
    actualizar_id = _PRESUPUESTO_CARGADO if body.actualizar and _PRESUPUESTO_CARGADO else None
    try:
        r = emitir_presupuesto(
            list(_CARRITO),
            _CLIENTE,
            nota=body.nota or _NOTA,
            vendedor=_VENDEDOR,
            presupuesto_id=actualizar_id,
        )
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    except Exception as e:
        raise HTTPException(500, f"Error presupuesto: {e}") from e
    _CARRITO.clear()
    _PRESUPUESTO_CARGADO = None
    _NOTA = ""
    return r


def _aplicar_presupuesto_en_carrito(pres_id: str, *, duplicar: bool):
    global _CARRITO, _CLIENTE, _PRESUPUESTO_CARGADO, _NOTA
    r = cargar_en_carrito(pres_id)
    items = []
    for it in r["items"]:
        prod = producto_por_id(str(it.get("id") or "")) if it.get("id") else None
        stock = int(prod.get("stock") or 0) if prod else None
        row = dict(it)
        row["stock"] = stock
        items.append(row)
    _CARRITO = items
    _CLIENTE = dict(r["cliente"])
    _PRESUPUESTO_CARGADO = None if duplicar else r.get("presupuesto_id")
    _NOTA = ""
    msg = r["mensaje"]
    if duplicar:
        msg = f"Duplicado Nº {int(r.get('numero') or 0):04d} · al emitir sale con número nuevo"
    return {
        "ok": True,
        "mensaje": msg,
        "items": _CARRITO,
        "totales": _totales(),
        "cliente": _CLIENTE,
        "numero": r.get("numero"),
        "vendedor": _VENDEDOR,
        "presupuesto_cargado_id": _PRESUPUESTO_CARGADO,
        "presupuesto_cargado_nro": None if duplicar else r.get("numero"),
    }


@app.post("/api/presupuestos/{pres_id}/cargar")
def api_cargar_presupuesto(pres_id: str):
    try:
        return _aplicar_presupuesto_en_carrito(pres_id, duplicar=False)
    except ValueError as e:
        raise HTTPException(404, str(e)) from e


@app.post("/api/presupuestos/{pres_id}/duplicar")
def api_duplicar_presupuesto(pres_id: str):
    try:
        return _aplicar_presupuesto_en_carrito(pres_id, duplicar=True)
    except ValueError as e:
        raise HTTPException(404, str(e)) from e


@app.post("/api/presupuestos/{pres_id}/pdf")
def api_pdf_presupuesto(pres_id: str):
    try:
        return regenerar_pdf(pres_id)
    except ValueError as e:
        raise HTTPException(404, str(e)) from e


# Compat nombres viejos
@app.post("/api/venta/presupuesto")
def api_presupuesto_compat(body: PresupuestoIn = PresupuestoIn()):
    return api_emitir_presupuesto(body)


@app.post("/api/venta/factura")
def api_emitir_factura(
    body: FacturaIn, request: Request, background_tasks: BackgroundTasks
):
    global _CARRITO, _CLIENTE, _PRESUPUESTO_CARGADO
    if not body.confirmar:
        raise HTTPException(400, "Confirmá que querés emitir una factura real")
    if not _CARRITO:
        raise HTTPException(400, "Carrito vacío")
    if body.cliente:
        _CLIENTE.update(body.cliente.model_dump())
        _CLIENTE["descuento"] = max(
            0.0, min(100.0, float(_CLIENTE.get("descuento") or 0))
        )
        if str(_CLIENTE.get("tipo_comprobante")) not in ("1", "6"):
            _CLIENTE["tipo_comprobante"] = "6"
    if request.state.usuario.get("rol") == "admin" and body.vendedor:
        global _VENDEDOR
        _VENDEDOR = body.vendedor.strip().upper() or _VENDEDOR
    try:
        resultado = emitir_factura(
            [dict(i) for i in _CARRITO],
            dict(_CLIENTE),
            forma_pago=body.forma_pago,
            vendedor=_VENDEDOR,
            observacion=body.observacion,
            presupuesto_id=_PRESUPUESTO_CARGADO,
            cuotas=body.cuotas,
            interes_pct=body.interes_pct,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(502, str(exc)) from exc
    if resultado.get("id"):
        background_tasks.add_task(
            registrar_puntos_factura,
            _VENDEDOR,
            float(resultado.get("total") or 0),
            str(resultado["id"]),
        )
    _CARRITO = []
    _CLIENTE = {
        "nombre": "CONSUMIDOR FINAL",
        "cuit": "",
        "tipo_comprobante": "6",
        "descuento": 0.0,
        "telefono": "",
        "condicion_iva": "",
    }
    _PRESUPUESTO_CARGADO = None
    return resultado


@app.get("/api/facturas")
def api_listar_facturas(
    limite: int = 40,
    q: str = Query(""),
    fecha_desde: str = Query(""),
    fecha_hasta: str = Query(""),
):
    return {
        "resultados": listar_facturas(
            limite=limite,
            q=q,
            fecha_desde=fecha_desde,
            fecha_hasta=fecha_hasta,
        )
    }


@app.get("/api/facturas/{factura_id}/ticket")
def api_ticket_factura(factura_id: str):
    try:
        return regenerar_ticket_factura(factura_id)
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc


app.mount("/static", StaticFiles(directory=str(STATIC)), name="static")
