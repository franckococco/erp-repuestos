"""Tests de carga de producto por voz."""
import unittest
from unittest.mock import patch

from modulos.carga_producto_voz import validar_y_preparar_carga_producto_voz
from modulos.normalizar_carga_producto import normalizar_orden_cargar_producto


class TestCargaProductoVoz(unittest.TestCase):
    def test_cantidad_sin_unidades(self):
        orden = (
            "Cargame el codigo 032115251d chupador bomba aceite marca accesorio argentino "
            "cantidad 4 vehiculo Volkswagen piso cero pasillo 1 modulo 1 fila 5"
        )
        datos = normalizar_orden_cargar_producto(
            {"accion": "cargar_producto", "codigo": "032115251D", "stock": 1},
            texto_original=orden,
        )
        self.assertEqual(datos.get("stock"), 4)
        self.assertNotIn("fondo", datos)
        self.assertEqual(datos.get("piso"), 0)
        self.assertEqual(datos.get("pasillo"), 1)

    @patch("modulos.carga_producto_voz.obtener_producto_por_codigo", return_value=None)
    def test_resumen_sin_fondo_inventado(self, _mock_db):
        orden = (
            "cargame codigo 111 filtro aceite cantidad 2 pasillo 1 piso 0 modulo 1 fila 3"
        )
        ok, payload, msg = validar_y_preparar_carga_producto_voz(
            {"accion": "cargar_producto", "codigo": "111", "descripcion": "FILTRO ACEITE"},
            texto_original=orden,
        )
        self.assertTrue(ok)
        self.assertEqual(payload.get("stock"), 2)
        self.assertNotIn("fondo", payload)
        self.assertNotIn("Fondo", msg)


if __name__ == "__main__":
    unittest.main()
