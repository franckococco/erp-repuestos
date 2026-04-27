import streamlit as st
import pandas as pd
from PIL import Image
import zipfile
from io import BytesIO
from fpdf import FPDF
from datetime import datetime

from modulos.ia_vision import procesar_factura_con_ia, decodificar_qr_desde_imagen
from modulos.ia_asistente import procesar_orden_voz
from modulos.db_firebase import (
    registrar_ingreso_inteligente, 
    obtener_inventario_completo, 
    obtener_proveedores,
    configurar_proveedor,
    eliminar_proveedor,
    obtener_marcas,
    agregar_marca,
    eliminar_marca,
    agregar_al_carrito,
    obtener_carrito,
    vaciar_carrito,
    confirmar_venta,
    borrar_toda_la_base_de_datos,
    calcular_cascada_precios,
    registrar_merma,
    registrar_aumento_stock,
    alta_manual_producto,
    obtener_clientes,
    configurar_cliente,
    eliminar_cliente
)
from modulos.generador_qr import generar_qr_producto

# --- FUNCIÓN PARA EL GENERADOR DE PDF ---
def generar_pdf_presupuesto(vendedor, items, total, cliente_nombre="Particular", descuento_aplicado=0.0):
    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("Helvetica", "B", 16)
    pdf.cell(190, 10, "PRESUPUESTO - HAFID REPUESTOS", new_x="LMARGIN", new_y="NEXT", align="C")
    
    pdf.set_font("Helvetica", "", 10)
    pdf.cell(190, 10, f"Cliente: {cliente_nombre} | Vendedor: {vendedor}", new_x="LMARGIN", new_y="NEXT", align="C")
    pdf.cell(190, 10, f"Fecha: {datetime.now().strftime('%d/%m/%Y %H:%M')}", new_x="LMARGIN", new_y="NEXT", align="C")
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
        pdf.cell(80, 10, str(item.get('descripcion', ''))[:35], 1)
        pdf.cell(30, 10, str(item.get('cantidad', 1)), 1)
        pdf.cell(40, 10, f"${item.get('subtotal', 0):,.2f}", 1)
        pdf.ln()
    
    pdf.ln(5)
    if descuento_aplicado > 0:
        pdf.set_font("Helvetica", "I", 10)
        descuento_monto = total * (descuento_aplicado / 100)
        pdf.cell(190, 8, f"Descuento Cliente ({descuento_aplicado}%): -${descuento_monto:,.2f}", new_x="LMARGIN", new_y="NEXT", align="R")
    
    pdf.set_font("Helvetica", "B", 12)
    total_final = total * (1 - descuento_aplicado / 100)
    pdf.cell(190, 10, f"TOTAL FINAL: ${total_final:,.2f}", new_x="LMARGIN", new_y="NEXT", align="R")
    
    return bytes(pdf.output())

st.set_page_config(page_title="Hafid IA", layout="wide")

if "temp_datos" not in st.session_state:
    st.session_state.temp_datos = None
if "cliente_activo" not in st.session_state:
    st.session_state.cliente_activo = {"nombre": "Particular", "descuento": 0.0}

