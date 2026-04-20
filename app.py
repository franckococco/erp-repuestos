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
    return bytes(pdf.output())

st.set_page_config(page_title="Hafid IA", layout="wide")

# Agregamos la nueva pestaña para Ventas
tab_carga, tab_inventario, tab_mostrador, tab_config = st.tabs(["📸 Carga Stock", "📦 Inventario & QR", "🛒 Mostrador", "⚙️ Configuración"])

# --- PESTAÑA 1: CARGA DE STOCK Y QR MASIVO (ZIP) ---
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

    if "temp_datos" in st.session_state:
        d = st.session_state.temp_datos
        st.write(f"### Proveedor detectado: {d.get('proveedor', 'DESCONOCIDO')}")
        st.table(d.get('articulos', []))
        
        st.divider()
        st.subheader("⚙️ Opciones de Etiquetas QR para esta factura")
        
        col1, col2 = st.columns([2, 1])
        with col1:
            tamano_qr = st.slider("Tamaño de los QR (10 estándar, bajalo para cajas chicas)", min_value=5, max_value=20, value=10)
            st.caption("👈 Ajustá el tamaño y observá la vista previa a la derecha antes de confirmar.")
        with col2:
            articulos = d.get('articulos', [])
            if articulos:
                art_ejemplo = articulos[0]
                cod_ej = str(art_ejemplo.get('codigo', 'DEMO')).strip() or 'DEMO'
                desc_ej = art_ejemplo.get('descripcion', 'Repuesto')
                precio_bruto = float(art_ejemplo.get('precio_unitario', 0))
                dto_prev = obtener_reglas_proveedor(d.get('proveedor', ''))
                precio_ej = precio_bruto * (1 - (dto_prev / 100))
            else:
                cod_ej, desc_ej, precio_ej = "DEMO-123", "Repuesto de Prueba", 0.0
            
            qr_preview = generar_qr_producto(cod_ej, desc_ej, precio_ej, tamano_caja=tamano_qr)
            st.image(qr_preview, caption="Vista Previa", width=150)
        
        if st.button("💾 Confirmar Ingreso y Generar TODOS los QR", type="primary", use_container_width=True):
            proveedor = d.get('proveedor', 'DESCONOCIDO')
            articulos = d.get('articulos', [])
            
            with st.spinner("Guardando stock y creando archivo ZIP con etiquetas..."):
                registrar_ingreso_mercaderia(proveedor, articulos)
                dto = obtener_reglas_proveedor(proveedor)
                zip_buffer = BytesIO()
                
                with zipfile.ZipFile(zip_buffer, "a", zipfile.ZIP_DEFLATED, False) as zip_file:
                    for art in articulos:
                        codigo = str(art.get('codigo', '')).strip()
                        if not codigo or codigo.lower() in ["null", "none"]:
                            desc_limpia = art.get('descripcion', '').strip()
                            codigo = desc_limpia.replace(' ', '_').upper()[:15] if desc_limpia else "SIN_CODIGO"
                        
                        desc = art.get('descripcion', 'Repuesto')
                        precio_bruto = float(art.get('precio_unitario', 0))
                        precio_final = precio_bruto * (1 - (dto / 100))
                        
                        qr_img_bytes = generar_qr_producto(codigo, desc, precio_final, tamano_caja=tamano_qr)
                        nombre_archivo = f"QR_{codigo}.png".replace("/", "-").replace("\\", "-")
                        zip_file.writestr(nombre_archivo, qr_img_bytes)
                
                st.session_state.zip_listo = zip_buffer.getvalue()
                st.session_state.zip_nombre = f"Etiquetas_{proveedor}.zip"
                del st.session_state.temp_datos
                st.success("¡Stock actualizado correctamente!")
                st.rerun()

    if "zip_listo" in st.session_state:
        st.success("📦 Lote de etiquetas generado. Listo para imprimir.")
        st.download_button(label="⬇️ DESCARGAR TODAS LAS ETIQUETAS (Archivo ZIP)", data=st.session_state.zip_listo, file_name=st.session_state.zip_nombre, mime="application/zip", type="primary", use_container_width=True)
        if st.button("Limpiar pantalla para nueva factura"):
            del st.session_state.zip_listo
            st.rerun()

