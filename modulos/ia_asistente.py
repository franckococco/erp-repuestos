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
    1. CÓDIGO VS PRECIO: Si el usuario menciona un número suelto sin la palabra "pesos", asume que es un CÓDIGO.
    2. FILTROS POR PROVEEDOR: Si pide "filtrar por" o "buscar proveedor X", lista productos cuyo Proveedor coincida.
    3. STOCK MÍNIMO: Si pregunta por "stock mínimo" o "sin stock", lista productos con stock <= 3.
    4. CLIENTES Y PRESUPUESTOS: Si pide hacer presupuesto o cargarle a un cliente específico, detecta el nombre.
    5. AGREGAR A PRESUPUESTO/CARRITO: Si el usuario dice "agregame", "cargame", "poneme X unidades de", debes detectar el ID del producto que quiere añadir al presupuesto activo.

    Devuelve ÚNICAMENTE un JSON válido eligiendo UNA de estas cinco opciones:

    OPCIÓN 1 (Consulta/Búsqueda/Filtro):
    {{"accion": "consulta", "respuesta": "Tu respuesta respetando los saltos de línea y viñetas."}}

    OPCIÓN 2 (Baja de Stock / Descontar):
    {{"accion": "baja", "id_producto": "ID_EXACTO", "cantidad": NUMERO, "respuesta": "Confirmación de baja."}}

    OPCIÓN 3 (Alta de Stock / Aumentar):
    {{"accion": "alta", "id_producto": "ID_EXACTO", "cantidad": NUMERO, "respuesta": "Confirmación de aumento."}}

    OPCIÓN 4 (Iniciar Presupuesto para Cliente):
    {{"accion": "set_cliente", "nombre_cliente": "NOMBRE_DEL_CLIENTE", "respuesta": "Confirmación amigable."}}

    OPCIÓN 5 (Añadir producto al carrito/presupuesto en curso):
    {{"accion": "agregar_carrito", "id_producto": "ID_EXACTO", "cantidad": NUMERO, "respuesta": "Confirmación de agregado al presupuesto."}}
    """

    try:
        chat_completion = client.chat.completions.create(
            messages=[{"role": "user", "content": prompt}],
            model="llama-3.3-70b-versatile", 
            temperature=0.0,
            response_format={"type": "json_object"} 
        )
        
        texto = chat_completion.choices[0].message.content.strip() # type: ignore
        
        if "```json" in texto:
            texto = texto.split("```json")[1].split("```")[0]
        elif "```" in texto:
            texto = texto.split("```")[1].split("```")[0]
            
        return json.loads(texto.strip())
        
    except Exception as e:
        return {"accion": "error", "respuesta": f"Error en lectura de IA: {str(e)}"}