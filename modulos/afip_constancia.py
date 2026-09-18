"""Consulta CUIT vía ARCA ws_sr_constancia_inscripcion (getPersona_v2).

Requiere certificado AFIP/ARCA del contribuyente emisor (el mismo de factura
electrónica), adherido al servicio ws_sr_constancia_inscripcion.

Variables / rutas:
  POS_AFIP_CERT / POS_AFIP_KEY  → rutas a .crt y .key
  o archivos en certificados/certificado.crt y certificados/privada.key
  POS_ARCA_CUIT                 → CUIT representado (emisor)
  POS_AFIP_HOMO=1               → ambiente homologación
"""
from __future__ import annotations

import base64
import os
import re
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Optional, Tuple
from xml.etree import ElementTree as ET

import requests

SERVICE_ID = "ws_sr_constancia_inscripcion"

WSAA_PROD = "https://wsaa.afip.gov.ar/ws/services/LoginCms"
WSAA_HOMO = "https://wsaahomo.afip.gov.ar/ws/services/LoginCms"
PADRON_PROD = "https://aws.afip.gov.ar/sr-padron/webservices/personaServiceA5"
PADRON_HOMO = "https://awshomo.afip.gov.ar/sr-padron/webservices/personaServiceA5"

_REPO = Path(__file__).resolve().parent.parent
_TICKET_CACHE: Dict[str, Any] = {}


def _homo() -> bool:
    return os.getenv("POS_AFIP_HOMO", "").strip() in ("1", "true", "TRUE", "yes")


def _rutas_cert() -> Tuple[Optional[Path], Optional[Path]]:
    env_crt = os.getenv("POS_AFIP_CERT", "").strip()
    env_key = os.getenv("POS_AFIP_KEY", "").strip()
    candidatos = []
    if env_crt and env_key:
        candidatos.append((Path(env_crt), Path(env_key)))
    # Archivos sueltos en la raíz del repo (como los copió el usuario)
    candidatos.append((_REPO / "sergiocrt.crt", _REPO / "sergiokey.key"))
    for base in (_REPO / "certificados", _REPO / "pos" / "certificados", _REPO / "afip", _REPO):
        candidatos.append((base / "certificado.crt", base / "privada.key"))
        candidatos.append((base / "cert.crt", base / "key.key"))
        candidatos.append((base / "afip.crt", base / "afip.key"))
        candidatos.append((base / "sergiocrt.crt", base / "sergiokey.key"))
    for crt, key in candidatos:
        if crt.is_file() and key.is_file():
            return crt, key
    return None, None


def certificados_disponibles() -> bool:
    crt, key = _rutas_cert()
    return crt is not None and key is not None


def _cuit_emisor() -> str:
    return re.sub(r"\D", "", os.getenv("POS_ARCA_CUIT", "20265010505"))


def _login_ticket_xml(service: str) -> str:
    # WSAA espera fecha con offset local (no UTC "Z").
    try:
        from zoneinfo import ZoneInfo

        tz = ZoneInfo("America/Argentina/Buenos_Aires")
    except Exception:
        tz = timezone(timedelta(hours=-3))
    ahora = datetime.now(tz)
    gen = (ahora - timedelta(minutes=10)).strftime("%Y-%m-%dT%H:%M:%S%z")
    exp = (ahora + timedelta(minutes=10)).strftime("%Y-%m-%dT%H:%M:%S%z")
    # %z -> -0300 ; AFIP suele querer -03:00
    if len(gen) >= 5 and gen[-5] in "+-" and ":" not in gen[-5:]:
        gen = f"{gen[:-2]}:{gen[-2:]}"
        exp = f"{exp[:-2]}:{exp[-2:]}"
    unique = str(int(ahora.timestamp()) % 2_000_000_000)
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<loginTicketRequest version="1.0">'
        "<header>"
        f"<uniqueId>{unique}</uniqueId>"
        f"<generationTime>{gen}</generationTime>"
        f"<expirationTime>{exp}</expirationTime>"
        "</header>"
        f"<service>{service}</service>"
        "</loginTicketRequest>"
    )