# --- PESTAÑA 2: INVENTARIO Y QR INDIVIDUAL ---
with tab_inventario:
    st.header("Stock en Sistema")
    inv = obtener_inventario_completo()
    if inv:
        st.dataframe(inv, use_container_width=True)
        st.divider()
        st.subheader("🖨️ Generar Etiqueta Individual")
        opciones = {f"{item['codigo']} - {item.get('descripcion', '')}": item for item in inv}
        seleccion = st.selectbox("Buscar repuesto guardado:", options=list(opciones.keys()))
        
        if seleccion:
            prod = opciones[seleccion]
            codigo = prod['codigo']
            desc = prod.get('descripcion', 'Sin descripción')
            precio = prod.get('precio_max', 0.0)
            qr_bytes = generar_qr_producto(codigo, desc, precio)
            col1, col2 = st.columns([1, 3])
            with col1: st.image(qr_bytes)
            with col2:
                st.write(f"### {desc}\n**Código Interno:** `{codigo}`\n**Precio Público:** ${precio:,.2f}")
                st.download_button(label="💾 Descargar PNG", data=qr_bytes, file_name=f"QR_{codigo}.png", mime="image/png")
    else:
        st.info("No hay productos cargados en la base de datos.")

# --- PESTAÑA 3: MOSTRADOR / VENTAS ---
with tab_mostrador:
    st.header("🛒 Punto de Venta / Presupuestos")
    
    # Identificar quién está usando el escáner para no mezclar carritos
    vendedor = st.radio("Usuario / Dispositivo:", ["Caja Principal", "Celular Depósito"], horizontal=True)
    
    # 1. OPCIÓN CÁMARA PARA LEER QR (Estable)
    st.markdown("### 📷 Escanear QR con Cámara")
    foto_qr = st.camera_input("Apuntá al QR del repuesto y sacá la foto", key=f"camara_mostrador_{vendedor}")
    
    if foto_qr:
        codigo_detectado = decodificar_qr_desde_imagen(Image.open(foto_qr))
        if codigo_detectado:
            # Extraemos el ID limpio saltando la palabra "COD:" y los saltos de línea
            id_limpio = codigo_detectado.split("\n")[0].replace("COD:", "").strip()
            exito, msj = agregar_al_carrito(vendedor, id_limpio)
            if exito:
                st.success(f"✅ Añadido: {id_limpio}")
            else:
                st.error(f"❌ {msj}")
        else:
            st.warning("No se detectó un código claro en la foto. Intentá de nuevo.")

    # 2. OPCIÓN MANUAL / PISTOLA LECTORA
    st.markdown("### ⌨️ O ingresar manualmente")
    col_input, col_btn = st.columns([3, 1])
    with col_input:
        codigo_escaneado = st.text_area("Lectura del QR:", height=68, key=f"scan_{vendedor}")
        
    with col_btn:
        st.write("")
        st.write("")
        if st.button("➕ Agregar Manual", type="primary", use_container_width=True):
            if codigo_escaneado:
                exito, msj = agregar_al_carrito(vendedor, codigo_escaneado)
                if exito:
                    st.success("✅ " + msj)
                else:
                    st.error("❌ " + msj)
            else:
                st.warning("El campo está vacío.")

    st.divider()
    
    # Mostrar el Carrito Actual
    st.subheader(f"📋 Presupuesto en tránsito - {vendedor}")
    carrito = obtener_carrito(vendedor)
    
    if carrito:
        total = sum(item.get("subtotal", 0) for item in carrito)
        st.table(carrito)
        st.markdown(f"### 💰 Total: ${total:,.2f}")
        
        # COLUMNAS PARA COBRAR, PDF Y VACIAR
        col_cobrar, col_pdf, col_vaciar = st.columns(3)
        with col_cobrar:
            if st.button("✅ Confirmar Venta (Descontar Stock)", type="primary", use_container_width=True):
                exito, msj = confirmar_venta(vendedor)
                if exito:
                    st.success(msj)
                    st.rerun()
                else:
                    st.error(msj)
        with col_pdf:
            pdf_bytes = generar_pdf_presupuesto(vendedor, carrito, total)
            st.download_button(label="📄 Imprimir PDF", data=pdf_bytes, file_name=f"Presupuesto_{vendedor}.pdf", mime="application/pdf", use_container_width=True)
        with col_vaciar:
            if st.button("🗑️ Vaciar Presupuesto", use_container_width=True):
                vaciar_carrito(vendedor)
                st.rerun()
    else:
        st.write("El carrito está vacío.")

# --- PESTAÑA 4: CONFIGURACIÓN ---
with tab_config:
    st.header("Configuración de Proveedores")
    with st.form("conf_prov"):
        col1, col2 = st.columns(2)
        p = col1.text_input("Nombre Proveedor (Ej: FIORI)").upper()
        d = col2.number_input("% Descuento", min_value=0.0, max_value=100.0, step=0.5)
        if st.form_submit_button("Guardar Regla"):
            if p:
                guardar_descuento_proveedor(p, d)
                st.success(f"Regla guardada: {p} con {d}%")
                st.rerun()
            else:
                st.error("Debes poner un nombre de proveedor.")
    st.subheader("Reglas actuales")
    st.json(obtener_todos_los_descuentos())