"""Ventas del POS: presupuesto PDF + factura ARCA/ticket, con fallback emulador."""
from __future__ import annotations

import base64
import os
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from inventory import _cred_path

VENDEDOR_POS = "POS_CAJA"
CUIT_EMISOR_ARCA = "20265010505"
CLAVE_EMISOR_ARCA = "111"


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
    cuit = re.sub(r"\D", "", str(cliente.get("cuit") or "")) or "00000000000"
    cbte = str(cliente.get("tipo_comprobante") or "6")
    if cbte not in ("1", "6"):
        cbte = "6"
    return {
        "nombre": str(cliente.get("nombre") or "CONSUMIDOR FINAL").upper(),
        "cuit": cuit,
        "descuento": float(cliente.get("descuento") or 0),
        "tipo_comprobante": cbte,
        "etiqueta_descuento": str(cliente.get("etiqueta_descuento") or ""),
        "tipo_cliente": str(cliente.get("tipo_cliente") or "ocasional"),
        "telefono": str(cliente.get("telefono") or ""),
        "condicion_iva": str(cliente.get("condicion_iva") or ""),
    }


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
    batch = db.batch()
    n = 0
    for item in carrito:
        item_id = str(item.get("id") or "")
        if item_id.upper().startswith("MANUAL_"):
            continue
        id_maestro = str(item.get("id_maestro") or item.get("codigo") or "")
        marca = str(item.get("marca") or "")
        if not id_maestro and "_" in item_id:
            id_maestro, marca = item_id.split("_", 1)
        if not id_maestro or not marca:
            continue
        cant = max(1, int(item.get("cantidad") or 1))
        ref = db.collection("productos").document(id_maestro)
        snap = ref.get()
        if not snap.exists:
            continue
        data = snap.to_dict() or {}
        ahora = datetime.now(timezone.utc)
        if "variantes" in data:
            batch.update(
                ref,
                {
                    f"variantes.{marca}.stock": firestore.Increment(-cant),  # type: ignore
                    f"variantes.{marca}.last_sale_at": ahora,
                    "ultima_actualizacion": ahora,
                },
            )
        else:
            batch.update(
                ref,
                {
                    "stock": firestore.Increment(-cant),  # type: ignore
                    "last_sale_at": ahora,
                    "ultima_actualizacion": ahora,
                },
            )
        n += 1
        if n % 400 == 0:
            batch.commit()
            batch = db.batch()
    if n % 400 != 0:
        batch.commit()
    try:
        from inventory import cargar_inventario

        cargar_inventario(force=True)
    except Exception:
        pass


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
            "<script>window.onload=function(){setTimeout(function(){window.print()},400)}</script></body>",
        )
    return html


def emitir_factura(
    carrito: List[Dict[str, Any]], cliente: Dict[str, Any], forma_pago: str = "Contado"
) -> Dict[str, Any]:
    if not carrito:
        raise ValueError("Carrito vacío")
    cli = _cliente_activo(cliente)
    if cli["tipo_comprobante"] == "1":
        dig = re.sub(r"\D", "", cli["cuit"])
        if len(dig) != 11 or set(dig) <= {"0"}:
            raise ValueError("Factura A requiere CUIT de 11 dígitos")

    bruto, desc, final = _totales(carrito, cli)
    items_fc = _items_factura(carrito, desc)
    datos_cli = {
        "cuit": cli["cuit"],
        "nombre": cli["nombre"],
        "cbte_tipo": cli["tipo_comprobante"],
        "telefono": cli.get("telefono") or "",
        "condicion_iva": cli.get("condicion_iva") or "",
    }
    letra = "A" if cli["tipo_comprobante"] == "1" else "B"

    r: Dict[str, Any] = {"success": False, "error": "ARCA deshabilitado en emulador"}
    if arca_habilitado():
        try:
            from modulos.factura_arca_client import generar_factura

            r = generar_factura(
                CUIT_EMISOR_ARCA, CLAVE_EMISOR_ARCA, datos_cli, items_fc, forma_pago
            )
        except Exception as exc:
            r = {"success": False, "error": str(exc)}
    else:
        r = {
            "success": False,
            "error": "Emulador: poné firebase_claves.json o POS_ARCA_REAL=1 para ARCA real",
        }

    if r.get("success") and isinstance(r.get("data"), dict):
        data = r["data"]
        stock_ok = False
        stock_msg = ""
        if firebase_disponible():
            try:
                _descontar_stock(carrito)
                stock_ok = True
                stock_msg = "Stock descontado"
                try:
                    db = _db()
                    db.collection("comprobantes_arca").document().set(
                        {
                            "vendedor": VENDEDOR_POS,
                            "cliente": datos_cli,
                            "cae": data.get("cae"),
                            "vencimiento_cae": data.get("vencimiento_cae"),
                            "punto_venta": data.get("punto_venta"),
                            "numero_factura": data.get("numero_factura"),
                            "nombre_empresa": data.get("nombre_empresa"),
                            "direccion_empresa": data.get("direccion_empresa"),
                            "items": items_fc,
                            "forma_pago": forma_pago,
                            "total": float(final),
                            "origen": "pos_caja",
                            "fecha": datetime.now(timezone.utc),
                        }
                    )
                except Exception:
                    pass
            except Exception as exc:
                stock_msg = f"CAE OK pero stock no descontado: {exc}"
        else:
            stock_msg = "CAE OK · stock no descontado (sin Firebase en esta PC)"

        from modulos.factura_arca_ticket_html import crear_ticket_html

        html = crear_ticket_html(
            data, datos_cli, items_fc, forma_pago=forma_pago, vendedor=VENDEDOR_POS
        )
        if "</body>" in html:
            html = html.replace(
                "</body>",
                "<script>window.onload=function(){setTimeout(function(){window.print()},400)}</script></body>",
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
            "nro": nro_txt,
            "cae": data.get("cae"),
            "total": final,
            "total_txt": f"${final:,.2f}".replace(",", "X").replace(".", ",").replace("X", "."),
            "ticket_html": html,
            "stock_ok": stock_ok,
            "stock_msg": stock_msg,
            "cliente": cli["nombre"],
        }

    # Fallback emulador
    html = _ticket_simulado(carrito, cli, final)
    err = (r.get("error") or "ARCA no disponible")[:180]
    return {
        "ok": True,
        "simulado": True,
        "mensaje": f"Emulador factura {letra} (ARCA: {err})",
        "nro": f"0007-{len(carrito):08d}",
        "total": final,
        "total_txt": f"${final:,.2f}".replace(",", "X").replace(".", ",").replace("X", "."),
        "ticket_html": html,
        "stock_ok": False,
        "stock_msg": "Simulación · sin descontar stock",
        "cliente": cli["nombre"],
        "nota": err,
    }


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
