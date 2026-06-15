"""PDF de presupuesto numerado (formato profesional)."""
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from fpdf import FPDF

from modulos.util_branding import NOMBRE_EMPRESA, ruta_logo_hafid
from modulos.util_pdf import texto_para_pdf

VALIDEZ_PRESUPUESTO_DIAS = 3

MARGIN_L = 12
ANCHO_UTIL = 186
W_C, W_D, W_Q, W_P, W_S = 34, 74, 12, 32, 34


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


def _fila_tabla_items(pdf: FPDF, y: float, item: Dict[str, Any]) -> float:
    cod = _codigo_item_presupuesto(item)
    desc = str(item.get("descripcion", ""))
    cant = int(item.get("cantidad", 1))
    precio_u = float(item.get("precio_unitario", 0))
    sub = float(item.get("subtotal", precio_u * cant))

    pdf.set_font("Helvetica", "", 7)
    lineas_cod = _lineas_texto(pdf, cod, W_C - 2, max_lineas=2)
    lineas_desc = _lineas_texto(pdf, desc, W_D - 2, max_lineas=2)
    lh = 3.4
    row_h = max(7.0, lh * max(len(lineas_cod), len(lineas_desc), 1) + 1.5)

    x = MARGIN_L
    pdf.rect(x, y, W_C, row_h)
    pdf.rect(x + W_C, y, W_D, row_h)
    pdf.rect(x + W_C + W_D, y, W_Q, row_h)
    pdf.rect(x + W_C + W_D + W_Q, y, W_P, row_h)
    pdf.rect(x + W_C + W_D + W_Q + W_P, y, W_S, row_h)

    yy = y + 1.2
    for linea in lineas_cod:
        pdf.set_xy(x + 1, yy)
        pdf.cell(W_C - 2, lh, linea, new_x="LMARGIN", new_y="NEXT")
        yy += lh

    yy = y + 1.2
    for linea in lineas_desc:
        pdf.set_xy(x + W_C + 1, yy)
        pdf.cell(W_D - 2, lh, linea, new_x="LMARGIN", new_y="NEXT")
        yy += lh

    pdf.set_font("Helvetica", "", 8)
    pdf.set_xy(x + W_C + W_D, y + (row_h - 4) / 2)
    pdf.cell(W_Q, 4, str(cant), align="C")
    pdf.set_xy(x + W_C + W_D + W_Q, y + (row_h - 4) / 2)
    pdf.cell(W_P, 4, f"${precio_u:,.2f}", align="R")
    pdf.set_xy(x + W_C + W_D + W_Q + W_P, y + (row_h - 4) / 2)
    pdf.cell(W_S, 4, f"${sub:,.2f}", align="R")

    return y + row_h


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
    nombre_cli = str(cli.get("nombre", "CONSUMIDOR FINAL")).upper()
    cuit_cli = str(cli.get("cuit", cli.get("cuit_dni", "")) or "")
    desc = float(descuento_pct)
    total_final = float(total_bruto) * (1 - desc / 100.0)
    nro_txt = _fmt_nro_presupuesto(numero)

    ahora = datetime.now()
    validez = ahora + timedelta(days=VALIDEZ_PRESUPUESTO_DIAS)

    nombre_emp = str(cfg.get("nombre_empresa") or NOMBRE_EMPRESA)
    direccion = str(cfg.get("direccion") or cfg.get("domicilio_comercial") or "")
    cuit_emp = str(cfg.get("cuit_emisor") or "")
    cond_iva = str(cfg.get("condicion_iva") or "")

    pdf = FPDF(orientation="P", unit="mm", format="A4")
    pdf.set_auto_page_break(auto=True, margin=18)
    pdf.add_page()
    pdf.set_margins(MARGIN_L, 12, MARGIN_L)

    y = 12
    logo = ruta_logo_hafid()
    if logo:
        pdf.image(logo, x=MARGIN_L, y=10, h=16)
        y = 30

    pdf.set_xy(MARGIN_L, y)
    pdf.set_font("Helvetica", "B", 11)
    pdf.cell(95, 5, texto_para_pdf(nombre_emp), new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", "", 8)
    if direccion:
        pdf.multi_cell(95, 4, texto_para_pdf(direccion))
    if cuit_emp:
        pdf.cell(95, 4, f"CUIT: {texto_para_pdf(cuit_emp)}", new_x="LMARGIN", new_y="NEXT")
    if cond_iva:
        pdf.cell(95, 4, texto_para_pdf(cond_iva), new_x="LMARGIN", new_y="NEXT")

    pdf.set_xy(108, y)
    pdf.set_font("Helvetica", "B", 16)
    pdf.cell(90, 8, "PRESUPUESTO", align="R")
    pdf.set_xy(108, y + 10)
    pdf.set_font("Helvetica", "B", 12)
    pdf.cell(90, 6, f"Nro {nro_txt}", align="R")
    pdf.set_font("Helvetica", "", 9)
    pdf.set_xy(108, y + 18)
    pdf.cell(90, 4, f"Fecha: {ahora.strftime('%d/%m/%Y %H:%M')}", align="R")
    pdf.set_xy(108, y + 23)
    pdf.cell(90, 4, f"Valido hasta: {validez.strftime('%d/%m/%Y')}", align="R")

    box_y = max(pdf.get_y(), y + 28) + 4
    pdf.rect(MARGIN_L, box_y, ANCHO_UTIL, 16)
    pdf.set_xy(MARGIN_L + 2, box_y + 3)
    pdf.set_font("Helvetica", "B", 9)
    pdf.cell(28, 5, "Cliente:")
    pdf.set_font("Helvetica", "", 9)
    pdf.cell(80, 5, _truncar_ancho(pdf, nombre_cli, 78))
    pdf.set_font("Helvetica", "B", 9)
    pdf.cell(22, 5, "CUIT/DNI:")
    pdf.set_font("Helvetica", "", 9)
    pdf.cell(52, 5, texto_para_pdf(cuit_cli) if cuit_cli else "-")
    pdf.set_xy(MARGIN_L + 2, box_y + 9)
    pdf.set_font("Helvetica", "B", 9)
    pdf.cell(28, 5, "Vendedor:")
    pdf.set_font("Helvetica", "", 9)
    pdf.cell(50, 5, texto_para_pdf(str(vendedor)))
    if desc > 0:
        pdf.set_font("Helvetica", "B", 9)
        pdf.cell(30, 5, "Descuento:")
        pdf.set_font("Helvetica", "", 9)
        pdf.cell(20, 5, f"{desc:g}%")

    y_tab = box_y + 20
    y_tab = _cabecera_tabla_items(pdf, y_tab)
    pdf.set_font("Helvetica", "", 8)
    for item in items:
        if y_tab > 248:
            pdf.add_page()
            y_tab = _cabecera_tabla_items(pdf, MARGIN_L)
        y_tab = _fila_tabla_items(pdf, y_tab, item)

    pdf.set_y(y_tab + 4)
    x_tot = MARGIN_L + W_C + W_D + W_Q
    pdf.set_font("Helvetica", "", 10)
    pdf.set_x(x_tot)
    pdf.cell(W_P, 7, "Subtotal:", align="R")
    pdf.cell(W_S, 7, f"${total_bruto:,.2f}", align="R", new_x="LMARGIN", new_y="NEXT")
    if desc > 0:
        desc_monto = total_bruto * desc / 100.0
        pdf.set_x(x_tot)
        pdf.set_font("Helvetica", "I", 10)
        pdf.cell(W_P, 7, f"Descuento ({desc:g}%):", align="R")
        pdf.cell(W_S, 7, f"-${desc_monto:,.2f}", align="R", new_x="LMARGIN", new_y="NEXT")
    pdf.set_x(x_tot)
    pdf.set_font("Helvetica", "B", 12)
    pdf.cell(W_P, 9, "TOTAL:", align="R")
    pdf.cell(W_S, 9, f"${total_final:,.2f}", align="R", new_x="LMARGIN", new_y="NEXT")

    pdf.ln(6)
    pdf.set_x(MARGIN_L)
    pdf.set_font("Helvetica", "", 8)
    leyendas = [
        f"Presupuesto valido por {VALIDEZ_PRESUPUESTO_DIAS} dias desde la fecha de emision.",
        "Documento sin validez fiscal. No reemplaza factura.",
        "Precios y stock sujetos a disponibilidad al momento de la compra.",
    ]
    if nota:
        leyendas.insert(0, texto_para_pdf(nota))
    for linea in leyendas:
        pdf.multi_cell(ANCHO_UTIL, 4, linea)

    return bytes(pdf.output())
