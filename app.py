import streamlit as st
from PIL import Image
import zipfile
from io import BytesIO
from fpdf import FPDF
from datetime import datetime

from modulos.ia_vision import procesar_factura_con_ia, decodificar_qr_desde_imagen
from modulos.db_firebase import (
    registrar_ingreso_mercaderia, 
    obtener_inventario_completo, 
    obtener_todos_los_descuentos,
    guardar_descuento_proveedor,
    obtener_reglas_proveedor,
    agregar_al_carrito,
    obtener_carrito,
    vaciar_carrito,
    confirmar_venta
)
from modulos.generador_qr import generar_qr_producto

# --- FUNCIÓN PARA EL GENERADOR DE PDF ---
def generar_pdf_presupuesto(vendedor, items, total):
    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("Helvetica", "B", 16)
    pdf.cell(190, 10, "PRESUPUESTO - HAFID REPUESTOS", new_x="LMARGIN", new_y="NEXT", align="C")
    
    pdf.set_font("Helvetica", "", 10)
    pdf.cell(190, 10, f"Vendedor: {vendedor} | Fecha: {datetime.now().strftime('%d/%m/%Y %H:%M')}", new_x="LMARGIN", new_y="NEXT", align="C")
    pdf.ln(10)
    
    pdf.set_font("Helvetica", "B", 10)
    pdf.cell(40, 10, "Codigo", 1)
    pdf.cell(80, 10, "Descripcion", 1)
    pdf.cell(30, 10, "Cant.", 1)
    pdf.cell(40, 10, "Subtotal", 1)
    pdf.ln()
    
    pdf.set_font("Helvetica", "", 10)
    for item in items:
        pdf.cell(40, 10, str(item['codigo']), 1)
        pdf.cell(80, 10, str(item['descripcion'])[:35], 1)
        pdf.cell(30, 10, str(item['cantidad']), 1)
        pdf.cell(40, 10, f"${item['subtotal']:,.2f}", 1)
        pdf.ln()
    
    pdf.ln(5)
    pdf.set_font("Helvetica", "B", 12)
    pdf.cell(190, 10, f"TOTAL: ${total:,.2f}", new_x="LMARGIN", new_y="NEXT", align="R")
    
    # FIX: Retornamos bytes para evitar error de bytearray en st.download_button
    return bytes(pdf.output())

st.set_page_config(page_title="Hafid IA", layout="wide")

# Inicializar sesión para datos temporales
if "temp_datos" not in st.session_state:
    st.session_state.temp_datos = None

tab_carga, tab_inventario, tab_mostrador, tab_config = st.tabs(["📸 Carga Stock", "📦 Inventario & QR", "🛒 Mostrador", "⚙️ Configuración"])

# --- PESTAÑA 1: CARGA DE STOCK ---
with tab_carga:
    st.header("Escanear Factura")
    foto = st.camera_input("Tomar foto", key="camara")
    archivo = st.file_uploader("O subir imagen", type=["png", "jpg", "jpeg"])
    img = foto if foto else archivo

    if img:
        if st.button("Procesar Factura"):
            with st.spinner("Leyendo factura con IA..."):
                datos = procesar_factura_con_ia(Image.open(img))
                if datos:
                    st.session_state.temp_datos = datos
                    st.rerun()

    if st.session_state.temp_datos:
        d = st.session_state.temp_datos
        st.write(f"### Proveedor detectado: {d.get('proveedor', 'DESCONOCIDO')}")
        st.table(d.get('articulos', []))
        
        st.divider()
        st.subheader("⚙️ Opciones de Etiquetas QR para esta factura")
        
        col1, col2 = st.columns([2, 1])
        with col1:
            tamano_qr = st.slider("Tamaño de los QR (10 estándar)", min_value=5, max_value=20, value=10)
        with col2:
            articulos = d.get('articulos', [])
            if articulos:
                art_ejemplo = articulos[0]
                cod_ej = str(art_ejemplo.get('codigo', 'DEMO')).strip() or 'DEMO'
                desc_ej = art_ejemplo.get('descripcion', 'Repuesto')
                precio_bruto = float(art_ejemplo.get('precio_unitario', 0))
                dto_prev = obtener_reglas_proveedor(d.get('proveedor', ''))
                precio_ej = precio_bruto * (1 - (dto_prev / 100))
                qr_preview = generar_qr_producto(cod_ej, desc_ej, precio_ej, tamano_caja=tamano_qr)
                st.image(qr_preview, caption="Vista Previa", width=150)
        
        if st.button("💾 Confirmar Ingreso y Generar TODOS los QR", type="primary", use_container_width=True):
            proveedor = d.get('proveedor', 'DESCONOCIDO')
            articulos = d.get('articulos', [])
            
            registrar_ingreso_mercaderia(proveedor, articulos)
            dto = obtener_reglas_proveedor(proveedor)
            zip_buffer = BytesIO()
            
            with zipfile.ZipFile(zip_buffer, "a", zipfile.ZIP_DEFLATED, False) as zip_file:
                for art in articulos:
                    codigo = str(art.get('codigo', '')).strip()
                    if not codigo or codigo.lower() in ["null", "none"]:
                        desc_limpia = art.get('descripcion', '').strip()
                        codigo = desc_limpia.replace(' ', '_').upper()[:15] if desc_limpia else "SIN_CODIGO"
                    
                    precio_f = float(art.get('precio_unitario', 0)) * (1 - (dto / 100))
                    qr_img_bytes = generar_qr_producto(codigo, art.get('descripcion', 'Repuesto'), precio_f, tamano_caja=tamano_qr)
                    zip_file.writestr(f"QR_{codigo}.png", qr_img_bytes)
            
            st.session_state.zip_listo = zip_buffer.getvalue()
            st.session_state.zip_nombre = f"Etiquetas_{proveedor}.zip"
            st.session_state.temp_datos = None
            st.rerun()

    if "zip_listo" in st.session_state:
        st.success("📦 Lote de etiquetas generado.")
        st.download_button(label="⬇️ DESCARGAR ZIP", data=st.session_state.zip_listo, file_name=st.session_state.zip_nombre, mime="application/zip", type="primary", use_container_width=True)
        if st.button("Limpiar pantalla"):
            del st.session_state.zip_listo
            st.rerun()

