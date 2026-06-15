"""Cliente HTTP para el backend de facturación ARCA/AFIP (facturahafid)."""
import requests

BASE_URL = "https://southamerica-east1-facturador-backend-2b9f5.cloudfunctions.net"


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
        return {"success": True, "data": r.json()}
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
