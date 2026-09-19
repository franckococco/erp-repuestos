"""Ventas del POS: presupuesto PDF + factura ARCA/ticket, con fallback emulador."""
from __future__ import annotations

import base64
import os
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from inventory import _cred_path

VENDEDOR_POS = "POS_CAJA"
CUIT_EMISOR_ARCA = os.getenv("POS_ARCA_CUIT", "20265010505").strip()
CLAVE_EMISOR_ARCA = os.getenv("POS_ARCA_CLAVE", "111").strip()


def firebase_disponible() -> bool:
    return _cred_path() is not None


def arca_habilitado() -> bool:
    """ARCA real solo con Firebase o POS_ARCA_REAL=1 (evita facturar AFIP con muestra)."""
    if os.getenv("POS_ARCA_REAL", "").strip() in ("1", "true", "TRUE", "yes"):
        return True
    return firebase_disponible()


def _db():
    """Cliente Firestore ya inicializado por inventory o se crea acá."""
    import firebase_admin
    from firebase_admin import credentials, firestore

    ruta = _cred_path()
    if ruta is None:
        raise RuntimeError("Sin credenciales Firebase")
    if not firebase_admin._apps:  # type: ignore[attr-defined]
        firebase_admin.initialize_app(credentials.Certificate(str(ruta)))
    return firestore.client()


def _cliente_activo(cliente: Dict[str, Any]) -> Dict[str, Any]:
    from modulos.comprobante_contexto import condicion_iva_cliente

    cuit = re.sub(r"\D", "", str(cliente.get("cuit") or "")) or "00000000000"
    cbte = str(cliente.get("tipo_comprobante") or "6")
    if cbte not in ("1", "6"):
        cbte = "6"
    base = {
        "nombre": str(cliente.get("nombre") or "CONSUMIDOR FINAL").upper(),
        "cuit": cuit,
        "descuento": float(cliente.get("descuento") or 0),
        "tipo_comprobante": cbte,
        "etiqueta_descuento": str(cliente.get("etiqueta_descuento") or ""),
        "tipo_cliente": str(cliente.get("tipo_cliente") or "ocasional"),
        "telefono": str(cliente.get("telefono") or ""),
        "condicion_iva": str(cliente.get("condicion_iva") or ""),
        "cbte_tipo": cbte,
    }
    base["condicion_iva"] = condicion_iva_cliente(base)
    return base


def _totales(carrito: List[Dict[str, Any]], cliente: Dict[str, Any]) -> Tuple[float, float, float]:
    bruto = sum(float(i.get("subtotal") or 0) for i in carrito)
    desc = float(cliente.get("descuento") or 0)
    final = bruto * (1 - desc / 100.0)
    return round(bruto, 2), desc, round(final, 2)


def _items_factura(carrito: List[Dict[str, Any]], descuento_pct: float) -> List[Dict[str, Any]]:
    factor = 1.0 - float(descuento_pct) / 100.0
    out = []
    for item in carrito:
        cant = max(1, int(item.get("cantidad") or 1))
        precio_u = float(item.get("precio_unitario") or 0) * factor
        sub = float(item.get("subtotal") or precio_u * cant) * factor
        if sub <= 0 and precio_u > 0:
            sub = precio_u * cant
        codigo = str(item.get("codigo") or item.get("id_maestro") or "").strip()
        desc = str(item.get("descripcion") or "Artículo").strip() or "Artículo"
        out.append(
            {
                "codigo": codigo,
                "id_maestro": codigo or str(item.get("id", "")).split("_")[0],
                "descripcion": desc[:120],
                "cantidad": cant,
                "precio_unitario": round(precio_u, 2),
                "precio": round(sub, 2),
            }
        )
    return out


def _siguiente_nro_presupuesto(db) -> int:
    from firebase_admin import firestore

    ref = db.collection("configuracion").document("contador_presupuestos")

    @firestore.transactional  # type: ignore[attr-defined]
    def _txn(transaction):
        snap = ref.get(transaction=transaction)
        data = (snap.to_dict() or {}) if snap.exists else {}
        anio_actual = datetime.now(timezone.utc).year
        if data.get("anio") != anio_actual:
            numero = 1
        else:
            numero = int(data.get("numero", 0) or 0) + 1
        transaction.set(
            ref,
            {
                "anio": anio_actual,
                "numero": numero,
                "actualizado": datetime.now(timezone.utc),
            },
        )
        return numero

    return _txn(db.transaction())


