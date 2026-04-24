import streamlit as st
import pandas as pd
from PIL import Image
import zipfile
from io import BytesIO
from fpdf import FPDF
from datetime import datetime

from modulos.ia_vision import procesar_factura_con_ia, decodificar_qr_desde_imagen
from modulos.db_firebase import (
    registrar_ingreso_inteligente, 
    obtener_inventario_completo, 
    obtener_proveedores,
    configurar_proveedor,
    obtener_marcas,
    agregar_marca,
    agregar_al_carrito,
    obtener_carrito,
    vaciar_carrito,
    confirmar_venta,
    borrar_toda_la_base_de_datos,
    calcular_cascada_precios
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
        codigo_display = str(item.get('id', item.get('codigo', '')))
        pdf.cell(40, 10, codigo_display, 1)
        pdf.cell(80, 10, str(item['descripcion'])[:35], 1)
        pdf.cell(30, 10, str(item['cantidad']), 1)
        pdf.cell(40, 10, f"${item['subtotal']:,.2f}", 1)
        pdf.ln()
    
    pdf.ln(5)
    pdf.set_font("Helvetica", "B", 12)
    pdf.cell(190, 10, f"TOTAL: ${total:,.2f}", new_x="LMARGIN", new_y="NEXT", align="R")
    
    return bytes(pdf.output())

st.set_page_config(page_title="Hafid IA", layout="wide")

# Inicializar sesión para datos temporales
if "temp_datos" not in st.session_state:
    st.session_state.temp_datos = None

tab_carga, tab_inventario, tab_mostrador, tab_config = st.tabs(["📸 Carga Stock", "📦 Inventario & QR", "🛒 Mostrador", "⚙️ Configuración"])

# --- PESTAÑA 1: CARGA DE STOCK ---
with tab_carga:
    st.header("Escanear Factura")
    
    st.write("**Condición de Pago de esta factura:**")
    condicion_pago = st.radio("Define el recargo financiero a aplicar:", ["Contado", "30 Días"], horizontal=True)
    st.divider()

    foto = st.camera_input("Tomar foto", key="camara")
    archivo = st.file_uploader("O subir imagen", type=["png", "jpg", "jpeg"])
    img = foto if foto else archivo

    if img:
        if st.button("Procesar Factura", type="primary"):
            with st.spinner("Leyendo factura con IA..."):
                try:
                    datos = procesar_factura_con_ia(Image.open(img))
                    if datos:
                        st.session_state.temp_datos = datos
                        st.rerun()
                except Exception as e:
                    st.error(f"❌ Error al procesar la factura: {e}")

    if st.session_state.temp_datos:
        d = st.session_state.temp_datos
        if not isinstance(d, dict):
            d = {}
            
        cuit_detectado = "".join(filter(str.isdigit, str(d.get('cuit_proveedor', '0'))))
        st.write(f"### Proveedor detectado: {d.get('proveedor', 'DESCONOCIDO')} (CUIT: {cuit_detectado})")
        
        articulos = d.get('articulos', [])
        st.table(articulos)
        
        st.divider()
        st.subheader("⚙️ Opciones de Etiquetas QR para esta factura")
        
        col1, col2 = st.columns([2, 1])
        with col1:
            tamano_qr = st.slider("Tamaño de los QR (10 estándar)", min_value=5, max_value=20, value=10)
        with col2:
            if articulos:
                art_ejemplo = articulos[0]
                if not isinstance(art_ejemplo, dict):
                    art_ejemplo = {}
                    
                cod_ej = str(art_ejemplo.get('codigo', 'DEMO')).strip() or 'DEMO'
                marca_ej = str(art_ejemplo.get('marca', 'GENERICO')).strip().upper()
                desc_ej = f"{art_ejemplo.get('descripcion', 'Repuesto')} ({marca_ej})"
                precio_bruto = float(art_ejemplo.get('precio_unitario', 0))
                
                provs = obtener_proveedores() or {}
                recargo_prev = 0.0
                if cuit_detectado in provs:
                    datos_prov = provs[cuit_detectado]
                    if isinstance(datos_prov, dict):
                        condiciones_prev = datos_prov.get('condiciones', {})
                        if isinstance(condiciones_prev, dict):
                            recargo_prev = float(condiciones_prev.get(condicion_pago, 0.0))
                
                calculos = calcular_cascada_precios(precio_bruto, recargo_prev)
                precio_etiqueta = calculos['precio_venta']
                
                qr_preview = generar_qr_producto(cod_ej, desc_ej, precio_etiqueta, tamano_caja=tamano_qr)
                st.image(qr_preview, caption="Vista Previa Público", width=150)
        
        if st.button("💾 Confirmar Ingreso y Generar TODOS los QR", type="primary", use_container_width=True):
            exito, msg = registrar_ingreso_inteligente(d, condicion_pago)
            
            if exito:
                prov_id = cuit_detectado
                provs = obtener_proveedores() or {}
                recargo = 0.0
                if prov_id in provs:
                    datos_prov = provs[prov_id]
                    if isinstance(datos_prov, dict):
                        condiciones_prov = datos_prov.get('condiciones', {})
                        if isinstance(condiciones_prov, dict):
                            recargo = float(condiciones_prov.get(condicion_pago, 0.0))

                zip_buffer = BytesIO()
                with zipfile.ZipFile(zip_buffer, "a", zipfile.ZIP_DEFLATED, False) as zip_file:
                    for art in articulos:
                        if not isinstance(art, dict):
                            continue
                            
                        codigo_base = str(art.get('codigo', '')).strip()
                        marca = str(art.get('marca', 'GENERICO')).strip().upper()
                        if not codigo_base or codigo_base.lower() in ["null", "none"]:
                            desc_limpia = str(art.get('descripcion', '')).strip()
                            codigo_base = desc_limpia.replace(' ', '_').upper()[:15] if desc_limpia else "SIN_CODIGO"
                        
                        id_producto = f"{codigo_base}_{marca}"
                        precio_f = float(art.get('precio_unitario', 0))
                        calc = calcular_cascada_precios(precio_f, recargo)
                        
                        desc_qr = f"{art.get('descripcion', 'Repuesto')} ({marca})"
                        qr_img_bytes = generar_qr_producto(id_producto, desc_qr, calc['precio_venta'], tamano_caja=tamano_qr)
                        zip_file.writestr(f"QR_{id_producto}.png", qr_img_bytes)
                
                st.session_state.zip_listo = zip_buffer.getvalue()
                st.session_state.zip_nombre = f"Etiquetas_{prov_id}.zip"
                st.session_state.temp_datos = None
                st.success(msg)
                st.rerun()
            else:
                st.error(msg)

    if "zip_listo" in st.session_state:
        st.success("📦 Lote de etiquetas generado y Stock Actualizado.")
        st.download_button(label="⬇️ DESCARGAR ZIP", data=st.session_state.zip_listo, file_name=st.session_state.zip_nombre, mime="application/zip", type="primary", use_container_width=True)
        if st.button("Limpiar pantalla"):
            del st.session_state.zip_listo
            st.rerun()

# --- PESTAÑA 2: INVENTARIO ---
with tab_inventario:
    st.header("Stock en Sistema")
    inv = obtener_inventario_completo()
    
    if inv:
        df = pd.DataFrame(inv)
        cols_deseadas = ['id', 'marca', 'descripcion', 'stock', 'ultimo_costo_base', 'precio_interno', 'precio_venta']
        cols_existentes = [c for c in cols_deseadas if c in df.columns]
        
        # BUSCADOR DE INVENTARIO
        busqueda_inv = st.text_input("🔍 Buscar repuesto (Código, Marca o Descripción):", placeholder="Ej: Correa, Bosch, 1234...")
        
        if busqueda_inv:
            termino = busqueda_inv.lower()
            # Filtra el DataFrame buscando coincidencias en cualquier columna
            df_filtrado = df[df.apply(lambda row: row.astype(str).str.lower().str.contains(termino).any(), axis=1)]
        else:
            df_filtrado = df
            
        st.dataframe(df_filtrado[cols_existentes], use_container_width=True, hide_index=True)
        
        st.divider()
        opciones = {f"{item['id']} - {item.get('descripcion', '')}": item for item in inv}
        seleccion = st.selectbox("Buscar repuesto para etiqueta individual:", options=list(opciones.keys()))
        if seleccion:
            prod = opciones[seleccion]
            qr_ind = generar_qr_producto(prod['id'], prod.get('descripcion',''), float(prod.get('precio_venta', 0.0)))
            st.image(qr_ind, width=150)
            st.download_button("Descargar PNG", qr_ind, f"QR_{prod['id']}.png", "image/png")
    else:
        st.info("El inventario está vacío.")

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
            else: st.error(msj)
        
        pdf_bytes = generar_pdf_presupuesto(vendedor, carrito, total)
        col_pdf.download_button("📄 Imprimir PDF", pdf_bytes, f"Presupuesto_{vendedor}.pdf", "application/pdf", use_container_width=True)
        
        if col_vac.button("🗑️ Vaciar", use_container_width=True):
            vaciar_carrito(vendedor)
            st.rerun()

# --- PESTAÑA 4: CONFIGURACIÓN ---
with tab_config:
    st.header("Configuración del Sistema")
    
    with st.expander("⚠️ ZONA DE PELIGRO - Borrar Base de Datos"):
        st.warning("Esto borrará todo el inventario, carritos y el historial de facturas. Es irreversible.")
        if st.checkbox("Entiendo los riesgos, habilitar borrado"):
            if st.button("💥 BORRAR TODA LA BASE DE DATOS", type="primary"):
                exito, msg = borrar_toda_la_base_de_datos()
                if exito:
                    st.success(msg)
                    st.rerun()
                else:
                    st.error(msg)
    
    st.divider()
    
    tab_prov, tab_marcas = st.tabs(["🏭 Proveedores y Recargos", "🏷️ Marcas"])
    
    with tab_prov:
        st.subheader("Alta y Edición de Proveedores")
        with st.form("conf_prov"):
            col1, col2 = st.columns(2)
            nombre_prov = col1.text_input("Nombre Proveedor (Ej: Filtros Juan)").upper()
            cuit_prov = col2.text_input("CUIT (Solo números)")
            
            st.write("Recargos Financieros (%)")
            col3, col4 = st.columns(2)
            rec_contado = col3.number_input("Pago Contado (%)", min_value=0.0, value=0.0, step=1.0)
            rec_30 = col4.number_input("Pago a 30 Días (%)", min_value=0.0, value=15.0, step=1.0)
            
            if st.form_submit_button("Guardar Proveedor"):
                if nombre_prov and cuit_prov:
                    configurar_proveedor(nombre_prov, cuit_prov, rec_contado, rec_30)
                    st.success(f"Proveedor {nombre_prov} guardado.")
                    st.rerun()
                else:
                    st.error("El nombre y el CUIT son obligatorios.")
        
        st.write("### Directorio de Proveedores")
        provs = obtener_proveedores() or {}
        if provs:
            datos_tabla = []
            for cuit, datos_prov in provs.items():
                if not isinstance(datos_prov, dict):
                    datos_prov = {}
                condiciones = datos_prov.get('condiciones', {})
                if not isinstance(condiciones, dict):
                    condiciones = {}
                    
                datos_tabla.append({
                    "Proveedor": datos_prov.get("nombre", ""),
                    "CUIT": cuit,
                    "Contado": f"{condiciones.get('Contado', 0)}%",
                    "30 Días": f"{condiciones.get('30 Días', 0)}%"
                })
            st.dataframe(datos_tabla, use_container_width=True)
            
    with tab_marcas:
        st.subheader("Gestión de Marcas")
        col_m, col_b = st.columns([3, 1])
        with col_m:
            nueva_marca = st.text_input("Agregar nueva marca (Ej: BOSCH, SKF)", label_visibility="collapsed")
        with col_b:
            if st.button("Guardar Marca", use_container_width=True):
                if nueva_marca:
                    agregar_marca(nueva_marca)
                    st.success("Agregada")
                    st.rerun()
        
        st.divider()
        marcas_actuales = obtener_marcas()
        if marcas_actuales:
            st.write("**Marcas registradas:**")
            st.write(", ".join(marcas_actuales))
        else:
            st.info("Aún no hay marcas cargadas.")