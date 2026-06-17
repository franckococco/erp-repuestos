"""Layout A4 compartido: presupuesto, factura y utilidades de impresión."""
from typing import Any, Dict, List, Optional, Tuple

from fpdf import FPDF

from modulos.util_branding import NOMBRE_EMPRESA, ruta_logo_hafid
from modulos.util_pdf import texto_para_pdf

MARGIN_L = 12
ANCHO_UTIL = 186
W_COL_IZQ = 98
X_COL_DER = MARGIN_L + 108
W_COL_DER = MARGIN_L + ANCHO_UTIL - X_COL_DER

W_C, W_D, W_Q, W_P, W_S = 34, 74, 12, 32, 34
W_TOT_LABEL = 46
W_TOT_VALUE = 34
X_TOT_VALUE = MARGIN_L + ANCHO_UTIL - W_TOT_VALUE
X_TOT_LABEL = X_TOT_VALUE - W_TOT_LABEL


def calc_totales_con_dto(total_bruto: float, descuento_pct: float) -> Tuple[float, float, float]:
    bruto = float(total_bruto)
    desc = max(0.0, float(descuento_pct))
    if desc > 0:
        monto_dto = round(bruto * desc / 100.0, 2)
        return bruto, monto_dto, round(bruto - monto_dto, 2)
    return bruto, 0.0, bruto


def _truncar_ancho(pdf: FPDF, texto: str, ancho_mm: float) -> str:
    t = texto_para_pdf(str(texto or ""))
    if not t:
        return "-"
    while len(t) > 1 and pdf.get_string_width(t) > ancho_mm - 2:
        t = t[:-3].rstrip() + "..." if len(t) > 4 else t[:-1]
    return t


def _lineas_texto(pdf: FPDF, texto: str, ancho_mm: float, max_lineas: int = 2) -> List[str]:
    t = texto_para_pdf(str(texto or ""))
    if not t:
        return ["-"]
    palabras = t.split()
    lineas: List[str] = []
    actual = ""
    for palabra in palabras:
        prueba = f"{actual} {palabra}".strip()
        if pdf.get_string_width(prueba) <= ancho_mm - 2:
            actual = prueba
        else:
            if actual:
                lineas.append(actual)
            actual = palabra
        if len(lineas) >= max_lineas:
            break
    if actual and len(lineas) < max_lineas:
        lineas.append(actual)
    if len(lineas) >= max_lineas and len(palabras) > len(" ".join(lineas).split()):
        ult = lineas[-1]
        if not ult.endswith("..."):
            lineas[-1] = _truncar_ancho(pdf, ult, ancho_mm)
    return lineas or ["-"]


def nueva_pagina_a4() -> FPDF:
    pdf = FPDF(orientation="P", unit="mm", format="A4")
    pdf.set_auto_page_break(auto=True, margin=18)
    pdf.add_page()
    pdf.set_margins(MARGIN_L, 12, MARGIN_L)
    return pdf


def dibujar_cabecera_documento(
    pdf: FPDF,
    cfg: Dict[str, Any],
    titulo: str,
    lineas_derecha: List[str],
    y_inicio: float = 12.0,
) -> float:
    """Cabecera en dos columnas sin superposición. Devuelve Y para la caja cliente."""
    nombre_emp = str(cfg.get("nombre_empresa") or NOMBRE_EMPRESA)
    direccion = str(cfg.get("direccion") or cfg.get("domicilio_comercial") or "")
    cuit_emp = str(cfg.get("cuit_emisor") or "")
    cond_iva = str(cfg.get("condicion_iva") or "")
    iibb = str(cfg.get("iibb") or "")
    inicio_act = str(cfg.get("inicio_act") or "")

    y = y_inicio
    logo = ruta_logo_hafid()
    if logo:
        pdf.image(logo, x=MARGIN_L, y=6, h=32)
        y = 40.0

    y_izq = y
    pdf.set_xy(MARGIN_L, y_izq)
    pdf.set_font("Helvetica", "B", 11)
    pdf.multi_cell(W_COL_IZQ, 5, texto_para_pdf(nombre_emp))
    y_izq = pdf.get_y()
    pdf.set_font("Helvetica", "", 8)
    if direccion:
        pdf.set_x(MARGIN_L)
        pdf.multi_cell(W_COL_IZQ, 4, texto_para_pdf(direccion))
        y_izq = pdf.get_y()
    for linea in (f"CUIT: {cuit_emp}" if cuit_emp else "", cond_iva, iibb, inicio_act):
        if linea:
            pdf.set_xy(MARGIN_L, y_izq)
            pdf.cell(W_COL_IZQ, 4, texto_para_pdf(linea), new_x="LMARGIN", new_y="NEXT")
            y_izq += 4

    y_der = y
    pdf.set_xy(X_COL_DER, y_der)
    pdf.set_font("Helvetica", "B", 16)
    pdf.cell(W_COL_DER, 8, texto_para_pdf(titulo), align="R")
    y_der += 10
    pdf.set_font("Helvetica", "", 9)
    for linea in lineas_derecha:
        pdf.set_xy(X_COL_DER, y_der)
        pdf.cell(W_COL_DER, 4, texto_para_pdf(linea), align="R")
        y_der += 5

    return max(y_izq, y_der) + 5