def _guardar_presupuesto_firebase(
    carrito: List[Dict[str, Any]], cliente: Dict[str, Any], nota: str = ""
) -> Tuple[int, str]:
    db = _db()
    cli = _cliente_activo(cliente)
    bruto, desc, final = _totales(carrito, cli)
    items_snap = []
    for item in carrito:
        items_snap.append(
            {
                "id": item.get("id"),
                "id_maestro": item.get("id_maestro") or str(item.get("codigo") or ""),
                "marca": item.get("marca"),
                "descripcion": item.get("descripcion"),
                "precio_unitario": float(item.get("precio_unitario") or 0),
                "cantidad": int(item.get("cantidad") or 0),
                "subtotal": float(item.get("subtotal") or 0),
            }
        )
    ahora = datetime.now(timezone.utc)
    numero = _siguiente_nro_presupuesto(db)
    ref = db.collection("presupuestos_guardados").document()
    ref.set(
        {
            "vendedor": VENDEDOR_POS,
            "cliente": cli,
            "items": items_snap,
            "total_bruto": bruto,
            "total_final": final,
            "descuento_pct": desc,
            "numero_presupuesto": numero,
            "estado": "abierto",
            "nota": str(nota or "").strip(),
            "origen": "pos_caja",
            "creado": ahora,
            "actualizado": ahora,
        }
    )
    return numero, ref.id


def _descontar_stock(carrito: List[Dict[str, Any]]) -> None:
    from firebase_admin import firestore

    db = _db()
    cantidades: Dict[Tuple[str, str], int] = {}
    for item in carrito:
        item_id = str(item.get("id") or "")
        if item.get("manual") or item_id.upper().startswith("MANUAL_"):
            continue
        id_maestro = str(item.get("id_maestro") or item.get("codigo") or "")
        marca = str(item.get("marca") or "")
        if not id_maestro and "_" in item_id:
            id_maestro, marca = item_id.split("_", 1)
        if not id_maestro or not marca:
            continue
        cant = max(1, int(item.get("cantidad") or 1))
        clave = (id_maestro, marca)
        cantidades[clave] = cantidades.get(clave, 0) + cant

    ids_maestros = sorted({clave[0] for clave in cantidades})
    refs = {
        item_id: db.collection("productos").document(item_id)
        for item_id in ids_maestros
    }
    snaps = {snap.id: snap for snap in db.get_all(list(refs.values()))}
    batch = db.batch()
    pendientes = 0
    ahora = datetime.now(timezone.utc)
    for id_maestro in ids_maestros:
        snap = snaps.get(id_maestro)
        if snap is None or not snap.exists:
            continue
        data = snap.to_dict() or {}
        ref = refs[id_maestro]
        variantes = {
            marca: cant
            for (item_id, marca), cant in cantidades.items()
            if item_id == id_maestro
        }
        if "variantes" in data:
            updates: Dict[str, Any] = {
                "ultima_actualizacion": ahora,
            }
            for marca, cant in variantes.items():
                updates.update(
                    {
                    f"variantes.{marca}.stock": firestore.Increment(-cant),  # type: ignore
                    f"variantes.{marca}.last_sale_at": ahora,
                    }
                )
            batch.update(ref, updates)
        else:
            cant_total = sum(variantes.values())
            batch.update(
                ref,
                {
                    "stock": firestore.Increment(-cant_total),  # type: ignore
                    "last_sale_at": ahora,
                    "ultima_actualizacion": ahora,
                },
            )
        pendientes += 1
        if pendientes == 400:
            batch.commit()
            batch = db.batch()
            pendientes = 0
    if pendientes:
        batch.commit()

    from inventory import descontar_stock_local

    descontar_stock_local(carrito)


def _pdf_presupuesto_b64(
    carrito: List[Dict[str, Any]], cliente: Dict[str, Any], numero: Optional[int]
) -> str:
    from modulos.presupuesto_pdf import crear_pdf_presupuesto

    cli = _cliente_activo(cliente)
    bruto, desc, _ = _totales(carrito, cli)
    pdf_bytes = crear_pdf_presupuesto(
        VENDEDOR_POS,
        carrito,
        bruto,
        cliente=cli,
        descuento_pct=desc,
        numero=numero,
    )
    return base64.b64encode(pdf_bytes).decode("ascii")


