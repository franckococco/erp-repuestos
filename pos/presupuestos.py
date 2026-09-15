"""Presupuestos del POS: PDF, historial local y Firebase si hay claves."""
from __future__ import annotations

import base64
import json
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from inventory import _cred_path

VENDEDOR_POS = "POS_CAJA"
ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
LOCAL_FILE = DATA / "presupuestos_local.json"
CONTADOR_FILE = DATA / "contador_local.json"


def firebase_disponible() -> bool:
    return _cred_path() is not None


def _db():
    import firebase_admin
    from firebase_admin import credentials, firestore

    ruta = _cred_path()
    if ruta is None:
        raise RuntimeError("Sin credenciales Firebase")
    if not firebase_admin._apps:  # type: ignore[attr-defined]
        firebase_admin.initialize_app(credentials.Certificate(str(ruta)))
    return firestore.client()


def cliente_activo(cliente: Dict[str, Any]) -> Dict[str, Any]:
    cuit = re.sub(r"\D", "", str(cliente.get("cuit") or "")) or "00000000000"
    return {
        "nombre": str(cliente.get("nombre") or "CONSUMIDOR FINAL").upper(),
        "cuit": cuit,
        "descuento": float(cliente.get("descuento") or 0),
        "tipo_comprobante": "6",
        "etiqueta_descuento": str(cliente.get("etiqueta_descuento") or ""),
        "tipo_cliente": str(cliente.get("tipo_cliente") or "ocasional"),
        "telefono": str(cliente.get("telefono") or ""),
        "condicion_iva": str(cliente.get("condicion_iva") or ""),
    }


def totales(carrito: List[Dict[str, Any]], cliente: Dict[str, Any]) -> Tuple[float, float, float]:
    bruto = sum(float(i.get("subtotal") or 0) for i in carrito)
    desc = float(cliente.get("descuento") or 0)
    final = bruto * (1 - desc / 100.0)
    return round(bruto, 2), desc, round(final, 2)


