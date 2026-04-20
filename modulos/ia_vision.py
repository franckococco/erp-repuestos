import os
import json
import cv2
import numpy as np
from dotenv import load_dotenv
from google import genai
from google.genai import types

# Cargamos las variables del archivo .env
load_dotenv()

# Inicializamos el cliente de la NUEVA librería.
cliente = genai.Client()

def procesar_factura_con_ia(imagen_pil):
    """
    Recibe una imagen, la envía a Gemini forzando 
    la salida nativa en formato JSON usando el nuevo SDK.
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
        # Usamos el modelo más moderno y activo de Google
        respuesta = cliente.models.generate_content(
            model='gemini-2.5-flash',
            contents=[prompt, imagen_pil],
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
            )
        )
        
        # Guardamos el texto y verificamos que no esté vacío
        texto_ia = respuesta.text
        if not texto_ia:
            print("Error: Gemini devolvió una respuesta vacía.")
            return None
            
        # Convertimos el texto JSON a diccionario
        diccionario_datos = json.loads(texto_ia)
        return diccionario_datos
            
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
        # Convertir imagen PIL a formato compatible con OpenCV (numpy array)
        opencv_img = cv2.cvtColor(np.array(imagen_pil), cv2.COLOR_RGB2BGR)
        
        # Inicializar el detector de QR
        detector = cv2.QRCodeDetector()
        
        # Detectar y decodificar
        datos, _, _ = detector.detectAndDecode(opencv_img)
        
        return datos
    except Exception as e:
        print(f"\n--- ERROR LECTURA QR ---")
        print(f"Detalle del error OpenCV: {e}")
        print("------------------------\n")
        return None