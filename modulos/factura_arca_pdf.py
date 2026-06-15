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

    nombre_emp = (
        _get_str(config, "nombre_empresa", "")
        or _get_str(datos_respuesta, "nombre_empresa", NOMBRE_EMPRESA)
    )
    dir_emp = (
        _get_str(config, "direccion", "")
        or _get_str(datos_respuesta, "direccion_empresa", "")
    )

    pdf.set_font("Helvetica", "B", font_base + 2)
    pdf.multi_cell(ancho_util, 4, nombre_emp, align="C")

    pdf.set_font("Helvetica", "", font_base - 1)
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
    cod_afip = "001" if es_factura_a else "006"

    m = 10.0
    ancho = 190.0
    mid_x = m + ancho / 2
    box_b_w = 16.0
    box_b_x = mid_x - box_b_w / 2
    col_izq_w = box_b_x - m - 3
    col_der_x = box_b_x + box_b_w + 4
    col_der_w = m + ancho - col_der_x
    val_x = m + 68

    nombre_emp = (
        _get_str(config, "nombre_empresa", "")
        or _get_str(datos_respuesta, "nombre_empresa", NOMBRE_EMPRESA)
    )
    dir_emp = (
        _get_str(config, "direccion", "")
        or _get_str(datos_respuesta, "direccion_empresa", "")
    )
    cond_iva_emisor = _get_str(config, "condicion_iva", "IVA Responsable Inscripto")
    cuit_emisor = _get_str(config, "cuit_emisor", "")
    iibb = _get_str(config, "iibb", "")
    inicio_act = _get_str(config, "inicio_act", "")

    nro_pto = int(_get_float(datos_respuesta, "punto_venta", 0.0))
    nro_fc = int(_get_float(datos_respuesta, "numero_factura", 0.0))
    cuit_cli = _get_str(datos_cliente, "cuit", "00000000000")
    nombre_cli = _get_str(datos_cliente, "nombre", "CONSUMIDOR FINAL")
    cond_cli = "Responsable Inscripto" if es_factura_a else "Consumidor Final"

    hdr_top = m
    hdr_h = 50.0
    pdf.rect(m, hdr_top, ancho, hdr_h)
    pdf.line(mid_x, hdr_top, mid_x, hdr_top + hdr_h)

    pdf.rect(box_b_x, hdr_top + 6, box_b_w, 18)
    pdf.set_font("Helvetica", "B", 22)
    pdf.set_xy(box_b_x, hdr_top + 7)
    pdf.cell(box_b_w, 10, tipo_letra, align="C")
    pdf.set_font("Helvetica", "B", 7)
    pdf.set_xy(box_b_x, hdr_top + 16)
    pdf.cell(box_b_w, 4, f"Cod. {cod_afip}", align="C")

    y_izq = hdr_top + 4
    pdf.set_xy(m + 2, y_izq)
    pdf.set_font("Helvetica", "B", 11)
    pdf.multi_cell(col_izq_w, 5, nombre_emp)
    y_izq = pdf.get_y() + 1
    if dir_emp:
        pdf.set_xy(m + 2, y_izq)
        pdf.set_font("Helvetica", "", 8)
        pdf.multi_cell(col_izq_w, 4, dir_emp)
        y_izq = pdf.get_y() + 1
    pdf.set_xy(m + 2, y_izq)
    pdf.set_font("Helvetica", "", 8)
    pdf.multi_cell(col_izq_w, 4, f"Condición frente al IVA: {cond_iva_emisor}")
    y_izq = pdf.get_y() + 1
    if cuit_emisor:
        pdf.set_xy(m + 2, y_izq)
        pdf.cell(col_izq_w, 4, f"CUIT: {cuit_emisor}", new_x="LMARGIN", new_y="NEXT")
        y_izq += 4
    if iibb:
        pdf.set_xy(m + 2, y_izq)
        pdf.cell(col_izq_w, 4, iibb, new_x="LMARGIN", new_y="NEXT")
        y_izq += 4
    if inicio_act:
        pdf.set_xy(m + 2, y_izq)
        pdf.cell(col_izq_w, 4, inicio_act, new_x="LMARGIN", new_y="NEXT")

    y_der = hdr_top + 4
    pdf.set_xy(col_der_x, y_der)
    pdf.set_font("Helvetica", "B", 13)
    pdf.cell(col_der_w, 6, "FACTURA")
    y_der += 8
    pdf.set_xy(col_der_x, y_der)
    pdf.set_font("Helvetica", "", 9)
    pdf.cell(col_der_w, 4, f"Punto de Venta: {nro_pto:04d}")
    y_der += 5
    pdf.set_xy(col_der_x, y_der)
    pdf.cell(col_der_w, 4, f"Comp. Nro: {nro_fc:08d}")
    y_der += 5
    pdf.set_xy(col_der_x, y_der)
    pdf.cell(col_der_w, 4, f"Fecha de Emisión: {datetime.now().strftime('%d/%m/%Y')}")

    cli_top = hdr_top + hdr_h + 4
    cli_h = 22.0
    pdf.rect(m, cli_top, ancho, cli_h)
    fila_h = 6.0

    y_cli = cli_top + 3
    pdf.set_font("Helvetica", "B", 9)
    pdf.set_xy(m + 2, y_cli)
    pdf.cell(60, fila_h, "CUIT/DNI:")
    pdf.set_font("Helvetica", "", 9)
    pdf.set_xy(val_x, y_cli)
    pdf.cell(60, fila_h, cuit_cli)

    y_cli += fila_h
    pdf.set_font("Helvetica", "B", 9)
    pdf.set_xy(m + 2, y_cli)
    pdf.cell(60, fila_h, "Apellido y Nombre / Razón Social:")
    pdf.set_font("Helvetica", "", 9)
    pdf.set_xy(val_x, y_cli)
    pdf.cell(ancho - val_x - 2, fila_h, nombre_cli)

    y_cli += fila_h
    pdf.set_font("Helvetica", "B", 9)
    pdf.set_xy(m + 2, y_cli)
    pdf.cell(60, fila_h, "Condición IVA:")
    pdf.set_font("Helvetica", "", 9)
    pdf.set_xy(val_x, y_cli)
    pdf.cell(60, fila_h, cond_cli)

    pdf.set_xy(m, cli_top + cli_h + 4)
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
            precio_unit_mostrar = (precio / cant) / 1.21 if cant else 0
            subtot_linea_mostrar = precio / 1.21
        else:
            precio_unit_mostrar = precio / cant if cant else 0
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
