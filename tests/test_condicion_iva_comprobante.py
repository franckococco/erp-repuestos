"""Condición IVA del ticket según tipo de factura."""
from modulos.comprobante_contexto import condicion_iva_cliente


def test_factura_a_siempre_responsable_inscripto():
    assert (
        condicion_iva_cliente(
            {
                "cbte_tipo": "1",
                "nombre": "BESIN GUSTAVO",
                "cuit": "20204553964",
                "condicion_iva": "IVA EXENTO",
            }
        )
        == "IVA Responsable Inscripto"
    )


def test_factura_b_consumidor_final_no_exento():
    assert (
        condicion_iva_cliente(
            {
                "cbte_tipo": "6",
                "nombre": "CONSUMIDOR FINAL",
                "cuit": "00000000000",
                "condicion_iva": "IVA EXENTO",
            }
        )
        == "Consumidor Final"
    )


def test_factura_b_exento_real_se_mantiene():
    assert (
        condicion_iva_cliente(
            {
                "cbte_tipo": "6",
                "nombre": "BESIN GUSTAVO ENRIQUE",
                "cuit": "20204553964",
                "condicion_iva": "IVA EXENTO",
            }
        )
        == "IVA Exento"
    )
