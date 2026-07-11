"""Tests de búsqueda repuesto + vehículo."""
import unittest

from modulos.util_busqueda import (
    buscar_en_inventario_con_vehiculo,
    buscar_en_inventario_mostrador,
    item_coincide_vehiculo,
)
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
    {
        "id": "D1",
        "codigo": "4246W70-H",
        "descripcion": "DISCO FRENO DEL Ø266mm 207-307-",
        "vehiculo": "PEUGEOT",
    },
    {
        "id": "B3",
        "codigo": "411314",
        "descripcion": "BIELETA BARRA ESTABILIZADORA VW POINTER",
        "vehiculo": "VOLKSWAGEN",
    },
    {
        "id": "B4",
        "codigo": "BUJE-B",
        "descripcion": "BUJE BIELETA BARRA ESTAB VW",
        "vehiculo": "UNIVERSAL",
    },
    {
        "id": "G1",
        "codigo": "7212SQ0-O",
        "descripcion": "GANCHO DE REMOLQUE ORIGINA 207-",
        "vehiculo": "PEUGEOT",
    },
]


class TestBusquedaVehiculo(unittest.TestCase):
    def test_207_no_en_medida_ruleman(self):
        item = INVENTARIO_FAKE[0]
        self.assertFalse(item_coincide_vehiculo(item, "207"))

    def test_207_en_bieleta_peugeot(self):
        item = INVENTARIO_FAKE[1]
        self.assertTrue(item_coincide_vehiculo(item, "207"))

    def test_biela_no_se_confunde_con_bieleta(self):
        """biela (motor) ya no se corrige a bieleta (suspensión)."""
        self.assertEqual(corregir_termino_repuesto("biela"), "biela")
        res = buscar_en_inventario_con_vehiculo(
            INVENTARIO_FAKE,
            corregir_termino_repuesto("biela"),
            "207",
        )
        self.assertEqual(res, [])

    def test_bieleta_para_207_prioriza_207(self):
        res = buscar_en_inventario_con_vehiculo(
            INVENTARIO_FAKE,
            corregir_termino_repuesto("bieleta"),
            "207",
        )
        ids = [r["id"] for r in res]
        self.assertIn("B1_GEN", ids)
        self.assertNotIn("R1_SKF", ids)

    def test_sin_vehiculo_no_filtra_por_207(self):
        res = buscar_en_inventario_con_vehiculo(INVENTARIO_FAKE, "ruleman", None)
        self.assertTrue(any(r["id"] == "R1_SKF" for r in res))

    def test_gancho_207_no_es_modelo(self):
        self.assertFalse(item_coincide_vehiculo(INVENTARIO_FAKE[5], "207"))

    def test_bieleta_suspension_207_mostrador(self):
        res = buscar_en_inventario_mostrador(INVENTARIO_FAKE, "bieleta de suspension 207")
        ids = [r["id"] for r in res]
        self.assertIn("B1_GEN", ids)
        self.assertIn("B2_GEN", ids)
        self.assertIn("B3", ids)
        self.assertIn("B4", ids)
        self.assertNotIn("D1", ids)
        self.assertNotIn("G1", ids)
        self.assertNotIn("R1_SKF", ids)
        self.assertEqual(ids[0], "B1_GEN")

    def test_bieleta_suspension_sin_vehiculo(self):
        res = buscar_en_inventario_mostrador(INVENTARIO_FAKE, "bieleta de suspension")
        ids = [r["id"] for r in res]
        self.assertIn("B3", ids)
        self.assertIn("B4", ids)
        self.assertNotIn("R1_SKF", ids)


if __name__ == "__main__":
    unittest.main()
