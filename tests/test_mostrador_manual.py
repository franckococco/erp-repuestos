"""Tests de líneas manuales y filtro estricto de vehículo."""
from modulos.db_firebase import es_linea_manual_carrito
from modulos.util_busqueda import buscar_en_inventario_con_vehiculo, item_coincide_vehiculo


def test_es_linea_manual_carrito():
    assert es_linea_manual_carrito("MANUAL_123")
    assert not es_linea_manual_carrito("111_GENERICO")


def test_filtro_vehiculo_estricto():
    items = [
        {"codigo": "1", "descripcion": "BIELETA", "vehiculo": "PEUGEOT 207", "vehiculos": ["PEUGEOT"]},
        {"codigo": "2", "descripcion": "BIELETA", "vehiculo": "UNIVERSAL", "vehiculos": ["UNIVERSAL"]},
    ]
    todos = buscar_en_inventario_con_vehiculo(items, "bieleta", "207", filtro_vehiculo_estricto=False)
    assert len(todos) == 2
    estricto = buscar_en_inventario_con_vehiculo(items, "bieleta", "207", filtro_vehiculo_estricto=True)
    assert len(estricto) == 1
    assert item_coincide_vehiculo(estricto[0], "207")
