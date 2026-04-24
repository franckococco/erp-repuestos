import os
import json
import google.generativeai as genai # type: ignore
import streamlit as st
from dotenv import load_dotenv

load_dotenv(override=True)

def obtener_modelo_valido():
    """Busca en Google la lista oficial y elige automáticamente un modelo compatible."""
    modelo_elegido = "gemini-1.5-flash" 
    try:
        # Usamos type ignore para que Pylance no moleste con la estructura interna de la librería
        for m in genai.list_models(): # type: ignore
            if 'generateContent' in m.supported_generation_methods:
                if '1.5-flash' in m.name:
                    return m.name
                elif 'gemini-pro' in m.name:
                    modelo_elegido = m.name
    except Exception:
        pass
    return modelo_elegido

def procesar_orden_voz(texto_usuario, inventario_actual):
    # --- BÚSQUEDA DINÁMICA DE LA CLAVE ---
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        try:
            api_key = st.secrets["GEMINI_API_KEY"]
        except Exception:
            api_key = None

    if not api_key:
        return {"accion": "error", "respuesta": "Falta configurar la GEMINI_API_KEY en los secretos."}

    # Configurar Gemini
    genai.configure(api_key=api_key) # type: ignore

    # --- AUTO-DETECCIÓN DEL MODELO ---
    try:
        nombre_modelo = obtener_modelo_valido()
        model = genai.GenerativeModel(nombre_modelo) # type: ignore
    except Exception as e:
        return {"accion": "error", "respuesta": f"Error al auto-detectar modelos: {str(e)}"}

    # Convertimos el inventario a un texto simple
    inv_str = ""
    # Aseguramos que inventario_actual sea tratado como lista para evitar errores de Pylance
    for item in (inventario_actual or []):
        if not isinstance(item, dict): continue
        inv_str += f"- ID: {item.get('id')}, Desc: {item.get('descripcion')}, Marca: {item.get('marca')}, Stock: {item.get('stock')}, Precio Venta: ${item.get('precio_venta')}\n"

    if not inv_str:
        inv_str = "El inventario está vacío actualmente."

    prompt = f"""
    Eres el Asistente Inteligente del Depósito de 'Hafid Repuestos'.
    Tu trabajo es ayudar al encargado que te está hablando por voz.
    
    Este es el inventario actual de la base de datos:
    {inv_str}

    El encargado te ha dictado la siguiente orden:
    "{texto_usuario}"

    Debes analizar la orden y devolver ÚNICAMENTE un objeto JSON válido con la siguiente estructura:

    OPCIÓN 1: Si es una consulta de stock o precio:
    {{"accion": "consulta", "respuesta": "Respuesta breve y directa."}}

    OPCIÓN 2: Si es una orden para dar de baja stock:
    {{"accion": "baja", "id_producto": "ID EXACTO", "cantidad": numero_entero, "respuesta": "Confirmación de la baja."}}
    """

    try:
        response = model.generate_content(prompt, generation_config={"temperature": 0.0}) # type: ignore
        texto = response.text.strip()
        
        # Limpieza de formato markdown por si la IA lo agrega
        if texto.startswith("```json"):
            texto = texto.replace("```json", "").replace("```", "").strip()
        elif texto.startswith("```"):
            texto = texto.replace("```", "").strip()
            
        return json.loads(texto)
    except Exception as e:
        return {"accion": "error", "respuesta": f"Error en la consulta: {str(e)}"}