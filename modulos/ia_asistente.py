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
    Limpia texto dictado sin fusionar código de producto con cantidad (ej. 2105320 10).
    """
    texto_limpio = re.sub(
        r"\b(guion|guión)\b", "-", str(texto or ""), flags=re.IGNORECASE
    )

    def unir_si_dictado(match):
        fragmento = match.group(0)
        partes = fragmento.split()
        if len(partes) == 2 and partes[1].isdigit() and int(partes[1]) <= 9999:
            return fragmento
        if len(partes) == 2 and len(partes[0]) <= 3 and partes[0].isdigit():
            return fragmento
        return fragmento.replace(" ", "")

    return re.sub(r"(?:\d+\s+)+\d+", unir_si_dictado, texto_limpio)


def _limpiar_codigo_orden(termino):
    t = str(termino or "").strip().upper().replace("/", "-")
    return re.sub(r"\s+", "-", t).strip(",.;:")


def _extraer_ubicacion_orden(t):
    ubi = {}
    for key, pat in (
        ("pasillo", r"pasillo\s+(\d+)"),
        ("piso", r"piso\s+(\d+)"),
        ("modulo", r"modulo\s+(\d+)"),
        ("fila", r"fila\s+(\d+)"),
    ):
        m = re.search(pat, t)
        if m:
            ubi[key] = int(m.group(1))
    return ubi


def _inferir_vehiculos_desde_texto(texto):
    t = normalizar_texto_basico(texto)
    claves = (
        ("volkswagen", "VOLKSWAGEN"),
        ("gol", "VOLKSWAGEN"),
        ("trend", "VOLKSWAGEN"),
        ("voyage", "VOLKSWAGEN"),
        ("suran", "VOLKSWAGEN"),
        ("polo", "VOLKSWAGEN"),
        ("vento", "VOLKSWAGEN"),
        ("amarok", "VOLKSWAGEN"),
        ("peugeot", "PEUGEOT"),
        ("208", "PEUGEOT"),
        ("207", "PEUGEOT"),
        ("308", "PEUGEOT"),
        ("citroen", "CITROEN"),
        ("c4", "CITROEN"),
        ("fiat", "FIAT"),
        ("cronos", "FIAT"),
        ("argo", "FIAT"),
        ("ford", "FORD"),
        ("ka", "FORD"),
        ("ranger", "FORD"),
        ("renault", "RENAULT"),
        ("clio", "RENAULT"),
        ("sandero", "RENAULT"),
        ("chevrolet", "CHEVROLET"),
        ("onix", "CHEVROLET"),
        ("corsa", "CHEVROLET"),
    )
    found = []
    for kw, marca in claves:
        if re.search(rf"\b{re.escape(kw)}\b", t) and marca not in found:
            found.append(marca)
    return found or ["UNIVERSAL"]


def _es_carga_producto_nuevo(t):
    """True si la orden describe un producto nuevo, no sumar stock."""
    if re.search(r"\b(?:carg\w*)\s+\d+\s*unidad\w*\s+(?:del?\s+|al?\s+)?", t):
        return False
    if re.search(r"\b(?:agreg\w*|sum\w*|aument\w*)\s+\d+\s*unidad", t):
        return False
    if re.search(r"\b(pasillo|piso|modulo|fila)\b", t):
        if re.search(r"\b(carg\w*|registr\w*|ingres\w*)\b", t):
            return True
    if re.search(
        r"\b(?:carg\w*|registr\w*|ingres\w*)\s+(?:el\s+)?(?:codigo\s+)?"
        r"[\dA-Za-z]+(?:[-/][\dA-Za-z]+)?\s+[a-z]{4,}.+\d+\s*unidad",
        t,
    ):
        return True
    return False


def parse_cargar_producto_rapido(texto_usuario):
    """
    Detecta alta de producto nuevo sin Groq.
    Ej: cargame 111 embrague de gol trend 5 unidades pasillo 1 piso 0 fila 3
    """
    if not texto_usuario:
        return None
    t = normalizar_texto_basico(str(texto_usuario)).lower()

    if not re.search(r"\b(carg\w*|registr\w*|ingres\w*)\b", t):
        return None

    # Alta simple de stock existente: cargá 5 unidades del 1252 / sumá 10 al 1491
    if re.search(
        r"\b(?:agreg\w*|sum\w*|aument\w*)\s+\d+\s*unidad\w*\s+(?:del?\s+|al?\s+)?",
        t,
    ):
        return None
    if re.search(
        r"\b(?:carg\w*)\s+\d+\s*unidad\w*\s+(?:del?\s+|al?\s+)?(?:codigo\s+)?[\w/-]+\s*$",
        t,
    ):
        return None
    if not _es_carga_producto_nuevo(t):
        return None

    ubi = _extraer_ubicacion_orden(t)
    t_sin_ubi = t
    for pat in (
        r"\bpasillo\s+\d+",
        r"\bpiso\s+\d+",
        r"\bmodulo\s+\d+",
        r"\bfila\s+\d+",
    ):
        t_sin_ubi = re.sub(pat, " ", t_sin_ubi)
    t_sin_ubi = re.sub(r"\s+", " ", t_sin_ubi).strip()

    codigo = descripcion = None
    stock = 1

    m = re.search(
        r"(?:carg\w*|registr\w*|ingres\w*)\s+(?:el\s+)?(?:codigo\s+)?"
        r"([\dA-Za-z]+(?:[-/][\dA-Za-z]+)?)\s+"
        r"(.+?)\s+"
        r"(\d{1,4})\s*unidad",
        t_sin_ubi,
    )
    if m:
        codigo, descripcion, stock = m.group(1), m.group(2).strip(), int(m.group(3))
    else:
        m2 = re.search(
            r"(?:carg\w*|registr\w*|ingres\w*)\s+(?:el\s+)?(?:codigo\s+)?"
            r"([\dA-Za-z]+(?:[-/][\dA-Za-z]+)?)\s+"
            r"(.++)",
            t_sin_ubi,
        )
        if not m2:
            return None
        codigo, descripcion = m2.group(1), m2.group(2).strip()

    descripcion = re.sub(r"\s+", " ", descripcion).strip(" ,.-")
    if not descripcion or len(descripcion) < 3:
        return None

    codigo = str(codigo or "").strip().upper().replace("/", "-")
    if not codigo:
        return None

    out = {
        "accion": "cargar_producto",
        "codigo": codigo,
        "descripcion": descripcion.upper(),
        "vehiculos": _inferir_vehiculos_desde_texto(f"{descripcion} {t}"),
        "stock": max(1, int(stock)),
        "marca": "GENERICO",
    }
    for k, v in ubi.items():
        out[k] = v
    return out


def parse_alta_baja_rapido(texto_usuario):
    """Extrae alta/baja + código + cantidad sin llamar a Groq."""
    if not texto_usuario:
        return None
    t = normalizar_texto_basico(texto_usuario).lower()

    if _es_carga_producto_nuevo(t):
        return None

    es_baja = bool(re.search(r"\b(baj\w*|rest\w*|descont\w*|sac\w*)\b", t))
    es_alta = bool(re.search(r"\b(agreg\w*|sum\w*|carg\w*|aument\w*|ingres\w*)\b", t))
    if not es_alta and not es_baja:
        return None

    accion = "baja" if es_baja and not es_alta else "alta"
    if es_baja and es_alta:
        accion = "baja" if t.find("baj") < t.find("agreg") and "baj" in t else "alta"

    cod_pat = r"([\dA-Za-z]+(?:[-/][\dA-Za-z]+)?)"
    patrones = [
        r"(?:agreg\w*|sum\w*|aument\w*)\s+(\d{1,4})\s*(?:unidad\w*)?\s+(?:al?\s+)?(?:codigo\s+)?" + cod_pat,
        r"(?:carg\w*)\s+(\d{1,4})\s*unidad\w*\s+(?:del?\s+|al?\s+)?(?:codigo\s+)?" + cod_pat,
        r"(?:agreg\w*|sum\w*|carg\w*|aument\w*|baj\w*|rest\w*)\s+(?:al?\s+)?(?:codigo\s+)?"
        + cod_pat + r"\s+(\d{1,4})\s*(?:unidad)?",
        r"(?:codigo\s+)" + cod_pat + r"\s+(\d{1,4})\s*(?:unidad)?",
        r"(\d{1,4})\s*(?:unidad)?\s+(?:al?\s+)?(?:codigo\s+)" + cod_pat,
    ]
    for i, patron in enumerate(patrones):
        m = re.search(patron, t)
        if not m:
            continue
        if i in (0, 1):
            cant, cod = m.group(1), m.group(2)
        elif i == 4:
            cant, cod = m.group(1), m.group(2)
        else:
            cod, cant = m.group(1), m.group(2)
        cod = _limpiar_codigo_orden(cod)
        if cod and "-" in cod and len(cod) > 20:
            continue
        if cod:
            return {"accion": accion, "termino": cod, "cantidad": int(cant)}
    return None

def procesar_orden_voz(texto_usuario, inventario_actual=None):
    api_key = os.getenv("GROQ_API_KEY")
    if not api_key:
        try:
            api_key = st.secrets["GROQ_API_KEY"]
        except Exception:
            api_key = None

    if not api_key:
        return {"accion": "error", "respuesta": "Falta configurar la GROQ_API_KEY en los secretos."}

    carga_nueva = parse_cargar_producto_rapido(texto_usuario)
    if carga_nueva:
        return carga_nueva

    rapido = parse_alta_baja_rapido(texto_usuario)
    if rapido:
        return rapido

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
    7. ALTA / BAJA DE STOCK (producto YA EXISTE): Solo si pide sumar/restar unidades a un código existente, SIN dar descripción ni datos de producto nuevo.
       Usa "alta" o "baja". Extrae código y cantidad.
       Ejemplo: "sumá 10 al código 1491" -> alta, termino: "1491", cantidad: 10
       Ejemplo: "cargá 5 unidades del 1252" -> alta, termino: "1252", cantidad: 5
       NO uses "alta" si el usuario describe un producto nuevo (descripción, vehículo, ubicación).
    8. BÚSQUEDA FLEXIBLE: El término puede ir en singular o plural (bujes/buje). Extrae la raíz limpia.
    9. CARGAR PRODUCTO NUEVO: Si pide registrar/cargar/ingresar un código CON descripción (y opcionalmente stock, vehículo, ubicación), usa "cargar_producto".
       NO confundir con "alta" (sumar stock). Si menciona descripción del repuesto -> cargar_producto.
       Ejemplo: "cargame el código 25412 con descripción buje amortiguador para gol, 4 unidades, pasillo 2 piso 1 módulo 3 fila 4"
       -> codigo: "25412", descripcion: "buje amortiguador", vehiculos: ["VOLKSWAGEN"], stock: 4, pasillo: 2, piso: 1, modulo: 3, fila: 4
       "Para gol" / "auto gol" -> VOLKSWAGEN. Si no dice vehículo -> ["UNIVERSAL"]. Si no dice stock -> 1. Si no dice marca -> "GENERICO".

    Devuelve ÚNICAMENTE un JSON válido eligiendo UNA de estas opciones:

    OPCIÓN 1 (Búsqueda general o Consulta de Ubicación por palabras clave):
    {{"accion": "buscar", "termino": "PALABRAS CLAVE LIMPIAS ESPACIADAS"}}

    OPCIÓN 2 (Reporte de Stock Matemático):
    {{"accion": "reporte_stock", "operador": "exacto" O "menor_o_igual" O "mayor_o_igual", "cantidad": NUMERO}}

    OPCIÓN 3 (Actualizar Ubicación Exacta):
    {{"accion": "actualizar_ubicacion", "termino": "RAIZ_LIMPIA", "pasillo": NUMERO_O_NULL, "piso": NUMERO_O_NULL, "modulo": NUMERO_O_NULL, "fila": NUMERO_O_NULL}}

    OPCIÓN 4 (Alta de Stock / Sumar unidades al inventario):
    {{"accion": "alta", "termino": "CODIGO_O_NOMBRE_LIMPIO", "cantidad": NUMERO}}
    Ejemplo: "sumá 10 al 1491" -> termino: "1491", cantidad: 10
    Ejemplo: "cargá 5 unidades del código 1252t" -> termino: "1252t", cantidad: 5

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

    OPCIÓN 12 (Registrar producto nuevo en inventario — requiere código y descripción):
    {{"accion": "cargar_producto", "codigo": "CODIGO_LIMPIO", "descripcion": "DESCRIPCION", "vehiculos": ["VOLKSWAGEN"], "stock": NUMERO, "marca": "GENERICO", "pasillo": NUMERO_O_NULL, "piso": NUMERO_O_NULL, "modulo": NUMERO_O_NULL, "fila": NUMERO_O_NULL, "precio_base": NUMERO_O_NULL}}
    Ejemplo: "registrame el 25412 buje amortiguador para gol 4 unidades pasillo 2" -> codigo: "25412", descripcion: "buje amortiguador", vehiculos: ["VOLKSWAGEN"], stock: 4, pasillo: 2
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