import os
import json
import re
from groq import Groq # type: ignore
import streamlit as st
from dotenv import load_dotenv

load_dotenv(override=True)

def preprocesar_texto_usuario(texto):
    """
    Une secuencias de números separados por espacios para corregir 
    el dictado de códigos por voz (ej. "50 51 03" -> "505103").
    """
    def unir_numeros(match):
        return match.group(0).replace(" ", "")
    
    # Busca 2 o más bloques de números separados por espacios
    texto_limpio = re.sub(r'(?:\d+\s+)+\d+', unir_numeros, texto)
    return texto_limpio

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
    
    texto_procesado = preprocesar_texto_usuario(texto_usuario)

    inv_str = ""
    for item in (inventario_actual or []):
        if not isinstance(item, dict): continue
        # Integramos proveedor y código al contexto para permitir filtros avanzados
        inv_str += f"- ID: {item.get('id')}, Código: {item.get('codigo', 'S/C')}, Desc: {item.get('descripcion')}, Marca: {item.get('marca')}, Proveedor: {item.get('proveedor', 'Desconocido')}, Stock: {item.get('stock')}, Precio: ${item.get('precio_venta')}\n"

    if not inv_str:
        inv_str = "El inventario está vacío."

    prompt = f"""
    Eres el sistema estricto de búsqueda de depósito de 'Hafid Repuestos'.

    INVENTARIO ACTUAL:
    {inv_str}

    ORDEN DEL USUARIO PROCESADA: "{texto_procesado}"
    (Nota interna: la orden cruda original era "{texto_usuario}")

    REGLAS ESTRICTAS PARA ENTENDER LA ORDEN:
    1. CÓDIGO VS PRECIO: Si el usuario menciona un número suelto sin la palabra "pesos" o el signo "$", asume SIEMPRE que es un CÓDIGO de artículo o parte de la descripción, NUNCA un precio. (Ej: "12500" busca el código 12500. "12500 pesos" busca por precio).
    2. FILTROS POR PROVEEDOR: Si el usuario pide "filtrar por" o "buscar proveedor X", lista todos los productos cuyo campo Proveedor coincida.
    3. STOCK MÍNIMO / CRÍTICO: Si el usuario pregunta por "stock mínimo", "bajo stock" o "sin stock", lista los productos con stock menor o igual a 3. Si además menciona un proveedor, aplica ambos filtros cruzados.
    4. FORMATO DE RESPUESTA: Para consultas, formatea la respuesta con viñetas claras mostrando: Código, Descripción, Proveedor, Stock y Precio.

    Devuelve ÚNICAMENTE un JSON válido eligiendo UNA de estas tres opciones:

    OPCIÓN 1 (Consulta/Búsqueda/Filtro):
    {{"accion": "consulta", "respuesta": "Tu respuesta respetando los saltos de línea y viñetas."}}

    OPCIÓN 2 (Baja de Stock / Descontar):
    {{"accion": "baja", "id_producto": "ID_EXACTO", "cantidad": NUMERO, "respuesta": "Confirmación de baja."}}

    OPCIÓN 3 (Alta de Stock / Aumentar):
    {{"accion": "alta", "id_producto": "ID_EXACTO", "cantidad": NUMERO, "respuesta": "Confirmación de aumento."}}
    """

    try:
        # Usamos el modelo 70b (mucho más inteligente para acatar reglas estrictas y razonamiento lógico)
        chat_completion = client.chat.completions.create(
            messages=[{"role": "user", "content": prompt}],
            model="llama-3.3-70b-versatile", 
            temperature=0.0,
            response_format={"type": "json_object"} 
        )
        
        texto = chat_completion.choices[0].message.content.strip() # type: ignore
        
        # Limpieza de seguridad por si Groq devuelve markdown envolviendo el JSON
        if "```json" in texto:
            texto = texto.split("```json")[1].split("```")[0]
        elif "```" in texto:
            texto = texto.split("```")[1].split("```")[0]
            
        return json.loads(texto.strip())
        
    except Exception as e:
        return {"accion": "error", "respuesta": f"Error en lectura de IA: {str(e)}"}