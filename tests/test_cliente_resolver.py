"""Tests de resolución inteligente de clientes (fonética + Firebase)."""
import unittest
from unittest.mock import patch

from modulos.cliente_resolver import (
    corregir_nombre_con_clientes,
    fold_fonetico_es,
    resolver_cliente_por_nombre,
    sugerencias_clientes,
    _score_cliente,
)


_CLIENTES_FAKE = {
    "20123456789": {
        "nombre": "JUAN GUZMÁN",
        "cuit_dni": "20123456789",
        "tipo_cliente": "mecanico",
        "descuento": 10.0,
        "tipo_comprobante": "6",
    },
    "20987654321": {
        "nombre": "CARLOS ALBERTO POCCIA",
        "cuit_dni": "20987654321",
        "tipo_cliente": "cuenta_corriente",
        "descuento": 5.0,
        "tipo_comprobante": "1",
    },
    "20333444555": {
        "nombre": "PABLO CASTELLANOS",
        "cuit_dni": "20333444555",
        "tipo_cliente": "mecanico",
        "descuento": 0.0,
        "tipo_comprobante": "6",
    },
}


class TestClienteResolver(unittest.TestCase):
    def test_fold_fonetico_guzman(self):
        self.assertGreater(
            _score_cliente("JUAN GUZAMN", "JUAN GUZMÁN"),
            0.75,
        )
        self.assertEqual(fold_fonetico_es("Castellanos"), fold_fonetico_es("castellanos"))

    def test_score_prefijo(self):
        self.assertGreater(_score_cliente("PABLO", "PABLO CASTELLANOS"), 0.9)

    def test_resolver_guzamn_fonetico(self):
        cli, score, metodo = resolver_cliente_por_nombre("JUAN GUZAMN", _CLIENTES_FAKE)
        self.assertIsNotNone(cli)
        self.assertGreaterEqual(score, 0.68)
        self.assertEqual(cli["nombre"], "JUAN GUZMÁN")

    def test_resolver_pablo_castellanos(self):
        cli, score, _ = resolver_cliente_por_nombre("pablo castellanos", _CLIENTES_FAKE)
        self.assertIsNotNone(cli)
        self.assertGreater(score, 0.9)
        self.assertEqual(cli["nombre"], "PABLO CASTELLANOS")

    def test_corregir_nombre_con_clientes(self):
        with patch("modulos.cliente_resolver.clientes_cache_mostrador", return_value=_CLIENTES_FAKE):
            self.assertEqual(
                corregir_nombre_con_clientes("carlos alberto poccia"),
                "CARLOS ALBERTO POCCIA",
            )

    def test_sugerencias_ordena_por_score(self):
        with patch("modulos.cliente_resolver.clientes_cache_mostrador", return_value=_CLIENTES_FAKE):
            sugs = sugerencias_clientes("pablo")
        self.assertTrue(sugs)
        self.assertIn("PABLO", sugs[0][0])

    def test_no_confundir_juan_pablo_con_juan_de_los_palotes(self):
        db = {
            "1": {"nombre": "JUAN DE LOS PALOTES", "tipo_cliente": "mecanico"},
            "2": {"nombre": "JUAN PEREZ", "tipo_cliente": "ocasional"},
        }
        cli, score, _ = resolver_cliente_por_nombre("JUAN PABLO CUZZO", db)
        self.assertIsNone(cli)
        self.assertLess(score, 0.68)
        self.assertEqual(corregir_nombre_con_clientes("JUAN PABLO CUZZO", db), "JUAN PABLO CUZZO")


if __name__ == "__main__":
    unittest.main()