def emitir_presupuesto(
    carrito: List[Dict[str, Any]], cliente: Dict[str, Any], nota: str = ""
) -> Dict[str, Any]:
    if not carrito:
        raise ValueError("Carrito vacío")
    cli = _cliente_activo(cliente)
    bruto, desc, final = _totales(carrito, cli)
    simulado = True
    numero: Optional[int] = None
    pres_id = None
    mensaje = "Presupuesto (emulador)"

    if firebase_disponible():
        try:
            numero, pres_id = _guardar_presupuesto_firebase(carrito, cli, nota)
            simulado = False
            mensaje = f"Presupuesto Nº {numero:04d} guardado"
        except Exception as exc:
            mensaje = f"No se pudo guardar en Firebase ({exc}); PDF local"
            numero = None

    pdf_b64 = _pdf_presupuesto_b64(carrito, cli, numero)
    nombre = f"Presupuesto_{numero:04d}.pdf" if numero else "Presupuesto_BORRADOR.pdf"
    return {
        "ok": True,
        "simulado": simulado,
        "mensaje": mensaje,
        "numero": numero,
        "presupuesto_id": pres_id,
        "total": final,
        "total_txt": f"${final:,.2f}".replace(",", "X").replace(".", ",").replace("X", "."),
        "pdf_base64": pdf_b64,
        "pdf_nombre": nombre,
        "cliente": cli["nombre"],
    }


def _ticket_simulado(
    carrito: List[Dict[str, Any]], cliente: Dict[str, Any], total: float
) -> str:
    from modulos.factura_arca_ticket_html import crear_ticket_html

    cli = _cliente_activo(cliente)
    items = _items_factura(carrito, cli["descuento"])
    datos = {
        "cae": "00000000000000",
        "vencimiento_cae": "2099-12-31",
        "punto_venta": 7,
        "numero_factura": len(carrito),
        "nombre_empresa": "HAFID AUTOPARTES (EMULADOR)",
        "direccion_empresa": "Emulador local — sin ARCA",
    }
    datos_cli = {
        "cuit": cli["cuit"],
        "nombre": cli["nombre"],
        "cbte_tipo": cli["tipo_comprobante"],
        "condicion_iva": cli.get("condicion_iva") or "",
        "telefono": cli.get("telefono") or "",
    }
    html = crear_ticket_html(
        datos, datos_cli, items, forma_pago="Contado", vendedor=VENDEDOR_POS
    )
    # Auto-print al abrir
    if "</body>" in html:
        html = html.replace(
            "</body>",
            "<script>window.addEventListener('afterprint',function(){window.close()});"
            "window.onload=function(){setTimeout(function(){window.print()},100)}</script></body>",
        )
    return html


