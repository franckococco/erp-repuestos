"""Tests de búsqueda por código con/sin espacios."""
from modulos.util_busqueda import (
    _normalizar_codigo_busqueda,
    buscar_codigo_exacto_inventario,
    parece_codigo_producto,
)


def test_normalizar_codigo_quita_espacios():
    assert _normalizar_codigo_busqueda("026 121013 2") == "0261210132"
    assert _normalizar_codigo_busqueda("0261210132") == "0261210132"
    assert _normalizar_codigo_busqueda(" 026  121013  2-- ") == "0261210132--"


def test_buscar_codigo_sin_espacios_encuentra_con_espacios():
    items = [
        {
            "id": "026 121013 2_EXPOYER",
            "codigo": "026 121013 2",
            "id_maestro": "026 121013 2",
            "marca": "EXPOYER",
            "descripcion": "CONTRACUERPO",
        },
        {
            "id": "OTRO_GEN",
            "codigo": "999",
            "id_maestro": "999",
            "marca": "GEN",
            "descripcion": "OTRO",
        },
    ]
    hits = buscar_codigo_exacto_inventario(items, "0261210132")
    assert len(hits) == 1
    assert hits[0]["codigo"] == "026 121013 2"

    hits2 = buscar_codigo_exacto_inventario(items, "026 121013 2")
    assert len(hits2) == 1


def test_parece_codigo_con_espacios():
    assert parece_codigo_producto("0261210132") is True
    assert parece_codigo_producto("026 121013 2") is True
    assert parece_codigo_producto("026 121013 2--") is True