def _firmar_cms(tra_xml: str, cert_path: Path, key_path: Path) -> str:
    """Firma PKCS#7/CMS del TRA (mismo esquema que WSAA AFIP)."""
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.serialization import pkcs7

    cert_pem = cert_path.read_bytes()
    key_pem = key_path.read_bytes()
    # Soporta PEM; si viene DER puro, cryptography lo detecta en load_*
    try:
        cert = x509.load_pem_x509_certificate(cert_pem)
    except ValueError:
        cert = x509.load_der_x509_certificate(cert_pem)
    try:
        key = serialization.load_pem_private_key(key_pem, password=None)
    except ValueError:
        key = serialization.load_der_private_key(key_pem, password=None)

    options = [pkcs7.PKCS7Options.Binary]
    # Detached=False → contenido embebido (AFIP espera CMS con data)
    builder = (
        pkcs7.PKCS7SignatureBuilder()
        .set_data(tra_xml.encode("utf-8"))
        .add_signer(cert, key, hashes.SHA256())
    )
    cms = builder.sign(serialization.Encoding.DER, options)
    return base64.b64encode(cms).decode("ascii")


def _soap_post(url: str, body_inner: str, soap_action: str = "") -> str:
    envelope = (
        '<?xml version="1.0" encoding="utf-8"?>'
        '<soapenv:Envelope xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/" '
        'xmlns:a5="http://a5.soap.ws.server.puc.sr/">'
        "<soapenv:Header/>"
        f"<soapenv:Body>{body_inner}</soapenv:Body>"
        "</soapenv:Envelope>"
    )
    headers = {
        "Content-Type": "text/xml; charset=utf-8",
        "SOAPAction": soap_action,
    }
    r = requests.post(url, data=envelope.encode("utf-8"), headers=headers, timeout=45)
    if r.status_code >= 400:
        raise RuntimeError(f"SOAP HTTP {r.status_code}: {(r.text or '')[:300]}")
    return r.text


def _obtener_ta(service: str = SERVICE_ID) -> Tuple[str, str]:
    cache_key = f"{service}:{'homo' if _homo() else 'prod'}"
    cached = _TICKET_CACHE.get(cache_key)
    if cached and cached.get("expira", 0) > datetime.now(timezone.utc).timestamp():
        return cached["token"], cached["sign"]

    crt, key = _rutas_cert()
    if not crt or not key:
        raise RuntimeError(
            "Faltan certificados AFIP. Configurá POS_AFIP_CERT y POS_AFIP_KEY "
            "o colocá certificado.crt / privada.key en la carpeta certificados/"
        )

    tra = _login_ticket_xml(service)
    cms_b64 = _firmar_cms(tra, crt, key)
    wsaa = WSAA_HOMO if _homo() else WSAA_PROD
    # LoginCms espera el CMS en el body SOAP
    inner = (
        '<wsaa:loginCms xmlns:wsaa="http://wsaa.view.sua.dvadac.cuit.edu.ar">'
        f"<wsaa:in0>{cms_b64}</wsaa:in0>"
        "</wsaa:loginCms>"
    )
    # WSAA usa otro namespace; armar envelope genérico
    envelope = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<soapenv:Envelope xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/" '
        'xmlns:wsaa="http://wsaa.view.sua.dvadac.cuit.edu.ar">'
        "<soapenv:Header/>"
        "<soapenv:Body>"
        f"<wsaa:loginCms><wsaa:in0>{cms_b64}</wsaa:in0></wsaa:loginCms>"
        "</soapenv:Body>"
        "</soapenv:Envelope>"
    )
    r = requests.post(
        wsaa,
        data=envelope.encode("utf-8"),
        headers={"Content-Type": "text/xml; charset=utf-8", "SOAPAction": ""},
        timeout=45,
    )
    if r.status_code >= 400:
        raise RuntimeError(f"WSAA HTTP {r.status_code}: {(r.text or '')[:400]}")

    # loginCmsReturn contiene XML escapado o CDATA
    text = r.text
    m = re.search(r"<loginCmsReturn[^>]*>([\s\S]*?)</loginCmsReturn>", text)
    if not m:
        raise RuntimeError(f"WSAA sin ticket: {(text or '')[:400]}")
    ta_xml = m.group(1).strip()
    ta_xml = (
        ta_xml.replace("&lt;", "<")
        .replace("&gt;", ">")
        .replace("&quot;", '"')
        .replace("&amp;", "&")
    )
    root = ET.fromstring(ta_xml.encode("utf-8") if isinstance(ta_xml, str) else ta_xml)
    # namespaces opcionales
    token = None
    sign = None
    for el in root.iter():
        tag = el.tag.split("}")[-1] if "}" in el.tag else el.tag
        if tag == "token":
            token = (el.text or "").strip()
        elif tag == "sign":
            sign = (el.text or "").strip()
    if not token or not sign:
        raise RuntimeError("WSAA no devolvió token/sign")

    _TICKET_CACHE[cache_key] = {
        "token": token,
        "sign": sign,
        "expira": datetime.now(timezone.utc).timestamp() + 8 * 3600,
    }
    return token, sign