def _registrar_puntos_vendedor(db, vendedor: str, monto: float, ref_id: str) -> str:
    vendedor_id = str(vendedor or "").strip().lower().replace(" ", "_")[:80]
    if not vendedor_id or not ref_id:
        return ""
    ya = (
        db.collection("puntos_movimientos")
        .where("ref_id", "==", str(ref_id))
        .limit(1)
        .stream()
    )
    if list(ya):
        return "Puntos ya registrados"
    ref = db.collection("vendedores").document(vendedor_id)
    snap = ref.get()
    data = snap.to_dict() or {}
    acumulado = float(data.get("ventas_acumuladas") or 0) + float(monto or 0)
    puntos_antes = int(data.get("puntos") or 0)
    ganados = int(acumulado // 100_000)
    acumulado = round(acumulado % 100_000, 2)
    puntos_total = puntos_antes + ganados
    ahora = datetime.now(timezone.utc)
    ref.set(
        {
            "nombre": data.get("nombre") or str(vendedor).title(),
            "rol": "vendedor",
            "puntos": puntos_total,
            "ventas_acumuladas": acumulado,
            "activo": True,
            "ultima_venta": ahora,
        },
        merge=True,
    )
    db.collection("puntos_movimientos").add(
        {
            "vendedor_id": vendedor_id,
            "monto": float(monto),
            "puntos_ganados": ganados,
            "puntos_total_despues": puntos_total,
            "ventas_acumuladas_despues": acumulado,
            "origen": "comprobante_arca_pos",
            "ref_id": str(ref_id),
            "fecha": ahora,
        }
    )
    return (
        f"+{ganados} punto(s)"
        if ganados
        else f"Faltan ${100_000 - acumulado:,.0f} para el próximo punto"
    )


def registrar_puntos_factura(vendedor: str, monto: float, ref_id: str) -> None:
    """Tarea posterior a la respuesta: no demora la apertura del ticket."""
    try:
        _registrar_puntos_vendedor(_db(), vendedor, monto, ref_id)
    except Exception as exc:
        print(f"[POS] No se pudieron registrar puntos de {ref_id}: {exc}", flush=True)


_CONTADOR_INTERNO_LOCAL = 0
_ALERTAS_ADMIN_LOCAL: List[Dict[str, Any]] = []


def _forma_pago_financiada(
    forma_pago: str, cuotas: int, interes_pct: float
) -> Tuple[str, int, float, bool]:
    forma_pago_base = str(forma_pago or "Contado")
    es_tarjeta = forma_pago_base.lower() == "tarjeta"
    cuotas_n = max(1, min(48, int(cuotas or 1))) if es_tarjeta else 1
    interes_n = (
        max(0.0, min(100.0, float(interes_pct or 0))) if es_tarjeta else 0.0
    )
    forma = forma_pago_base
    if es_tarjeta:
        forma = f"Tarjeta · {cuotas_n} cuota(s)"
        if interes_n:
            forma += f" · interés {interes_n:g}%"
    return forma, cuotas_n, interes_n, es_tarjeta


def _validar_stock_carrito(
    carrito: List[Dict[str, Any]], *, permitir_sin_stock: bool
) -> None:
    if permitir_sin_stock:
        return
    for item in carrito:
        if item.get("manual"):
            continue
        stock = item.get("stock")
        cantidad = max(1, int(item.get("cantidad") or 1))
        if stock is not None and cantidad > int(stock):
            raise ValueError(
                f"Stock insuficiente para {item.get('descripcion') or item.get('codigo')}: "
                f"hay {stock} y se pidieron {cantidad}"
            )


def _clasificar_alertas_admin(carrito: List[Dict[str, Any]]) -> Tuple[List[Dict], List[Dict]]:
    sin_stock: List[Dict[str, Any]] = []
    manuales: List[Dict[str, Any]] = []
    for item in carrito:
        cant = max(1, int(item.get("cantidad") or 1))
        base = {
            "codigo": str(item.get("codigo") or "").strip(),
            "descripcion": str(item.get("descripcion") or "Artículo").strip(),
            "cantidad": cant,
            "precio_unitario": float(item.get("precio_unitario") or 0),
            "stock": item.get("stock"),
            "manual": bool(item.get("manual")),
        }
        if item.get("manual") or not base["codigo"]:
            manuales.append(dict(base))
        stock = item.get("stock")
        if not item.get("manual") and stock is not None and cant > int(stock):
            sin_stock.append(dict(base))
    return sin_stock, manuales


def registrar_alerta_alta_producto(
    *,
    codigo: str,
    descripcion: str,
    marca: str,
    precio: float,
    stock: int,
    vendedor: str,
) -> None:
    """Avisa al admin que se dio de alta un producto desde caja (para pedido/stock)."""
    global _ALERTAS_ADMIN_LOCAL
    ahora = datetime.now(timezone.utc)
    fila = {
        "tipo": "alta_pos",
        "item": {
            "codigo": str(codigo or "").strip(),
            "descripcion": str(descripcion or "").strip(),
            "marca": str(marca or "").strip(),
            "cantidad": max(0, int(stock or 0)),
            "precio_unitario": float(precio or 0),
            "stock": max(0, int(stock or 0)),
            "manual": False,
        },
        "vendedor": str(vendedor or VENDEDOR_POS),
        "ref_id": str(codigo or "").strip(),
        "origen": "alta_pos",
        "fecha": ahora,
        "resuelto": False,
    }
    if firebase_disponible():
        try:
            ref = _db().collection("pos_admin_alertas").document()
            ref.set(fila)
            return
        except Exception as exc:
            print(f"[POS] alerta alta_pos Firebase: {exc}", flush=True)
    fila["id"] = f"local-alta-{len(_ALERTAS_ADMIN_LOCAL) + 1}"
    fila["fecha"] = ahora.isoformat()
    _ALERTAS_ADMIN_LOCAL.insert(0, fila)


def _guardar_alertas_admin(
    *,
    sin_stock: List[Dict[str, Any]],
    manuales: List[Dict[str, Any]],
    vendedor: str,
    ref_id: str,
    origen: str,
) -> None:
    global _ALERTAS_ADMIN_LOCAL
    ahora = datetime.now(timezone.utc)
    filas: List[Dict[str, Any]] = []
    for item in sin_stock:
        filas.append(
            {
                "tipo": "sin_stock",
                "item": item,
                "vendedor": str(vendedor or VENDEDOR_POS),
                "ref_id": ref_id,
                "origen": origen,
                "fecha": ahora,
                "resuelto": False,
            }
        )
    for item in manuales:
        filas.append(
            {
                "tipo": "manual",
                "item": item,
                "vendedor": str(vendedor or VENDEDOR_POS),
                "ref_id": ref_id,
                "origen": origen,
                "fecha": ahora,
                "resuelto": False,
            }
        )
    if not filas:
        return
    if firebase_disponible():
        try:
            db = _db()
            batch = db.batch()
            for fila in filas:
                ref = db.collection("pos_admin_alertas").document()
                batch.set(ref, fila)
                fila["id"] = ref.id
            batch.commit()
            return
        except Exception as exc:
            print(f"[POS] No se pudieron guardar alertas admin: {exc}", flush=True)
    for fila in filas:
        fila["id"] = f"local-{len(_ALERTAS_ADMIN_LOCAL) + 1}"
        fila["fecha"] = ahora.isoformat()
        _ALERTAS_ADMIN_LOCAL.insert(0, fila)


def listar_alertas_admin(limite: int = 80) -> List[Dict[str, Any]]:
    if firebase_disponible():
        try:
            from firebase_admin import firestore

            docs = (
                _db()
                .collection("pos_admin_alertas")
                .order_by("fecha", direction=firestore.Query.DESCENDING)  # type: ignore
                .limit(max(80, limite * 2))
                .stream()
            )
            out = []
            for doc in docs:
                data = doc.to_dict() or {}
                if data.get("resuelto"):
                    continue
                data["id"] = doc.id
                fecha = data.get("fecha")
                if hasattr(fecha, "isoformat"):
                    data["fecha"] = fecha.isoformat()
                out.append(data)
                if len(out) >= limite:
                    break
            return out
        except Exception as exc:
            print(f"[POS] listar alertas admin: {exc}", flush=True)
    return [dict(a) for a in _ALERTAS_ADMIN_LOCAL if not a.get("resuelto")][:limite]


def resolver_alerta_admin(alerta_id: str) -> bool:
    global _ALERTAS_ADMIN_LOCAL
    aid = str(alerta_id or "").strip()
    if not aid:
        return False
    if firebase_disponible() and not aid.startswith("local-"):
        try:
            _db().collection("pos_admin_alertas").document(aid).update(
                {"resuelto": True, "resuelto_en": datetime.now(timezone.utc)}
            )
            return True
        except Exception:
            return False
    for fila in _ALERTAS_ADMIN_LOCAL:
        if fila.get("id") == aid:
            fila["resuelto"] = True
            return True
    return False


def _siguiente_nro_interno(db) -> Tuple[int, int]:
    from firebase_admin import firestore

    ref = db.collection("configuracion").document("contador_comprobantes_internos")
    pto = int(os.getenv("POS_PUNTO_VENTA_INTERNO", "1") or 1)

    @firestore.transactional  # type: ignore[attr-defined]
    def _txn(transaction):
        snap = ref.get(transaction=transaction)
        data = (snap.to_dict() or {}) if snap.exists else {}
        numero = int(data.get("numero", 0) or 0) + 1
        transaction.set(
            ref,
            {
                "numero": numero,
                "punto_venta": pto,
                "actualizado": datetime.now(timezone.utc),
            },
            merge=True,
        )
        return pto, numero

    return _txn(db.transaction())


def _html_ticket_imprimible(
    data: Dict[str, Any],
    datos_cli: Dict[str, Any],
    items_fc: List[Dict[str, Any]],
    *,
    forma_pago: str,
    vendedor: str,
    observacion: str,
) -> str:
    from modulos.factura_arca_ticket_html import crear_ticket_html

    html = crear_ticket_html(
        data,
        datos_cli,
        items_fc,
        forma_pago=forma_pago,
        vendedor=str(vendedor or VENDEDOR_POS),
        observacion=str(observacion or "").strip(),
    )
    if "</body>" in html:
        html = html.replace(
            "</body>",
            "<script>window.addEventListener('afterprint',function(){window.close()});"
            "window.onload=function(){setTimeout(function(){window.print()},100)}</script></body>",
        )
    return html


def emitir_factura(
    carrito: List[Dict[str, Any]],
    cliente: Dict[str, Any],
    forma_pago: str = "Contado",
    vendedor: str = VENDEDOR_POS,
    observacion: str = "",
    presupuesto_id: Optional[str] = None,
    cuotas: int = 1,
    interes_pct: float = 0.0,
    permitir_sin_stock: bool = False,
) -> Dict[str, Any]:
    if not carrito:
        raise ValueError("Carrito vacío")
    cli = _cliente_activo(cliente)
    if cli["tipo_comprobante"] == "1":
        dig = re.sub(r"\D", "", cli["cuit"])
        if len(dig) != 11 or set(dig) <= {"0"}:
            raise ValueError("Factura A requiere CUIT de 11 dígitos")
    _validar_stock_carrito(carrito, permitir_sin_stock=permitir_sin_stock)

    bruto, desc, final_base = _totales(carrito, cli)
    forma_pago, cuotas, interes_pct, _ = _forma_pago_financiada(
        forma_pago, cuotas, interes_pct
    )
    final = round(final_base * (1 + interes_pct / 100.0), 2)
    if final <= 0:
        raise ValueError("El total de la factura debe ser mayor a cero")
    items_fc = _items_factura(carrito, desc)
    if interes_pct:
        factor = 1 + interes_pct / 100.0
        for item in items_fc:
            item["precio_unitario"] = round(float(item["precio_unitario"]) * factor, 2)
            item["precio"] = round(float(item["precio"]) * factor, 2)
    datos_cli = {
        "cuit": cli["cuit"],
        "nombre": cli["nombre"],
        "cbte_tipo": cli["tipo_comprobante"],
        "telefono": cli.get("telefono") or "",
        "condicion_iva": cli.get("condicion_iva") or "",
    }
    letra = "A" if cli["tipo_comprobante"] == "1" else "B"
    alertas_sin, alertas_man = _clasificar_alertas_admin(carrito)

    if not arca_habilitado():
        raise RuntimeError("ARCA no está habilitado en esta PC")
    db = _db()

    try:
        from modulos.factura_arca_client import generar_factura

        r = generar_factura(
            CUIT_EMISOR_ARCA, CLAVE_EMISOR_ARCA, datos_cli, items_fc, forma_pago
        )
    except Exception as exc:
        raise RuntimeError(f"No se pudo consultar ARCA: {exc}") from exc

    if not r.get("success") or not isinstance(r.get("data"), dict):
        raise RuntimeError(str(r.get("error") or "ARCA rechazó la factura"))
    data = r["data"]
    if not data.get("cae"):
        raise RuntimeError("ARCA no devolvió un CAE")

    ahora = datetime.now(timezone.utc)
    ref = db.collection("comprobantes_arca").document()
    ref.set(
        {
            "vendedor": str(vendedor or VENDEDOR_POS),
            "cliente": datos_cli,
            "cae": data.get("cae"),
            "vencimiento_cae": data.get("vencimiento_cae"),
            "punto_venta": data.get("punto_venta"),
            "numero_factura": data.get("numero_factura"),
            "nombre_empresa": data.get("nombre_empresa"),
            "direccion_empresa": data.get("direccion_empresa"),
            "items": items_fc,
            "forma_pago": forma_pago,
            "cuotas": cuotas,
            "interes_pct": interes_pct,
            "total_base": float(final_base),
            "total": float(final),
            "observacion": str(observacion or "").strip(),
            "tipo_comprobante": cli["tipo_comprobante"],
            "presupuesto_id": presupuesto_id,
            "origen": "pos_caja",
            "permitir_sin_stock": bool(permitir_sin_stock),
            "fecha": ahora,
        }
    )

    stock_ok = False
    stock_msg = ""
    try:
        _descontar_stock(carrito)
        stock_ok = True
        stock_msg = "Stock descontado"
    except Exception as exc:
        stock_msg = f"CAE OK pero stock no descontado: {exc}"

    if presupuesto_id:
        try:
            db.collection("presupuestos_guardados").document(str(presupuesto_id)).update(
                {"estado": "facturado", "factura_id": ref.id, "actualizado": ahora}
            )
        except Exception:
            pass

    _guardar_alertas_admin(
        sin_stock=alertas_sin,
        manuales=alertas_man,
        vendedor=str(vendedor or VENDEDOR_POS),
        ref_id=ref.id,
        origen="factura_arca",
    )

    html = _html_ticket_imprimible(
        data,
        datos_cli,
        items_fc,
        forma_pago=forma_pago,
        vendedor=str(vendedor or VENDEDOR_POS),
        observacion=str(observacion or "").strip(),
    )
    try:
        pto = int(float(data.get("punto_venta") or 0))
        nro = int(float(data.get("numero_factura") or 0))
        nro_txt = f"{pto:04d}-{nro:08d}"
    except (TypeError, ValueError):
        nro_txt = "—"
    return {
        "ok": True,
        "simulado": False,
        "mensaje": f"Factura {letra} {nro_txt} · CAE {data.get('cae')}",
        "id": ref.id,
        "nro": nro_txt,
        "cae": data.get("cae"),
        "total": final,
        "total_txt": f"${final:,.2f}".replace(",", "X").replace(".", ",").replace("X", "."),
        "ticket_html": html,
        "stock_ok": stock_ok,
        "stock_msg": stock_msg,
        "puntos_pendientes": True,
        "cuotas": cuotas,
        "interes_pct": interes_pct,
        "total_base": final_base,
        "valor_cuota": round(final / cuotas, 2),
        "cliente": cli["nombre"],
    }


def emitir_comprobante_interno(
    carrito: List[Dict[str, Any]],
    cliente: Dict[str, Any],
    forma_pago: str = "Contado",
    vendedor: str = VENDEDOR_POS,
    observacion: str = "",
    presupuesto_id: Optional[str] = None,
    cuotas: int = 1,
    interes_pct: float = 0.0,
    permitir_sin_stock: bool = False,
) -> Dict[str, Any]:
    """Ticket igual al fiscal, sin CAE/QR y sin leyendas de validez."""
    global _CONTADOR_INTERNO_LOCAL
    if not carrito:
        raise ValueError("Carrito vacío")
    cli = _cliente_activo(cliente)
    _validar_stock_carrito(carrito, permitir_sin_stock=permitir_sin_stock)

    bruto, desc, final_base = _totales(carrito, cli)
    forma_pago, cuotas, interes_pct, _ = _forma_pago_financiada(
        forma_pago, cuotas, interes_pct
    )
    final = round(final_base * (1 + interes_pct / 100.0), 2)
    if final <= 0:
        raise ValueError("El total debe ser mayor a cero")
    items_fc = _items_factura(carrito, desc)
    if interes_pct:
        factor = 1 + interes_pct / 100.0
        for item in items_fc:
            item["precio_unitario"] = round(float(item["precio_unitario"]) * factor, 2)
            item["precio"] = round(float(item["precio"]) * factor, 2)
    datos_cli = {
        "cuit": cli["cuit"],
        "nombre": cli["nombre"],
        "cbte_tipo": cli["tipo_comprobante"],
        "telefono": cli.get("telefono") or "",
        "condicion_iva": cli.get("condicion_iva") or "",
    }
    letra = "A" if cli["tipo_comprobante"] == "1" else "B"
    alertas_sin, alertas_man = _clasificar_alertas_admin(carrito)
    ahora = datetime.now(timezone.utc)
    simulado = not firebase_disponible()
    ref_id = f"local-int-{_CONTADOR_INTERNO_LOCAL + 1}"
    pto = int(os.getenv("POS_PUNTO_VENTA_INTERNO", "1") or 1)
    nro = _CONTADOR_INTERNO_LOCAL + 1

    if firebase_disponible():
        db = _db()
        pto, nro = _siguiente_nro_interno(db)
        ref = db.collection("comprobantes_internos").document()
        ref.set(
            {
                "vendedor": str(vendedor or VENDEDOR_POS),
                "cliente": datos_cli,
                "cae": "",
                "vencimiento_cae": "",
                "punto_venta": pto,
                "numero_factura": nro,
                "items": items_fc,
                "forma_pago": forma_pago,
                "cuotas": cuotas,
                "interes_pct": interes_pct,
                "total_base": float(final_base),
                "total": float(final),
                "observacion": str(observacion or "").strip(),
                "tipo_comprobante": cli["tipo_comprobante"],
                "presupuesto_id": presupuesto_id,
                "origen": "pos_interno",
                "permitir_sin_stock": bool(permitir_sin_stock),
                "fecha": ahora,
            }
        )
        ref_id = ref.id
        simulado = False
        if presupuesto_id:
            try:
                db.collection("presupuestos_guardados").document(str(presupuesto_id)).update(
                    {
                        "estado": "comprobante_interno",
                        "comprobante_interno_id": ref_id,
                        "actualizado": ahora,
                    }
                )
            except Exception:
                pass
    else:
        _CONTADOR_INTERNO_LOCAL = nro

    stock_ok = False
    stock_msg = ""
    try:
        if firebase_disponible():
            _descontar_stock(carrito)
        else:
            from inventory import descontar_stock_local

            descontar_stock_local(carrito)
        stock_ok = True
        stock_msg = "Stock descontado"
    except Exception as exc:
        stock_msg = f"Comprobante OK pero stock no descontado: {exc}"

    _guardar_alertas_admin(
        sin_stock=alertas_sin,
        manuales=alertas_man,
        vendedor=str(vendedor or VENDEDOR_POS),
        ref_id=ref_id,
        origen="comprobante_interno",
    )

    from modulos.util_branding import NOMBRE_EMPRESA

    data = {
        "cae": "",
        "vencimiento_cae": "",
        "punto_venta": pto,
        "numero_factura": nro,
        "nombre_empresa": NOMBRE_EMPRESA,
        "direccion_empresa": "",
    }
    html = _html_ticket_imprimible(
        data,
        datos_cli,
        items_fc,
        forma_pago=forma_pago,
        vendedor=str(vendedor or VENDEDOR_POS),
        observacion=str(observacion or "").strip(),
    )
    nro_txt = f"{pto:04d}-{nro:08d}"
    return {
        "ok": True,
        "simulado": simulado,
        "interno": True,
        "mensaje": f"Comprobante {letra} {nro_txt}",
        "id": ref_id,
        "nro": nro_txt,
        "cae": "",
        "total": final,
        "total_txt": f"${final:,.2f}".replace(",", "X").replace(".", ",").replace("X", "."),
        "ticket_html": html,
        "stock_ok": stock_ok,
        "stock_msg": stock_msg,
        "puntos_pendientes": True,
        "cuotas": cuotas,
        "interes_pct": interes_pct,
        "total_base": final_base,
        "valor_cuota": round(final / cuotas, 2),
        "cliente": cli["nombre"],
    }


def listar_facturas(
    limite: int = 40,
    q: str = "",
    fecha_desde: str = "",
    fecha_hasta: str = "",
) -> List[Dict[str, Any]]:
    if not firebase_disponible():
        return []
    from firebase_admin import firestore

    docs = (
        _db()
        .collection("comprobantes_arca")
        .order_by("fecha", direction=firestore.Query.DESCENDING)  # type: ignore
        .limit(max(120, limite * 3))
        .stream()
    )
    texto = str(q or "").strip().upper()
    digitos = re.sub(r"\D", "", str(q or ""))
    desde = str(fecha_desde or "")[:10]
    hasta = str(fecha_hasta or "")[:10]
    resultados: List[Dict[str, Any]] = []
    for doc in docs:
        data = doc.to_dict() or {}
        cli = data.get("cliente") or {}
        raw_fecha = data.get("fecha")
        fecha = raw_fecha.date().isoformat() if isinstance(raw_fecha, datetime) else str(raw_fecha or "")[:10]
        if desde and fecha < desde:
            continue
        if hasta and fecha > hasta:
            continue
        try:
            nro = f"{int(float(data.get('punto_venta') or 0)):04d}-{int(float(data.get('numero_factura') or 0)):08d}"
        except (TypeError, ValueError):
            nro = "—"
        blob = f"{nro} {data.get('cae','')} {cli.get('nombre','')} {cli.get('cuit','')}".upper()
        if texto and texto not in blob and (not digitos or digitos not in re.sub(r"\D", "", blob)):
            continue
        total = float(data.get("total") or 0)
        resultados.append(
            {
                "id": doc.id,
                "numero": nro,
                "letra": "A" if str(data.get("tipo_comprobante") or cli.get("cbte_tipo")) == "1" else "B",
                "cliente": cli.get("nombre") or "CONSUMIDOR FINAL",
                "cuit": cli.get("cuit") or "",
                "fecha": fecha,
                "cae": str(data.get("cae") or ""),
                "forma_pago": data.get("forma_pago") or "",
                "total": total,
                "total_txt": f"${total:,.2f}".replace(",", "X").replace(".", ",").replace("X", "."),
            }
        )
        if len(resultados) >= limite:
            break
    return resultados


def regenerar_ticket_factura(factura_id: str) -> Dict[str, Any]:
    doc = _db().collection("comprobantes_arca").document(str(factura_id)).get()
    if not doc.exists:
        raise ValueError("Factura no encontrada")
    data = doc.to_dict() or {}
    cli = data.get("cliente") or {}
    respuesta = {
        "cae": data.get("cae"),
        "vencimiento_cae": data.get("vencimiento_cae"),
        "punto_venta": data.get("punto_venta"),
        "numero_factura": data.get("numero_factura"),
        "nombre_empresa": data.get("nombre_empresa"),
        "direccion_empresa": data.get("direccion_empresa"),
    }
    from modulos.factura_arca_ticket_html import crear_ticket_html

    html = crear_ticket_html(
        respuesta,
        cli,
        data.get("items") or [],
        forma_pago=data.get("forma_pago") or "Contado",
        vendedor=data.get("vendedor") or VENDEDOR_POS,
        observacion=data.get("observacion") or "",
    )
    return {"ok": True, "ticket_html": html}


def buscar_cliente(termino: str, limite: int = 15) -> List[Dict[str, Any]]:
    if not firebase_disponible() or not (termino or "").strip():
        return []
    db = _db()
    docs = db.collection("clientes").limit(400).stream()
    t = termino.strip().upper()
    dig = re.sub(r"\D", "", termino)
    hits = []
    for d in docs:
        data = d.to_dict() or {}
        nombre = str(data.get("nombre") or "").upper()
        cuit = str(d.id)
        if dig and dig in cuit:
            hits.append({"cuit": cuit, **_cliente_activo({**data, "cuit": cuit})})
        elif t and t in nombre:
            hits.append({"cuit": cuit, **_cliente_activo({**data, "cuit": cuit})})
        if len(hits) >= limite:
            break
    return hits