# --- PESTAÑA 2: INVENTARIO ---
with tab_inventario:
    st.header("Stock en Sistema")
    inv = obtener_inventario_completo()
    if inv:
        st.dataframe(inv, use_container_width=True)
        st.divider()
        opciones = {f"{item['codigo']} - {item.get('descripcion', '')}": item for item in inv}
        seleccion = st.selectbox("Buscar repuesto para etiqueta individual:", options=list(opciones.keys()))
        if seleccion:
            prod = opciones[seleccion]
            qr_ind = generar_qr_producto(prod['codigo'], prod.get('descripcion',''), prod.get('precio_max', 0.0))
            st.image(qr_ind, width=150)
            st.download_button("Descargar PNG", qr_ind, f"QR_{prod['codigo']}.png", "image/png")

# --- PESTAÑA 3: MOSTRADOR ---
with tab_mostrador:
    st.header("🛒 Punto de Venta / Presupuestos")
    vendedor = st.radio("Usuario / Dispositivo:", ["Caja Principal", "Celular Depósito"], horizontal=True)
    
    foto_qr = st.camera_input("Escanear QR con Cámara", key=f"cam_{vendedor}")
    if foto_qr:
        cod_detectado = decodificar_qr_desde_imagen(Image.open(foto_qr))
        if cod_detectado:
            id_limpio = cod_detectado.split("\n")[0].replace("COD:", "").strip()
            exito, msj = agregar_al_carrito(vendedor, id_limpio)
            if exito: st.success(f"Añadido: {id_limpio}")
            else: st.error(msj)

    codigo_manual = st.text_area("Lectura del QR (Ingreso Manual o Pistola):", height=68, key=f"scan_{vendedor}")
    if st.button("➕ Agregar Artículo"):
        if codigo_manual:
            exito, msj = agregar_al_carrito(vendedor, codigo_manual)
            if exito: st.success(msj); st.rerun()
            else: st.error(msj)

    st.divider()
    carrito = obtener_carrito(vendedor)
    if carrito:
        total = sum(item.get("subtotal", 0) for item in carrito)
        st.table(carrito)
        st.write(f"### Total: ${total:,.2f}")
        
        col_cob, col_pdf, col_vac = st.columns(3)
        if col_cob.button("✅ Confirmar Venta", type="primary", use_container_width=True):
            exito, msj = confirmar_venta(vendedor)
            if exito: st.success(msj); st.rerun()
        
        pdf_bytes = generar_pdf_presupuesto(vendedor, carrito, total)
        col_pdf.download_button("📄 Imprimir PDF", pdf_bytes, f"Presupuesto_{vendedor}.pdf", "application/pdf", use_container_width=True)
        
        if col_vac.button("🗑️ Vaciar", use_container_width=True):
            vaciar_carrito(vendedor)
            st.rerun()

# --- PESTAÑA 4: CONFIGURACIÓN ---
with tab_config:
    st.header("Configuración de Proveedores")
    with st.form("conf_prov"):
        col1, col2 = st.columns(2)
        p = col1.text_input("Nombre Proveedor").upper()
        d = col2.number_input("% Descuento", 0.0, 100.0, step=0.5)
        if st.form_submit_button("Guardar Regla"):
            if p:
                guardar_descuento_proveedor(p, d)
                st.success("Guardado.")
                st.rerun()
    st.json(obtener_todos_los_descuentos())