tab_carga, tab_inventario, tab_mostrador, tab_asistente, tab_config = st.tabs(["📸 Carga Stock", "📦 Inventario & QR", "🛒 Mostrador", "🤖 Asistente", "⚙️ Configuración"])

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
        d = st.session_state.temp_datos or {}
        if not isinstance(d, dict): d = {}
            
        cuit_detectado = "".join(filter(str.isdigit, str(d.get('cuit_proveedor', '0'))))
        st.write(f"### Proveedor detectado: {d.get('proveedor', 'DESCONOCIDO')} (CUIT: {cuit_detectado})")
        
        st.info("💡 **Revisá los datos.** Podés hacer doble clic en cualquier celda para corregir códigos, precios, o seleccionar la marca correcta antes de guardar.")
        
        articulos = d.get('articulos', [])
        df_articulos = pd.DataFrame(articulos)
        
        if 'marca' not in df_articulos.columns:
            df_articulos['marca'] = "GENERICO"
            
        marcas_db = obtener_marcas() or []
        opciones_marcas = ["GENERICO"] + marcas_db if marcas_db else ["GENERICO"]

        df_editado = st.data_editor(
            df_articulos,
            column_config={
                "codigo": st.column_config.TextColumn("Código (Ref)", required=True),
                "descripcion": st.column_config.TextColumn("Descripción", required=True),
                "cantidad": st.column_config.NumberColumn("Cant.", min_value=1, step=1, required=True),
                "precio_unitario": st.column_config.NumberColumn("Precio Base ($)", min_value=0.0, format="$ %.2f", required=True),
                "marca": st.column_config.SelectboxColumn("Marca", help="Seleccioná la marca", options=opciones_marcas, required=True)
            },
            use_container_width=True,
            num_rows="dynamic",
            key="grilla_validacion"
        )
        
        st.divider()
        st.subheader("⚙️ Opciones de Etiquetas QR para esta factura")
        
        col1, col2 = st.columns([2, 1])
        with col1:
            tamano_qr = st.slider("Tamaño de los QR (10 estándar)", min_value=5, max_value=20, value=10)
        with col2:
            if not df_editado.empty:
                art_ejemplo = df_editado.iloc[0].to_dict() or {}
                if not isinstance(art_ejemplo, dict): art_ejemplo = {}
                
                cod_ej = str(art_ejemplo.get('codigo', 'DEMO')).strip() or 'DEMO'
                marca_ej = str(art_ejemplo.get('marca', 'GENERICO')).strip().upper()
                desc_ej = f"{art_ejemplo.get('descripcion', 'Repuesto')} ({marca_ej})"
                precio_bruto = float(art_ejemplo.get('precio_unitario', 0))
                
                provs = obtener_proveedores() or {}
                recargo_prev = 0.0
                if cuit_detectado in provs:
                    datos_prov = provs[cuit_detectado]
                    if isinstance(datos_prov, dict):
                        recargo_prev = float(datos_prov.get('condiciones', {}).get(str(condicion_pago), 0.0))
                
                calculos = calcular_cascada_precios(precio_bruto, recargo_prev)
                qr_preview = generar_qr_producto(cod_ej, desc_ej, calculos['precio_venta'], tamano_caja=tamano_qr)
                st.image(qr_preview, caption="Vista Previa Público", width=150)
        
        if st.button("💾 Confirmar Ingreso y Generar TODOS los QR", type="primary", use_container_width=True):
            nombre_prov = d.get('proveedor', 'DESCONOCIDO')
            articulos_lista = df_editado.to_dict('records')
            
            for art in articulos_lista:
                if isinstance(art, dict):
                    art['proveedor'] = nombre_prov
                    art['cuit_proveedor'] = cuit_detectado
                
            d['articulos'] = articulos_lista
            
            exito, msg = registrar_ingreso_inteligente(d, str(condicion_pago))
            
            if exito:
                prov_id = cuit_detectado
                provs = obtener_proveedores() or {}
                recargo = 0.0
                if prov_id in provs:
                    datos_prov = provs[prov_id]
                    if isinstance(datos_prov, dict):
                        recargo = float(datos_prov.get('condiciones', {}).get(str(condicion_pago), 0.0))

                zip_buffer = BytesIO()
                with zipfile.ZipFile(zip_buffer, "a", zipfile.ZIP_DEFLATED, False) as zip_file:
                    for art in d.get('articulos', []):
                        if not isinstance(art, dict): continue
                        
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

