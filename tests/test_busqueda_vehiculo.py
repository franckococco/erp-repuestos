"""Tests de búsqueda repuesto + vehículo."""
import unittest

from modulos.util_busqueda import buscar_en_inventario_con_vehiculo, item_coincide_vehiculo
from modulos.voz_repuestos import corregir_termino_repuesto


INVENTARIO_FAKE = [
    {
        "id": "R1_SKF",
        "codigo": "3247090",
        "descripcion": "RULEMAN SEMIEJE 6006 206-207-20 (SKF)",
        "vehiculo": "UNIVERSAL",
    },
    {
        "id": "B1_GEN",
        "codigo": "B207",
        "descripcion": "BIELETA PEUGEOT 207 DELANTERA",
        "vehiculo": "PEUGEOT",
        "vehiculos_busqueda": "PEUGEOT",
    },
    {
        "id": "B2_GEN",
        "codigo": "B111",
        "descripcion": "BIELETA GENERICA",
        "vehiculo": "UNIVERSAL",
    },
]


class TestBusquedaVehiculo(unittest.TestCase):
    def test_207_no_en_medida_ruleman(self):
        item = INVENTARIO_FAKE[0]
        self.assertFalse(item_coincide_vehiculo(item, "207"))

    def test_207_en_bieleta_peugeot(self):
        item = INVENTARIO_FAKE[1]
        self.assertTrue(item_coincide_vehiculo(item, "207"))

    def test_biela_para_207_solo_bieletas_207(self):
        res = buscar_en_inventario_con_vehiculo(
            INVENTARIO_FAKE,
            corregir_termino_repuesto("biela"),
            "207",
        )
        ids = [r["id"] for r in res]
        self.assertIn("B1_GEN", ids)
        self.assertNotIn("R1_SKF", ids)

    def test_sin_vehiculo_no_filtra_por_207(self):
        res = buscar_en_inventario_con_vehiculo(INVENTARIO_FAKE, "ruleman", None)
        self.assertTrue(any(r["id"] == "R1_SKF" for r in res))


if __name__ == "__main__":
    unittest.main()
