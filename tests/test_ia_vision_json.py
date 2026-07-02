"""Tests del parser JSON tolerante de ia_vision."""
import json
import pytest

from modulos.ia_vision import (
    _extraer_json_respuesta,
    _normalizar_datos_documento,
    _reparar_json_simple,
)


def test_extraer_json_con_markdown():
    raw = '```json\n{"proveedor": "ACME", "cuit_proveedor": "20123456789"}\n```'
    data = _extraer_json_respuesta(raw)
    assert data["proveedor"] == "ACME"


def test_reparar_json_trailing_comma():
    raw = '{"proveedor": "ACME", "articulos": [],}'
    data = json.loads(_reparar_json_simple(raw))
    assert data["proveedor"] == "ACME"


def test_normalizar_factura_sanitiza_descripcion():
    data = _normalizar_datos_documento(
        {
            "proveedor": "Test",
            "cuit_proveedor": "20-12345678-9",
            "punto_venta": "1",
            "numero_comprobante": "99",
            "articulos": [
                {
                    "codigo": "123",
                    "descripcion": 'AMORT. "DELANTERO"\n',
                    "marca": "GEN",
                    "cantidad": "2",
                    "precio_unitario": "1.234,50",
                }
            ],
        },
        tipo="factura",
    )
    assert data["cuit_proveedor"] == "20123456789"
    assert data["articulos"][0]["cantidad"] == 2
    assert '"' not in data["articulos"][0]["descripcion"] or "DELANTERO" in data["articulos"][0]["descripcion"]
