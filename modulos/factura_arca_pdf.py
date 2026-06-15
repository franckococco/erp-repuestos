"""Generación de PDF ticket (58mm) y A4 para comprobantes ARCA."""
from datetime import datetime
from typing import Any, Dict, List, Optional

from fpdf import FPDF

from modulos.util_branding import NOMBRE_EMPRESA
from modulos.util_pdf import texto_para_pdf


def _get_str(d: Dict[str, Any], k: str, default: str = "") -> str:
    val = d.get(k)
    raw = str(val) if val is not None else default
    return texto_para_pdf(raw)


def _get_float(d: Dict[str, Any], k: str, default: float = 0.0) -> float:
    val = d.get(k)
    if val is None:
        return default
    try:
        return float(val)
    except (ValueError, TypeError):
        return default


def crear_ticket(
    datos_respuesta: Dict[str, Any],
    datos_cliente: Dict[str, Any],
    items: List[Dict[str, Any]],
    config: Optional[Dict[str, Any]] = None,
) -> bytes:
    if config is None:
        config = {}

    margen_x = _get_float(config, "margen_x", 2.0)
    margen_y = _get_float(config, "margen_y", 2.0)
    font_base = int(_get_float(config, "font_size", 8.0))
    altura_ticket = 120 + (len(items) * 10)

    pdf = FPDF(orientation="P", unit="mm", format=(58, altura_ticket))
    pdf.add_page()
    pdf.set_margins(margen_x, margen_y, margen_x)
    pdf.set_auto_page_break(auto=True, margin=margen_y)

    cbte_tipo = _get_str(datos_cliente, "cbte_tipo", "6")
    es_factura_a = cbte_tipo == "1"
    tipo_letra = "A" if es_factura_a else "B"
    ancho_util = 58.0 - (margen_x * 2)

    pdf.set_font("Helvetica", "B", font_base + 2)
    pdf.multi_cell(
        ancho_util, 4, _get_str(datos_respuesta, "nombre_empresa", NOMBRE_EMPRESA), align="C"
    )

    pdf.set_font("Helvetica", "", font_base - 1)
    dir_emp = _get_str(datos_respuesta, "direccion_empresa", "")
    if dir_emp:
        pdf.multi_cell(ancho_util, 3, dir_emp, align="C")

    pdf.cell(ancho_util, 3, f"CUIT: {_get_str(config, 'cuit_emisor', '')}", align="C", new_x="LMARGIN", new_y="NEXT")
    pdf.cell(ancho_util, 3, _get_str(config, "iibb", ""), align="C", new_x="LMARGIN", new_y="NEXT")
    pdf.cell(ancho_util, 3, _get_str(config, "inicio_act", ""), align="C", new_x="LMARGIN", new_y="NEXT")
    pdf.cell(ancho_util, 3, _get_str(config, "condicion_iva", ""), align="C", new_x="LMARGIN", new_y="NEXT")

    pdf.line(margen_x, pdf.get_y(), 58 - margen_x, pdf.get_y())
    pdf.set_y(pdf.get_y() + 1)

    pdf.set_font("Helvetica", "B", font_base)
    nro_fc = (
        f"{int(_get_float(datos_respuesta, 'punto_venta', 0.0)):04d}-"
        f"{int(_get_float(datos_respuesta, 'numero_factura', 0.0)):08d}"
    )
    pdf.cell(ancho_util, 4, f"FACTURA {tipo_letra} Nro: {nro_fc}", align="C", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", "", font_base - 1)
    pdf.cell(
        ancho_util, 3, f"Fecha: {datetime.now().strftime('%d/%m/%Y %H:%M')}",
        align="C", new_x="LMARGIN", new_y="NEXT",
    )

    pdf.line(margen_x, pdf.get_y(), 58 - margen_x, pdf.get_y())
    pdf.set_y(pdf.get_y() + 1)

    pdf.set_font("Helvetica", "B", font_base - 1)
    pdf.cell(ancho_util, 3, "CLIENTE:", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", "", font_base - 1)
    pdf.multi_cell(ancho_util, 3, _get_str(datos_cliente, "nombre", "Consumidor Final"))
    pdf.cell(
        ancho_util, 3, f"CUIT/DNI: {_get_str(datos_cliente, 'cuit', '00000000000')}",
        new_x="LMARGIN", new_y="NEXT",
    )

    pdf.line(margen_x, pdf.get_y(), 58 - margen_x, pdf.get_y())
    pdf.set_y(pdf.get_y() + 1)

    pdf.set_font("Helvetica", "B", font_base - 2)
    pdf.cell(6, 3, "Cant", align="L")
    pdf.cell(32, 3, "Descripcion", align="L")
    pdf.cell(16, 3, "Total", align="R", new_x="LMARGIN", new_y="NEXT")

    pdf.set_font("Helvetica", "", font_base - 2)
    total_factura = 0.0
    for item in items:
        cant = _get_float(item, "cantidad", 1.0)
        desc = _get_str(item, "descripcion", "Item")[:18]
        precio = _get_float(item, "precio", 0.0)
        total_factura += precio
        cant_str = f"{int(cant)}" if cant.is_integer() else f"{cant:.2f}"
        pdf.cell(6, 4, cant_str, align="L")
        pdf.cell(32, 4, desc, align="L")
        pdf.cell(16, 4, f"${precio:.2f}", align="R", new_x="LMARGIN", new_y="NEXT")

    pdf.line(margen_x, pdf.get_y(), 58 - margen_x, pdf.get_y())
    pdf.set_y(pdf.get_y() + 1)

    pdf.set_font("Helvetica", "B", font_base)
    if es_factura_a:
        subtotal = total_factura / 1.21
        iva = total_factura - subtotal
        pdf.set_font("Helvetica", "", font_base - 1)
        pdf.cell(34, 4, "Neto Gravado:", align="R")
        pdf.cell(20, 4, f"${subtotal:.2f}", align="R", new_x="LMARGIN", new_y="NEXT")
        pdf.cell(34, 4, "IVA 21%:", align="R")
        pdf.cell(20, 4, f"${iva:.2f}", align="R", new_x="LMARGIN", new_y="NEXT")
        pdf.set_font("Helvetica", "B", font_base + 1)
        pdf.cell(34, 6, "TOTAL:", align="R")
        pdf.cell(20, 6, f"${total_factura:.2f}", align="R", new_x="LMARGIN", new_y="NEXT")
    else:
        pdf.set_font("Helvetica", "B", font_base + 2)
        pdf.cell(34, 6, "TOTAL:", align="R")
        pdf.cell(20, 6, f"${total_factura:.2f}", align="R", new_x="LMARGIN", new_y="NEXT")

    pdf.line(margen_x, pdf.get_y(), 58 - margen_x, pdf.get_y())
    pdf.set_y(pdf.get_y() + 1)

    pdf.set_font("Helvetica", "B", font_base - 1)
    pdf.cell(ancho_util, 4, f"CAE: {_get_str(datos_respuesta, 'cae', '')}", align="C", new_x="LMARGIN", new_y="NEXT")
    pdf.cell(
        ancho_util, 4, f"Vto: {_get_str(datos_respuesta, 'vencimiento_cae', '')}",
        align="C", new_x="LMARGIN", new_y="NEXT",
    )

    pdf.set_y(pdf.get_y() + 2)
    pdf.set_font("Helvetica", "I", font_base - 2)
    pdf.multi_cell(ancho_util, 3, _get_str(config, "leyenda_extra", "Gracias por su compra"), align="C")

    return bytes(pdf.output())


def crear_a4(
    datos_respuesta: Dict[str, Any],
    datos_cliente: Dict[str, Any],
    items: List[Dict[str, Any]],
    config: Optional[Dict[str, Any]] = None,
) -> bytes:
    if config is None:
        config = {}

    pdf = FPDF(orientation="P", unit="mm", format="A4")
    pdf.add_page()
    pdf.set_margins(10, 10, 10)
    pdf.set_auto_page_break(auto=True, margin=15)

    cbte_tipo = _get_str(datos_cliente, "cbte_tipo", "6")
    es_factura_a = cbte_tipo == "1"
    tipo_letra = "A" if es_factura_a else "B"

    pdf.rect(10, 10, 190, 40)
    pdf.line(105, 10, 105, 50)
    pdf.rect(98, 10, 14, 14)
    pdf.set_font("Helvetica", "B", 24)
    pdf.set_xy(98, 11)
    pdf.cell(14, 10, tipo_letra, align="C")
    pdf.set_font("Helvetica", "B", 8)
    pdf.set_xy(98, 19)
    pdf.cell(14, 4, f"Cod. 0{cbte_tipo}", align="C")

    pdf.set_font("Helvetica", "B", 14)
    pdf.set_xy(12, 12)
    pdf.cell(80, 8, _get_str(datos_respuesta, "nombre_empresa", NOMBRE_EMPRESA))

    pdf.set_font("Helvetica", "", 9)
    pdf.set_xy(12, 22)
    pdf.multi_cell(80, 4, _get_str(datos_respuesta, "direccion_empresa", ""))

    pdf.set_xy(12, 34)
    pdf.cell(80, 4, f"Condición frente al IVA: {_get_str(config, 'condicion_iva', '')}")

    pdf.set_font("Helvetica", "B", 14)
    pdf.set_xy(110, 12)
    pdf.cell(80, 8, "FACTURA")

    pdf.set_font("Helvetica", "B", 11)
    pdf.set_xy(110, 22)
    nro_pto = int(_get_float(datos_respuesta, "punto_venta", 0.0))
    nro_fc = int(_get_float(datos_respuesta, "numero_factura", 0.0))
    pdf.cell(80, 5, f"Punto de Venta: {nro_pto:04d}  Comp. Nro: {nro_fc:08d}")

    pdf.set_xy(110, 28)
    pdf.cell(80, 5, f"Fecha de Emisión: {datetime.now().strftime('%d/%m/%Y')}")

    pdf.set_font("Helvetica", "", 9)
    pdf.set_xy(110, 36)
    pdf.cell(80, 4, f"CUIT: {_get_str(config, 'cuit_emisor', '')}")
    pdf.set_xy(110, 40)
    pdf.cell(80, 4, _get_str(config, "iibb", ""))
    pdf.set_xy(110, 44)
    pdf.cell(80, 4, _get_str(config, "inicio_act", ""))

    pdf.rect(10, 52, 190, 20)
    pdf.set_font("Helvetica", "B", 9)
    pdf.set_xy(12, 54)
    pdf.cell(20, 5, "CUIT/DNI:")
    pdf.set_font("Helvetica", "", 9)
    pdf.cell(50, 5, _get_str(datos_cliente, "cuit", "00000000000"))

    pdf.set_font("Helvetica", "B", 9)
    pdf.set_xy(12, 60)
    pdf.cell(50, 5, "Apellido y Nombre / Razón Social:")
    pdf.set_font("Helvetica", "", 9)
    pdf.cell(100, 5, _get_str(datos_cliente, "nombre", "Consumidor Final"))

    pdf.set_font("Helvetica", "B", 9)
    pdf.set_xy(12, 66)
    pdf.cell(25, 5, "Condición IVA:")
    pdf.set_font("Helvetica", "", 9)
    cond_cli = "Responsable Inscripto" if es_factura_a else "Consumidor Final"
    pdf.cell(50, 5, cond_cli)

    pdf.set_xy(10, 75)
    pdf.set_font("Helvetica", "B", 9)
    pdf.set_fill_color(220, 220, 220)
    pdf.cell(20, 6, "Cantidad", border=1, align="C", fill=True)
    pdf.cell(90, 6, "Descripción", border=1, align="C", fill=True)
    pdf.cell(25, 6, "Precio Unit.", border=1, align="C", fill=True)
    pdf.cell(25, 6, "Bonif.", border=1, align="C", fill=True)
    pdf.cell(30, 6, "Subtotal", border=1, align="C", fill=True, new_x="LMARGIN", new_y="NEXT")

    pdf.set_font("Helvetica", "", 9)
    total_factura = 0.0
    for item in items:
        cant = _get_float(item, "cantidad", 1.0)
        desc = _get_str(item, "descripcion", "Item")
        precio = _get_float(item, "precio", 0.0)
        total_factura += precio
        if es_factura_a:
            precio_unit_mostrar = (precio / cant) / 1.21
            subtot_linea_mostrar = precio / 1.21
        else:
            precio_unit_mostrar = precio / cant
            subtot_linea_mostrar = precio
        cant_str = f"{int(cant)}" if cant.is_integer() else f"{cant:.2f}"
        pdf.cell(20, 6, cant_str, border="B", align="C")
        pdf.cell(90, 6, desc[:50], border="B", align="L")
        pdf.cell(25, 6, f"${precio_unit_mostrar:.2f}", border="B", align="R")
        pdf.cell(25, 6, "$0.00", border="B", align="R")
        pdf.cell(30, 6, f"${subtot_linea_mostrar:.2f}", border="B", align="R", new_x="LMARGIN", new_y="NEXT")

    pdf.set_y(240)
    pdf.line(10, 238, 200, 238)

    if es_factura_a:
        subtotal = total_factura / 1.21
        iva = total_factura - subtotal
        pdf.set_font("Helvetica", "B", 10)
        pdf.cell(160, 6, "Importe Neto Gravado: $", align="R")
        pdf.cell(30, 6, f"{subtotal:.2f}", align="R", new_x="LMARGIN", new_y="NEXT")
        pdf.cell(160, 6, "IVA 21%: $", align="R")
        pdf.cell(30, 6, f"{iva:.2f}", align="R", new_x="LMARGIN", new_y="NEXT")
        pdf.set_font("Helvetica", "B", 12)
        pdf.cell(160, 8, "IMPORTE TOTAL: $", align="R")
        pdf.cell(30, 8, f"{total_factura:.2f}", align="R", new_x="LMARGIN", new_y="NEXT")
    else:
        pdf.set_font("Helvetica", "B", 12)
        pdf.cell(160, 8, "IMPORTE TOTAL: $", align="R")
        pdf.cell(30, 8, f"{total_factura:.2f}", align="R", new_x="LMARGIN", new_y="NEXT")

    pdf.set_y(270)
    pdf.set_font("Helvetica", "B", 10)
    cae = _get_str(datos_respuesta, "cae", "")
    vto = _get_str(datos_respuesta, "vencimiento_cae", "")
    pdf.cell(100, 5, f"CAE N°: {cae}", new_x="LMARGIN", new_y="NEXT")
    pdf.cell(100, 5, f"Fecha Vto. CAE: {vto}")

    return bytes(pdf.output())
