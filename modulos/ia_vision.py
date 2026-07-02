import os
import json
import re
import cv2
import numpy as np
import streamlit as st
import base64
from io import BytesIO
from dotenv import load_dotenv
from anthropic import Anthropic
from anthropic.types import TextBlock

from modulos.util_imagen import mejorar_imagen_documento

load_dotenv(override=True)

key_ai = os.getenv("ANTHROPIC_API_KEY")
try:
    if not key_ai and "ANTHROPIC_API_KEY" in st.secrets:
        key_ai = st.secrets["ANTHROPIC_API_KEY"]
except Exception:
    pass

cliente: Anthropic | None = Anthropic(api_key=key_ai) if key_ai else None

_MAX_TOKENS_DOCUMENTO = 8192

_REEMPLAZOS_TEXTO = str.maketrans({
    "\u201c": '"',
    "\u201d": '"',
    "\u2018": "'",
    "\u2019": "'",
    "\u00ab": '"',
    "\u00bb": '"',
    "\u2013": "-",
    "\u2014": "-",
})


def _anthropic_client() -> Anthropic:
    if cliente is None:
        raise Exception("Falta la API Key de Anthropic.")
    return cliente


def pil_a_base64(imagen_pil):
    buffered = BytesIO()
    if imagen_pil.mode != "RGB":
        imagen_pil = imagen_pil.convert("RGB")
    imagen_pil.save(buffered, format="JPEG")
    return base64.b64encode(buffered.getvalue()).decode("utf-8")


def _limpiar_bloque_json(texto_limpio: str) -> str:
    texto = (texto_limpio or "").strip()
    if "```json" in texto:
        texto = texto.split("```json", 1)[1].split("```", 1)[0]
    elif "```" in texto:
        texto = texto.split("```", 1)[1].split("```", 1)[0]
    texto = texto.strip()
    inicio = texto.find("{")
    fin = texto.rfind("}")
    if inicio >= 0 and fin > inicio:
        texto = texto[inicio: fin + 1]
    return texto.strip()


def _reparar_json_simple(texto: str) -> str:
    t = _limpiar_bloque_json(texto)
    t = re.sub(r",\s*([}\]])", r"\1", t)
    t = t.translate(_REEMPLAZOS_TEXTO)
    return t


def _sanitizar_texto_campo(valor) -> str:
    if valor is None:
        return ""
    texto = str(valor).translate(_REEMPLAZOS_TEXTO)
    texto = texto.replace("\r", " ").replace("\n", " ").replace("\t", " ")
    texto = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", texto)
    return re.sub(r"\s+", " ", texto).strip()


def _coercion_numero(valor, default=0):
    if valor is None or valor == "":
        return default
    if isinstance(valor, (int, float)):
        return valor
    limpio = str(valor).strip().replace(".", "").replace(",", ".")
    limpio = re.sub(r"[^\d.\-]", "", limpio)
    try:
        if "." in limpio:
            return float(limpio)
        return int(limpio)
    except (TypeError, ValueError):
        return default


def _normalizar_articulo_factura(art: dict) -> dict:
    if not isinstance(art, dict):
        return {}
    marca = _sanitizar_texto_campo(art.get("marca") or art.get("condicion") or "GENERICO")
    return {
        "codigo": _sanitizar_texto_campo(art.get("codigo", "")),
        "descripcion": _sanitizar_texto_campo(art.get("descripcion", "")),
        "marca": marca or "GENERICO",
        "cantidad": max(1, int(_coercion_numero(art.get("cantidad"), 1))),
        "precio_unitario": float(_coercion_numero(art.get("precio_unitario"), 0)),
    }


def _normalizar_articulo_remito(art: dict) -> dict:
    if not isinstance(art, dict):
        return {}
    marca = _sanitizar_texto_campo(art.get("marca") or art.get("condicion") or "GENERICO")
    return {
        "codigo": _sanitizar_texto_campo(art.get("codigo", "")),
        "descripcion": _sanitizar_texto_campo(art.get("descripcion", "")),
        "marca": marca or "GENERICO",
        "cantidad": max(1, int(_coercion_numero(art.get("cantidad"), 1))),
    }


def _normalizar_datos_documento(data: dict, tipo: str) -> dict:
    if not isinstance(data, dict):
        raise ValueError("La IA no devolvió un objeto JSON válido.")
    normalizador = _normalizar_articulo_factura if tipo == "factura" else _normalizar_articulo_remito
    articulos_raw = data.get("articulos") or []
    articulos = [normalizador(a) for a in articulos_raw if isinstance(a, dict)]
    articulos = [a for a in articulos if a.get("codigo") or a.get("descripcion")]
    resultado = {
        "proveedor": _sanitizar_texto_campo(data.get("proveedor", "")),
        "cuit_proveedor": "".join(filter(str.isdigit, str(data.get("cuit_proveedor", "")))),
        "articulos": articulos,
    }
    if tipo == "factura":
        resultado["punto_venta"] = _sanitizar_texto_campo(data.get("punto_venta", ""))
        resultado["numero_comprobante"] = _sanitizar_texto_campo(data.get("numero_comprobante", ""))
    else:
        resultado["numero_remito"] = _sanitizar_texto_campo(data.get("numero_remito", ""))
    return resultado


