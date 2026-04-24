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
    Eres el sistema estricto de búsqueda de depósito de 'Hafid Repuestos'.

    INVENTARIO ACTUAL:
    {inv_str}

    ORDEN DEL USUARIO: "{texto_usuario}"

    REGLAS ESTRICTAS DE BÚSQUEDA Y FORMATO:
    1. BÚSQUEDA LITERAL: Busca ÚNICAMENTE la palabra clave que pide el usuario. Si pide "maza", MUESTRA SOLO descripciones que contengan "maza". ¡PROHIBIDO mostrar rulemanes o productos relacionados que no tengan la palabra exacta! Si pide "correa", SOLO correas.
    2. SIN INVENTOS: Si en el inventario no hay nada que coincida exactamente con la palabra buscada, responde: "No encontré [producto] en el stock."
    3. FORMATO VISUAL: Tu respuesta debe ser texto puro formateado con saltos de línea (\\n) y viñetas (-). 
       ¡ESTÁ TOTALMENTE PROHIBIDO usar formato de lista de programación como ['item1', 'item2']!
       
       EJEMPLO DE CÓMO DEBES RESPONDER:
       "Encontré los siguientes artículos:\\n\\n- MAZA RUEDA DELANTERA: Stock 4, Precio $82990\\n- MAZA RUEDA TRASERA: Stock 2, Precio $131700"

    Devuelve ÚNICAMENTE un JSON válido eligiendo UNA de estas tres opciones:

    OPCIÓN 1 (Consulta/Búsqueda):
    {{"accion": "consulta", "respuesta": "Tu respuesta respetando los saltos de línea y viñetas como en el ejemplo."}}

    OPCIÓN 2 (Baja de Stock / Descontar):
    {{"accion": "baja", "id_producto": "ID_EXACTO", "cantidad": NUMERO, "respuesta": "Confirmación de baja."}}

    OPCIÓN 3 (Alta de Stock / Aumentar):
    {{"accion": "alta", "id_producto": "ID_EXACTO", "cantidad": NUMERO, "respuesta": "Confirmación de aumento."}}
    """

    try:
        # CAMBIO CLAVE: Usamos el modelo 70b (mucho más inteligente para acatar reglas estrictas)
        chat_completion = client.chat.completions.create(
            messages=[{"role": "user", "content": prompt}],
            model="llama-3.3-70b-versatile", 
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