def dibujar_caja_cliente(
    pdf: FPDF,
    y: float,
    nombre_cli: str,
    cuit_cli: str,
    vendedor: Optional[str] = None,
    filas_extra: Optional[List[Tuple[str, str]]] = None,
) -> float:
    """Recuadro cliente de dos filas bien separadas."""
    box_h = 18.0
    pdf.rect(MARGIN_L, y, ANCHO_UTIL, box_h)
    pdf.line(MARGIN_L, y + 9, MARGIN_L + ANCHO_UTIL, y + 9)

    pdf.set_xy(MARGIN_L + 2, y + 2)
    pdf.set_font("Helvetica", "B", 9)
    pdf.cell(24, 5, "Cliente:")
    pdf.set_font("Helvetica", "", 9)
    pdf.cell(78, 5, _truncar_ancho(pdf, nombre_cli.upper(), 76))
    pdf.set_font("Helvetica", "B", 9)
    pdf.cell(22, 5, "CUIT/DNI:")
    pdf.set_font("Helvetica", "", 9)
    pdf.cell(ANCHO_UTIL - 128, 5, texto_para_pdf(cuit_cli) if cuit_cli else "-")

    pdf.set_xy(MARGIN_L + 2, y + 11)
    if filas_extra:
        for i, (lbl, val) in enumerate(filas_extra[:2]):
            if i:
                pdf.set_x(MARGIN_L + 96)
            pdf.set_font("Helvetica", "B", 9)
            pdf.cell(24, 5, f"{lbl}:")
            pdf.set_font("Helvetica", "", 9)
            pdf.cell(70 if not i else ANCHO_UTIL - 122, 5, _truncar_ancho(pdf, val, 68))
    elif vendedor:
        pdf.set_font("Helvetica", "B", 9)
        pdf.cell(24, 5, "Vendedor:")
        pdf.set_font("Helvetica", "", 9)
        pdf.cell(ANCHO_UTIL - 28, 5, texto_para_pdf(str(vendedor)))

    return y + box_h + 4


def _fila_total_pdf(pdf: FPDF, label: str, valor: str, alto: float = 7, tam: int = 10, estilo: str = ""):
    y = pdf.get_y()
    pdf.set_xy(X_TOT_LABEL, y)
    if estilo == "B":
        pdf.set_font("Helvetica", "B", tam)
    elif estilo == "I":
        pdf.set_font("Helvetica", "I", tam)
    else:
        pdf.set_font("Helvetica", "", tam)
    pdf.cell(W_TOT_LABEL, alto, label, align="R")
    pdf.set_xy(X_TOT_VALUE, y)
    pdf.cell(W_TOT_VALUE, alto, valor, align="R", new_x="LMARGIN", new_y="NEXT")


def _modo_descuento_cliente(cliente: Optional[Dict[str, Any]]) -> Tuple[float, str, bool]:
    """Devuelve (desc_pct, etiqueta, mostrar_dto_en_pdf)."""
    cli = dict(cliente or {})
    desc = float(cli.get("descuento", 0) or 0)
    etiqueta = str(cli.get("etiqueta_descuento", "") or "").strip().upper()
    tipo = str(cli.get("tipo_cliente", "ocasional") or "ocasional").strip().lower()
    if tipo == "mecanico" and desc > 0:
        return desc, etiqueta, False
    if desc > 0 and etiqueta and tipo != "ocasional":
        return desc, etiqueta, False
    return desc, "", desc > 0


def sufijo_etiqueta_discreta(etiqueta: str) -> str:
    etq = str(etiqueta or "").strip().upper()
    return etq


def dibujar_etiqueta_discreta_cae(pdf: FPDF, etiqueta: str):
    """Sigla discreta debajo del bloque CAE (factura)."""
    etq = sufijo_etiqueta_discreta(etiqueta)
    if not etq:
        return
    pdf.ln(1)
    pdf.set_x(MARGIN_L)
    pdf.set_font("Helvetica", "I", 7)
    pdf.set_text_color(100, 100, 100)
    pdf.cell(ANCHO_UTIL, 3, etq, align="L", new_x="LMARGIN", new_y="NEXT")
    pdf.set_text_color(0, 0, 0)


def dibujar_etiqueta_pie_pdf(pdf: FPDF, etiqueta: str):
    """Compat: ya no usa bloque aparte; ver presupuesto/factura."""
    pass


