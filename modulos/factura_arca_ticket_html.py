"""Ticket fiscal térmico en HTML (58/80 mm). La factura A4 sigue en PDF."""
from __future__ import annotations

import base64
import html
import json
from io import BytesIO
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from modulos.comprobante_contexto import armar_contexto_comprobante
from modulos.util_fechas import ahora_ar

_LOGO_CACHE_B64: Optional[str] = None
# Marcador visible en caption / HTML para confirmar deploy en Streamlit Cloud
TICKET_DISENO_VERSION = "v6-ancho-completo-claro"


def _f(val, default: float = 0.0) -> float:
    try:
        return float(val)
    except (TypeError, ValueError):
        return default


def _fmt_money(val: float) -> str:
    return f"${val:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def _logo_aclarado_png_b64(path: Path) -> Optional[str]:
    """Aclara el logo (brillo/contraste) para que no salga negro en térmica."""
    try:
        from PIL import Image, ImageEnhance

        im = Image.open(path)
        if im.mode in ("RGBA", "LA") or (im.mode == "P" and "transparency" in im.info):
            rgba = im.convert("RGBA")
            fondo = Image.new("RGBA", rgba.size, (255, 255, 255, 255))
            im = Image.alpha_composite(fondo, rgba).convert("RGB")
        else:
            im = im.convert("RGB")

        # Reducir tamaño de embed (ticket 80 mm)
        resample = getattr(getattr(Image, "Resampling", Image), "LANCZOS", Image.LANCZOS)
        im.thumbnail((520, 240), resample)
        im = ImageEnhance.Brightness(im).enhance(1.45)
        im = ImageEnhance.Contrast(im).enhance(1.35)
        im = ImageEnhance.Color(im).enhance(1.15)

        buf = BytesIO()
        im.save(buf, format="PNG", optimize=True)
        return base64.b64encode(buf.getvalue()).decode("ascii")
    except Exception:
        return None


def _logo_hafid_data_uri() -> str:
    """Logo HAFID embebido, aclarado para impresión térmica legible."""
    global _LOGO_CACHE_B64
    if _LOGO_CACHE_B64 is not None:
        return _LOGO_CACHE_B64

    raiz = Path(__file__).resolve().parent.parent
    candidatos = [
        Path(__file__).resolve().parent / "logo_hafid.jpeg",
        raiz / "logo_hafid.jpeg",
        Path(__file__).resolve().parent / "logo_hafid.jpg",
        raiz / "logo_hafid.jpg",
        Path(__file__).resolve().parent / "logo_hafid.png",
        raiz / "logo_hafid.png",
        Path(__file__).resolve().parent / "logo_hafid.webp",
        raiz / "logo_hafid.webp",
    ]
    mime_por_ext = {
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".png": "image/png",
        ".webp": "image/webp",
    }
    for path in candidatos:
        try:
            if not path.is_file():
                continue
            aclarado = _logo_aclarado_png_b64(path)
            if aclarado:
                _LOGO_CACHE_B64 = f"data:image/png;base64,{aclarado}"
                return _LOGO_CACHE_B64
            mime = mime_por_ext.get(path.suffix.lower(), "image/jpeg")
            b64 = base64.b64encode(path.read_bytes()).decode("ascii")
            _LOGO_CACHE_B64 = f"data:{mime};base64,{b64}"
            return _LOGO_CACHE_B64
        except Exception:
            continue
    _LOGO_CACHE_B64 = ""
    return ""


def _digitos(val: Any) -> str:
    return "".join(c for c in str(val or "") if c.isdigit())


def _tipo_doc_receptor(cuit_cli: str) -> Tuple[int, int]:
    """Código AFIP tipoDocRec + nroDocRec para el QR."""
    dig = _digitos(cuit_cli)
    if not dig or dig == "0" * len(dig) or dig in ("00000000000", "0"):
        return 99, 0
    if len(dig) == 11:
        return 80, int(dig)
    if 7 <= len(dig) <= 8:
        return 96, int(dig)
    try:
        return 99, int(dig) if dig else 0
    except ValueError:
        return 99, 0


