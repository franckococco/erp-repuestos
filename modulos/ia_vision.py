import os
import json
import cv2
import numpy as np
import streamlit as st
import base64
from io import BytesIO
from dotenv import load_dotenv
from anthropic import Anthropic
from anthropic.types import TextBlock

# Cargamos variables del .env
load_dotenv(override=True)

key_ai = os.getenv("ANTHROPIC_API_KEY")
if not key_ai and "ANTHROPIC_API_KEY" in st.secrets:
    key_ai = st.secrets["ANTHROPIC_API_KEY"]

cliente = Anthropic(api_key=key_ai) if key_ai else None

def pil_a_base64(imagen_pil):
    buffered = BytesIO()
    if imagen_pil.mode != 'RGB':
        imagen_pil = imagen_pil.convert('RGB')
    imagen_pil.save(buffered, format="JPEG")
    return base64.b64encode(buffered.getvalue()).decode('utf-8')

def procesar_factura_con_ia(imagen_pil):
    if not cliente:
        raise Exception("Falta la API Key de Anthropic en el .env")

    prompt = """
    Eres un experto en visión artificial. Extrae la tabla de esta factura. 
    REGLAS DE ORO:
    1. CANTIDAD: Es el primer número a la izquierda. ¡No asumas que es 1! Lee el número real.
    2. CÓDIGO: El código alfanumérico a la derecha de la cantidad.
    3. PRECIO: El monto con decimales (usa punto para decimales).

    Devuelve ÚNICAMENTE un JSON con esta estructura:
    {
      "proveedor": "NOMBRE",
      "articulos": [{"codigo": "str", "descripcion": "str", "cantidad": int, "precio_unitario": float}]
    }
    """
    try:
        imagen_b64 = pil_a_base64(imagen_pil)

        # Usamos el modelo Sonnet 4.6 que figura en tu lista (Equilibrio perfecto velocidad/precisión)
        respuesta = cliente.messages.create(
            model="claude-sonnet-4-6", 
            max_tokens=2048,
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
                        {"type": "text", "text": prompt}
                    ],
                }
            ],
        )
        
        texto_limpio = ""
        for bloque in respuesta.content:
            if isinstance(bloque, TextBlock):
                texto_limpio = bloque.text.strip()
                break
        
        # Limpieza de formato markdown si la IA lo agrega
        if "```json" in texto_limpio:
            texto_limpio = texto_limpio.split("```json")[1].split("```")[0]
        elif "```" in texto_limpio:
            texto_limpio = texto_limpio.split("```")[1].split("```")[0]
        
        return json.loads(texto_limpio.strip())
        
    except Exception as e:
        raise Exception(f"Error en Claude 4: {str(e)}")

def decodificar_qr_desde_imagen(imagen_pil):
    try:
        opencv_img = cv2.cvtColor(np.array(imagen_pil), cv2.COLOR_RGB2BGR)
        detector = cv2.QRCodeDetector()
        datos, _, _ = detector.detectAndDecode(opencv_img)
        return datos
    except:
        return None