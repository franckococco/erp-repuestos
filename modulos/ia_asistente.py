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
    # Convertir la palabra "guion" o "guión" hablada a un caracter "-"
    texto_limpio = re.sub(r'\b(guion|guión)\b', '-', texto_limpio, flags=re.IGNORECASE)
    return texto_limpio

# Mantenemos el parámetro inventario_actual para no romper otras funciones
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
    TU ÚNICO TRABAJO ES EXTRAER LA INTENCIÓN DEL USUARIO Y LAS PALABRAS CLAVE. NO TIENES ACCESO AL INVENTARIO NI A LOS PRECIOS.

    ORDEN DEL USUARIO PROCESADA: "{texto_procesado}"
    (Nota interna: la orden original era "{texto_usuario}")

    REGLAS ESTRICTAS PARA ENTENDER LA ORDEN:
    1. BÚSQUEDA GENERAL: Si pide "filtrar", "buscar", "tenemos", "hay".
    2. CONSULTA DE UBICACIÓN: Si pregunta "dónde está", "ubicación", "en qué pasillo", "dónde guardo".
    3. ALTA DE STOCK (SUMAR): Si dice "agregar", "sumar", "ingresar", "cargar" unidades.
    4. BAJA DE STOCK (RESTAR): Si dice "descontar", "restar", "sacar", "bajar" unidades.
    5. CLIENTES Y PRESUPUESTOS: Si pide hacer presupuesto para alguien, detecta el nombre.
    6. CARRITO: Si pide "añadir", "meter al carrito" un producto exacto.

    REGLA CRÍTICA PARA CÓDIGOS DE PRODUCTO:
    Si el usuario dicta un código, EXTRAE SOLO LA RAÍZ. Elimina guiones, espacios y letras sueltas al final (ej: si dice "1542514-fg" o "15 42 514 f g", el id_producto/termino debe ser estrictamente "1542514"). Queremos la base más limpia posible para hacer una búsqueda amplia.

    Devuelve ÚNICAMENTE un JSON válido eligiendo UNA de estas opciones:

    OPCIÓN 1 (Búsqueda general):
    {{"accion": "buscar", "termino": "RAIZ_LIMPIA", "respuesta": "Buscando información..."}}

    OPCIÓN 2 (Consulta de Ubicación):
    {{"accion": "ubicacion", "termino": "RAIZ_LIMPIA", "respuesta": "Buscando ubicación en el depósito..."}}

    OPCIÓN 3 (Alta de Stock / Sumar):
    {{"accion": "alta", "id_producto": "RAIZ_LIMPIA", "cantidad": NUMERO, "respuesta": "Procesando alta de stock..."}}

    OPCIÓN 4 (Baja de Stock / Descontar):
    {{"accion": "baja", "id_producto": "RAIZ_LIMPIA", "cantidad": NUMERO, "respuesta": "Procesando baja de stock..."}}

    OPCIÓN 5 (Iniciar Presupuesto para Cliente):
    {{"accion": "set_cliente", "nombre_cliente": "NOMBRE", "respuesta": "Iniciando presupuesto..."}}

    OPCIÓN 6 (Añadir producto al carrito/presupuesto):
    {{"accion": "agregar_carrito", "id_producto": "RAIZ_LIMPIA", "cantidad": NUMERO, "respuesta": "Agregando al carrito..."}}
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