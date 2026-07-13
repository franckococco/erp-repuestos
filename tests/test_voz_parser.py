"""Tests del parser de órdenes de voz del mostrador."""
import unittest

from modulos.mostrador_voz_flujo import (
    extraer_cliente_orden_voz,
    extraer_items_orden_voz,
    interpretar_orden_voz_mostrador,
    normalizar_orden_voz_mostrador,
)
from modulos.voz_repuestos import corregir_termino_repuesto


class TestVozParser(unittest.TestCase):
    def test_sinonimo_bielete(self):
        self.assertEqual(corregir_termino_repuesto("bielete"), "bieleta")
        self.assertEqual(corregir_termino_repuesto("biela"), "biela")
        self.assertEqual(corregir_termino_repuesto("ferodo"), "pastilla")
        self.assertEqual(corregir_termino_repuesto("amorti"), "amortiguador")
        self.assertEqual(corregir_termino_repuesto("ruliman"), "ruleman")
        self.assertEqual(corregir_termino_repuesto("homo"), "homocinetica")

    def test_cotizacion_como_presupuesto(self):
        t = "cotizame para lucas dos amortiguadores para el gol 2 unidades"
        interp = interpretar_orden_voz_mostrador(t)
        self.assertEqual(interp["intent"], "presupuesto")
        self.assertEqual(interp["cliente"].get("nombre_cliente"), "LUCAS")
        amortis = [i for i in interp["items"] if "AMORTIGUADOR" in i["termino"]]
        self.assertTrue(amortis)
        self.assertEqual(amortis[0]["cantidad"], 2)

    def test_meteme_codigo_jerga(self):
        items = extraer_items_orden_voz("meteme codi 222 3 unidades")
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["termino"], "222")
        self.assertEqual(items[0]["cantidad"], 3)

    def test_factura_abierta(self):
        interp = interpretar_orden_voz_mostrador(
            "factura abierta para taller san martin codigo 111 1"
        )
        self.assertEqual(interp["intent"], "factura_a")

    def test_treinta_y_dos_unidades(self):
        norm = normalizar_orden_voz_mostrador("pastilla treinta y dos unidades")
        self.assertIn("32 unidades", norm)

    def test_ademas_separador(self):
        items = extraer_items_orden_voz(
            "bieleta 2 unidades ademas codigo 111 1 para el 207"
        )
        self.assertGreaterEqual(len(items), 2)

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

    def test_presupuesto_para_pedro_bieleta_207(self):
        t = "haceme un presupuesto para pedro de una bieleta para el 207 2 unidades"
        self.assertEqual(extraer_cliente_orden_voz(t).get("nombre_cliente"), "PEDRO")
        items = extraer_items_orden_voz(t)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["termino"], "BIELETA")
        self.assertEqual(items[0].get("vehiculo"), "207")
        self.assertNotIn("PEDRO", items[0]["termino"])

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

    def test_normalizar_necesito_presupuesto(self):
        t = "necesito un presupuesto para maria de una bieleta para el 207 2 unidades"
        norm = normalizar_orden_voz_mostrador(t)
        self.assertIn("presupuesto", norm)
        self.assertNotIn("necesito", norm)
        self.assertEqual(extraer_cliente_orden_voz(t).get("nombre_cliente"), "MARIA")
        items = extraer_items_orden_voz(t)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["termino"], "BIELETA")

    def test_normalizar_a_nombre_de(self):
        t = "factura be a nombre de carlos codigo 111 2"
        interp = interpretar_orden_voz_mostrador(t)
        self.assertEqual(interp["cliente"].get("nombre_cliente"), "CARLOS")
        self.assertEqual(interp["intent"], "factura_b")
        self.assertEqual(len(interp["items"]), 1)

    def test_cantidad_en_palabras(self):
        t = "dame dos bujes para el gol 1 unidad"
        items = extraer_items_orden_voz(t)
        self.assertGreaterEqual(len(items), 1)
        bujes = [i for i in items if "BUJE" in i["termino"]]
        self.assertTrue(bujes)
        self.assertEqual(bujes[0]["cantidad"], 2)

    def test_interpretar_resumen(self):
        t = "haceme un presupuesto para pedro de una bieleta para el 207 2 unidades"
        interp = interpretar_orden_voz_mostrador(t)
        self.assertIn("PEDRO", interp["resumen"])
        self.assertIn("BIELETA", interp["resumen"])
        self.assertEqual(interp["intent"], "presupuesto")

    def test_cliente_nombre_completo_tres_palabras(self):
        t = (
            "haceme un presupuesto para carlos alberto poccia de 2 bieletas "
            "de suspension 207"
        )
        cli = extraer_cliente_orden_voz(t)
        self.assertEqual(cli.get("nombre_cliente"), "CARLOS ALBERTO POCCIA")
        items = extraer_items_orden_voz(t)
        self.assertGreaterEqual(len(items), 1)
        bieletas = [i for i in items if "BIELETA" in i.get("termino", "")]
        self.assertTrue(bieletas)
        self.assertNotIn("POCCIA", bieletas[0]["termino"])
        self.assertNotIn("ALBERTO", bieletas[0]["termino"])

    def test_mismo_pedido_orden_invertido(self):
        a = (
            "presupuesto para carlos alberto poccia de 2 bieletas de suspension 207"
        )
        b = (
            "carlos alberto poccia presupuesto 2 bieletas suspension para el 207"
        )
        ca = extraer_cliente_orden_voz(a).get("nombre_cliente")
        cb = extraer_cliente_orden_voz(b).get("nombre_cliente")
        self.assertEqual(ca, "CARLOS ALBERTO POCCIA")
        self.assertEqual(cb, "CARLOS ALBERTO POCCIA")
        ta = {i["termino"] for i in extraer_items_orden_voz(a)}
        tb = {i["termino"] for i in extraer_items_orden_voz(b)}
        self.assertTrue(any("BIELETA" in x for x in ta))
        self.assertTrue(any("BIELETA" in x for x in tb))


    def test_cliente_para_el_cliente_presupuesto_codigo(self):
        t = "para el cliente pablo castellanos haceme un presupuesto del codigo 111 3 unidades"
        self.assertEqual(
            extraer_cliente_orden_voz(t).get("nombre_cliente"),
            "PABLO CASTELLANOS",
        )
        items = extraer_items_orden_voz(t)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["termino"], "111")
        self.assertEqual(items[0]["cantidad"], 3)
        interp = interpretar_orden_voz_mostrador(t)
        self.assertIn("PABLO CASTELLANOS", interp["resumen"])
        self.assertNotIn("CASTELLANOS 111", interp["resumen"])

    def test_juan_guzman_bieleta_sin_unidades(self):
        t = "haceme un presupuesto para juan guzman bieleta de suspension 207"
        self.assertEqual(
            extraer_cliente_orden_voz(t).get("nombre_cliente"),
            "JUAN GUZMAN",
        )
        items = extraer_items_orden_voz(t)
        self.assertEqual(len(items), 1)
        self.assertIn("BIELETA", items[0]["termino"])
        self.assertEqual(items[0]["cantidad"], 1)
        self.assertEqual(items[0].get("vehiculo"), "207")

    def test_juan_guzman_de_bieleta_tres_unidades(self):
        t = "haceme un presupuesto para juan guzamn de bieleta de suspension 3 unidades"
        self.assertEqual(
            extraer_cliente_orden_voz(t).get("nombre_cliente"),
            "JUAN GUZAMN",
        )
        items = extraer_items_orden_voz(t)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["termino"], "BIELETA SUSPENSION")
        self.assertEqual(items[0]["cantidad"], 3)
        self.assertNotIn("GUZAMN", items[0]["termino"])


    def test_jorge_real_factura_codigo_y_bieleta(self):
        t = (
            "quiero una factura para jorge real codigo 111 3 unidades "
            "y una bieleta de suspension 3 unidades"
        )
        self.assertEqual(
            extraer_cliente_orden_voz(t).get("nombre_cliente"),
            "JORGE REAL",
        )
        items = extraer_items_orden_voz(t)
        self.assertEqual(len(items), 2)
        por_term = {i["termino"]: i for i in items}
        self.assertEqual(por_term["111"]["cantidad"], 3)
        self.assertEqual(por_term["111"].get("modo"), "codigo")
        bieletas = [i for i in items if "BIELETA" in i["termino"]]
        self.assertEqual(len(bieletas), 1)
        self.assertEqual(bieletas[0]["cantidad"], 3)
        self.assertEqual(bieletas[0].get("modo"), "descripcion")
        terminos = [i["termino"] for i in items]
        self.assertNotIn("A 111", terminos)
        self.assertNotIn("JORGE REAL", " ".join(terminos))


if __name__ == "__main__":
    unittest.main()
