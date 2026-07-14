"""Frases reales del mostrador — regresión de dictado por voz."""
import unittest

from modulos.mostrador_voz_flujo import (
    extraer_cliente_orden_voz,
    extraer_items_orden_voz,
    interpretar_orden_voz_mostrador,
)
from modulos.voz_lenguaje_natural import aplicar_lenguaje_natural_mostrador


class TestFrasesRealesMostrador(unittest.TestCase):
    """Órdenes como las dicta el vendedor en el mostrador."""

    def _prep(self, frase: str) -> str:
        return aplicar_lenguaje_natural_mostrador(frase)

    def test_presupuesto_pablo_bieleta_207(self):
        frase = self._prep(
            "eh bueno haceme un presupuesto para pablo de dos bieletas para el 207"
        )
        cli = extraer_cliente_orden_voz(frase)
        self.assertEqual(cli.get("nombre_cliente"), "PABLO")
        items = extraer_items_orden_voz(frase)
        self.assertTrue(any("BIELETA" in i["termino"].upper() for i in items))

    def test_factura_cuenta_corriente_codigo(self):
        frase = self._prep("factura a para taller san martin codigo 111 una unidad")
        interp = interpretar_orden_voz_mostrador(frase)
        self.assertEqual(interp.get("intent"), "factura_a")
        self.assertIn("SAN MARTIN", interp["cliente"].get("nombre_cliente", ""))

    def test_muletillas_y_cantidad(self):
        frase = self._prep("dale meteme tipo tres bujes guia para el gol")
        items = extraer_items_orden_voz(frase)
        bujes = [i for i in items if "BUJE" in i["termino"].upper()]
        self.assertTrue(bujes)
        self.assertEqual(bujes[0]["cantidad"], 3)

    def test_listo_al_final(self):
        raw = "presupuesto para julio bieleta suspension 207 2 unidades listo"
        interp = interpretar_orden_voz_mostrador(raw)
        self.assertTrue(interp.get("listo"))
        self.assertEqual(interp["cliente"].get("nombre_cliente"), "JULIO")

    def test_ademas_separador_multiple(self):
        frase = self._prep(
            "codigo 222 2 unidades ademas pastillas delanteras gol 1 y buje 1"
        )
        items = extraer_items_orden_voz(frase)
        self.assertGreaterEqual(len(items), 2)

    def test_consumidor_final_factura_b(self):
        frase = self._prep("factura b consumidor final amortiguador 1")
        cli = extraer_cliente_orden_voz(frase)
        self.assertTrue(cli.get("consumidor_final"))

    def test_factura_franco_cocco_biela_arranque_gol_trend(self):
        frase = (
            "haceme una factura para franco cocco de una biela y un arranque de gol trend"
        )
        cli = extraer_cliente_orden_voz(frase)
        self.assertEqual(cli.get("nombre_cliente"), "FRANCO COCCO")
        items = extraer_items_orden_voz(frase)
        terminos = {i["termino"].upper() for i in items}
        self.assertIn("BIELA", terminos)
        self.assertIn("ARRANQUE", terminos)
        self.assertEqual(len(items), 2)
        for it in items:
            self.assertEqual(it.get("vehiculo"), "gol trend")

    def test_factura_codigo_y_rotula_no_pierde_111(self):
        frase = (
            "haceme una factura para juan picazo codigo 111 2 unidades "
            "y una rotula para el 207 2 unidades"
        )
        cli = extraer_cliente_orden_voz(frase)
        self.assertEqual(cli.get("nombre_cliente"), "JUAN PICAZO")
        items = extraer_items_orden_voz(frase)
        terminos = {str(i["termino"]).upper() for i in items}
        self.assertIn("111", terminos)
        self.assertTrue(any("ROTULA" in t for t in terminos))
        self.assertEqual(len(items), 2)
        cod = next(i for i in items if str(i["termino"]) == "111")
        self.assertEqual(cod["cantidad"], 2)


if __name__ == "__main__":
    unittest.main()
