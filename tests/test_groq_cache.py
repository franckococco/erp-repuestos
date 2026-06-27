"""Tests de caché Groq (sin llamar a la API)."""
import unittest
from unittest.mock import MagicMock, patch

import streamlit as st

from modulos.orden_mostrador_inteligente import (
    _groq_cache_get,
    _groq_cache_set,
    interpretar_orden_groq,
)


class TestGroqCache(unittest.TestCase):
    def setUp(self):
        st.session_state.clear()

    def test_cache_guarda_y_recupera(self):
        data = {"accion": "flujo_factura", "nombre_cliente": "PABLO"}
        _groq_cache_set("presupuesto pablo bieleta", "llama-test", data)
        cached = _groq_cache_get("presupuesto pablo bieleta", "llama-test")
        self.assertEqual(cached, data)

    def test_cache_distinto_modelo(self):
        _groq_cache_set("orden test", "modelo-a", {"accion": "error"})
        self.assertIsNone(_groq_cache_get("orden test", "modelo-b"))

    @patch("modulos.orden_mostrador_inteligente._groq_api_key", return_value="fake-key")
    @patch("modulos.orden_mostrador_inteligente.Groq")
    def test_interpretar_usa_cache_sin_api(self, mock_groq_cls, _mock_key):
        texto = "presupuesto para pedro bieleta 207 2"
        esperado = {"accion": "flujo_factura", "nombre_cliente": "PEDRO"}
        _groq_cache_set(texto, "llama-3.1-8b-instant", esperado)

        with patch(
            "modulos.mostrador_voz_flujo.preprocesar_texto_mostrador",
            return_value=texto,
        ), patch(
            "modulos.ia_mostrador._orden_es_flujo_complejo",
            return_value=False,
        ):
            result = interpretar_orden_groq(texto)

        self.assertEqual(result, esperado)
        mock_groq_cls.assert_not_called()


if __name__ == "__main__":
    unittest.main()