def _texto(el: Optional[ET.Element]) -> str:
    if el is None or el.text is None:
        return ""
    return str(el.text).strip()


def _find_text(root: ET.Element, names: Tuple[str, ...]) -> str:
    wanted = {n.lower() for n in names}
    for el in root.iter():
        tag = el.tag.split("}")[-1] if "}" in el.tag else el.tag
        if tag.lower() in wanted and (el.text or "").strip():
            return (el.text or "").strip()
    return ""


def _map_condicion_iva(root: ET.Element) -> str:
    """Deriva condición IVA a partir de impuestos/domicilio/constancia."""
    blobs = []
    for el in root.iter():
        tag = (el.tag.split("}")[-1] if "}" in el.tag else el.tag).lower()
        txt = (el.text or "").strip()
        if txt and tag in (
            "descripcionimpuesto",
            "descimpuesto",
            "condicion",
            "descripcion",
            "estado",
            "nombreimpuesto",
        ):
            blobs.append(txt.upper())
    joined = " | ".join(blobs)
    if "IVA" in joined and (
        "ACTIVO" in joined or "RESPONSABLE INSCRIPTO" in joined or "RI" in joined
    ):
        # Heurística: impuesto IVA activo → RI
        if "EXENTO" in joined and "IVA" in joined:
            return "IVA EXENTO"
        if "MONOTRIBUTO" in joined:
            return "MONOTRIBUTO"
        if "NO RESPONSABLE" in joined:
            return "IVA NO RESPONSABLE"
        if "CONSUMIDOR FINAL" in joined:
            return "CONSUMIDOR FINAL"
        return "IVA RESPONSABLE INSCRIPTO"
    if "MONOTRIBUTO" in joined:
        return "MONOTRIBUTO"
    if "EXENTO" in joined:
        return "IVA EXENTO"
    # datosGenerales no siempre trae IVA; dejar vacío si no se deduce
    return ""


def consultar_persona(cuit_consultar: str) -> Dict[str, Any]:
    """Devuelve nombre + condición IVA vía getPersona_v2."""
    dig = re.sub(r"\D", "", str(cuit_consultar or ""))
    if len(dig) != 11:
        raise ValueError("CUIT a consultar inválido")
    cuit_rep = _cuit_emisor()
    if len(cuit_rep) != 11:
        raise RuntimeError("POS_ARCA_CUIT inválido")

    token, sign = _obtener_ta(SERVICE_ID)
    padron = PADRON_HOMO if _homo() else PADRON_PROD
    inner = (
        "<a5:getPersona_v2>"
        f"<token>{token}</token>"
        f"<sign>{sign}</sign>"
        f"<cuitRepresentada>{cuit_rep}</cuitRepresentada>"
        f"<idPersona>{dig}</idPersona>"
        "</a5:getPersona_v2>"
    )
    xml = _soap_post(padron, inner, soap_action="")
    root = ET.fromstring(xml)

    # Errores AFIP
    err = _find_text(root, ("errorConstancia", "errorRegimenGeneral", "mensaje", "faultstring"))
    # faultstring tiene prioridad si hay Fault
    for el in root.iter():
        tag = el.tag.split("}")[-1] if "}" in el.tag else el.tag
        if tag.lower() == "faultstring" and (el.text or "").strip():
            raise RuntimeError((el.text or "").strip())

    apellido = _find_text(root, ("apellido",))
    nombre = _find_text(root, ("nombre",))
    razon = _find_text(root, ("razonSocial", "denominacion", "nombreCompleto"))
    if razon:
        denominacion = razon
    elif apellido or nombre:
        denominacion = f"{apellido} {nombre}".strip()
    else:
        denominacion = ""
    if not denominacion:
        # a veces errorConstancia indica persona inexistente
        if err:
            raise RuntimeError(err)
        raise RuntimeError("AFIP no devolvió el nombre del contribuyente")

    condicion = _map_condicion_iva(root)
    tipo_fc = "1" if "RESPONSABLE INSCRIPTO" in condicion.upper() else "6"

    return {
        "nombre": denominacion.upper(),
        "razon_social": denominacion.upper(),
        "denominacion": denominacion.upper(),
        "condicion_iva": condicion,
        "tipo_comprobante": tipo_fc,
        "cuit": dig,
        "fuente": "ws_sr_constancia_inscripcion",
    }
