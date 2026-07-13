"""Tests del intérprete inteligente (normalización rígida, sin llamar a Groq)."""
import unittest

from modulos.orden_mostrador_inteligente import (
    normalizar_accion_mostrador,
    orden_compuesta_requiere_groq,
)
from modulos.ia_mostrador import parse_flujo_rapido_voz


class TestOrdenMostradorInteligente(unittest.TestCase):
    def test_orden_compuesta_detecta_presupuesto_largo(self):
        t = "presupuesto para carlos alberto poccia de 2 bieletas suspension 207"
        self.assertTrue(orden_compuesta_requiere_groq(t))

    def test_comando_corto_no_usa_groq_primero(self):
        self.assertFalse(orden_compuesta_requiere_groq("listo"))
        self.assertFalse(orden_compuesta_requiere_groq("111 2"))

    def test_normalizar_flujo_nombre_completo(self):
        raw = parse_flujo_rapido_voz(
            "presupuesto para carlos alberto poccia de 2 bieletas suspension 207"
        )
        self.assertIsNotNone(raw)
        norm = normalizar_accion_mostrador(raw, "presupuesto para carlos alberto poccia de 2 bieletas suspension 207")
        self.assertEqual(norm.get("nombre_cliente"), "CARLOS ALBERTO POCCIA")
        self.assertEqual(norm.get("intent_sugerido"), "presupuesto")
        items = norm.get("items") or []
        self.assertTrue(any("BIELETA" in str(i.get("termino", "")) for i in items))

    def test_normalizar_preserva_items_groq(self):
        groq = {
            "accion": "flujo_factura",
            "nombre_cliente": "julio",
            "intent_sugerido": "presupuesto",
            "items": [{"termino": "bieleta suspension", "cantidad": 2, "vehiculo": "207"}],
            "ir_verificacion": True,
        }
        norm = normalizar_accion_mostrador(groq, "")
        self.assertEqual(norm["nombre_cliente"], "JULIO")
        self.assertEqual(norm["items"][0]["termino"], "BIELETA SUSPENSION")
        self.assertEqual(norm["items"][0]["vehiculo"], "207")

    def test_fusionar_prefiere_cliente_local_completo(self):
        from modulos.orden_mostrador_inteligente import fusionar_con_parser_local, normalizar_accion_mostrador

        groq = {
            "accion": "flujo_factura",
            "nombre_cliente": "JUAN",
            "intent_sugerido": "presupuesto",
            "items": [{"termino": "GUZAMN BIELETA SUSPENSION", "cantidad": 3}],
        }
        texto = "haceme un presupuesto para juan guzamn de bieleta de suspension 3 unidades"
        merged = normalizar_accion_mostrador(
            fusionar_con_parser_local(groq, texto),
            texto,
        )
        self.assertEqual(merged.get("nombre_cliente"), "JUAN GUZAMN")
        self.assertEqual(merged["items"][0]["termino"], "BIELETA SUSPENSION")

    def test_normalizar_zapatilla_como_descripcion(self):
        raw = parse_flujo_rapido_voz(
            "haceme un presupuesto de zapatilla 3 unidades para el cliente juan perez"
        )
        norm = normalizar_accion_mostrador(
            raw,
            "haceme un presupuesto de zapatilla 3 unidades para el cliente juan perez",
        )
        self.assertEqual(norm.get("nombre_cliente"), "JUAN PEREZ")
        self.assertEqual(len(norm.get("items") or []), 1)
        self.assertEqual(norm["items"][0]["termino"], "PASTILLA")
        self.assertEqual(norm["items"][0]["modo"], "descripcion")


if __name__ == "__main__":
    unittest.main()
