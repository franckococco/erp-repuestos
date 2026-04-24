import os
import json
from groq import Groq # type: ignore
import streamlit as st
from dotenv import load_dotenv

load_dotenv(override=True)

def procesar_orden_voz(texto_usuario, inventario_actual):
    # --- BÚSQUEDA DE LA CLAVE GROQ ---
    api_key = os.getenv("GROQ_API_KEY")
    if not api_key:
        try:
            api_key = st.secrets["GROQ_API_KEY"]
        except Exception:
            api_key = None

    if not api_key:
        return {"accion": "error", "respuesta": "Falta configurar la GROQ_API_KEY en los secretos."}

    # Inicializar cliente Groq
    client = Groq(api_key=api_key)

    # Convertimos el inventario a un texto simple para la IA
    inv_str = ""
    for item in (inventario_actual or []):
        if not isinstance(item, dict): continue
        inv_str += f"- ID: {item.get('id')}, Desc: {item.get('descripcion')}, Marca: {item.get('marca')}, Stock: {item.get('stock')}, Precio: ${item.get('precio_venta')}\n"

    if not inv_str:
        inv_str = "El inventario está vacío."

    prompt = f"""
    Eres el Asistente de Depósito de 'Hafid Repuestos'. Tu objetivo es ayudar al operario con stock, precios y EQUIVALENCIAS.
    
    INVENTARIO ACTUAL:
    {inv_str}

    ORDEN DEL OPERARIO:
    "{texto_usuario}"

    REGLAS DE EQUIVALENCIA:
    - Si el operario busca una marca y no hay, busca el mismo repuesto (misma descripción o código) en OTRAS marcas disponibles.
    - Responde siempre de forma breve y técnica.

    Devuelve ÚNICAMENTE un JSON (sin markdown):
    OPCIÓN 1 (Consulta/Equivalencia):
    {{"accion": "consulta", "respuesta": "Tu respuesta sobre stock, precio y si encontraste equivalentes."}}

    OPCIÓN 2 (Baja de Stock):
    {{"accion": "baja", "id_producto": "ID_EXACTO", "cantidad": 1, "respuesta": "Confirmación de baja."}}
    """

    try:
        # Usamos Llama 3 para máxima velocidad y razonamiento
        chat_completion = client.chat.completions.create(
            messages=[{"role": "user", "content": prompt}],
            model="llama3-8b-8192",
            temperature=0.0,
        )
        
        texto = chat_completion.choices[0].message.content.strip() # type: ignore
        
        # Limpieza de JSON
        if texto.startswith("```json"):
            texto = texto.replace("```json", "").replace("```", "").strip()
        elif texto.startswith("```"):
            texto = texto.replace("```", "").strip()
            
        return json.loads(texto)
    except Exception as e:
        return {"accion": "error", "respuesta": f"Error con Groq: {str(e)}"}