"""PDF de presupuesto numerado (formato profesional)."""
from datetime import timedelta
from typing import Any, Dict, List, Optional

from modulos.util_fechas import ahora_ar

from modulos.pdf_a4_comun import (
    MARGIN_L,
    ANCHO_UTIL,
    dibujar_cabecera_documento,
    dibujar_caja_cliente,
    dibujar_tabla_items,
    dibujar_totales_cliente_pdf,
    dibujar_etiqueta_discreta_cae,
    nueva_pagina_a4,
)
from modulos.util_pdf import texto_para_pdf

VALIDEZ_PRESUPUESTO_DIAS = 3


def _fmt_nro_presupuesto(numero: Optional[int]) -> str:
    if numero is None or int(numero) <= 0:
        return "BORRADOR"
    return f"{int(numero):04d}"


def _codigo_item_presupuesto(item: Dict[str, Any]) -> str:
    cod = item.get("codigo") or item.get("id_maestro")
    if cod:
        return str(cod).strip()
    raw_id = str(item.get("id", ""))
    if "_" in raw_id:
        return raw_id.split("_", 1)[0]
    return raw_id


def crear_pdf_presupuesto(
    vendedor: str,
    items: List[Dict[str, Any]],
    total_bruto: float,
    cliente: Optional[Dict[str, Any]] = None,
    descuento_pct: float = 0.0,
    numero: Optional[int] = None,
    config: Optional[Dict[str, Any]] = None,
    nota: str = "",
) -> bytes:
    cfg = dict(config or {})
    cli = dict(cliente or {})
    if descuento_pct and not cli.get("descuento"):
        cli["descuento"] = float(descuento_pct)
    nombre_cli = str(cli.get("nombre", "CONSUMIDOR FINAL"))
    cuit_cli = str(cli.get("cuit", cli.get("cuit_dni", "")) or "")
    nro_txt = _fmt_nro_presupuesto(numero)

    ahora = ahora_ar()
    validez = ahora + timedelta(days=VALIDEZ_PRESUPUESTO_DIAS)

    pdf = nueva_pagina_a4()
    y_cli_box = dibujar_cabecera_documento(
        pdf,
        cfg,
        "PRESUPUESTO",
        [
            f"Nro {nro_txt}",
            f"Fecha: {ahora.strftime('%d/%m/%Y %H:%M')}",
            f"Valido hasta: {validez.strftime('%d/%m/%Y')}",
        ],
    )
    y_tab = dibujar_caja_cliente(pdf, y_cli_box, nombre_cli, cuit_cli, vendedor=str(vendedor))
    y_tab = dibujar_tabla_items(pdf, y_tab, items, _codigo_item_presupuesto)

    pdf.set_y(y_tab + 4)
    etiqueta = dibujar_totales_cliente_pdf(pdf, float(total_bruto), cli)

    pdf.ln(4)
    pdf.set_xy(MARGIN_L, pdf.get_y())
    pdf.set_font("Helvetica", "", 8)
    leyendas = [
        f"Presupuesto valido por {VALIDEZ_PRESUPUESTO_DIAS} dias desde la fecha de emision.",
        "Documento sin validez fiscal. No reemplaza factura.",
        "Precios y stock sujetos a disponibilidad al momento de la compra.",
    ]
    if etiqueta:
        leyendas[-1] = leyendas[-1] + f"  ({etiqueta})"
    if nota:
        leyendas.insert(0, texto_para_pdf(nota))
    for linea in leyendas:
        pdf.set_x(MARGIN_L)
        pdf.multi_cell(ANCHO_UTIL, 4, linea)

    return bytes(pdf.output())
