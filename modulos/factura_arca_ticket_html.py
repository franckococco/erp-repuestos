"""Ticket fiscal térmico en HTML (58/80 mm). La factura A4 sigue en PDF."""
from __future__ import annotations

import base64
import html
from pathlib import Path
from typing import Any, Dict, List, Optional

from modulos.comprobante_contexto import armar_contexto_comprobante
from modulos.util_fechas import ahora_ar

_LOGO_CACHE_B64: Optional[str] = None


def _f(val, default: float = 0.0) -> float:
    try:
        return float(val)
    except (TypeError, ValueError):
        return default


def _fmt_money(val: float) -> str:
    return f"${val:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def _logo_hafid_data_uri() -> str:
    """Logo embebido en base64 para preview e impresión."""
    global _LOGO_CACHE_B64
    if _LOGO_CACHE_B64 is not None:
        return _LOGO_CACHE_B64

    raiz = Path(__file__).resolve().parent.parent
    candidatos = [
        Path(__file__).resolve().parent / "logo_hafid.jpeg",
        raiz / "logo_hafid.jpeg",
        Path(__file__).resolve().parent / "logo_hafid.jpg",
        raiz / "logo_hafid.jpg",
    ]
    for path in candidatos:
        try:
            if path.is_file():
                raw = path.read_bytes()
                b64 = base64.b64encode(raw).decode("ascii")
                _LOGO_CACHE_B64 = f"data:image/jpeg;base64,{b64}"
                return _LOGO_CACHE_B64
        except Exception:
            continue
    _LOGO_CACHE_B64 = ""
    return ""


def _lineas_items_ticket(items: List[Dict[str, Any]], es_factura_a: bool) -> List[Dict[str, Any]]:
    filas = []
    for item in items or []:
        if not isinstance(item, dict):
            continue
        cant = max(1, int(_f(item.get("cantidad"), 1)))
        desc = str(item.get("descripcion", "Artículo")).strip() or "Artículo"
        precio_linea = _f(item.get("precio"), 0.0)
        precio_u = precio_linea / cant if cant else precio_linea
        filas.append({
            "cantidad": cant,
            "descripcion": desc,
            "precio_unitario": precio_u,
            "importe": precio_linea,
        })
    return filas


