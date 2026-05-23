import os
import json
import re
from groq import Groq # type: ignore
import streamlit as st
from dotenv import load_dotenv

load_dotenv(override=True)

def preprocesar_texto_usuario(texto):
    """
    Limpia el texto inicial para ayudar a la IA con los números dictados.
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
    Eres el asistente inteligente de depósito y mostrador de repuestos.
    TU ÚNICO TRABAJO ES EXTRAER LA INTENCIÓN DEL USUARIO, NORMALIZAR LA BÚSQUEDA Y DEFINIR OPERADORES MATEMÁTICOS.

    ORDEN DEL USUARIO: "{texto_procesado}"

    REGLAS ESTRICTAS PARA ENTENDER LA ORDEN:
    1. BÚSQUEDA Y CONSULTA (Vehículos, Marcas, Repuestos): Extrae todas las palabras clave relevantes. Ignora palabras de relleno como "para", "de", "el", "la", "un". 
       Ej: "buscame filtro de aire para peugeot" -> el termino debe ser "filtro aire peugeot".
    2. REPORTE DE STOCK (MATEMÁTICA ESTRICTA): 
       - Si el usuario pide los que tienen una cantidad específica (ej: "los que tienen 3"), el operador es "exacto".
       - Si pide por debajo de una cantidad, "punto mínimo" o "faltantes", el operador es "menor_o_igual".
       - Si no especifica cantidad en un reporte de mínimos, asume 3.
    3. RELEVAMIENTO (UBICACIÓN): Si menciona pasillo, piso, módulo o fila, extrae los números. Lo que no mencione, es null.
    4. CÓDIGOS ESPECÍFICOS: Para sumar, restar o vender, extrae el código lo más limpio posible (la raíz). Ej: "15 42 514 f g" -> "1542514".
    5. PROVEEDORES: Si el usuario pide filtrar, mostrar o buscar repuestos de un proveedor específico, extrae solo el nombre del proveedor.

    Devuelve ÚNICAMENTE un JSON válido eligiendo UNA de estas opciones:

    OPCIÓN 1 (Búsqueda general o Consulta de Ubicación por palabras clave):
    {{"accion": "buscar", "termino": "PALABRAS CLAVE LIMPIAS ESPACIADAS"}}

    OPCIÓN 2 (Reporte de Stock Matemático):
    {{"accion": "reporte_stock", "operador": "exacto" O "menor_o_igual", "cantidad": NUMERO}}

    OPCIÓN 3 (Actualizar Ubicación Exacta):
    {{"accion": "actualizar_ubicacion", "termino": "RAIZ_LIMPIA", "pasillo": NUMERO_O_NULL, "piso": NUMERO_O_NULL, "modulo": NUMERO_O_NULL, "fila": NUMERO_O_NULL}}

    OPCIÓN 4 (Alta de Stock / Sumar):
    {{"accion": "alta", "termino": "RAIZ_LIMPIA", "cantidad": NUMERO}}

    OPCIÓN 5 (Baja de Stock / Descontar):
    {{"accion": "baja", "termino": "RAIZ_LIMPIA", "cantidad": NUMERO}}

    OPCIÓN 6 (Iniciar Presupuesto para Cliente):
    {{"accion": "set_cliente", "nombre_cliente": "NOMBRE"}}

    OPCIÓN 7 (Añadir producto al carrito/presupuesto):
    {{"accion": "agregar_carrito", "termino": "RAIZ_LIMPIA", "cantidad": NUMERO}}

    OPCIÓN 8 (Filtrar o listar repuestos por Proveedor):
    {{"accion": "filtrar_proveedor", "proveedor": "NOMBRE DEL PROVEEDOR LIMPIO"}}
    """

    try:
        chat_completion = client.chat.completions.create(
            messages=[{"role": "user", "content": prompt}],
            model="llama-3.3-70b-versatile", 
            temperature=0.0,
            response_format={"type": "json_object"} 
        )
        
        texto = chat_completion.choices[0].message.content.strip() # type: ignore
        
        # Limpieza segura para evitar errores de sintaxis al copiar/pegar
        texto = texto.replace("```json", "").replace("```", "").strip()
        
        return json.loads(texto)
        
    except Exception as e:
        return {"accion": "error", "respuesta": f"Error en lectura de IA: {str(e)}"}