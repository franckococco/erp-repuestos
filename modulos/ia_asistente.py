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
    texto_limpio = re.sub(r'\b(guion|guión)\b', '-', texto_limpio, flags=re.IGNORECASE)
    return texto_limpio

def procesar_orden_voz(texto_usuario, inventario_actual=None):
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

    prompt = f"""
    Eres el asistente rápido de mostrador de 'Hafid Repuestos'.
    TU ÚNICO TRABAJO ES EXTRAER LA INTENCIÓN DEL USUARIO Y LAS PALABRAS CLAVE.

    ORDEN DEL USUARIO PROCESADA: "{texto_procesado}"

    REGLAS ESTRICTAS PARA ENTENDER LA ORDEN:
    1. BÚSQUEDA GENERAL: "filtrar", "buscar", "tenemos", "hay".
    2. CONSULTA DE UBICACIÓN: "dónde está", "ubicación".
    3. ACTUALIZAR UBICACIÓN (RELEVAMIENTO): Si indica que un código o producto ESTÁ o SE GUARDÓ en un pasillo, piso, módulo o fila. Extrae los números. Si no menciona alguno, devuelve null en ese campo.
    4. REPORTE STOCK MÍNIMO: Si pide listado de stock mínimo, faltantes, o productos con "X" unidades. Si no menciona cantidad, asume 3.
    5. ALTA DE STOCK (SUMAR): "agregar", "sumar", "ingresar".
    6. BAJA DE STOCK (RESTAR): "descontar", "restar".
    7. CLIENTES Y PRESUPUESTOS: "presupuesto para...".
    8. CARRITO: "añadir", "meter al carrito".

    REGLA CRÍTICA PARA CÓDIGOS:
    Extrae SIEMPRE LA RAÍZ limpia (sin guiones ni letras sueltas al final). Ej: "15 42 514 f g" -> "1542514".

    Devuelve ÚNICAMENTE un JSON válido eligiendo UNA de estas opciones:

    OPCIÓN 1 (Búsqueda general):
    {{"accion": "buscar", "termino": "RAIZ_LIMPIA"}}

    OPCIÓN 2 (Consulta de Ubicación):
    {{"accion": "ubicacion", "termino": "RAIZ_LIMPIA"}}

    OPCIÓN 3 (Actualizar Ubicación Exacta):
    {{"accion": "actualizar_ubicacion", "id_producto": "RAIZ_LIMPIA", "pasillo": NUMERO_O_NULL, "piso": NUMERO_O_NULL, "modulo": NUMERO_O_NULL, "fila": NUMERO_O_NULL}}

    OPCIÓN 4 (Reporte de Stock Mínimo):
    {{"accion": "reporte_stock", "cantidad": NUMERO_LIMITE_O_3}}

    OPCIÓN 5 (Alta de Stock / Sumar):
    {{"accion": "alta", "id_producto": "RAIZ_LIMPIA", "cantidad": NUMERO}}

    OPCIÓN 6 (Baja de Stock / Descontar):
    {{"accion": "baja", "id_producto": "RAIZ_LIMPIA", "cantidad": NUMERO}}

    OPCIÓN 7 (Iniciar Presupuesto para Cliente):
    {{"accion": "set_cliente", "nombre_cliente": "NOMBRE"}}

    OPCIÓN 8 (Añadir producto al carrito/presupuesto):
    {{"accion": "agregar_carrito", "id_producto": "RAIZ_LIMPIA", "cantidad": NUMERO}}
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