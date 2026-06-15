"""Contexto unificado para PDF ticket y A4 (emisor + cliente)."""
from datetime import datetime
from typing import Any, Dict, Optional

from modulos.util_branding import NOMBRE_EMPRESA
from modulos.util_pdf import texto_para_pdf


def _s(d: Dict[str, Any], k: str, default: str = "") -> str:
    val = d.get(k)
    return texto_para_pdf(str(val) if val is not None else default)


def condicion_iva_cliente(datos_cliente: Dict[str, Any]) -> str:
    explicita = _s(datos_cliente, "condicion_iva", "")
    if explicita:
        return explicita
    cbte = _s(datos_cliente, "cbte_tipo", "6")
    if cbte == "1":
        return "IVA Responsable Inscripto"
    return "Consumidor Final"


def armar_contexto_comprobante(
    datos_respuesta: Dict[str, Any],
    datos_cliente: Dict[str, Any],
    config: Optional[Dict[str, Any]] = None,
    forma_pago: str = "Contado",
) -> Dict[str, Any]:
    cfg = dict(config or {})
    cli = dict(datos_cliente or {})

    nombre_fantasia = _s(cfg, "nombre_empresa") or _s(datos_respuesta, "nombre_empresa", NOMBRE_EMPRESA)
    razon_social = _s(cfg, "razon_social") or nombre_fantasia
    domicilio_comercial = (
        _s(cfg, "domicilio_comercial")
        or _s(cfg, "direccion")
        or _s(datos_respuesta, "direccion_empresa", "")
    )

    return {
        "emisor": {
            "nombre_fantasia": nombre_fantasia,
            "razon_social": razon_social,
            "domicilio_comercial": domicilio_comercial,
            "condicion_iva": _s(cfg, "condicion_iva", "IVA Responsable Inscripto"),
            "cuit": _s(cfg, "cuit_emisor", ""),
            "iibb": _s(cfg, "iibb", ""),
            "inicio_actividades": _s(cfg, "inicio_actividades") or _s(cfg, "inicio_act", ""),
        },
        "comprobante": {
            "tipo_letra": "A" if _s(cli, "cbte_tipo", "6") == "1" else "B",
            "cod_afip": "001" if _s(cli, "cbte_tipo", "6") == "1" else "006",
            "punto_venta": int(float(datos_respuesta.get("punto_venta") or 0)),
            "numero": int(float(datos_respuesta.get("numero_factura") or 0)),
            "fecha_emision": datetime.now().strftime("%d/%m/%Y"),
        },
        "cliente": {
            "cuit": _s(cli, "cuit", "00000000000"),
            "nombre": _s(cli, "nombre", "CONSUMIDOR FINAL"),
            "condicion_iva": condicion_iva_cliente(cli),
            "domicilio": _s(cli, "domicilio", ""),
            "condicion_venta": _s({"p": forma_pago}, "p", "Contado"),
        },
        "cae": {
            "numero": _s(datos_respuesta, "cae", ""),
            "vencimiento": _s(datos_respuesta, "vencimiento_cae", ""),
        },
        "leyenda_extra": _s(cfg, "leyenda_extra", "Gracias por su compra"),
    }
