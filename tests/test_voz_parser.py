"""Tests del parser de órdenes de voz del mostrador."""
import unittest

from modulos.mostrador_voz_flujo import extraer_cliente_orden_voz, extraer_items_orden_voz
from modulos.voz_repuestos import corregir_termino_repuesto


class TestVozParser(unittest.TestCase):
    def test_sinonimo_bielete(self):
        self.assertEqual(corregir_termino_repuesto("bielete"), "bieleta")

    def test_cliente_al_final_presupuesto(self):
        r = extraer_cliente_orden_voz(
            "207 bielete 2 codigo 111 1 presupuesto julio"
        )
        self.assertEqual(r.get("nombre_cliente"), "JULIO")

    def test_cliente_al_inicio_presupuesto(self):
        r = extraer_cliente_orden_voz(
            "presupuesto pablo bielete para el 207 2 unidades codigo 111 1"
        )
        self.assertEqual(r.get("nombre_cliente"), "PABLO")

    def test_multiples_items(self):
        items = extraer_items_orden_voz(
            "codigo 111 1 buje guia 2 cazoletas ford 2"
        )
        self.assertEqual(len(items), 3)
        terminos = [i["termino"] for i in items]
        self.assertIn("111", terminos)
        self.assertIn("BUJE GUIA", terminos)
        self.assertIn("CAZOLETAS FORD", terminos)

    def test_cliente_para_al_final(self):
        r = extraer_cliente_orden_voz("bielete 2 codigo 111 1 para julio")
        self.assertEqual(r.get("nombre_cliente"), "JULIO")

    def test_bielete_con_codigo_y_vehiculo(self):
        items = extraer_items_orden_voz(
            "presupuesto pablo bielete para el 207 2 codigo 111 1"
        )
        self.assertEqual(len(items), 2)
        por_term = {i["termino"]: i for i in items}
        self.assertEqual(por_term["BIELETA"]["cantidad"], 2)
        self.assertEqual(por_term["BIELETA"].get("vehiculo"), "207")
        self.assertEqual(por_term["111"]["cantidad"], 1)
        self.assertNotIn("vehiculo", por_term["111"])

    def test_bielete_para_gol(self):
        items = extraer_items_orden_voz("amortiguador para el gol 2 unidades")
        self.assertEqual(len(items), 1)
        self.assertIn("vehiculo", items[0])
        self.assertIn("gol", str(items[0].get("vehiculo", "")).lower())
        items = extraer_items_orden_voz(
            "presupuesto pablo bielete para el 207 2 codigo 111 1"
        )
        self.assertEqual(len(items), 2)
        por_term = {i["termino"]: i for i in items}
        self.assertEqual(por_term["BIELETA"]["cantidad"], 2)
        self.assertEqual(por_term["BIELETA"].get("vehiculo"), "207")
        self.assertEqual(por_term["111"]["cantidad"], 1)
        self.assertNotIn("vehiculo", por_term["111"])
        items = extraer_items_orden_voz("bielete para el 207 2 unidades")
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].get("cantidad"), 2)
        self.assertIn("vehiculo", items[0])


if __name__ == "__main__":
    unittest.main()
