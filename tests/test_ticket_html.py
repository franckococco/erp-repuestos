"""Tests del ticket HTML térmico."""
from modulos.factura_arca_ticket_html import crear_ticket_html


def test_ticket_html_incluye_descripcion_completa():
    html = crear_ticket_html(
        {"punto_venta": 1, "numero_factura": 99, "cae": "123", "vencimiento_cae": "2026-12-31"},
        {"nombre": "JUAN PEREZ", "cuit": "20123456789", "cbte_tipo": "6"},
        [{
            "descripcion": "BIELETA SUSPENSION DELANTERA LARGA DESCRIPCION",
            "cantidad": 2,
            "precio": 5000.0,
        }],
        {"nombre_empresa": "HAFID REPUESTOS", "cuit_emisor": "20999999999"},
    )
    assert "BIELETA SUSPENSION DELANTERA LARGA DESCRIPCION" in html
    assert "FACTURA B" in html
    assert "font-weight: 700" in html or "font-weight:700" in html.replace(" ", "")