# --- PESTAÑA 2: INVENTARIO Y ALTA MANUAL ---
with tab_inventario:
    st.header("📦 Gestión de Inventario")
    
    tab_lista, tab_alta = st.tabs(["📋 Listado Actual", "➕ Alta Manual de Producto"])
    
    with tab_lista:
        inv = obtener_inventario_completo() or []
        if inv:
            for item in inv:
                if isinstance(item, dict):
                    ubi = item.get('ubicacion', {})
                    if isinstance(ubi, dict):
                        item['Mostrador'] = ubi.get('mostrador', '')
                        item['Depósito'] = ubi.get('deposito', '')
                    else:
                        item['Mostrador'] = ''
                        item['Depósito'] = ''

            df = pd.DataFrame(inv)
            cols_deseadas = ['id', 'marca', 'descripcion', 'proveedor', 'stock', 'precio_venta', 'Mostrador', 'Depósito']
            cols_existentes = [c for c in cols_deseadas if c in df.columns]
            
            busqueda_inv = st.text_input("🔍 Buscar repuesto:", placeholder="Ej: Correa, Bosch, 1234, o Nombre Proveedor...")
            
            if busqueda_inv:
                termino = busqueda_inv.lower()
                df_filtrado = df[df.apply(lambda row: row.astype(str).str.lower().str.contains(termino).any(), axis=1)]
            else:
                df_filtrado = df
                
            st.dataframe(df_filtrado[cols_existentes], use_container_width=True, hide_index=True)
            
            st.divider()
            opciones = {f"{item.get('id', '')} - {item.get('descripcion', '')}": item for item in inv if isinstance(item, dict)}
            seleccion = st.selectbox("Buscar repuesto para etiqueta individual:", options=list(opciones.keys()))
            if seleccion:
                prod = opciones[seleccion]
                qr_ind = generar_qr_producto(str(prod.get('id', '')), str(prod.get('descripcion','')), float(prod.get('precio_venta', 0.0)))
                st.image(qr_ind, width=150)
                st.download_button("Descargar PNG", qr_ind, f"QR_{prod.get('id', 'N')}.png", "image/png")
        else:
            st.info("El inventario está vacío.")

    with tab_alta:
        st.subheader("Ingresar Artículo sin Factura")
        provs = obtener_proveedores() or {}
        marcas = obtener_marcas() or []

        if not provs:
            st.warning("⚠️ Debes registrar al menos un proveedor en la pestaña Configuración antes de cargar artículos.")
        else:
            with st.form("form_alta_manual"):
                st.write("#### 1. Origen y Costos")
                col_prov, col_cond = st.columns(2)
                
                opciones_prov = {f"{datos.get('nombre', 'Desconocido')} (CUIT: {cuit})": cuit for cuit, datos in provs.items() if isinstance(datos, dict)}
                sel_prov = col_prov.selectbox("Proveedor", options=list(opciones_prov.keys()))
                cond_pago = col_cond.radio("Condición de Pago", ["Contado", "30 Días"], horizontal=True)

                st.write("#### 2. Identidad del Repuesto")
                col_cod, col_marca = st.columns(2)
                codigo_manual = col_cod.text_input("Código de Artículo (Obligatorio)")
                marca_manual = col_marca.selectbox("Marca", options=["GENERICO"] + marcas)
                desc_manual = st.text_input("Descripción del Producto (Obligatorio)")

                st.write("#### 3. Valores y Logística")
                col_v1, col_v2, col_v3, col_v4 = st.columns(4)
                precio_base_manual = col_v1.number_input("Precio Costo Base ($)", min_value=0.0, format="%.2f", step=100.0)
                stock_manual = col_v2.number_input("Stock Inicial", min_value=1, step=1)
                ubi_mostrador = col_v3.text_input("Ubicación Mostrador", placeholder="Ej: Estante A-4")
                ubi_deposito = col_v4.text_input("Ubicación Depósito", placeholder="Ej: Sector B Rack 2")

                submit_alta = st.form_submit_button("💾 Guardar Repuesto en Inventario", type="primary", use_container_width=True)

                if submit_alta:
                    if not codigo_manual or not desc_manual:
                        st.error("❌ El Código y la Descripción son campos obligatorios.")
                    else:
                        llave_prov = str(sel_prov) if sel_prov else ""
                        cuit_seleccionado = str(opciones_prov.get(llave_prov, ""))
                        datos_prov = provs.get(cuit_seleccionado, {})
                        
                        if not isinstance(datos_prov, dict): 
                            datos_prov = {}
                            
                        condiciones_prov = datos_prov.get("condiciones", {})
                        if not isinstance(condiciones_prov, dict): 
                            condiciones_prov = {}
                            
                        llave_cond = str(cond_pago) if cond_pago else "Contado"
                        recargo_financiero = float(condiciones_prov.get(llave_cond, 0.0))

                        exito, msj_alta = alta_manual_producto(
                            codigo=codigo_manual,
                            marca=marca_manual,
                            descripcion=desc_manual,
                            cuit_proveedor=cuit_seleccionado,
                            precio_base=precio_base_manual,
                            recargo=recargo_financiero,
                            stock=stock_manual,
                            ubi_mostrador=ubi_mostrador,
                            ubi_deposito=ubi_deposito
                        )
                        
                        if exito:
                            st.success(f"✅ {msj_alta}")
                            st.rerun()
                        else:
                            st.error(f"❌ {msj_alta}")