def dibujar_totales_cliente_pdf(
    pdf: FPDF,
    total_bruto: float,
    cliente: Optional[Dict[str, Any]] = None,
    total_final_override: Optional[float] = None,
):
    """Totales según tipo de cliente: Dto visible u oculto con etiqueta al pie."""
    desc, etiqueta, mostrar_dto = _modo_descuento_cliente(cliente)
    bruto, monto_dto, total_final = calc_totales_con_dto(total_bruto, desc)
    if total_final_override is not None:
        total_final = float(total_final_override)
    if mostrar_dto and monto_dto > 0.005:
        dibujar_totales_con_dto(pdf, bruto, desc)
    else:
        _fila_total_pdf(pdf, "TOTAL:", f"${total_final:,.2f}", alto=9, tam=12, estilo="B")
    return etiqueta


def dibujar_totales_con_dto(
    pdf: FPDF,
    total_bruto: float,
    descuento_pct: float = 0.0,
    monto_dto_fijo: Optional[float] = None,
):
    bruto, monto_dto, total_final = calc_totales_con_dto(total_bruto, descuento_pct)
    if monto_dto_fijo is not None and monto_dto_fijo > 0:
        monto_dto = float(monto_dto_fijo)
        total_final = round(bruto - monto_dto, 2)
    if monto_dto > 0.005:
        _fila_total_pdf(pdf, "Subtotal:", f"${bruto:,.2f}", tam=10)
        _fila_total_pdf(pdf, "Dto:", f"${monto_dto:,.2f}", tam=10)
    _fila_total_pdf(pdf, "TOTAL:", f"${total_final:,.2f}", alto=9, tam=12, estilo="B")


def _cabecera_tabla_items(pdf: FPDF, y: float) -> float:
    h = 7
    pdf.set_xy(MARGIN_L, y)
    pdf.set_font("Helvetica", "B", 8)
    pdf.set_fill_color(230, 230, 230)
    pdf.cell(W_C, h, "Codigo", 1, align="C", fill=True)
    pdf.cell(W_D, h, "Descripcion", 1, align="C", fill=True)
    pdf.cell(W_Q, h, "Cant.", 1, align="C", fill=True)
    pdf.cell(W_P, h, "P. unit.", 1, align="C", fill=True)
    pdf.cell(W_S, h, "Subtotal", 1, align="R", fill=True, new_x="LMARGIN", new_y="NEXT")
    return pdf.get_y()


def _fila_tabla_item_generico(
    pdf: FPDF,
    y: float,
    codigo: str,
    descripcion: str,
    cant: float,
    precio_u: float,
    subtotal: float,
) -> float:
    pdf.set_font("Helvetica", "", 7)
    lineas_cod = _lineas_texto(pdf, codigo, W_C - 2, max_lineas=2)
    lineas_desc = _lineas_texto(pdf, descripcion, W_D - 2, max_lineas=2)
    lh = 3.4
    row_h = max(7.0, lh * max(len(lineas_cod), len(lineas_desc), 1) + 1.5)

    x = MARGIN_L
    for w in (W_C, W_D, W_Q, W_P, W_S):
        pdf.rect(x, y, w, row_h)
        x += w

    yy = y + 1.2
    for linea in lineas_cod:
        pdf.set_xy(MARGIN_L + 1, yy)
        pdf.cell(W_C - 2, lh, linea, new_x="LMARGIN", new_y="NEXT")
        yy += lh

    yy = y + 1.2
    for linea in lineas_desc:
        pdf.set_xy(MARGIN_L + W_C + 1, yy)
        pdf.cell(W_D - 2, lh, linea, new_x="LMARGIN", new_y="NEXT")
        yy += lh

    cant_str = f"{int(cant)}" if float(cant).is_integer() else f"{cant:.2f}"
    pdf.set_font("Helvetica", "", 8)
    pdf.set_xy(MARGIN_L + W_C + W_D, y + (row_h - 4) / 2)
    pdf.cell(W_Q, 4, cant_str, align="C")
    pdf.set_xy(MARGIN_L + W_C + W_D + W_Q, y + (row_h - 4) / 2)
    pdf.cell(W_P, 4, f"${precio_u:,.2f}", align="R")
    pdf.set_xy(MARGIN_L + W_C + W_D + W_Q + W_P, y + (row_h - 4) / 2)
    pdf.cell(W_S, 4, f"${subtotal:,.2f}", align="R")
    return y + row_h


def dibujar_tabla_items(
    pdf: FPDF,
    y: float,
    filas: List[Dict[str, Any]],
    codigo_fn,
) -> float:
    y = _cabecera_tabla_items(pdf, y)
    pdf.set_font("Helvetica", "", 8)
    for fila in filas:
        if y > 248:
            pdf.add_page()
            y = _cabecera_tabla_items(pdf, MARGIN_L)
        cod = codigo_fn(fila)
        desc = str(fila.get("descripcion", ""))
        cant = float(fila.get("cantidad", 1))
        precio_u = float(fila.get("precio_unitario", fila.get("precio_u", 0)))
        sub = float(fila.get("subtotal", fila.get("precio", precio_u * cant)))
        if not precio_u and cant:
            precio_u = sub / cant
        y = _fila_tabla_item_generico(pdf, y, cod, desc, cant, precio_u, sub)
    return y