def _qr_arca_data_uri(
    *,
    cuit_emisor: str,
    punto_venta: int,
    tipo_cmp: int,
    nro_cmp: int,
    importe: float,
    cuit_cliente: str,
    cae: str,
    fecha_iso: str,
) -> str:
    """Genera PNG del QR oficial ARCA/AFIP (URL con JSON en base64)."""
    dig_cuit = _digitos(cuit_emisor)
    dig_cae = _digitos(cae)
    if len(dig_cuit) != 11 or not dig_cae or nro_cmp <= 0:
        return ""

    tipo_doc, nro_doc = _tipo_doc_receptor(cuit_cliente)
    payload = {
        "ver": 1,
        "fecha": fecha_iso,
        "cuit": int(dig_cuit),
        "ptoVta": int(punto_venta),
        "tipoCmp": int(tipo_cmp),
        "nroCmp": int(nro_cmp),
        "importe": round(float(importe), 2),
        "moneda": "PES",
        "ctz": 1,
        "tipoDocRec": int(tipo_doc),
        "nroDocRec": int(nro_doc),
        "tipoCodAut": "E",
        "codAut": int(dig_cae),
    }
    raw_json = json.dumps(payload, separators=(",", ":"), ensure_ascii=False)
    p_b64 = base64.b64encode(raw_json.encode("utf-8")).decode("ascii")
    url = f"https://www.arca.gob.ar/fe/qr/?p={p_b64}"

    try:
        import qrcode

        qr = qrcode.QRCode(
            version=None,
            error_correction=qrcode.constants.ERROR_CORRECT_M,
            box_size=4,
            border=1,
        )
        qr.add_data(url)
        qr.make(fit=True)
        img = qr.make_image(fill_color="black", back_color="white")
        buf = BytesIO()
        img.save(buf, format="PNG")
        b64 = base64.b64encode(buf.getvalue()).decode("ascii")
        return f"data:image/png;base64,{b64}"
    except Exception:
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
    """HTML térmico: logo HAFID, Arial negrita, total grande, QR ARCA, operario y obs."""
    cfg = dict(config or {})
    ctx = armar_contexto_comprobante(datos_respuesta, datos_cliente, cfg, forma_pago=forma_pago)
    emisor = ctx["emisor"]
    comp = ctx["comprobante"]
    cli = ctx["cliente"]
    cae = ctx["cae"]

    es_a = comp["tipo_letra"] == "A"
    tipo_cmp = 1 if es_a else 6
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

    ahora = ahora_ar()
    fecha_hora = ahora.strftime("%d/%m/%Y %H:%M")
    fecha_iso = ahora.strftime("%Y-%m-%d")
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
    bloque_obs = (
        f'<div class="bloque"><div class="obs"><strong>Obs.:</strong> {esc(obs)}</div></div>'
        if obs
        else ""
    )

    qr_uri = _qr_arca_data_uri(
        cuit_emisor=str(emisor.get("cuit") or ""),
        punto_venta=int(comp["punto_venta"]),
        tipo_cmp=tipo_cmp,
        nro_cmp=int(comp["numero"]),
        importe=total,
        cuit_cliente=str(cli.get("cuit") or ""),
        cae=str(cae.get("numero") or ""),
        fecha_iso=fecha_iso,
    )
    qr_html = (
        f'<div class="qr-wrap">'
        f'<img class="qr" src="{qr_uri}" alt="QR ARCA">'
        f'<div class="qr-cap">Escaneá para verificar en ARCA</div>'
        f"</div>"
        if qr_uri
        else ""
    )

    return f"""<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width={ancho_mm}mm, initial-scale=1">
<title>Ticket {esc(nro_fc)}</title>
<!-- ticket-design:{TICKET_DISENO_VERSION} -->
<style>
  @page {{
    size: {ancho_mm}mm auto;
    margin: 0;
  }}
  * {{ box-sizing: border-box; }}
  html, body {{
    margin: 0;
    padding: 0;
  }}
  body {{
    width: {ancho_mm}mm;
    max-width: {ancho_mm}mm;
    margin: 0;
    padding: 0;
    font-family: Arial, Helvetica, sans-serif;
    font-weight: 700;
    font-size: 12px;
    line-height: 1.2;
    color: #000;
    background: #fff;
    -webkit-print-color-adjust: exact;
    print-color-adjust: exact;
  }}
  .ticket {{
    width: 100%;
    border: none;
    padding: 0.4mm 0.8mm;
    background: #fff;
  }}
  .bloque {{
    border: 1.5px solid #000;
    padding: 1mm 1.2mm;
    margin: 0 0 1mm;
  }}
  .bloque:last-child {{
    margin-bottom: 0;
  }}
  .center {{ text-align: center; }}
  .logo-wrap {{
    text-align: center;
    margin: 0;
    line-height: 0;
  }}
  .logo {{
    max-width: 72mm;
    max-height: 26mm;
    width: auto;
    height: auto;
    object-fit: contain;
  }}
  h1 {{
    font-family: Arial, Helvetica, sans-serif;
    font-size: 14px;
    font-weight: 700;
    margin: 2px 0 0;
    text-align: center;
    letter-spacing: 0.3px;
    word-wrap: break-word;
    color: #000;
  }}
  .sub {{
    font-family: Arial, Helvetica, sans-serif;
    font-size: 11px;
    font-weight: 700;
    text-align: center;
    margin: 0;
    word-wrap: break-word;
    color: #000;
  }}
  .factura {{
    font-family: Arial, Helvetica, sans-serif;
    font-size: 13px;
    font-weight: 700;
    text-align: center;
    margin: 0;
    letter-spacing: 0.2px;
    color: #000;
  }}
  .cliente {{
    font-family: Arial, Helvetica, sans-serif;
    font-size: 12px;
    font-weight: 700;
    text-align: center;
    word-wrap: break-word;
    overflow-wrap: anywhere;
    color: #000;
  }}
  .sec-label {{
    font-size: 10px;
    letter-spacing: 1px;
    text-transform: uppercase;
    margin-bottom: 1px;
    text-align: center;
    color: #000;
  }}
  table.items {{
    width: 100%;
    border-collapse: collapse;
    margin: 0;
    font-size: 11px;
    font-family: Arial, Helvetica, sans-serif;
  }}
  table.items thead td {{
    border-bottom: 2px solid #000;
    padding: 0 0 2px;
    font-size: 10px;
    letter-spacing: 0.4px;
    text-transform: uppercase;
    color: #000;
  }}
  table.items tr.item td {{
    vertical-align: top;
    padding: 2px 0;
    font-weight: 700;
    border-bottom: 1.5px solid #000;
    color: #000;
  }}
  table.items tr.item:last-child td {{
    border-bottom: none;
    padding-bottom: 0;
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
    font-size: 10px;
    font-weight: 700;
    margin-top: 0;
  }}
  td.imp {{
    width: 18mm;
    text-align: right;
    white-space: nowrap;
  }}
  .row {{
    display: flex;
    justify-content: space-between;
    font-weight: 700;
    font-family: Arial, Helvetica, sans-serif;
    margin: 1px 0;
    font-size: 11px;
    color: #000;
  }}
  .total-box {{
    text-align: center;
    margin: 0;
    padding: 0;
  }}
  .total-label {{
    font-family: Arial, Helvetica, sans-serif;
    font-size: 13px;
    font-weight: 700;
    letter-spacing: 2px;
    color: #000;
  }}
  .total-monto {{
    font-family: Arial, Helvetica, sans-serif;
    font-size: 26px;
    font-weight: 700;
    margin-top: 1px;
    line-height: 1.05;
    color: #000;
  }}
  .cae {{
    text-align: center;
    font-size: 11px;
    font-weight: 700;
    font-family: Arial, Helvetica, sans-serif;
    word-wrap: break-word;
    color: #000;
  }}
  .obs {{
    font-family: Arial, Helvetica, sans-serif;
    font-size: 11px;
    font-weight: 700;
    text-align: left;
    word-wrap: break-word;
    overflow-wrap: anywhere;
    margin: 0;
    color: #000;
  }}
  .qr-wrap {{
    text-align: center;
    margin: 2px 0 0;
  }}
  .qr {{
    width: 28mm;
    height: 28mm;
    image-rendering: pixelated;
  }}
  .qr-cap {{
    font-size: 9px;
    font-weight: 700;
    margin-top: 1px;
    letter-spacing: 0.2px;
    color: #000;
  }}
  .pie {{
    text-align: center;
    font-size: 11px;
    font-weight: 700;
    font-family: Arial, Helvetica, sans-serif;
    margin-top: 2px;
    color: #000;
  }}
  .noprint {{
    margin: 10px 0 4px;
    text-align: center;
  }}
  .noprint button {{
    font-family: Arial, Helvetica, sans-serif;
    font-weight: 700;
    padding: 12px 20px;
    font-size: 15px;
    cursor: pointer;
    background: #000;
    color: #fff;
    border: 2px solid #000;
    border-radius: 6px;
    width: 95%;
  }}
  @media print {{
    .noprint {{ display: none !important; }}
    html, body {{
      width: {ancho_mm}mm !important;
      max-width: {ancho_mm}mm !important;
      margin: 0 !important;
      padding: 0 !important;
    }}
    .ticket {{
      padding: 0.2mm 0.5mm !important;
    }}
  }}
</style>
</head>
<body>
<div class="ticket">
  <div class="bloque">
    {logo_html}
    <h1>{esc(emisor.get("nombre_fantasia"))}</h1>
    <div class="sub">{esc(emisor.get("domicilio_comercial"))}</div>
    <div class="sub">CUIT: {esc(emisor.get("cuit"))}</div>
    <div class="sub">{esc(emisor.get("iibb"))}</div>
    <div class="sub">Inicio act.: {esc(emisor.get("inicio_actividades"))}</div>
    <div class="sub">{esc(emisor.get("condicion_iva"))}</div>
  </div>

  <div class="bloque">
    <div class="factura">FACTURA {comp["tipo_letra"]} Nº {esc(nro_fc)}</div>
    <div class="sub">{fecha_hora}</div>
    {linea_operario}
  </div>

  <div class="bloque">
    <div class="sec-label">Cliente</div>
    <div class="cliente">{esc(cli.get("nombre", "CONSUMIDOR FINAL"))}</div>
    <div class="sub">CUIT/DNI: {esc(cli.get("cuit", "00000000000"))}</div>
    {linea_cond}
  </div>

  <div class="bloque">
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
  </div>

  <div class="bloque">
    {desglose_html}
    <div class="total-box">
      <div class="total-label">TOTAL</div>
      <div class="total-monto">{total_txt}</div>
    </div>
    <div class="sub center">Pago: {esc(cli.get("condicion_venta", forma_pago))}</div>
  </div>

  {bloque_obs}

  <div class="bloque">
    <div class="cae">CAE: {esc(cae.get("numero"))}</div>
    <div class="cae">Vto CAE: {esc(cae.get("vencimiento"))}</div>
    {qr_html}
    <div class="pie">{leyenda}</div>
  </div>
</div>

  <div class="noprint">
    <button type="button" onclick="window.print()">🖨️ Imprimir ticket</button>
  </div>
</body>
</html>"""
