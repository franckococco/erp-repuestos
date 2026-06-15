"""IA de voz/texto exclusiva del mostrador (ventas, cliente, facturación)."""
import json
import os
import re

import streamlit as st
from dotenv import load_dotenv
from groq import Groq  # type: ignore

from modulos.ia_asistente import preprocesar_texto_usuario

load_dotenv(override=True)

FORMAS_PAGO = ("Contado", "Transferencia", "Tarjeta", "Cheque", "MercadoPago")


def normalizar_forma_pago(texto):
    if not texto:
        return "Contado"
    t = str(texto).strip().lower()
    if "transfer" in t:
        return "Transferencia"
    if "tarjeta" in t:
        return "Tarjeta"
    if "cheque" in t:
        return "Cheque"
    if "mercado" in t or "mp" == t:
        return "MercadoPago"
    if "contado" in t or "efectivo" in t or "cash" in t:
        return "Contado"
    for fp in FORMAS_PAGO:
        if fp.lower() in t:
            return fp
    return "Contado"


def es_confirmacion_usuario(texto):
    t = str(texto or "").strip().lower()
    return bool(re.search(
        r"\b(si|sí|dale|confirmá|confirmar|confirmalo|ok|de acuerdo|hacelo|"
        r"procedé|procede|listo|avanzá|avanza)\b",
        t,
    ))


def es_cancelacion_usuario(texto):
    t = str(texto or "").strip().lower()
    return bool(re.search(r"\b(no|cancelá|cancelar|anulá|anular|detener|pará)\b", t))


def procesar_orden_mostrador(texto_usuario):
    api_key = os.getenv("GROQ_API_KEY")
    if not api_key:
        try:
            api_key = st.secrets["GROQ_API_KEY"]
        except Exception:
            api_key = None

    if not api_key:
        return {"accion": "error", "respuesta": "Falta configurar la GROQ_API_KEY en los secretos."}

    if es_confirmacion_usuario(texto_usuario):
        return {"accion": "confirmar_pendiente"}
    if es_cancelacion_usuario(texto_usuario):
        return {"accion": "cancelar_pendiente"}

    client = Groq(api_key=api_key)
    texto_procesado = preprocesar_texto_usuario(texto_usuario)

    prompt = f"""
    Eres el asistente de MOSTRADOR / CAJA de "Hafid Repuestos".
    Solo ayudás a armar presupuestos, elegir cliente, forma de pago, guardar presupuesto,
    confirmar venta o emitir factura fiscal. NO gestionás depósito ni ubicaciones.

    ORDEN DEL USUARIO: "{texto_procesado}"

    REGLAS:
    1. Agregar al carrito: "poneme", "agregá", "sumá al presupuesto" -> agregar_carrito con termino limpio y cantidad.
    2. Buscar/consultar precio/stock sin agregar -> buscar.
    3. Cliente registrado por nombre -> set_cliente (solo nombre, sin CUIT).
    4. Consumidor final / particular sin nombre -> consumidor_final.
    5. Forma de pago -> set_forma_pago (Contado, Transferencia, Tarjeta, Cheque, MercadoPago).
    6. Guardar presupuesto -> guardar_presupuesto (nota opcional).
    7. Cobrar / confirmar venta SIN factura fiscal -> confirmar_venta.
    8. Facturar / emitir factura ARCA/AFIP -> facturar.
    9. Vaciar carrito / limpiar presupuesto -> vaciar_carrito.
    10. Limpia términos basura al buscar o agregar productos.

    Devolvé SOLO JSON con UNA acción:

    {{"accion": "agregar_carrito", "termino": "RAIZ", "cantidad": NUMERO}}
    {{"accion": "buscar", "termino": "RAIZ"}}
    {{"accion": "set_cliente", "nombre_cliente": "NOMBRE"}}
    {{"accion": "consumidor_final"}}
    {{"accion": "set_forma_pago", "forma_pago": "Contado|Transferencia|Tarjeta|Cheque|MercadoPago"}}
    {{"accion": "guardar_presupuesto", "nota": "TEXTO_O_VACIO"}}
    {{"accion": "confirmar_venta"}}
    {{"accion": "facturar"}}
    {{"accion": "vaciar_carrito"}}
    {{"accion": "consulta", "respuesta": "Texto si no entendiste"}}
    """

    try:
        chat_completion = client.chat.completions.create(
            messages=[{"role": "user", "content": prompt}],
            model="llama-3.3-70b-versatile",
            temperature=0.0,
            response_format={"type": "json_object"},
        )
        texto = chat_completion.choices[0].message.content.strip()  # type: ignore
        texto = texto.replace("```json", "").replace("```", "").strip()
        return json.loads(texto)
    except Exception as e:
        return {"accion": "error", "respuesta": f"Error en lectura de IA: {str(e)}"}
