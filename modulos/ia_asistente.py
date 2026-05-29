import os
import json
import re
import unicodedata
from groq import Groq # type: ignore
import streamlit as st
from dotenv import load_dotenv

load_dotenv(override=True)

def normalizar_texto_basico(texto):
    if not texto:
        return ""
    texto = str(texto).lower()
    texto = unicodedata.normalize('NFD', texto)
    return ''.join(c for c in texto if unicodedata.category(c) != 'Mn')

def es_consulta_mayor_o_igual(texto):
    texto_norm = normalizar_texto_basico(texto)
    if re.search(r'\b(mas de|al menos|como minimo|mayores a|mayor a|superior a|por encima de|o mas|mayor o igual|mayor que|>=|\+)\b', texto_norm):
        if not re.search(r'\b(menos de|hasta|a lo sumo|como maximo|menor o igual|menor que|<=)\b', texto_norm):
            return True
    return False

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
    Eres el asistente inteligente del depósito "Hafid Repuestos".
    TU ÚNICO TRABAJO ES EXTRAER LA INTENCIÓN DEL USUARIO, NORMALIZAR LA BÚSQUEDA Y DEFINIR OPERADORES MATEMÁTICOS.

    ORDEN DEL USUARIO: "{texto_procesado}"

    REGLAS ESTRICTAS PARA ENTENDER LA ORDEN (MUY IMPORTANTE):
    1. Si el usuario pide buscar, consultar stock, o pregunta "¿cuánto hay de...?", la acción obligatoria es "buscar". NO uses "reporte_stock" a menos que pida un reporte general de cantidades.
    2. LIMPIEZA DEL TÉRMINO: Extrae SOLO la raíz del repuesto o el código. ELIMINA TOTALMENTE palabras basura como: "buscame", "el", "código", "decime", "stock", "de", "cuanto", "hay", "para", "quiero", "saber".
       Ejemplo 1: "decime el stock del código 1252t" -> termino: "1252t"
       Ejemplo 2: "buscame rotula para ranger" -> termino: "rotula ranger"
    3. REPORTE DE STOCK (MATEMÁTICA ESTRICTA): 
       - Si pide los que tienen una cantidad específica (ej: "los que tienen 3"), operador: "exacto".
       - Si pide por debajo de una cantidad, "punto mínimo" o "faltantes", operador: "menor_o_igual".
       - Si pide "3 o más", "al menos 3" o "más de 3", operador: "mayor_o_igual".
       - Si no especifica cantidad en un reporte, asume 3.
    4. RELEVAMIENTO (UBICACIÓN): Si menciona pasillo, piso, módulo o fila, extrae los números. Lo que no mencione, es null.
    5. CÓDIGOS ESPECÍFICOS: Para sumar, restar o vender, extrae el código lo más limpio posible.
    6. PROVEEDORES: Si pide filtrar por proveedor, extrae solo la raíz del nombre (ej: "expoyer", no "EXPOYER S.A." ni "productos de").

    Devuelve ÚNICAMENTE un JSON válido eligiendo UNA de estas opciones:

    OPCIÓN 1 (Búsqueda general o Consulta de Ubicación por palabras clave):
    {{"accion": "buscar", "termino": "PALABRAS CLAVE LIMPIAS ESPACIADAS"}}

    OPCIÓN 2 (Reporte de Stock Matemático):
    {{"accion": "reporte_stock", "operador": "exacto" O "menor_o_igual" O "mayor_o_igual", "cantidad": NUMERO}}

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

    OPCIÓN 9 (Agregar texto a la descripción de un código):
    {{"accion": "agregar_descripcion", "codigo": "CODIGO_LIMPIO", "texto": "TEXTO A SUMAR AL FINAL"}}
    Ejemplo: "al código 1252t agregale a la descripción filtro de aceite" -> codigo: "1252t", texto: "filtro de aceite"

    OPCIÓN 10 (Cambiar la marca de un código con UNA sola variante):
    {{"accion": "cambiar_marca", "codigo": "CODIGO_LIMPIO", "marca_nueva": "MARCA_NUEVA"}}
    Ejemplo: "cambiá la marca del código 1491 a SKF" -> codigo: "1491", marca_nueva: "SKF"
    Ejemplo: "al 1491 poneme marca FRAM" -> codigo: "1491", marca_nueva: "FRAM"
    Solo usar si el usuario pide cambiar/corregir/renombrar la MARCA de un código. NO confundir con buscar stock.

    OPCIÓN 11 (Cambiar vehículos compatibles de un código maestro):
    {{"accion": "cambiar_vehiculos", "codigo": "CODIGO_LIMPIO", "modo": "reemplazar" O "agregar" O "quitar", "vehiculos": ["PEUGEOT", "VOLKSWAGEN"]}}
    Ejemplo: "al 1491 poneme vehículos Peugeot y Volkswagen" -> codigo: "1491", modo: "reemplazar", vehiculos: ["PEUGEOT", "VOLKSWAGEN"]
    Ejemplo: "al código 1491 agregale Citroën" -> codigo: "1491", modo: "agregar", vehiculos: ["CITROEN"]
    Ejemplo: "al 1491 sacale Ford" -> codigo: "1491", modo: "quitar", vehiculos: ["FORD"]
    Vehículos válidos: UNIVERSAL, VOLKSWAGEN, PEUGEOT, CITROEN, FIAT, FORD, RENAULT, CHEVROLET.
    Si no indica modo, usar "reemplazar". NO confundir con buscar stock ni con cambiar marca.
    """

    try:
        chat_completion = client.chat.completions.create(
            messages=[{"role": "user", "content": prompt}],
            model="llama-3.3-70b-versatile", 
            temperature=0.0,
            response_format={"type": "json_object"} 
        )
        
        texto = chat_completion.choices[0].message.content.strip() # type: ignore
        texto = texto.replace("```json", "").replace("```", "").strip()
        resultado = json.loads(texto)

        if isinstance(resultado, dict) and resultado.get("accion") == "reporte_stock":
            operador = str(resultado.get("operador", "") or "").strip().lower()
            if operador not in {"exacto", "menor_o_igual", "mayor_o_igual"}:
                operador = "menor_o_igual"

            # Corrección del bug: Usamos directamente la validación sin llamar a la función inexistente
            if operador == "exacto":
                resultado["operador"] = operador
            elif es_consulta_mayor_o_igual(texto_usuario):
                resultado["operador"] = "mayor_o_igual"
            else:
                resultado["operador"] = operador

        return resultado
        
    except Exception as e:
        return {"accion": "error", "respuesta": f"Error en lectura de IA: {str(e)}"}