# --- PESTAÑA 3: MOSTRADOR ---
with tab_mostrador:
    st.header("🛒 Punto de Venta / Presupuestos")
    
    col_cli1, col_cli2 = st.columns([3,1])
    with col_cli1:
        st.subheader(f"👤 Cliente: {st.session_state.cliente_activo['nombre']}")
        if st.session_state.cliente_activo['descuento'] > 0:
            st.caption(f"Descuento especial aplicado: {st.session_state.cliente_activo['descuento']}%")
    with col_cli2:
        if st.button("❌ Limpiar Cliente", use_container_width=True):
            st.session_state.cliente_activo = {"nombre": "Particular", "descuento": 0.0}
            st.rerun()
            
    vendedor = st.radio("Usuario / Dispositivo:", ["Caja Principal", "Celular Depósito"], horizontal=True)
    
    st.write("### ➕ Agregar Productos")
    
    # NUEVO: Separamos los 3 métodos en sub-pestañas visuales
    t_manual, t_ia, t_qr = st.tabs(["⌨️ Pistola / Manual", "🤖 Asistente IA (Voz)", "📷 Escáner QR"])
    
    # 1. PISTOLA / MANUAL
    with t_manual:
        with st.form("form_carga_rapida", clear_on_submit=True):
            col_scan1, col_scan2 = st.columns([4, 1])
            codigo_manual = col_scan1.text_input("Ingreso Manual o Pistola de Código:", key=f"scan_{vendedor}")
            submit_btn = col_scan2.form_submit_button("➕ Agregar Artículo", use_container_width=True)
            
            if submit_btn and codigo_manual:
                exito, msj = agregar_al_carrito(str(vendedor), codigo_manual)
                if exito: 
                    st.success(msj)
                    st.rerun()
                else: 
                    st.error(msj)
                    
    # 2. ASISTENTE IA (VOZ)
    with t_ia:
        with st.form("form_ia_mostrador", clear_on_submit=True):
            col_ia1, col_ia2 = st.columns([4, 1])
            orden_usuario_mostrador = col_ia1.text_input("Dicte o escriba su orden (Ej: 'Cargame 2 unidades del código X', 'Presupuesto para Luis'):", key=f"ia_most_{vendedor}")
            submit_ia = col_ia2.form_submit_button("🤖 Ejecutar", use_container_width=True)
            
            if submit_ia and orden_usuario_mostrador:
                with st.spinner("Hafid IA procesando..."):
                    inventario = obtener_inventario_completo() or []
                    respuesta_json = procesar_orden_voz(orden_usuario_mostrador, inventario) or {}
                    accion = respuesta_json.get("accion")
                    
                    if accion == "agregar_carrito":
                        id_producto = respuesta_json.get("id_producto")
                        cant = int(respuesta_json.get("cantidad", 1))
                        exito, msj_db = agregar_al_carrito(str(vendedor), id_producto, cant)
                        if exito:
                            st.success(f"🛒 {msj_db}")
                            st.rerun()
                        else:
                            st.error(f"❌ Error: {msj_db}")
                            
                    elif accion == "set_cliente":
                        nombre_det = respuesta_json.get("nombre_cliente", "").upper()
                        clientes_db = obtener_clientes() or {}
                        cliente_encontrado = next((c for c in clientes_db.values() if nombre_det in str(c.get('nombre', '')).upper()), None)
                        
                        if cliente_encontrado:
                            st.session_state.cliente_activo = {"nombre": cliente_encontrado['nombre'], "descuento": float(cliente_encontrado.get('descuento', 0.0))}
                            st.success(f"✅ Cliente {cliente_encontrado['nombre']} activado.")
                            st.rerun()
                        else:
                            st.warning(f"⚠️ '{nombre_det}' no está en la base de datos.")
                    else:
                        st.info(respuesta_json.get("respuesta", "Orden procesada."))

    # 3. CÁMARA QR
    with t_qr:
        foto_qr = st.camera_input("Escanear QR con Cámara", key=f"cam_{vendedor}")
        if foto_qr:
            cod_detectado = decodificar_qr_desde_imagen(Image.open(foto_qr))
            if cod_detectado:
                id_limpio = cod_detectado.split("\n")[0].replace("COD:", "").strip()
                exito, msj = agregar_al_carrito(str(vendedor), id_limpio)
                if exito: 
                    st.success(f"Añadido: {id_limpio}")
                    st.rerun()
                else: 
                    st.error(msj)

    st.divider()
    carrito = obtener_carrito(str(vendedor)) or []
    if carrito:
        total_bruto = sum(item.get("subtotal", 0) for item in carrito if isinstance(item, dict))
        desc_porc = st.session_state.cliente_activo['descuento']
        total_final = total_bruto * (1 - desc_porc / 100)
        
        st.table(carrito)
        
        st.write(f"### Subtotal: ${total_bruto:,.2f}")
        if desc_porc > 0:
            st.write(f"### Descuento ({desc_porc}%): -${(total_bruto * desc_porc / 100):,.2f}")
        st.write(f"## TOTAL: ${total_final:,.2f}")
        
        col_cob, col_pdf, col_vac = st.columns(3)
        if col_cob.button("✅ Confirmar Venta", type="primary", use_container_width=True):
            exito, msj = confirmar_venta(str(vendedor))
            if exito: st.success(msj); st.rerun()
            else: st.error(msj)
        
        pdf_bytes = generar_pdf_presupuesto(str(vendedor), carrito, total_bruto, st.session_state.cliente_activo['nombre'], desc_porc)
        col_pdf.download_button("📄 Imprimir PDF", pdf_bytes, f"Presupuesto_{vendedor}.pdf", "application/pdf", use_container_width=True)
        
        if col_vac.button("🗑️ Vaciar", use_container_width=True):
            vaciar_carrito(str(vendedor))
            st.rerun()

