"""Tests de lenguaje natural y parsers del asistente de depósito."""
import unittest

from modulos.ia_asistente import (
    normalizar_orden_voz_deposito,
    parse_alta_baja_rapido,
    parse_buscar_rapido,
    parse_proveedor_rapido,
    parse_reporte_rapido,
    parse_ubicacion_rapido,
    procesar_orden_voz,
)
from modulos.voz_lenguaje_natural import aplicar_lenguaje_natural_deposito


class TestLenguajeNaturalDeposito(unittest.TestCase):
    def test_quita_muletillas_buscar(self):
        norm = aplicar_lenguaje_natural_deposito(
            "che fijate si tenés buje de directa para el gol"
        )
        self.assertIn("buscar", norm)
        self.assertIn("buje de directa", norm)
        self.assertNotIn("che", norm.split())

    def test_preserva_buje_de_directa(self):
        norm = aplicar_lenguaje_natural_deposito("buje de directa para el gol")
        self.assertIn("de directa", norm)

    def test_tres_unidades_a_numero(self):
        norm = aplicar_lenguaje_natural_deposito("sumame tres al codigo 1491")
        self.assertIn("3", norm)
        self.assertIn("1491", norm)


class TestParsersLocalesAsistente(unittest.TestCase):
    def _prep(self, frase: str) -> str:
        return normalizar_orden_voz_deposito(frase)

    def test_buscar_buje_gol(self):
        r = parse_buscar_rapido(self._prep("fijate si tenés buje de directa para el gol"))
        self.assertIsNotNone(r)
        self.assertEqual(r["accion"], "buscar")
        self.assertIn("buje", r["termino"].lower())

    def test_alta_tres_1491(self):
        r = parse_alta_baja_rapido(self._prep("sumame tres al codigo 1491"))
        self.assertIsNotNone(r)
        self.assertEqual(r["accion"], "alta")
        self.assertEqual(r["termino"], "1491")
        self.assertEqual(r["cantidad"], 3)

    def test_carga_producto_con_ubicacion(self):
        frase = self._prep(
            "cargame el codigo 25412 buje amortiguador para el gol "
            "5 unidades pasillo 2 piso 1 modulo 3 fila 4"
        )
        from modulos.ia_asistente import _es_carga_producto_nuevo

        self.assertIn("cargar", frase)
        self.assertTrue(_es_carga_producto_nuevo(frase))
        self.assertIn("25412", frase)
        self.assertIn("pasillo 2", frase)

    def test_ubicacion_1491(self):
        r = parse_ubicacion_rapido(self._prep("el 1491 va en pasillo 2 piso 1 modulo 3"))
        self.assertIsNotNone(r)
        self.assertEqual(r["accion"], "actualizar_ubicacion")
        self.assertEqual(r["termino"], "1491")
        self.assertEqual(r["pasillo"], 2)
        self.assertEqual(r["piso"], 1)

    def test_reporte_menos_de_tres(self):
        r = parse_reporte_rapido(self._prep("mostrame los que tienen menos de 3 unidades"))
        self.assertIsNotNone(r)
        self.assertEqual(r["accion"], "reporte_stock")
        self.assertEqual(r["operador"], "menor_o_igual")
        self.assertEqual(r["cantidad"], 3)

    def test_proveedor_expoyer(self):
        r = parse_proveedor_rapido(self._prep("mostrame lo de expoyer"))
        self.assertIsNotNone(r)
        self.assertEqual(r["accion"], "filtrar_proveedor")
        self.assertIn("expoyer", r["proveedor"].lower())


class TestProcesarOrdenVoz(unittest.TestCase):
    def test_buscar_sin_groq(self):
        r = procesar_orden_voz("che fijate buje de directa gol")
        if r.get("accion") == "error" and "GROQ" in str(r.get("respuesta", "")):
            self.skipTest("sin GROQ_API_KEY")
        self.assertEqual(r.get("accion"), "buscar")
        self.assertIn("buje", r.get("termino", "").lower())


if __name__ == "__main__":
    unittest.main()
