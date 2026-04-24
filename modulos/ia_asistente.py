import os
import json
from groq import Groq # type: ignore
import streamlit as st
from dotenv import load_dotenv

load_dotenv(override=True)

def procesar_orden_voz(texto_usuario, inventario_actual):
    api_key = os.getenv("GROQ_API_KEY")
    if not api_key:
        try:
            api_key = st.secrets["GROQ_API_KEY"]
        except Exception:
            api_key = None

    if not api_key:
        return {"accion": "error", "respuesta": "Falta configurar la GROQ_API_KEY en los secretos."}

    client = Groq(api_key=api_key)

    inv_str = ""
    for item in (inventario_actual or []):
        if not isinstance(item, dict): continue
        inv_str += f"- ID: {item.get('id')}, Desc: {item.get('descripcion')}, Marca: {item.get('marca')}, Stock: {item.get('stock')}, Precio: ${item.get('precio_venta')}\n"

    if not inv_str:
        inv_str = "El inventario está vacío."

    prompt = f"""
    Eres el Asistente de Depósito de 'Hafid Repuestos'. Tu objetivo es ayudar al operario.
    
    INVENTARIO ACTUAL:
    {inv_str}

    ORDEN DEL OPERARIO:
    "{texto_usuario}"

    REGLAS ESTRICTAS DE BÚSQUEDA Y FORMATO:
    1. FILTRADO EXACTO: Si el operario busca un producto (ej: "correas", "bomba"), revisa el inventario y MUESTRA ÚNICAMENTE los productos que contengan esa palabra en su descripción o ID. ¡IGNORA y oculta todo el resto del inventario!
    2. SIN INVENTOS: Si no hay ningún producto que coincida con lo que pide, responde simplemente: "No encontré [producto] en el stock actual."
    3. EQUIVALENCIAS: Si pide una marca específica y no hay, pero SÍ hay de otra marca, muéstrale esa otra marca avisando que es un equivalente.
    4. FORMATO VISUAL: Usa viñetas ( - ) y saltos de línea para listar los productos encontrados. Sé directo, no saludes ni des explicaciones largas.

    Devuelve ÚNICAMENTE un JSON eligiendo UNA de estas tres opciones:

    OPCIÓN 1 (Consulta/Búsqueda):
    {{"accion": "consulta", "respuesta": "Lista de los productos EXACTOS que pidió el usuario."}}

    OPCIÓN 2 (Baja de Stock / Descontar):
    {{"accion": "baja", "id_producto": "ID_EXACTO", "cantidad": NUMERO, "respuesta": "Confirmación de baja."}}

    OPCIÓN 3 (Alta de Stock / Aumentar):
    {{"accion": "alta", "id_producto": "ID_EXACTO", "cantidad": NUMERO, "respuesta": "Confirmación de aumento."}}
    """

    try:
        chat_completion = client.chat.completions.create(
            messages=[{"role": "user", "content": prompt}],
            model="llama-3.1-8b-instant",
            temperature=0.0,
            response_format={"type": "json_object"} 
        )
        
        texto = chat_completion.choices[0].message.content.strip() # type: ignore
        
        if texto.startswith("```json"):
            texto = texto.replace("```json", "").replace("```", "").strip()
        elif texto.startswith("```"):
            texto = texto.replace("```", "").strip()
            
        return json.loads(texto)
    except Exception as e:
        return {"accion": "error", "respuesta": f"Error con Groq: {str(e)}"}