def crear_ticket_html(
    datos_respuesta: Dict[str, Any],
    datos_cliente: Dict[str, Any],
    items: List[Dict[str, Any]],
    config: Optional[Dict[str, Any]] = None,
    forma_pago: str = "Contado",
    ancho_mm: int = 80,
    vendedor: str = "",
    observacion: str = "",
) -> str:
    """HTML térmico: logo HAFID, Arial negrita, total grande, operario y observación."""
    cfg = dict(config or {})
    ctx = armar_contexto_comprobante(datos_respuesta, datos_cliente, cfg, forma_pago=forma_pago)
    emisor = ctx["emisor"]
    comp = ctx["comprobante"]
    cli = ctx["cliente"]
    cae = ctx["cae"]

    es_a = comp["tipo_letra"] == "A"
    nro_fc = f"{int(comp['punto_venta']):04d}-{int(comp['numero']):08d}"
    filas = _lineas_items_ticket(items, es_a)
    total = sum(f["importe"] for f in filas)

    filas_html = []
    for f in filas:
        cant_txt = str(f["cantidad"])
        desc_txt = html.escape(f["descripcion"])
        imp_txt = _fmt_money(f["importe"])
        pu_txt = _fmt_money(f["precio_unitario"])
        filas_html.append(
            f'<tr class="item">'
            f'<td class="cant">{cant_txt}</td>'
            f'<td class="desc"><span class="d">{desc_txt}</span>'
            f'<span class="pu">{pu_txt} c/u</span></td>'
            f'<td class="imp">{imp_txt}</td>'
            f'</tr>'
        )
    items_body = "\n".join(filas_html) if filas_html else (
        '<tr><td colspan="3" class="center">Sin ítems</td></tr>'
    )

    if es_a:
        neto = total / 1.21
        iva = total - neto
        desglose_html = f"""
        <div class="row"><span>Neto gravado</span><span>{_fmt_money(neto)}</span></div>
        <div class="row"><span>IVA 21%</span><span>{_fmt_money(iva)}</span></div>
        """
    else:
        desglose_html = ""

    def esc(s):
        return html.escape(str(s or ""))

    fecha_hora = ahora_ar().strftime("%d/%m/%Y %H:%M")
    leyenda = esc(ctx.get("leyenda_extra", "Gracias por su compra"))
    total_txt = _fmt_money(total)

    nombre_cli = str(cli.get("nombre", "CONSUMIDOR FINAL") or "")
    cond_cli = str(cli.get("condicion_iva") or "").strip()
    mostrar_cond = bool(cond_cli) and not (
        cond_cli.lower() in ("consumidor final", "consumidorfinal")
        and "consumidor final" in nombre_cli.lower()
    )
    linea_cond = f'<div class="sub">{esc(cond_cli)}</div>' if mostrar_cond else ""

    logo_uri = _logo_hafid_data_uri()
    logo_html = (
        f'<div class="logo-wrap"><img class="logo" src="{logo_uri}" alt="HAFID"></div>'
        if logo_uri
        else ""
    )

    operario = str(vendedor or "").strip()
    linea_operario = (
        f'<div class="sub">Atendido por: <strong>{esc(operario.upper())}</strong></div>'
        if operario
        else ""
    )

    obs = str(observacion or "").strip()
    linea_obs = (
        f'<hr class="sep"><div class="obs"><strong>Obs.:</strong> {esc(obs)}</div>'
        if obs
        else ""
    )

    return f"""<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width={ancho_mm}mm, initial-scale=1">
<title>Ticket {esc(nro_fc)}</title>
<style>
  @page {{
    size: {ancho_mm}mm auto;
    margin: 1.5mm;
  }}
  * {{ box-sizing: border-box; }}
  body {{
    width: {ancho_mm - 4}mm;
    max-width: {ancho_mm - 4}mm;
    margin: 0 auto;
    padding: 2mm;
    font-family: Arial, Helvetica, sans-serif;
    font-weight: 700;
    font-size: 11px;
    line-height: 1.3;
    color: #000;
    background: #fff;
  }}
  .center {{ text-align: center; }}
  .logo-wrap {{
    text-align: center;
    margin: 0 0 3px;
  }}
  .logo {{
    max-width: 42mm;
    max-height: 18mm;
    width: auto;
    height: auto;
    object-fit: contain;
  }}
  .sep {{
    border: none;
    border-top: 1px dashed #000;
    margin: 3px 0;
  }}
  h1 {{
    font-family: Arial, Helvetica, sans-serif;
    font-size: 13px;
    font-weight: 700;
    margin: 0 0 2px;
    text-align: center;
    word-wrap: break-word;
  }}
  .sub {{
    font-family: Arial, Helvetica, sans-serif;
    font-size: 10px;
    font-weight: 700;
    text-align: center;
    margin: 1px 0;
    word-wrap: break-word;
  }}
  .factura {{
    font-family: Arial, Helvetica, sans-serif;
    font-size: 12px;
    font-weight: 700;
    text-align: center;
    margin: 4px 0;
  }}
  .cliente {{
    font-family: Arial, Helvetica, sans-serif;
    font-weight: 700;
    word-wrap: break-word;
    overflow-wrap: anywhere;
  }}
  table.items {{
    width: 100%;
    border-collapse: collapse;
    margin: 4px 0;
    font-size: 10px;
    font-family: Arial, Helvetica, sans-serif;
  }}
  table.items td {{
    vertical-align: top;
    padding: 2px 0;
    font-weight: 700;
  }}
  td.cant {{
    width: 9mm;
    text-align: left;
    white-space: nowrap;
  }}
  td.desc {{
    width: auto;
    padding-right: 2px;
  }}
  td.desc .d {{
    display: block;
    font-weight: 700;
    word-wrap: break-word;
    overflow-wrap: anywhere;
    white-space: normal;
  }}
  td.desc .pu {{
    display: block;
    font-size: 9px;
    font-weight: 700;
    margin-top: 1px;
  }}
  td.imp {{
    width: 16mm;
    text-align: right;
    white-space: nowrap;
  }}
  .row {{
    display: flex;
    justify-content: space-between;
    font-weight: 700;
    font-family: Arial, Helvetica, sans-serif;
    margin: 2px 0;
    font-size: 10px;
  }}
  .total-box {{
    text-align: center;
    margin: 6px 0 4px;
    padding: 4px 0;
  }}
  .total-label {{
    font-family: Arial, Helvetica, sans-serif;
    font-size: 14px;
    font-weight: 700;
    letter-spacing: 0.5px;
  }}
  .total-monto {{
    font-family: Arial, Helvetica, sans-serif;
    font-size: 22px;
    font-weight: 700;
    margin-top: 2px;
    line-height: 1.15;
  }}
  .cae {{
    text-align: center;
    font-size: 10px;
    font-weight: 700;
    font-family: Arial, Helvetica, sans-serif;
    word-wrap: break-word;
  }}
  .obs {{
    font-family: Arial, Helvetica, sans-serif;
    font-size: 10px;
    font-weight: 700;
    text-align: left;
    word-wrap: break-word;
    overflow-wrap: anywhere;
    margin: 2px 0;
  }}
  .pie {{
    text-align: center;
    font-size: 10px;
    font-weight: 700;
    font-family: Arial, Helvetica, sans-serif;
    margin-top: 4px;
  }}
  .noprint {{
    margin: 8px 0;
    text-align: center;
  }}
  .noprint button {{
    font-family: Arial, Helvetica, sans-serif;
    font-weight: 700;
    padding: 8px 16px;
    font-size: 13px;
    cursor: pointer;
  }}
  @media print {{
    .noprint {{ display: none !important; }}
    body {{ width: {ancho_mm - 4}mm; }}
  }}
</style>
</head>
<body>
  {logo_html}
  <h1>{esc(emisor.get("nombre_fantasia"))}</h1>
  <div class="sub">{esc(emisor.get("domicilio_comercial"))}</div>
  <div class="sub">CUIT: {esc(emisor.get("cuit"))}</div>
  <div class="sub">{esc(emisor.get("iibb"))}</div>
  <div class="sub">Inicio act.: {esc(emisor.get("inicio_actividades"))}</div>
  <div class="sub">{esc(emisor.get("condicion_iva"))}</div>

  <hr class="sep">
  <div class="factura">FACTURA {comp["tipo_letra"]} Nº {esc(nro_fc)}</div>
  <div class="sub">{fecha_hora}</div>
  {linea_operario}

  <hr class="sep">
  <div><strong>CLIENTE:</strong></div>
  <div class="cliente">{esc(cli.get("nombre", "CONSUMIDOR FINAL"))}</div>
  <div class="sub">CUIT/DNI: {esc(cli.get("cuit", "00000000000"))}</div>
  {linea_cond}

  <hr class="sep">
  <table class="items">
    <thead>
      <tr>
        <td class="cant">Cant</td>
        <td class="desc">Descripción</td>
        <td class="imp">Importe</td>
      </tr>
    </thead>
    <tbody>
      {items_body}
    </tbody>
  </table>

  <hr class="sep">
  {desglose_html}
  <div class="total-box">
    <div class="total-label">TOTAL</div>
    <div class="total-monto">{total_txt}</div>
  </div>
  <div class="sub center">Pago: {esc(cli.get("condicion_venta", forma_pago))}</div>
  {linea_obs}

  <hr class="sep">
  <div class="cae">CAE: {esc(cae.get("numero"))}</div>
  <div class="cae">Vto CAE: {esc(cae.get("vencimiento"))}</div>
  <div class="pie">{leyenda}</div>

  <div class="noprint">
    <button type="button" onclick="window.print()">🖨️ Imprimir ticket</button>
  </div>
</body>
</html>"""
