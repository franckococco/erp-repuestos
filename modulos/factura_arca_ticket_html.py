"""Ticket fiscal térmico en HTML (58/80 mm). La factura A4 sigue en PDF."""
from __future__ import annotations

import html
from typing import Any, Dict, List, Optional

from modulos.comprobante_contexto import armar_contexto_comprobante
from modulos.util_fechas import ahora_ar


def _f(val, default: float = 0.0) -> float:
    try:
        return float(val)
    except (TypeError, ValueError):
        return default


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
) -> str:
    """HTML optimizado para impresora térmica: negrita, texto completo, sin cortes rígidos."""
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
        imp_txt = f"${f['importe']:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
        pu_txt = f"${f['precio_unitario']:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
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
        totales_html = f"""
        <div class="row"><span>Neto gravado</span><span>${neto:,.2f}</span></div>
        <div class="row"><span>IVA 21%</span><span>${iva:,.2f}</span></div>
        <div class="row total"><span>TOTAL</span><span>${total:,.2f}</span></div>
        """
    else:
        totales_html = f'<div class="row total"><span>TOTAL</span><span>${total:,.2f}</span></div>'

    def esc(s):
        return html.escape(str(s or ""))

    fecha_hora = ahora_ar().strftime("%d/%m/%Y %H:%M")
    leyenda = esc(ctx.get("leyenda_extra", "Gracias por su compra"))

    nombre_cli = str(cli.get("nombre", "CONSUMIDOR FINAL") or "")
    cond_cli = str(cli.get("condicion_iva") or "").strip()
    # Evitar «Consumidor Final» duplicado debajo del nombre
    mostrar_cond = bool(cond_cli) and not (
        cond_cli.lower() in ("consumidor final", "consumidorfinal")
        and "consumidor final" in nombre_cli.lower()
    )
    linea_cond = f'<div class="sub">{esc(cond_cli)}</div>' if mostrar_cond else ""

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
    font-family: "Courier New", Courier, monospace;
    font-weight: 700;
    font-size: 11px;
    line-height: 1.3;
    color: #000;
    background: #fff;
  }}
  .center {{ text-align: center; }}
  .sep {{
    border: none;
    border-top: 1px dashed #000;
    margin: 3px 0;
  }}
  h1 {{
    font-size: 13px;
    font-weight: 900;
    margin: 0 0 2px;
    text-align: center;
    word-wrap: break-word;
  }}
  .sub {{
    font-size: 10px;
    font-weight: 700;
    text-align: center;
    margin: 1px 0;
    word-wrap: break-word;
  }}
  .factura {{
    font-size: 12px;
    font-weight: 900;
    text-align: center;
    margin: 4px 0;
  }}
  .cliente {{
    font-weight: 800;
    word-wrap: break-word;
    overflow-wrap: anywhere;
  }}
  table.items {{
    width: 100%;
    border-collapse: collapse;
    margin: 4px 0;
    font-size: 10px;
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
    font-weight: 800;
    word-wrap: break-word;
    overflow-wrap: anywhere;
    white-space: normal;
  }}
  td.desc .pu {{
    display: block;
    font-size: 9px;
    font-weight: 600;
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
    font-weight: 800;
    margin: 2px 0;
  }}
  .row.total {{
    font-size: 13px;
    font-weight: 900;
    margin-top: 4px;
  }}
  .cae {{
    text-align: center;
    font-size: 10px;
    font-weight: 800;
    word-wrap: break-word;
  }}
  .pie {{
    text-align: center;
    font-size: 10px;
    font-weight: 700;
    margin-top: 4px;
  }}
  .noprint {{
    margin: 8px 0;
    text-align: center;
  }}
  .noprint button {{
    font-weight: 800;
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
  <h1>{esc(emisor.get("nombre_fantasia"))}</h1>
  <div class="sub">{esc(emisor.get("domicilio_comercial"))}</div>
  <div class="sub">CUIT: {esc(emisor.get("cuit"))}</div>
  <div class="sub">{esc(emisor.get("iibb"))}</div>
  <div class="sub">Inicio act.: {esc(emisor.get("inicio_actividades"))}</div>
  <div class="sub">{esc(emisor.get("condicion_iva"))}</div>

  <hr class="sep">
  <div class="factura">FACTURA {comp["tipo_letra"]} Nº {esc(nro_fc)}</div>
  <div class="sub">{fecha_hora}</div>

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
  {totales_html}
  <div class="sub center">Pago: {esc(cli.get("condicion_venta", forma_pago))}</div>

  <hr class="sep">
  <div class="cae">CAE: {esc(cae.get("numero"))}</div>
  <div class="cae">Vto CAE: {esc(cae.get("vencimiento"))}</div>
  <div class="pie">{leyenda}</div>

  <div class="noprint">
    <button type="button" onclick="window.print()">🖨️ Imprimir ticket</button>
  </div>
</body>
</html>"""