def _leer_local() -> List[Dict[str, Any]]:
    if not LOCAL_FILE.exists():
        return []
    try:
        data = json.loads(LOCAL_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except Exception:
        return []


def _guardar_local_lista(items: List[Dict[str, Any]]) -> None:
    DATA.mkdir(parents=True, exist_ok=True)
    LOCAL_FILE.write_text(json.dumps(items, ensure_ascii=False, indent=2, default=str), encoding="utf-8")


def _siguiente_nro_local() -> int:
    DATA.mkdir(parents=True, exist_ok=True)
    anio = datetime.now().year
    data = {"anio": anio, "numero": 0}
    if CONTADOR_FILE.exists():
        try:
            data = json.loads(CONTADOR_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    if int(data.get("anio") or 0) != anio:
        data = {"anio": anio, "numero": 1}
    else:
        data["numero"] = int(data.get("numero") or 0) + 1
    CONTADOR_FILE.write_text(json.dumps(data), encoding="utf-8")
    return int(data["numero"])


def _siguiente_nro_firebase(db) -> int:
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


def _snap_items(carrito: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    out = []
    for item in carrito:
        out.append(
            {
                "id": item.get("id"),
                "id_maestro": item.get("id_maestro") or str(item.get("codigo") or ""),
                "codigo": item.get("codigo"),
                "marca": item.get("marca"),
                "descripcion": item.get("descripcion"),
                "precio_unitario": float(item.get("precio_unitario") or 0),
                "cantidad": int(item.get("cantidad") or 0),
                "subtotal": float(item.get("subtotal") or 0),
            }
        )
    return out


def _pdf_b64(
    carrito: List[Dict[str, Any]],
    cliente: Dict[str, Any],
    numero: Optional[int],
    nota: str = "",
    vendedor: str = "",
) -> str:
    from modulos.presupuesto_pdf import crear_pdf_presupuesto

    cli = cliente_activo(cliente)
    bruto, desc, _ = totales(carrito, cli)
    vend = (vendedor or VENDEDOR_POS).strip() or VENDEDOR_POS
    pdf_bytes = crear_pdf_presupuesto(
        vend,
        carrito,
        bruto,
        cliente=cli,
        descuento_pct=desc,
        numero=numero,
        nota=nota,
    )
    return base64.b64encode(pdf_bytes).decode("ascii")


def emitir(
    carrito: List[Dict[str, Any]],
    cliente: Dict[str, Any],
    nota: str = "",
    vendedor: str = "",
    presupuesto_id: Optional[str] = None,
) -> Dict[str, Any]:
    if not carrito:
        raise ValueError("Carrito vacío")
    cli = cliente_activo(cliente)
    bruto, desc, final = totales(carrito, cli)
    items = _snap_items(carrito)
    ahora = datetime.now(timezone.utc).isoformat()
    nota = str(nota or "").strip()
    vend = (vendedor or VENDEDOR_POS).strip() or VENDEDOR_POS

    # Actualizar uno ya cargado (mismo número)
    if presupuesto_id:
        existente = obtener(presupuesto_id)
        if existente and existente.get("estado") in (None, "", "abierto"):
            numero = int(existente.get("numero_presupuesto") or 0)
            pres_id = str(existente.get("id"))
            origen = str(existente.get("origen") or "local")
            registro = {
                "id": pres_id,
                "numero_presupuesto": numero,
                "vendedor": vend,
                "cliente": cli,
                "items": items,
                "total_bruto": bruto,
                "total_final": final,
                "descuento_pct": desc,
                "estado": "abierto",
                "nota": nota,
                "origen": origen,
                "creado": existente.get("creado") or ahora,
                "actualizado": ahora,
            }
            lista = _leer_local()
            found = False
            for i, p in enumerate(lista):
                if p.get("id") == pres_id:
                    lista[i] = registro
                    found = True
                    break
            if not found:
                lista.insert(0, registro)
            _guardar_local_lista(lista[:200])
            if firebase_disponible() and not str(pres_id).startswith("local_"):
                try:
                    _db().collection("presupuestos_guardados").document(pres_id).update(
                        {
                            "vendedor": vend,
                            "cliente": cli,
                            "items": items,
                            "total_bruto": bruto,
                            "total_final": final,
                            "descuento_pct": desc,
                            "nota": nota,
                            "actualizado": datetime.now(timezone.utc),
                        }
                    )
                    origen = "firebase"
                except Exception:
                    pass
            pdf_b64 = _pdf_b64(items, cli, numero, nota, vend)
            return {
                "ok": True,
                "origen": origen,
                "actualizado": True,
                "mensaje": f"Presupuesto Nº {numero:04d} actualizado",
                "numero": numero,
                "presupuesto_id": pres_id,
                "total": final,
                "total_txt": f"${final:,.2f}".replace(",", "X").replace(".", ",").replace("X", "."),
                "pdf_base64": pdf_b64,
                "pdf_nombre": f"Presupuesto_{numero:04d}.pdf",
                "cliente": cli["nombre"],
                "vendedor": vend,
            }

    origen = "local"
    numero: int
    pres_id: str
    mensaje: str

    if firebase_disponible():
        try:
            db = _db()
            numero = _siguiente_nro_firebase(db)
            ref = db.collection("presupuestos_guardados").document()
            ref.set(
                {
                    "vendedor": vend,
                    "cliente": cli,
                    "items": items,
                    "total_bruto": bruto,
                    "total_final": final,
                    "descuento_pct": desc,
                    "numero_presupuesto": numero,
                    "estado": "abierto",
                    "nota": nota,
                    "origen": "pos_caja",
                    "creado": datetime.now(timezone.utc),
                    "actualizado": datetime.now(timezone.utc),
                }
            )
            pres_id = ref.id
            origen = "firebase"
            mensaje = f"Presupuesto Nº {numero:04d} guardado en Firebase"
        except Exception as exc:
            numero = _siguiente_nro_local()
            pres_id = f"local_{uuid.uuid4().hex[:10]}"
            origen = "local"
            mensaje = f"Firebase falló ({exc}); guardado local Nº {numero:04d}"
    else:
        numero = _siguiente_nro_local()
        pres_id = f"local_{uuid.uuid4().hex[:10]}"
        mensaje = f"Presupuesto Nº {numero:04d} (local)"

    registro = {
        "id": pres_id,
        "numero_presupuesto": numero,
        "vendedor": vend,
        "cliente": cli,
        "items": items,
        "total_bruto": bruto,
        "total_final": final,
        "descuento_pct": desc,
        "estado": "abierto",
        "nota": nota,
        "origen": origen,
        "creado": ahora,
    }
    lista = _leer_local()
    lista.insert(0, registro)
    _guardar_local_lista(lista[:200])

    pdf_b64 = _pdf_b64(items, cli, numero, nota, vend)
    return {
        "ok": True,
        "origen": origen,
        "actualizado": False,
        "mensaje": mensaje,
        "numero": numero,
        "presupuesto_id": pres_id,
        "total": final,
        "total_txt": f"${final:,.2f}".replace(",", "X").replace(".", ",").replace("X", "."),
        "pdf_base64": pdf_b64,
        "pdf_nombre": f"Presupuesto_{numero:04d}.pdf",
        "cliente": cli["nombre"],
        "vendedor": vend,
    }


def _resumen(p: Dict[str, Any]) -> Dict[str, Any]:
    cli = p.get("cliente") or {}
    nro = p.get("numero_presupuesto")
    return {
        "id": p.get("id"),
        "numero": nro,
        "numero_txt": f"{int(nro):04d}" if nro else "—",
        "cliente": str(cli.get("nombre") or "CONSUMIDOR FINAL"),
        "cuit": str(cli.get("cuit") or ""),
        "total": float(p.get("total_final") or 0),
        "total_txt": f"${float(p.get('total_final') or 0):,.2f}".replace(",", "X").replace(".", ",").replace("X", "."),
        "items": len(p.get("items") or []),
        "estado": p.get("estado") or "abierto",
        "origen": p.get("origen") or "local",
        "creado": str(p.get("creado") or "")[:19].replace("T", " "),
        "nota": p.get("nota") or "",
    }


def listar(limite: int = 30, q: str = "", incluir_anulados: bool = False) -> List[Dict[str, Any]]:
    """Lista presupuestos: abiertos (+ anulados si se pide)."""
    out: List[Dict[str, Any]] = []
    vistos = set()
    filtro = (q or "").strip().upper()
    dig = "".join(c for c in (q or "") if c.isdigit())

    def _pasa_filtro(res: Dict[str, Any]) -> bool:
        if not filtro and not dig:
            return True
        blob = f"{res.get('numero_txt','')} {res.get('cliente','')} {res.get('cuit','')} {res.get('nota','')}".upper()
        if dig and dig in str(res.get("cuit") or ""):
            return True
        if dig and dig in str(res.get("numero") or ""):
            return True
        return filtro in blob if filtro else False

    def _pasa_estado(estado) -> bool:
        est = str(estado or "abierto").lower()
        if incluir_anulados:
            return est in ("abierto", "anulado", "")
        return est in ("abierto", "")

    if firebase_disponible():
        try:
            db = _db()
            from firebase_admin import firestore

            docs = (
                db.collection("presupuestos_guardados")
                .order_by("creado", direction=firestore.Query.DESCENDING)  # type: ignore
                .limit(max(limite * 3, 80))
                .stream()
            )
            for d in docs:
                data = {"id": d.id, **(d.to_dict() or {})}
                if not _pasa_estado(data.get("estado")):
                    continue
                res = _resumen(data)
                if not _pasa_filtro(res):
                    continue
                out.append(res)
                vistos.add(d.id)
                if len(out) >= limite:
                    return out
        except Exception:
            pass

    for p in _leer_local():
        pid = p.get("id")
        if pid in vistos:
            continue
        if not _pasa_estado(p.get("estado")):
            continue
        res = _resumen(p)
        if not _pasa_filtro(res):
            continue
        out.append(res)
        if len(out) >= limite:
            break
    return out[:limite]


def anular(pres_id: str) -> Dict[str, Any]:
    p = obtener(pres_id)
    if not p:
        raise ValueError("Presupuesto no encontrado")
    # Local
    lista = _leer_local()
    cambiado = False
    for item in lista:
        if item.get("id") == pres_id:
            item["estado"] = "anulado"
            cambiado = True
    if cambiado:
        _guardar_local_lista(lista)
    # Firebase
    if firebase_disponible() and not str(pres_id).startswith("local_"):
        try:
            _db().collection("presupuestos_guardados").document(str(pres_id)).update(
                {"estado": "anulado", "actualizado": datetime.now(timezone.utc)}
            )
        except Exception:
            pass
    nro = int(p.get("numero_presupuesto") or 0)
    return {"ok": True, "mensaje": f"Presupuesto Nº {nro:04d} anulado"}


def obtener(pres_id: str) -> Optional[Dict[str, Any]]:
    if not pres_id:
        return None
    for p in _leer_local():
        if p.get("id") == pres_id:
            return p
    if firebase_disponible():
        try:
            doc = _db().collection("presupuestos_guardados").document(str(pres_id)).get()
            if doc.exists:
                return {"id": doc.id, **(doc.to_dict() or {})}
        except Exception:
            pass
    return None


def cargar_en_carrito(pres_id: str) -> Dict[str, Any]:
    """Devuelve items + cliente para poner en el carrito del POS."""
    p = obtener(pres_id)
    if not p:
        raise ValueError("Presupuesto no encontrado")
    if p.get("estado") not in (None, "", "abierto"):
        raise ValueError(f"El presupuesto está {p.get('estado')}")
    items = []
    for it in p.get("items") or []:
        items.append(
            {
                "id": it.get("id"),
                "id_maestro": it.get("id_maestro") or it.get("codigo"),
                "codigo": it.get("codigo") or it.get("id_maestro") or "",
                "descripcion": it.get("descripcion"),
                "marca": it.get("marca") or "",
                "precio_unitario": float(it.get("precio_unitario") or 0),
                "cantidad": int(it.get("cantidad") or 1),
                "subtotal": float(it.get("subtotal") or 0),
            }
        )
    return {
        "ok": True,
        "mensaje": f"Cargado presupuesto Nº {int(p.get('numero_presupuesto') or 0):04d}",
        "items": items,
        "cliente": cliente_activo(p.get("cliente") or {}),
        "numero": p.get("numero_presupuesto"),
        "presupuesto_id": p.get("id"),
    }


def regenerar_pdf(pres_id: str) -> Dict[str, Any]:
    p = obtener(pres_id)
    if not p:
        raise ValueError("Presupuesto no encontrado")
    cli = cliente_activo(p.get("cliente") or {})
    items = p.get("items") or []
    numero = p.get("numero_presupuesto")
    vend = str(p.get("vendedor") or VENDEDOR_POS)
    pdf_b64 = _pdf_b64(items, cli, numero, p.get("nota") or "", vend)
    return {
        "ok": True,
        "pdf_base64": pdf_b64,
        "pdf_nombre": f"Presupuesto_{int(numero or 0):04d}.pdf",
        "mensaje": f"PDF Nº {int(numero or 0):04d}",
    }
