"""PDF de presupuesto numerado (formato profesional)."""
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from fpdf import FPDF

from modulos.util_branding import NOMBRE_EMPRESA, ruta_logo_hafid
from modulos.util_pdf import texto_para_pdf

VALIDEZ_PRESUPUESTO_DIAS = 3


def _fmt_nro_presupuesto(numero: Optional[int]) -> str:
    if numero is None or int(numero) <= 0:
        return "BORRADOR"
    return f"{int(numero):04d}"


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

    MARGIN_L = 12
    ANCHO_UTIL = 186

    pdf = FPDF(orientation="P", unit="mm", format="A4")
    pdf.set_auto_page_break(auto=True, margin=18)
    pdf.add_page()
    pdf.set_margins(MARGIN_L, 12, MARGIN_L)

    y = 12
    logo = ruta_logo_hafid()
    if logo:
        pdf.image(logo, x=12, y=10, h=16)
        y = 30

    pdf.set_xy(12, y)
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
    pdf.rect(12, box_y, 186, 16)
    pdf.set_xy(14, box_y + 3)
    pdf.set_font("Helvetica", "B", 9)
    pdf.cell(28, 5, "Cliente:")
    pdf.set_font("Helvetica", "", 9)
    pdf.cell(80, 5, texto_para_pdf(nombre_cli))
    pdf.set_font("Helvetica", "B", 9)
    pdf.cell(22, 5, "CUIT/DNI:")
    pdf.set_font("Helvetica", "", 9)
    pdf.cell(50, 5, texto_para_pdf(cuit_cli) if cuit_cli else "—")
    pdf.set_xy(14, box_y + 9)
    pdf.set_font("Helvetica", "B", 9)
    pdf.cell(28, 5, "Vendedor:")
    pdf.set_font("Helvetica", "", 9)
    pdf.cell(50, 5, texto_para_pdf(str(vendedor)))
    if desc > 0:
        pdf.set_font("Helvetica", "B", 9)
        pdf.cell(30, 5, "Descuento:")
        pdf.set_font("Helvetica", "", 9)
        pdf.cell(20, 5, f"{desc:g}%")

    pdf.set_xy(12, box_y + 20)
    pdf.set_font("Helvetica", "B", 8)
    pdf.set_fill_color(230, 230, 230)
    w_c, w_d, w_q, w_p, w_s = 26, 82, 14, 32, 32
    h = 7
    pdf.cell(w_c, h, "Codigo", 1, align="C", fill=True)
    pdf.cell(w_d, h, "Descripcion", 1, align="C", fill=True)
    pdf.cell(w_q, h, "Cant.", 1, align="C", fill=True)
    pdf.cell(w_p, h, "P. unit.", 1, align="C", fill=True)
    pdf.cell(w_s, h, "Subtotal", 1, align="R", fill=True, new_x="LMARGIN", new_y="NEXT")

    pdf.set_font("Helvetica", "", 8)
    for item in items:
        if pdf.get_y() > 250:
            pdf.add_page()
            pdf.set_font("Helvetica", "B", 8)
            pdf.cell(w_c, h, "Codigo", 1, align="C", fill=True)
            pdf.cell(w_d, h, "Descripcion", 1, align="C", fill=True)
            pdf.cell(w_q, h, "Cant.", 1, align="C", fill=True)
            pdf.cell(w_p, h, "P. unit.", 1, align="C", fill=True)
            pdf.cell(w_s, h, "Subtotal", 1, align="R", fill=True, new_x="LMARGIN", new_y="NEXT")
            pdf.set_font("Helvetica", "", 8)

        cod = str(item.get("id", item.get("codigo", "")))[:24]
        desc_item = str(item.get("descripcion", ""))[:52]
        cant = int(item.get("cantidad", 1))
        precio_u = float(item.get("precio_unitario", 0))
        sub = float(item.get("subtotal", precio_u * cant))

        pdf.cell(w_c, h, texto_para_pdf(cod), 1)
        pdf.cell(w_d, h, texto_para_pdf(desc_item), 1)
        pdf.cell(w_q, h, str(cant), 1, align="C")
        pdf.cell(w_p, h, f"${precio_u:,.2f}", 1, align="R")
        pdf.cell(w_s, h, f"${sub:,.2f}", 1, align="R", new_x="LMARGIN", new_y="NEXT")

    pdf.ln(4)
    x_tot = MARGIN_L + w_c + w_d + w_q
    pdf.set_font("Helvetica", "", 10)
    pdf.set_x(x_tot)
    pdf.cell(w_p, 7, "Subtotal:", align="R")
    pdf.cell(w_s, 7, f"${total_bruto:,.2f}", align="R", new_x="LMARGIN", new_y="NEXT")
    if desc > 0:
        desc_monto = total_bruto * desc / 100.0
        pdf.set_x(x_tot)
        pdf.set_font("Helvetica", "I", 10)
        pdf.cell(w_p, 7, f"Descuento ({desc:g}%):", align="R")
        pdf.cell(w_s, 7, f"-${desc_monto:,.2f}", align="R", new_x="LMARGIN", new_y="NEXT")
    pdf.set_x(x_tot)
    pdf.set_font("Helvetica", "B", 12)
    pdf.cell(w_p, 9, "TOTAL:", align="R")
    pdf.cell(w_s, 9, f"${total_final:,.2f}", align="R", new_x="LMARGIN", new_y="NEXT")

    pdf.ln(6)
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
