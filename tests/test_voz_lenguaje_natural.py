"""Tests de la capa de lenguaje natural del mostrador."""
import unittest

from modulos.voz_lenguaje_natural import (
    aplicar_lenguaje_natural_mostrador,
    es_calificador_producto,
    segmentar_orden_natural,
)
from modulos.mostrador_voz_flujo import interpretar_orden_voz_mostrador


class TestLenguajeNatural(unittest.TestCase):
    def test_quita_muletillas_che_bueno_dale(self):
        t = "che bueno dale haceme un presupuesto para juan"
        norm = aplicar_lenguaje_natural_mostrador(t)
        self.assertIn("presupuesto", norm)
        self.assertIn("para juan", norm)
        self.assertNotIn("che", norm.split())
        self.assertNotIn("bueno", norm.split())

    def test_preserva_bieleta_de_suspension(self):
        t = "bieleta de suspension para el 207"
        norm = aplicar_lenguaje_natural_mostrador(t)
        self.assertIn("de suspension", norm)

    def test_cotizame_a_presupuesto(self):
        norm = aplicar_lenguaje_natural_mostrador("cotizame para lucas dos amortiguadores")
        self.assertIn("presupuesto", norm)
        self.assertIn("amortiguador", norm)

    def test_necesito_quiero_dame(self):
        for muletilla in ("necesito", "quiero", "dame", "fijate si tenes"):
            norm = aplicar_lenguaje_natural_mostrador(
                f"{muletilla} presupuesto para maria codigo 111 2 unidades"
            )
            self.assertIn("presupuesto", norm)
            self.assertNotIn(muletilla.replace(" ", ""), norm.replace(" ", ""))

    def test_treinta_y_dos_unidades(self):
        norm = aplicar_lenguaje_natural_mostrador("pastilla treinta y dos unidades")
        self.assertIn("32 unidades", norm)

    def test_ademas_como_separador(self):
        norm = aplicar_lenguaje_natural_mostrador(
            "bieleta 2 unidades ademas codigo 111 1"
        )
        self.assertIn(" y ", norm)

    def test_contado_normalizado(self):
        norm = aplicar_lenguaje_natural_mostrador("factura b para pedro contado")
        self.assertIn("contado", norm)

    def test_es_calificador_producto(self):
        self.assertTrue(es_calificador_producto("suspension"))
        self.assertTrue(es_calificador_producto("directa"))
        self.assertFalse(es_calificador_producto("juan"))

    def test_segmentar_juan_guzman_bieleta_207(self):
        t = "che bueno haceme un presupuesto para juan guzman bieleta de suspension 207"
        seg = segmentar_orden_natural(t)
        self.assertEqual(seg["cliente"].get("nombre_cliente"), "JUAN GUZMAN")
        self.assertGreaterEqual(len(seg["items"]), 1)
        self.assertIn("BIELETA", seg["items"][0]["termino"])

    def test_segmentar_pablo_castellanos_codigo(self):
        t = "para el cliente pablo castellanos haceme un presupuesto del codigo 111 3 unidades"
        seg = segmentar_orden_natural(t)
        self.assertEqual(seg["cliente"].get("nombre_cliente"), "PABLO CASTELLANOS")
        self.assertEqual(len(seg["items"]), 1)
        self.assertEqual(seg["items"][0]["termino"], "111")

    def test_orden_invertida_mismo_cliente(self):
        a = "presupuesto para carlos alberto poccia de 2 bieletas de suspension 207"
        b = "carlos alberto poccia necesito presupuesto 2 bieletas suspension para el 207"
        ca = segmentar_orden_natural(a)["cliente"].get("nombre_cliente")
        cb = segmentar_orden_natural(b)["cliente"].get("nombre_cliente")
        self.assertEqual(ca, "CARLOS ALBERTO POCCIA")
        self.assertEqual(cb, "CARLOS ALBERTO POCCIA")

    def test_interpretar_con_muletillas(self):
        t = (
            "por favor che bueno dale haceme un presupuesto para "
            "juan guzamn de bieleta de suspension 3 unidades"
        )
        interp = interpretar_orden_voz_mostrador(t)
        self.assertEqual(interp["cliente"].get("nombre_cliente"), "JUAN GUZAMN")
        self.assertIn("BIELETA", interp["items"][0]["termino"])
        self.assertNotIn("GUZAMN", interp["items"][0]["termino"])


if __name__ == "__main__":
    unittest.main()
