import os
import json
import cv2
import numpy as np
import streamlit as st
from dotenv import load_dotenv
from google import genai
from google.genai import types

# Cargamos las variables del archivo .env
load_dotenv()

# Inicialización Híbrida de API Key
key_ai = st.secrets["GEMINI_API_KEY"] if "GEMINI_API_KEY" in st.secrets else os.getenv("GEMINI_API_KEY")

# Inicializamos el cliente de la NUEVA librería.
cliente = genai.Client(api_key=key_ai)

def procesar_factura_con_ia(imagen_pil):
    """
    Recibe una imagen y extrae datos usando las reglas de negocio originales del cliente.
    """
    prompt = """
    Extrae los datos de esta factura.
    
    REGLAS ESTRICTAS PARA LA EXTRACCIÓN:
    1. PRECIO UNITARIO: Toma el valor exacto de la columna 'Neto' o 'Precio'. PROHIBIDO hacer cálculos matemáticos. NO dividas el Neto por la Cantidad. El Neto ya es el precio por unidad.
    2. FORMATO NUMÉRICO: Para el 'precio_unitario', devuelve un número decimal usando ÚNICAMENTE punto para los decimales y NINGÚN separador de miles. 
       - Correcto: 69650.08
       - Incorrecto: 69.650,08
       - Incorrecto: 69650,08
    3. DESCRIPCIÓN: Usa la columna 'Fabrica', 'Descripción' o 'Marca'.

    Estructura la salida exactamente con estas claves:
    {
      "proveedor": "NOMBRE DEL PROVEEDOR (EN MAYUSCULAS)",
      "articulos": [
        {
          "codigo": "código",
          "descripcion": "descripción",
          "cantidad": numero entero,
          "precio_unitario": numero decimal
        }
      ]
    }
    """
    
    respuesta = None 
    
    try:
        respuesta = cliente.models.generate_content(
            model='gemini-2.0-flash',
            contents=[prompt, imagen_pil],
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
            )
        )
        
        texto_ia = respuesta.text
        if not texto_ia:
            print("Error: Gemini devolvió una respuesta vacía.")
            return None
            
        return json.loads(texto_ia)
            
    except Exception as e:
        print("\n--- ERROR INTERNO ---")
        print(f"Detalle del error: {e}")
        if respuesta is not None:
            print(f"Lo que intentó responder Gemini: {getattr(respuesta, 'text', 'Sin texto')}")
        print("---------------------\n")
        return None

def decodificar_qr_desde_imagen(imagen_pil):
    """
    Convierte la imagen de la cámara enviada por Streamlit 
    y extrae el texto del QR usando OpenCV.
    """
    try:
        opencv_img = cv2.cvtColor(np.array(imagen_pil), cv2.COLOR_RGB2BGR)
        detector = cv2.QRCodeDetector()
        datos, _, _ = detector.detectAndDecode(opencv_img)
        return datos
    except Exception as e:
        print(f"Error QR: {e}")
        return None