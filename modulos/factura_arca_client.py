"""Cliente HTTP para el backend de facturación ARCA/AFIP (facturahafid)."""
import requests

BASE_URL = "https://southamerica-east1-facturador-backend-2b9f5.cloudfunctions.net"


def _pick(d: dict, *keys):
    for k in keys:
        v = d.get(k)
        if v is not None and str(v).strip() != "":
            return v
    return None


def normalizar_respuesta_arca(data):
    """Unifica campos CAE / número desde distintas formas de respuesta del backend."""
    if not isinstance(data, dict):
        return {}

    cae = _pick(data, "cae", "CAE", "Cae")
    if cae:
        return {
            **data,
            "cae": str(cae),
            "vencimiento_cae": _pick(
                data,
                "vencimiento_cae",
                "vencimientoCae",
                "cae_vencimiento",
                "fecha_vencimiento_cae",
                "vto_cae",
            ),
            "punto_venta": _pick(data, "punto_venta", "puntoVenta", "PtoVta"),
            "numero_factura": _pick(
                data,
                "numero_factura",
                "numeroFactura",
                "CbteDesde",
                "numero",
                "nro_comprobante",
            ),
            "nombre_empresa": _pick(data, "nombre_empresa", "nombreEmpresa"),
            "direccion_empresa": _pick(data, "direccion_empresa", "direccionEmpresa"),
        }

    for key in ("resultado", "comprobante", "data", "factura", "response"):
        nested = data.get(key)
        if isinstance(nested, dict):
            flat = normalizar_respuesta_arca(nested)
            if flat.get("cae"):
                return {**data, **flat}
    return data


def generar_factura(cuit, clave, cliente, items, pago):
    url = f"{BASE_URL}/generar_factura"
    payload = {
        "cuit_facturador": cuit,
        "clave_secreta": clave,
        "datos_cliente": cliente,
        "items": items,
        "forma_pago": pago,
    }
    try:
        r = requests.post(url, json=payload, timeout=120)
        r.raise_for_status()
        body = r.json()
        if isinstance(body, dict):
            if body.get("success") is False:
                return {
                    "success": False,
                    "error": body.get("error") or body.get("message") or str(body),
                }
            if isinstance(body.get("data"), dict):
                body = body["data"]
        data = normalizar_respuesta_arca(body if isinstance(body, dict) else {})
        if not data.get("cae"):
            return {
                "success": False,
                "error": f"ARCA no devolvió CAE. Respuesta: {str(body)[:400]}",
            }
        return {"success": True, "data": data}
    except Exception as e:
        err = str(e)
        try:
            if hasattr(e, "response") and e.response is not None:
                err = e.response.text or err
        except Exception:
            pass
        return {"success": False, "error": err}


def obtener_historial(cuit, clave):
    url = f"{BASE_URL}/obtenerHistorial"
    try:
        r = requests.post(
            url, json={"cuit_facturador": cuit, "clave_secreta": clave}, timeout=60
        )
        return {"success": True, "data": r.json()}
    except Exception as e:
        return {"success": False, "error": str(e)}


def cargar_datos_nube(cuit, clave):
    url = f"{BASE_URL}/obtenerConfiguracion"
    payload = {"cuit_facturador": cuit, "clave_secreta": clave}
    try:
        r = requests.post(url, json=payload, timeout=30)
        if r.status_code == 200:
            return {"success": True, "data": r.json()}
        return {"success": False, "error": "Credenciales inválidas"}
    except Exception as e:
        return {"success": False, "error": str(e)}
