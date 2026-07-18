"""Tests del ticket HTML térmico."""
from modulos.factura_arca_ticket_html import crear_ticket_html


def test_ticket_html_incluye_descripcion_completa():
    html = crear_ticket_html(
        {
            "punto_venta": 7,
            "numero_factura": 20,
            "cae": "71234567890123",
            "vencimiento_cae": "2026-12-31",
        },
        {"nombre": "JUAN PEREZ", "cuit": "20123456789", "cbte_tipo": "6"},
        [{
            "descripcion": "BIELETA SUSPENSION DELANTERA LARGA DESCRIPCION",
            "cantidad": 2,
            "precio": 5000.0,
        }],
        {"nombre_empresa": "HAFID REPUESTOS", "cuit_emisor": "20265010505"},
    )
    assert "BIELETA SUSPENSION DELANTERA LARGA DESCRIPCION" in html
    assert "FACTURA B" in html
    assert "font-weight: 700" in html or "font-weight:700" in html.replace(" ", "")
    assert "border-bottom: 2px solid #000" in html or 'class="ticket"' in html
    assert "qr-wrap" in html
    assert "Escaneá para verificar en ARCA" in html
    assert "data:image/png;base64," in html
    assert 'class="bloque"' in html
    assert 'class="ticket"' in html


def test_ticket_html_sin_cae_no_rompe():
    html = crear_ticket_html(
        {"punto_venta": 1, "numero_factura": 1, "cae": "", "vencimiento_cae": ""},
        {"nombre": "CF", "cuit": "00000000000", "cbte_tipo": "6"},
        [{"descripcion": "X", "cantidad": 1, "precio": 10.0}],
        {"nombre_empresa": "HAFID", "cuit_emisor": "20265010505"},
    )
    assert "FACTURA B" in html
    assert "TOTAL" in html
