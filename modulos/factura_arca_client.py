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


def consultar_cuit(cuit_facturador, clave, cuit_consultar):
    """Consulta padrón: primero WS local constancia_inscripcion, luego Cloud Function."""
    dig = "".join(c for c in str(cuit_consultar or "") if c.isdigit())

    # 1) Web Service oficial en esta PC (certificados AFIP)
    try:
        from modulos.afip_constancia import certificados_disponibles, consultar_persona

        if certificados_disponibles():
            data = consultar_persona(dig)
            return {"success": True, "data": data}
    except Exception as exc:
        local_err = str(exc)
    else:
        local_err = ""

    # 2) Cloud Function (si ya tiene /consultar_cuit)
    url = f"{BASE_URL}/consultar_cuit"
    payload = {
        "cuit_facturador": cuit_facturador,
        "clave_secreta": clave,
        "cuit": dig,
        "cuit_consultar": dig,
    }
    try:
        r = requests.post(url, json=payload, timeout=45)
        if r.status_code == 404:
            msg = (
                local_err
                or "Faltan certificados AFIP en esta PC "
                "(POS_AFIP_CERT / POS_AFIP_KEY o carpeta certificados/) "
                "y el Cloud Function aún no expone /consultar_cuit."
            )
            return {"success": False, "error": msg}
        body = r.json() if r.content else {}
        if r.status_code >= 400:
            err = f"HTTP {r.status_code}"
            if isinstance(body, dict):
                err = body.get("error") or body.get("message") or err
            elif r.text:
                err = r.text[:300]
            return {"success": False, "error": local_err or err}
        if isinstance(body, dict) and body.get("success") is False:
            return {
                "success": False,
                "error": local_err
                or body.get("error")
                or body.get("message")
                or str(body),
            }
        data = (
            body.get("data")
            if isinstance(body, dict) and isinstance(body.get("data"), dict)
            else body
        )
        if not isinstance(data, dict):
            return {"success": False, "error": local_err or "Respuesta inválida del padrón"}
        nombre = (
            data.get("nombre")
            or data.get("razon_social")
            or data.get("denominacion")
            or data.get("nombre_completo")
            or ""
        )
        if not str(nombre).strip():
            return {"success": False, "error": local_err or "AFIP no devolvió el nombre"}
        return {"success": True, "data": data}
    except Exception as e:
        err = str(e)
        try:
            if hasattr(e, "response") and e.response is not None:
                err = e.response.text or err
        except Exception:
            pass
        return {"success": False, "error": local_err or err}


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