# --- PESTAÑA 4: ASISTENTE DE VOZ (Consultas y Stock) ---
with tab_asistente:
    st.header("🤖 Asistente de Depósito")
    st.info("Escribí o dictá tu orden. Ej: 'Buscame pastillas de freno', 'Descontame 1 filtro'.")
    
    if "ultima_orden" not in st.session_state:
        st.session_state.ultima_orden = None
    if "ultima_respuesta" not in st.session_state:
        st.session_state.ultima_respuesta = None
    if "ultimo_estado" not in st.session_state:
        st.session_state.ultimo_estado = None 

    orden_usuario = st.chat_input("Dicte o escriba aquí...")
    
    if orden_usuario:
        st.session_state.ultima_orden = orden_usuario
        
        with st.spinner("Procesando en el depósito..."):
            inventario = obtener_inventario_completo() or []
            
            respuesta_json = procesar_orden_voz(orden_usuario, inventario) or {}
            if not isinstance(respuesta_json, dict): respuesta_json = {}
            
            accion = respuesta_json.get("accion")
            texto_ia = respuesta_json.get("respuesta", "Lo siento, no entendí bien la orden.")
            
            if accion == "set_cliente":
                nombre_det = respuesta_json.get("nombre_cliente", "").upper()
                clientes_db = obtener_clientes() or {}
                cliente_encontrado = next((c for c in clientes_db.values() if nombre_det in str(c.get('nombre', '')).upper()), None)
                
                if cliente_encontrado:
                    st.session_state.cliente_activo = {"nombre": cliente_encontrado['nombre'], "descuento": float(cliente_encontrado.get('descuento', 0.0))}
                    st.session_state.ultima_respuesta = f"✅ Listo. Cliente {cliente_encontrado['nombre']} activado ({cliente_encontrado['descuento']}% descuento)."
                    st.session_state.ultimo_estado = "success"
                else:
                    st.session_state.cliente_activo = {"nombre": nombre_det, "descuento": 0.0}
                    st.session_state.ultima_respuesta = f"⚠️ {texto_ia} (Nota: '{nombre_det}' no está en la base de datos, se aplicará 0% de descuento)."
                    st.session_state.ultimo_estado = "normal"
            
            elif accion == "agregar_carrito":
                id_producto = respuesta_json.get("id_producto")
                cant = int(respuesta_json.get("cantidad", 1))
                exito, msj_db = agregar_al_carrito("Caja Principal", id_producto, cant)
                if exito:
                    st.session_state.ultima_respuesta = f"🛒 Listo. {texto_ia} ({msj_db})"
                    st.session_state.ultimo_estado = "success"
                else:
                    st.session_state.ultima_respuesta = f"❌ Error: {msj_db}"
                    st.session_state.ultimo_estado = "error"

            elif accion == "baja":
                id_producto = respuesta_json.get("id_producto")
                cant = int(respuesta_json.get("cantidad", 1))
                exito, msj_db = registrar_merma(id_producto, cant)
                st.session_state.ultima_respuesta = f"✅ Listo. {texto_ia} ({msj_db})" if exito else f"❌ Error: {msj_db}"
                st.session_state.ultimo_estado = "success" if exito else "error"
            
            elif accion == "alta":
                id_producto = respuesta_json.get("id_producto")
                cant = int(respuesta_json.get("cantidad", 1))
                exito, msj_db = registrar_aumento_stock(id_producto, cant)
                st.session_state.ultima_respuesta = f"✅ Listo. {texto_ia} ({msj_db})" if exito else f"❌ Error: {msj_db}"
                st.session_state.ultimo_estado = "success" if exito else "error"
            
            else: 
                st.session_state.ultima_respuesta = texto_ia
                st.session_state.ultimo_estado = "normal"

    if st.session_state.ultima_orden:
        with st.chat_message("user"):
            st.markdown(st.session_state.ultima_orden)
            
        with st.chat_message("assistant"):
            if st.session_state.ultimo_estado == "success":
                st.success(st.session_state.ultima_respuesta)
            elif st.session_state.ultimo_estado == "error":
                st.error(st.session_state.ultima_respuesta)
            else:
                st.markdown(st.session_state.ultima_respuesta)