def _reintentar_json_con_ia(texto_roto: str) -> dict:
    respuesta = _anthropic_client().messages.create(
        model="claude-sonnet-4-6",
        max_tokens=_MAX_TOKENS_DOCUMENTO,
        temperature=0.0,
        messages=[
            {
                "role": "user",
                "content": (
                    "Corregí el siguiente JSON para que sea válido y parseable. "
                    "Escapá comillas dentro de strings. No agregues comentarios ni texto extra. "
                    "Devolvé ÚNICAMENTE el JSON corregido:\n\n"
                    f"{texto_roto[:120000]}"
                ),
            }
        ],
    )
    texto = ""
    for bloque in respuesta.content:
        if isinstance(bloque, TextBlock):
            texto = bloque.text.strip()
            break
    return json.loads(_reparar_json_simple(texto))


def _extraer_json_respuesta(texto_limpio: str) -> dict:
    candidatos = [
        texto_limpio,
        _limpiar_bloque_json(texto_limpio),
        _reparar_json_simple(texto_limpio),
    ]
    visto = set()
    ultimo_error = None
    for candidato in candidatos:
        if not candidato or candidato in visto:
            continue
        visto.add(candidato)
        try:
            return json.loads(candidato)
        except json.JSONDecodeError as e:
            ultimo_error = e
    try:
        return _reintentar_json_con_ia(texto_limpio)
    except Exception as e:
        detalle = str(ultimo_error or e)
        raise ValueError(f"JSON inválido de la IA ({detalle})") from e


def _procesar_documento_ia(imagen_pil, prompt, tipo="factura"):
    imagen_b64 = pil_a_base64(imagen_pil)
    respuesta = _anthropic_client().messages.create(
        model="claude-sonnet-4-6",
        max_tokens=_MAX_TOKENS_DOCUMENTO,
        temperature=0.0,
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": "image/jpeg",
                            "data": imagen_b64,
                        },
                    },
                    {"type": "text", "text": prompt},
                ],
            }
        ],
    )
    texto_limpio = ""
    for bloque in respuesta.content:
        if isinstance(bloque, TextBlock):
            texto_limpio = bloque.text.strip()
            break
    data = _extraer_json_respuesta(texto_limpio)
    return _normalizar_datos_documento(data, tipo)


_PROMPT_JSON_STRICT = """
IMPORTANTE sobre el JSON:
- Devolvé ÚNICAMENTE JSON válido, sin markdown ni texto adicional.
- En descripciones y textos: sin saltos de línea; escapá comillas dobles con \\".
- Ignorá símbolos decorativos; conservá el significado del repuesto.
- Si no hay marca, usá "GENERICO".
"""


def procesar_factura_con_ia(imagen_pil, mejorar_imagen=True):
    if mejorar_imagen:
        imagen_pil = mejorar_imagen_documento(imagen_pil)
    prompt = f"""
    Eres un experto en facturación argentina. Extrae de CUALQUIER proveedor:
    1. CUIT del emisor (11 dígitos).
    2. Punto de Venta (dígitos antes del guion en el número de comprobante).
    3. Número de factura (dígitos después del guion).
    4. Nombre del Proveedor.
    5. Tabla de artículos: Cantidad, Código, Descripción, Marca si aparece, Precio Unitario Neto.

    Devolvé ÚNICAMENTE un JSON con esta estructura exacta:
    {{
      "proveedor": "NOMBRE",
      "cuit_proveedor": "str",
      "punto_venta": "str",
      "numero_comprobante": "str",
      "articulos": [{{"codigo": "str", "descripcion": "str", "marca": "str", "cantidad": int, "precio_unitario": float}}]
    }}
    {_PROMPT_JSON_STRICT}
    """
    try:
        return _procesar_documento_ia(imagen_pil, prompt, tipo="factura")
    except Exception as e:
        raise Exception(f"Error en lectura de IA: {str(e)}") from e


def procesar_remito_con_ia(imagen_pil, mejorar_imagen=True):
    if mejorar_imagen:
        imagen_pil = mejorar_imagen_documento(imagen_pil)
    prompt = f"""
    Eres un experto en documentos logísticos argentinos (remitos de entrega).
    Extrae de CUALQUIER proveedor:
    1. CUIT del emisor (11 dígitos).
    2. Nombre del Proveedor / transportista que entrega.
    3. Número de remito.
    4. Tabla de artículos entregados: Cantidad, Código, Descripción, Marca (si aparece).
    NO incluyas precios — los remitos no tienen precio de venta.

    Devolvé ÚNICAMENTE un JSON con esta estructura exacta:
    {{
      "proveedor": "NOMBRE",
      "cuit_proveedor": "str",
      "numero_remito": "str",
      "articulos": [{{"codigo": "str", "descripcion": "str", "marca": "str", "cantidad": int}}]
    }}
    {_PROMPT_JSON_STRICT}
    """
    try:
        return _procesar_documento_ia(imagen_pil, prompt, tipo="remito")
    except Exception as e:
        raise Exception(f"Error en lectura de remito: {str(e)}") from e


def decodificar_qr_desde_imagen(imagen_pil):
    """Función restaurada para la lectura de códigos QR si es necesario"""
    try:
        opencv_img = cv2.cvtColor(np.array(imagen_pil), cv2.COLOR_RGB2BGR)
        detector = cv2.QRCodeDetector()
        datos, _, _ = detector.detectAndDecode(opencv_img)
        return datos
    except Exception as e:
        print(f"Error QR: {e}")
        return None
