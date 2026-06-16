"""Generación de PDF ticket (58mm) y A4 para comprobantes ARCA."""
from datetime import datetime
from typing import Any, Dict, List, Optional

from fpdf import FPDF

from modulos.pdf_a4_comun import (
    MARGIN_L,
    ANCHO_UTIL,
    X_COL_DER,
    W_COL_DER,
    _fila_total_pdf,
    dibujar_cabecera_documento,
    dibujar_caja_cliente,
    dibujar_tabla_items,
    dibujar_totales_con_dto,
    nueva_pagina_a4,
)
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


def _codigo_item_factura(item: Dict[str, Any]) -> str:
    cod = item.get("codigo") or item.get("id_maestro")
    if cod:
        return str(cod).strip()
    desc = str(item.get("descripcion", "")).strip()
    return desc[:12] if desc else "-"


def _items_factura_a_filas(items: List[Dict[str, Any]], es_factura_a: bool) -> List[Dict[str, Any]]:
    filas = []
    for item in items:
        cant = _get_float(item, "cantidad", 1.0)
        desc = _get_str(item, "descripcion", "Item")
        precio_linea = _get_float(item, "precio", 0.0)
        if es_factura_a:
            sub = precio_linea / 1.21
            precio_u = sub / cant if cant else 0.0
        else:
            sub = precio_linea
            precio_u = precio_linea / cant if cant else 0.0
        filas.append({
            "codigo": _codigo_item_factura(item),
            "descripcion": desc,
            "cantidad": cant,
            "precio_unitario": precio_u,
            "subtotal": sub,
        })
    return filas


def crear_a4(
    datos_respuesta: Dict[str, Any],
    datos_cliente: Dict[str, Any],
    items: List[Dict[str, Any]],
    config: Optional[Dict[str, Any]] = None,
) -> bytes:
    if config is None:
        config = {}

    cbte_tipo = _get_str(datos_cliente, "cbte_tipo", "6")
    es_factura_a = cbte_tipo == "1"
    tipo_letra = "A" if es_factura_a else "B"
    cod_afip = "001" if es_factura_a else "006"

    nro_pto = int(_get_float(datos_respuesta, "punto_venta", 0.0))
    nro_fc = int(_get_float(datos_respuesta, "numero_factura", 0.0))
    cuit_cli = _get_str(datos_cliente, "cuit", "00000000000")
    nombre_cli = _get_str(datos_cliente, "nombre", "CONSUMIDOR FINAL")
    cond_cli = "Responsable Inscripto" if es_factura_a else "Consumidor Final"

    filas = _items_factura_a_filas(items, es_factura_a)
    total_factura = sum(float(f["subtotal"]) for f in filas)
    if es_factura_a:
        total_factura = sum(_get_float(it, "precio", 0.0) for it in items)

    cfg = dict(config)
    if not cfg.get("nombre_empresa"):
        cfg["nombre_empresa"] = _get_str(datos_respuesta, "nombre_empresa", NOMBRE_EMPRESA)
    if not cfg.get("direccion"):
        cfg["direccion"] = _get_str(datos_respuesta, "direccion_empresa", "")

    pdf = nueva_pagina_a4()
    y_box = dibujar_cabecera_documento(
        pdf,
        cfg,
        f"FACTURA {tipo_letra}",
        [
            f"Pto. Vta: {nro_pto:04d}",
            f"Comp. Nro: {nro_fc:08d}",
            f"Cod. {cod_afip}",
            f"Fecha: {datetime.now().strftime('%d/%m/%Y %H:%M')}",
        ],
    )

    box_b_w = 14.0
    box_b_x = X_COL_DER + W_COL_DER - box_b_w
    pdf.rect(box_b_x, 12, box_b_w, 14)
    pdf.set_font("Helvetica", "B", 20)
    pdf.set_xy(box_b_x, 13)
    pdf.cell(box_b_w, 10, tipo_letra, align="C")

    y_tab = dibujar_caja_cliente(
        pdf,
        y_box,
        nombre_cli,
        cuit_cli,
        filas_extra=[("Cond. IVA", cond_cli)],
    )
    y_tab = dibujar_tabla_items(pdf, y_tab, filas, _codigo_item_factura)

    pdf.set_y(y_tab + 4)
    if es_factura_a:
        subtotal = total_factura / 1.21
        iva = total_factura - subtotal
        _fila_total_pdf(pdf, "Neto gravado:", f"${subtotal:,.2f}", tam=10)
        _fila_total_pdf(pdf, "IVA 21%:", f"${iva:,.2f}", tam=10)
        _fila_total_pdf(pdf, "TOTAL:", f"${total_factura:,.2f}", alto=9, tam=12, estilo="B")
    else:
        desc_pct = float(datos_cliente.get("descuento", 0) or 0)
        bruto_items = sum(_get_float(it, "precio", 0.0) for it in items)
        if desc_pct > 0 and bruto_items > total_factura:
            dibujar_totales_con_dto(pdf, bruto_items, desc_pct)
        else:
            _fila_total_pdf(pdf, "TOTAL:", f"${total_factura:,.2f}", alto=9, tam=12, estilo="B")

    pdf.ln(6)
    pdf.set_x(MARGIN_L)
    pdf.set_font("Helvetica", "B", 10)
    cae = _get_str(datos_respuesta, "cae", "")
    vto = _get_str(datos_respuesta, "vencimiento_cae", "")
    pdf.cell(ANCHO_UTIL, 5, f"CAE N: {cae}", new_x="LMARGIN", new_y="NEXT")
    pdf.set_x(MARGIN_L)
    pdf.cell(ANCHO_UTIL, 5, f"Vto. CAE: {vto}")

    leyenda = _get_str(config, "leyenda_extra", "")
    if leyenda:
        pdf.ln(3)
        pdf.set_x(MARGIN_L)
        pdf.set_font("Helvetica", "I", 9)
        pdf.multi_cell(ANCHO_UTIL, 4, leyenda)

    return bytes(pdf.output())