# --- PESTAÑA 5: CONFIGURACIÓN ---
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
    
    tab_prov, tab_marcas, tab_clientes = st.tabs(["🏭 Proveedores y Recargos", "🏷️ Marcas", "👥 Clientes"])
    
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
        
        st.divider()
        st.write("### Directorio de Proveedores")
        provs = obtener_proveedores() or {}
        if provs:
            datos_tabla = []
            for cuit, datos_prov in provs.items():
                if not isinstance(datos_prov, dict): datos_prov = {}
                condiciones = datos_prov.get('condiciones', {})
                if not isinstance(condiciones, dict): condiciones = {}
                    
                datos_tabla.append({
                    "Proveedor": datos_prov.get("nombre", ""),
                    "CUIT": cuit,
                    "Contado": f"{condiciones.get('Contado', 0)}%",
                    "30 Días": f"{condiciones.get('30 Días', 0)}%"
                })
            st.dataframe(datos_tabla, use_container_width=True)
            
            with st.expander("🗑️ Eliminar un Proveedor"):
                prov_a_borrar = st.selectbox(
                    "Seleccionar proveedor a eliminar:", 
                    options=list(provs.keys()), 
                    format_func=lambda x: f"{(provs.get(x) or {}).get('nombre', 'Desconocido')} (CUIT: {x})"
                )
                if st.button("Eliminar Proveedor", type="primary"):
                    eliminar_proveedor(str(prov_a_borrar))
                    st.success("Proveedor eliminado del sistema.")
                    st.rerun()
            
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
        marcas_actuales = obtener_marcas() or []
        if marcas_actuales:
            st.write("**Marcas registradas:**")
            st.write(", ".join(marcas_actuales))
            
            with st.expander("🗑️ Eliminar una Marca"):
                marca_a_borrar = st.selectbox("Seleccionar marca a eliminar:", options=marcas_actuales)
                if st.button("Eliminar Marca", type="primary"):
                    eliminar_marca(str(marca_a_borrar))
                    st.success("Marca eliminada del sistema.")
                    st.rerun()
        else:
            st.info("Aún no hay marcas cargadas.")

    with tab_clientes:
        st.subheader("Alta y Edición de Clientes")
        with st.form("conf_cliente"):
            c1, c2, c3 = st.columns([3, 2, 1])
            nombre_cli = c1.text_input("Nombre / Razón Social").upper()
            cuit_cli = c2.text_input("DNI o CUIT")
            desc_cli = c3.number_input("% Descuento", min_value=0.0, step=1.0)
            
            if st.form_submit_button("Guardar Cliente"):
                if nombre_cli and cuit_cli:
                    configurar_cliente(nombre_cli, cuit_cli, desc_cli)
                    st.success(f"Cliente {nombre_cli} guardado.")
                    st.rerun()
                else:
                    st.error("El nombre y el DNI/CUIT son obligatorios.")
        
        st.divider()
        st.write("### Directorio de Clientes")
        clis = obtener_clientes() or {}
        if clis:
            datos_cli = []
            for id_c, d_cli in clis.items():
                datos_cli.append({
                    "Nombre": d_cli.get("nombre", ""),
                    "CUIT/DNI": id_c,
                    "Descuento": f"{d_cli.get('descuento', 0)}%"
                })
            st.dataframe(datos_cli, use_container_width=True)
            
            with st.expander("🗑️ Eliminar un Cliente"):
                cli_borrar = st.selectbox(
                    "Seleccionar cliente a eliminar:",
                    options=list(clis.keys()),
                    format_func=lambda x: f"{(clis.get(x) or {}).get('nombre', 'Desconocido')} (Doc: {x})"
                )
                if st.button("Eliminar Cliente", type="primary"):
                    eliminar_cliente(str(cli_borrar))
                    st.success("Cliente eliminado.")
                    st.rerun()
        else:
            st.info("Aún no hay clientes cargados.")