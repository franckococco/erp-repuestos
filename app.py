import streamlit as st
import os
import traceback

# Debe ser la primera llamada a Streamlit (evita pantalla genérica "Oh, no")
st.set_page_config(page_title="Hafid Repuestos", layout="wide", initial_sidebar_state="expanded")

_ARCHIVOS_MODULOS = (
    "__init__.py",
    "ia_vision.py",
    "ia_asistente.py",
    "ia_vinculacion.py",
    "db_firebase.py",
    "generador_qr.py",
    "ui_estilos.py",
    "control_remito.py",
    "ui_control_remito.py",
    "pedidos_db.py",
    "comparar_pedido.py",
    "ui_pedidos.py",
    "ui_vinculacion.py",
    "util_fechas.py",
    "util_vehiculos.py",
    "util_codigos.py",
    "factura_borrador.py",
    "ui_carga_factura.py",
)

_modulos_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "modulos")
_faltantes = [
    nombre for nombre in _ARCHIVOS_MODULOS
    if nombre != "__init__.py"
    and not os.path.isfile(os.path.join(_modulos_dir, nombre))
]
if not os.path.isdir(_modulos_dir):
    _faltantes = list(_ARCHIVOS_MODULOS)

if _faltantes:
    st.error("Faltan archivos en la carpeta `modulos/` del repositorio (GitHub / Streamlit Cloud).")
    st.markdown("Subí **todos** estos archivos juntos a `modulos/` en el repo **erp-repuestos**:")
    for nombre in _ARCHIVOS_MODULOS:
        st.write(f"- `modulos/{nombre}`")
    st.info(
        "Copiá la carpeta `modulos/` completa desde tu entorno local al repositorio "
        "**erp-repuestos** (rama `main`) y hacé redeploy en Streamlit Cloud."
    )
    st.stop()

try:
    import streamlit.components.v1 as components
    import pandas as pd
    from PIL import Image
    from fpdf import FPDF
    from datetime import datetime
    import unicodedata
    import re

    from modulos.ia_vision import decodificar_qr_desde_imagen
    from modulos.ia_asistente import procesar_orden_voz
    from modulos.db_firebase import (
        get_db,
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
        eliminar_cliente,
        actualizar_ubicacion_relevamiento,
        actualizar_producto_desde_grilla,
        obtener_producto_por_codigo,
        exportar_inventario_csv,
        restaurar_inventario_csv,
        agregar_texto_descripcion,
        sanitizar_clave_marca,
        formatear_id_variante,
    )
    from modulos.generador_qr import generar_qr_producto
    from modulos.ui_estilos import aplicar_estilos_globales, render_sidebar, titulo_seccion, ayuda, metricas_inventario
    from modulos.util_vehiculos import OPCIONES_VEHICULO, normalizar_lista_vehiculos, vehiculos_a_texto

    get_db()

except Exception as e:
    st.error("Error al iniciar la aplicación")
    st.exception(e)
    st.markdown(
        "**Streamlit Cloud:** entrá a [share.streamlit.io](https://share.streamlit.io) → tu app → "
        "**⋮** (menú arriba a la derecha) → **Manage app** → pestaña **Logs**. "
        "Verificá que en **Settings → Secrets** estén `firebase_key`, `GROQ_API_KEY` y `ANTHROPIC_API_KEY`."
    )
    st.code(traceback.format_exc())
    st.stop()

aplicar_estilos_globales()

# --- MOTOR DE ATAJOS DE TECLADO (sidebar radio) ---
components.html(
    """
    <script>
    const doc = window.parent.document;
    doc.addEventListener('keydown', function(e) {
        if (!e.ctrlKey) return;
        if (e.key.toLowerCase() === 'g') {
            e.preventDefault();
            const buttons = doc.querySelectorAll('button');
            for (const b of buttons) {
                if (b.innerText && b.innerText.includes('Guardar borrador')) {
                    b.click();
                    return;
                }
            }
            return;
        }
        const map = { s: 0, i: 1, m: 2, a: 3, c: 4 };
        const idx = map[e.key.toLowerCase()];
        if (idx === undefined) return;
        e.preventDefault();
        const sidebar = doc.querySelector('[data-testid="stSidebar"]');
        if (!sidebar) return;
        const radios = sidebar.querySelectorAll('div[role="radiogroup"] label');
        if (radios.length > idx) radios[idx].click();
    });
    </script>
    """,
    height=0, width=0,
)

# --- FUNCIÓN DE NORMALIZACIÓN EXTREMA ---
def normalizar_para_busqueda(texto):
    if not texto:
        return ""
    t = ''.join(c for c in unicodedata.normalize('NFD', str(texto)) if unicodedata.category(c) != 'Mn')
    return re.sub(r'[^a-z0-9\s]', '', t.lower())


_STOPWORDS_PROV = frozenset({
    "de", "del", "la", "las", "el", "los", "y", "e", "en", "por", "para", "con", "un", "una", "productos", "repuestos",
})
_SUFIJOS_PROV = frozenset({"sa", "sas", "srl", "ltda", "inc", "cia", "co", "corp", "ltd", "sau"})


def _palabras_clave_proveedor(texto):
    """Raíz útil del nombre: 'expoyer' desde 'EXPOYER S.A.' o 'filtrame productos de expoyer'."""
    palabras = []
    for w in normalizar_para_busqueda(texto).split():
        if len(w) < 2 or w in _STOPWORDS_PROV or w in _SUFIJOS_PROV:
            continue
        palabras.append(w)
    return palabras


def _proveedor_coincide_busqueda(terminos_clave, nombre_proveedor):
    if not terminos_clave:
        return False
    palabras = _palabras_clave_proveedor(nombre_proveedor)
    if not palabras:
        return False
    texto = " ".join(palabras)
    for t in terminos_clave:
        if t in texto:
            continue
        if not any(t in p or p.startswith(t) for p in palabras):
            return False
    return True


def _cuits_proveedor_en_catalogo(termino, provs_catalogo):
    terminos_clave = _palabras_clave_proveedor(termino)
    cuits = set()
    for cuit, datos in (provs_catalogo or {}).items():
        if isinstance(datos, dict) and _proveedor_coincide_busqueda(terminos_clave, datos.get("nombre", "")):
            cuits.add(str(cuit))
    return cuits, terminos_clave


def preparar_item_inventario(item):
    if not isinstance(item, dict):
        return item
    ubi = item.get('ubicacion', {})
    if isinstance(ubi, dict):
        item['Pasillo'] = int(ubi.get('pasillo', 0))
        item['Piso'] = int(ubi.get('piso', 0))
        item['Módulo'] = int(ubi.get('modulo', 0))
        item['Fila'] = int(ubi.get('fila', 0))
    else:
        item['Pasillo'] = item['Piso'] = item['Módulo'] = item['Fila'] = 0
    item['Marca'] = item.get('marca', item.get('condicion', 'GENERICO'))
    vehs = item.get('vehiculos')
    item['Vehículo'] = vehiculos_a_texto(vehs) if vehs else item.get('vehiculo', 'UNIVERSAL')
    item['Stock'] = int(item.get('stock', 0))
    item['Precio Final'] = int(item.get('precio_venta', 0) or 0)
    item['Descripción'] = item.get('descripcion', '')
    item['id_maestro'] = item.get('id_maestro', item.get('codigo', ''))
    return item


def agrupar_por_maestro(items):
    grupos = {}
    for item in items:
        if not isinstance(item, dict):
            continue
        key = item.get('id_maestro') or str(item.get('codigo', '')).strip()
        if not key:
            key = str(item.get('id', '')).split('_')[0]
        if key not in grupos:
            grupos[key] = {
                'id_maestro': key,
                'codigo': item.get('codigo', key),
                'descripcion': item.get('descripcion', ''),
                'vehiculo': item.get('vehiculo', 'UNIVERSAL'),
                'ubicacion': item.get('ubicacion', {}),
                'variantes': []
            }
        grupos[key]['variantes'].append(item)
    return grupos


def filtrar_inventario(items, termino_busqueda):
    if not termino_busqueda:
        return items
    terminos = normalizar_para_busqueda(termino_busqueda).split()
    resultado = []
    for item in items:
        texto = normalizar_para_busqueda(
            f"{item.get('codigo', '')} {item.get('descripcion', '')} {item.get('vehiculo', '')} "
            f"{item.get('vehiculos_busqueda', '')} {item.get('marca', '')} {item.get('id', '')} "
            f"{item.get('proveedor', '')}"
        )
        if all(t in texto for t in terminos):
            resultado.append(item)
    return resultado


def texto_resultados_agrupados(encontrados, termino):
    grupos = agrupar_por_maestro(encontrados)
    if not grupos:
        return f"No hay stock de nada relacionado con '{termino}'."
    lista_txt = f"🔍 Resultados agrupados para '{termino}':\n\n"
    for key in sorted(grupos.keys(), key=lambda k: grupos[k]['descripcion']):
        g = grupos[key]
        ubi = g.get('ubicacion', {})
        if not isinstance(ubi, dict):
            ubi = {}
        loc_str = (
            f"Pasillo: {ubi.get('pasillo', 0)} | Piso: {ubi.get('piso', 0)} | "
            f"Módulo: {ubi.get('modulo', 0)} | Fila: {ubi.get('fila', 0)}"
        )
        lista_txt += f"### {g['descripcion']} ({g['vehiculo']}) — Cód. {g['codigo']}\n"
        lista_txt += f"📍 {loc_str}\n"
        for v in g['variantes']:
            marca_disp = v.get('marca', v.get('condicion', ''))
            lista_txt += (
                f"  - **Marca {marca_disp}:** {v.get('stock', 0)} u. | "
                f"${v.get('precio_venta', 0):,.0f} | ID: `{v.get('id', '')}`\n"
            )
        lista_txt += "\n"
    return lista_txt


# --- FUNCIÓN PARA EL GENERADOR DE PDF ---
def generar_pdf_presupuesto(vendedor, items, total, cliente_nombre="Particular", descuento_aplicado=0.0):
    pdf = FPDF()
    pdf.add_page()

    if os.path.exists("logo_hafid.jpeg"):
        pdf.image("logo_hafid.jpeg", x=85, y=10, w=40)
        pdf.ln(35)
    elif os.path.exists("logo_hafid.jpg"):
        pdf.image("logo_hafid.jpg", x=85, y=10, w=40)
        pdf.ln(35)
    elif os.path.exists("logo_hafid.png"):
        pdf.image("logo_hafid.png", x=85, y=10, w=40)
        pdf.ln(35)

    pdf.set_font("Helvetica", "B", 16)
    pdf.cell(190, 10, "PRESUPUESTO - MAGNUM VALORES SAS", new_x="LMARGIN", new_y="NEXT", align="C")

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

if "temp_datos" not in st.session_state:
    st.session_state.temp_datos = None
if "borrador_id" not in st.session_state:
    st.session_state.borrador_id = None
if "cliente_activo" not in st.session_state:
    st.session_state.cliente_activo = {"nombre": "Particular", "descuento": 0.0}
if "resultados_ia_mostrador" not in st.session_state:
    st.session_state.resultados_ia_mostrador = None
if "msg_ia_mostrador" not in st.session_state:
    st.session_state.msg_ia_mostrador = None
if "pagina" not in st.session_state:
    st.session_state.pagina = "carga"

pagina = render_sidebar(st.session_state.cliente_activo)

# --- CARGA DE STOCK ---
if pagina == "carga":
    titulo_seccion("Carga y control", "Ctrl+S")
    vista_carga = st.radio(
        "Sección",
        ["📸 Carga factura", "📋 Factura vs Remito", "📦 Pedidos"],
        horizontal=True,
        label_visibility="collapsed",
    )

    if vista_carga.startswith("📦"):
        from modulos.ui_pedidos import render_pedidos
        render_pedidos()

    elif vista_carga.startswith("📋"):
        from modulos.ui_control_remito import render_control_factura_remito
        render_control_factura_remito()

    elif vista_carga.startswith("📸"):
        st.subheader("Subir factura")
        from modulos.ui_carga_factura import render_carga_factura
        render_carga_factura()

# --- INVENTARIO Y ALTA MANUAL ---
elif pagina == "inventario":
    titulo_seccion("Inventario", "Ctrl+I")

    tab_lista, tab_alta, tab_vinc = st.tabs(["Listado", "Alta manual", "Vincular códigos"])

    with tab_lista:
        inv = obtener_inventario_completo() or []
        if inv:
            inv = [preparar_item_inventario(item) for item in inv if isinstance(item, dict)]

            busqueda_inv = st.text_input(
                "Buscar repuesto",
                placeholder="Código, descripción, marca, vehículo, proveedor…",
                label_visibility="collapsed",
            )
            inv_filtrado = filtrar_inventario(inv, busqueda_inv)
            metricas_inventario(inv_filtrado)

            vista_inv = st.radio(
                "Vista",
                ["Resumen por artículo", "Editar variantes", "Etiqueta QR"],
                horizontal=True,
                label_visibility="collapsed",
            )

            if vista_inv == "Resumen por artículo":
                grupos = agrupar_por_maestro(inv_filtrado)
                if grupos:
                    for key in sorted(grupos.keys(), key=lambda k: grupos[k]['descripcion']):
                        g = grupos[key]
                        variantes = g['variantes']
                        stock_total = sum(int(v.get('stock', 0)) for v in variantes)
                        titulo = (
                            f"{g['descripcion']} | {g['vehiculo']} | "
                            f"Cód. {g['codigo']} | {len(variantes)} marca(s) | Stock: {stock_total}"
                        )
                        with st.expander(titulo, expanded=bool(busqueda_inv)):
                            filas_var = []
                            for v in variantes:
                                filas_var.append({
                                    "Marca": v.get('marca', ''),
                                    "Stock": int(v.get('stock', 0)),
                                    "Precio": f"${float(v.get('precio_venta', 0)):,.0f}",
                                    "Proveedor": v.get('proveedor', ''),
                                    "ID": v.get('id', '')
                                })
                            st.dataframe(pd.DataFrame(filas_var), hide_index=True, use_container_width=True)
                else:
                    st.info("Sin coincidencias para la búsqueda.")

            elif vista_inv == "Editar variantes":
                ayuda(
                    "Ayuda — Edición",
                    "Editá descripción, vehículo, marca, stock, precio y ubicación. "
                    "Código maestro e ID variante no se modifican acá. Luego **Guardar cambios**.",
                )
                df = pd.DataFrame(inv_filtrado)
                cols_deseadas = [
                    'id', 'id_maestro', 'codigo', 'Descripción', 'Vehículo', 'Marca',
                    'Stock', 'Precio Final', 'Pasillo', 'Piso', 'Módulo', 'Fila'
                ]
                cols_existentes = [c for c in cols_deseadas if c in df.columns]
                df_filtrado = df[cols_existentes].reset_index(drop=True)

                st.data_editor(
                    df_filtrado,
                    use_container_width=True,
                    hide_index=True,
                    column_config={
                        "id": st.column_config.TextColumn("ID variante", disabled=True, width="small"),
                        "id_maestro": st.column_config.TextColumn("ID maestro", disabled=True, width="small"),
                        "codigo": st.column_config.TextColumn("Cód. maestro", disabled=True, width="small"),
                        "Descripción": st.column_config.TextColumn("Descripción", width="medium"),
                        "Vehículo": st.column_config.TextColumn("Vehículo", width="small"),
                        "Marca": st.column_config.TextColumn("Marca", width="small"),
                        "Stock": st.column_config.NumberColumn("Stock", min_value=0, step=1, width="small"),
                        "Precio Final": st.column_config.NumberColumn("Precio", min_value=0, step=10, width="small"),
                        "Pasillo": st.column_config.NumberColumn("Pasillo", min_value=0, step=1, width="small"),
                        "Piso": st.column_config.NumberColumn("Piso", min_value=0, step=1, width="small"),
                        "Módulo": st.column_config.NumberColumn("Módulo", min_value=0, step=1, width="small"),
                        "Fila": st.column_config.NumberColumn("Fila", min_value=0, step=1, width="small"),
                    },
                    key="grilla_inv"
                )

                if "grilla_inv" in st.session_state and st.session_state.grilla_inv.get("edited_rows"):
                    if st.button("Guardar cambios", type="primary"):
                        cambios = st.session_state.grilla_inv["edited_rows"]
                        errores = []
                        guardados = 0
                        for row_idx, dict_cambios in cambios.items():
                            fila = df_filtrado.iloc[int(row_idx)]
                            id_prod = str(fila['id'])
                            id_m = str(fila.get('id_maestro') or fila.get('codigo') or '').strip()
                            prefijo = f"{id_m}_"
                            if id_m and id_prod.startswith(prefijo):
                                marca_fila = id_prod[len(prefijo):].upper()
                            else:
                                marca_fila = str(fila.get('Marca', 'GENERICO')).upper()
                            for col_name, new_val in dict_cambios.items():
                                ok, msj = actualizar_producto_desde_grilla(
                                    id_prod, col_name, new_val,
                                    id_maestro=id_m, marca=marca_fila
                                )
                                if ok:
                                    guardados += 1
                                else:
                                    errores.append(f"{col_name}: {msj}")
                        if errores:
                            st.error("Algunos cambios no se guardaron:\n" + "\n".join(errores))
                        if guardados:
                            st.success(f"Inventario actualizado ({guardados} celda(s)).")
                            st.rerun()
                        elif not errores:
                            st.info("No hubo cambios para guardar.")

            else:
                opciones = {
                    f"{item.get('id', '')} - {item.get('descripcion', '')} ({item.get('marca', '')})": item
                    for item in inv_filtrado
                }
                seleccion = st.selectbox("Producto para etiqueta QR", options=list(opciones.keys()))
                if seleccion:
                    prod = opciones[seleccion]
                    marca_qr = prod.get('marca', '')
                    desc_qr = f"{prod.get('descripcion', '')} ({marca_qr})"
                    col_qr1, col_qr2 = st.columns([1, 2])
                    qr_ind = generar_qr_producto(
                        str(prod.get('id', '')),
                        desc_qr,
                        float(prod.get('precio_venta', 0.0))
                    )
                    col_qr1.image(qr_ind, width=140)
                    col_qr2.download_button("Descargar PNG", qr_ind, f"QR_{prod.get('id', 'N')}.png", "image/png")
        else:
            st.info("El inventario está vacío.")

    with tab_alta:
        st.subheader("Ingresar Artículo sin Factura")
        st.caption("El **código** identifica el artículo maestro. La **marca** es la variante.")
        provs = obtener_proveedores() or {}

        if not provs:
            st.warning("⚠️ Debes registrar al menos un proveedor en la pestaña Configuración antes de cargar artículos.")
        else:
            with st.form("form_alta_manual"):
                st.write("#### 1. Origen y Costos")
                col_prov, col_cond = st.columns(2)

                opciones_prov = {
                    f"{datos.get('nombre', 'Desconocido')} (CUIT: {cuit})": cuit
                    for cuit, datos in provs.items() if isinstance(datos, dict)
                }
                sel_prov = col_prov.selectbox("Proveedor", options=list(opciones_prov.keys()))
                cond_pago = col_cond.radio("Condición de Pago", ["Contado", "30 Días"], horizontal=True)

                st.write("#### 2. Identidad del Repuesto (Maestro + Variante)")
                col_cod, col_marca = st.columns(2)
                codigo_manual = col_cod.text_input("Código Maestro (Obligatorio)")
                marca_manual = col_marca.text_input("Marca / Variante", value="GENERICO")
                veh_manual = st.multiselect(
                    "Vehículos compatibles",
                    options=OPCIONES_VEHICULO,
                    default=["UNIVERSAL"],
                )
                desc_manual = st.text_input("Descripción del Producto (Obligatorio)")

                prod_existente = None
                if codigo_manual:
                    prod_existente = obtener_producto_por_codigo(codigo_manual)
                    if prod_existente:
                        st.success(
                            f"Maestro existente: {prod_existente.get('descripcion', '')} — "
                            f"Se agregará/actualizará la variante **{marca_manual.upper()}**."
                        )

                st.write("#### 3. Valores")
                col_v1, col_v2 = st.columns(2)
                precio_base_manual = col_v1.number_input("Precio Costo Base ($)", min_value=0.0, format="%.2f", step=100.0)
                stock_manual = col_v2.number_input("Stock Inicial", min_value=1, step=1)

                st.write("#### 4. Ubicación Exacta (del artículo maestro)")
                col_u1, col_u2, col_u3, col_u4 = st.columns(4)
                pasillo_manual = col_u1.number_input("Pasillo", min_value=0, step=1)
                piso_manual = col_u2.number_input("Piso", min_value=0, step=1)
                modulo_manual = col_u3.number_input("Módulo", min_value=0, step=1)
                fila_manual = col_u4.number_input("Fila", min_value=0, step=1)

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
                            condicion=marca_manual,
                            vehiculo=normalizar_lista_vehiculos(veh_manual),
                            descripcion=desc_manual,
                            cuit_proveedor=cuit_seleccionado,
                            precio_base=precio_base_manual,
                            recargo=recargo_financiero,
                            stock=stock_manual,
                            pasillo=pasillo_manual,
                            piso=piso_manual,
                            modulo=modulo_manual,
                            fila=fila_manual
                        )

                        if exito:
                            st.success(f"✅ {msj_alta}")
                            st.rerun()
                        else:
                            st.error(f"❌ {msj_alta}")

    with tab_vinc:
        from modulos.ui_vinculacion import render_vinculacion_inventario
        render_vinculacion_inventario()

# --- MOSTRADOR ---
elif pagina == "mostrador":
    titulo_seccion("Mostrador / Presupuesto", "Ctrl+M")

    col_cli1, col_cli2 = st.columns([3, 1])
    with col_cli1:
        st.markdown(f"**Cliente:** {st.session_state.cliente_activo['nombre']}")
        if st.session_state.cliente_activo['descuento'] > 0:
            st.caption(f"Descuento: {st.session_state.cliente_activo['descuento']}%")
    with col_cli2:
        if st.button("Limpiar cliente", use_container_width=True):
            st.session_state.cliente_activo = {"nombre": "Particular", "descuento": 0.0}
            st.rerun()

    vendedor = st.radio("Punto de venta", ["Caja Principal", "Celular Depósito"], horizontal=True, label_visibility="collapsed")

    t_buscar, t_manual, t_ia, t_qr = st.tabs(["🔍 Buscador", "⌨️ Pistola / Manual", "🤖 Asistente IA (Voz)", "📷 Escáner QR"])

    with t_buscar:
        inv_completo = obtener_inventario_completo() or []
        if inv_completo:
            opciones_desc = {}
            for item in inv_completo:
                if isinstance(item, dict):
                    marca_item = item.get('marca', item.get('condicion', ''))
                    desc = (
                        f"{item.get('codigo', '')} | {item.get('vehiculo', '')} - "
                        f"{marca_item} | {item.get('descripcion', '')} - ${item.get('precio_venta', 0)}"
                    )
                    opciones_desc[desc] = item.get('id')

            sel_prod = st.selectbox("Buscar por nombre, código, vehículo o marca:", options=[""] + list(opciones_desc.keys()))

            col_b1, col_b2 = st.columns([1, 3])
            cant_b = col_b1.number_input("Cantidad", min_value=1, step=1, key=f"cant_b_{vendedor}")

            if col_b2.button("➕ Agregar al Presupuesto", use_container_width=True, type="primary"):
                if sel_prod:
                    id_real = opciones_desc[sel_prod]
                    exito, msj = agregar_al_carrito(str(vendedor), id_real, int(cant_b))
                    if exito:
                        st.success(msj)
                        st.rerun()
                    else:
                        st.error(msj)
                else:
                    st.warning("Seleccione un producto del menú desplegable primero.")
        else:
            st.info("El inventario está vacío. Agregue productos primero.")

    with t_manual:
        with st.form("form_carga_rapida", clear_on_submit=True):
            col_scan1, col_scan2 = st.columns([4, 1])
            codigo_manual_scan = col_scan1.text_input(
                "Ingreso Manual o Pistola (ID variante CODIGO_MARCA):",
                key=f"scan_{vendedor}"
            )
            submit_btn = col_scan2.form_submit_button("➕ Agregar Artículo", use_container_width=True)

            if submit_btn and codigo_manual_scan:
                exito, msj = agregar_al_carrito(str(vendedor), codigo_manual_scan)
                if exito:
                    st.success(msj)
                    st.rerun()
                else:
                    st.error(msj)

    with t_ia:
        with st.form("form_ia_mostrador", clear_on_submit=True):
            col_ia1, col_ia2 = st.columns([4, 1])
            orden_usuario_mostrador = col_ia1.text_input("Dicte o escriba su orden:", key=f"ia_most_{vendedor}")
            submit_ia = col_ia2.form_submit_button("🤖 Ejecutar", use_container_width=True)

            if submit_ia and orden_usuario_mostrador:
                with st.spinner("Hafid IA procesando..."):
                    inventario = obtener_inventario_completo() or []
                    respuesta_json = procesar_orden_voz(orden_usuario_mostrador, inventario) or {}
                    accion = respuesta_json.get("accion")

                    if accion == "agregar_carrito":
                        termino = str(respuesta_json.get("termino", ""))
                        cant_raw = respuesta_json.get("cantidad")
                        cant = int(cant_raw) if cant_raw is not None and str(cant_raw).isdigit() else 1

                        terminos_busqueda = normalizar_para_busqueda(termino).split()

                        encontrados = []
                        for p in inventario:
                            marca_p = p.get('marca', p.get('condicion', ''))
                            texto_item = f"{p.get('descripcion', '')} {p.get('codigo', '')} {p.get('vehiculo', '')} {marca_p}"
                            texto_norm = normalizar_para_busqueda(texto_item)
                            if all(t in texto_norm for t in terminos_busqueda):
                                encontrados.append(p)

                        if len(encontrados) == 1:
                            exito, msj_db = agregar_al_carrito(str(vendedor), encontrados[0]['id'], cant)
                            if exito:
                                st.success(f"🛒 {msj_db}")
                                st.session_state.resultados_ia_mostrador = None
                                st.rerun()
                            else:
                                st.error(f"❌ Error: {msj_db}")
                        elif len(encontrados) > 1:
                            st.warning(f"Encontré {len(encontrados)} alternativas para '{termino}'.")
                            st.session_state.resultados_ia_mostrador = encontrados
                            st.session_state.msg_ia_mostrador = f"Elegí qué variante de '{termino}' querés agregar:"
                        else:
                            st.error(f"❌ No encontré ningún producto asociado a '{termino}'.")

                    elif accion == "set_cliente":
                        nombre_det = respuesta_json.get("nombre_cliente", "").upper()
                        clientes_db = obtener_clientes() or {}
                        cliente_encontrado = next(
                            (c for c in clientes_db.values() if nombre_det in str(c.get('nombre', '')).upper()),
                            None
                        )

                        if cliente_encontrado:
                            st.session_state.cliente_activo = {
                                "nombre": cliente_encontrado['nombre'],
                                "descuento": float(cliente_encontrado.get('descuento', 0.0))
                            }
                            st.success(f"✅ Cliente {cliente_encontrado['nombre']} activado.")
                            st.session_state.resultados_ia_mostrador = None
                            st.rerun()
                        else:
                            st.warning(f"⚠️ '{nombre_det}' no está en la base de datos.")

                    elif accion == "buscar" or accion == "consulta":
                        termino = respuesta_json.get("termino", "")
                        if not termino:
                            termino = orden_usuario_mostrador

                        if termino:
                            terminos_busqueda = normalizar_para_busqueda(termino).split()
                            encontrados = []
                            for prod in inventario:
                                if isinstance(prod, dict):
                                    marca_p = prod.get('marca', prod.get('condicion', ''))
                                    texto_item = (
                                        f"{prod.get('descripcion', '')} {marca_p} "
                                        f"{prod.get('vehiculo', '')} {prod.get('codigo', '')}"
                                    )
                                    texto_norm = normalizar_para_busqueda(texto_item)
                                    if all(t in texto_norm for t in terminos_busqueda):
                                        encontrados.append(prod)

                            if encontrados:
                                st.session_state.resultados_ia_mostrador = encontrados[:10]
                                st.session_state.msg_ia_mostrador = f"🔍 Encontré estas opciones para '{termino}':"
                            else:
                                st.warning(f"No encontré coincidencias exactas para '{termino}'.")
                                st.session_state.resultados_ia_mostrador = None
                        else:
                            st.warning("No detecté qué producto querés buscar.")
                            st.session_state.resultados_ia_mostrador = None
                    else:
                        st.info("Orden procesada, pero no aplica a la vista Mostrador.")
                        st.session_state.resultados_ia_mostrador = None

        if st.session_state.resultados_ia_mostrador:
            st.markdown(f"### {st.session_state.msg_ia_mostrador}")
            grupos_most = agrupar_por_maestro(st.session_state.resultados_ia_mostrador)
            for key in sorted(grupos_most.keys(), key=lambda k: grupos_most[k]['descripcion']):
                g = grupos_most[key]
                st.markdown(f"**{g['descripcion']}** ({g['vehiculo']}) — Cód. {g['codigo']}")
                for res in g['variantes']:
                    precio_f = float(res.get('precio_venta', 0))
                    marca_res = res.get('marca', res.get('condicion', ''))
                    btn_label = (
                        f"➕ {marca_res}: {res.get('stock', 0)} u. — "
                        f"${precio_f:,.2f}"
                    )
                    if st.button(btn_label, key=f"btn_add_most_{res.get('id', 'N')}"):
                        exito, msj_db = agregar_al_carrito(str(vendedor), res.get('id'), 1)
                        if exito:
                            st.success("🛒 Agregado al carrito!")
                            st.session_state.resultados_ia_mostrador = None
                            st.rerun()
                        else:
                            st.error(f"❌ Error: {msj_db}")

            if st.button("❌ Cancelar Búsqueda", key="btn_cancel_search_most"):
                st.session_state.resultados_ia_mostrador = None
                st.rerun()

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
            if exito:
                st.success(msj)
                st.rerun()
            else:
                st.error(msj)

        pdf_bytes = generar_pdf_presupuesto(
            str(vendedor), carrito, total_bruto,
            st.session_state.cliente_activo['nombre'], desc_porc
        )
        col_pdf.download_button(
            "📄 Imprimir PDF", pdf_bytes, f"Presupuesto_{vendedor}.pdf", "application/pdf",
            use_container_width=True
        )

        if col_vac.button("🗑️ Vaciar", use_container_width=True):
            vaciar_carrito(str(vendedor))
            st.rerun()

# --- ASISTENTE ---
elif pagina == "asistente":
    titulo_seccion("Asistente de depósito", "Ctrl+A")
    ayuda(
        "Ayuda — Comandos",
        "Búsqueda de stock, reportes (ej. *menos de 3*), ubicación, altas/bajas, filtro por proveedor, "
        "completar descripciones por código. Resultados agrupados por artículo maestro.",
    )

    if "ultima_orden" not in st.session_state:
        st.session_state.ultima_orden = None
    if "ultima_respuesta" not in st.session_state:
        st.session_state.ultima_respuesta = None
    if "ultimo_estado" not in st.session_state:
        st.session_state.ultimo_estado = None
    if "df_reporte" not in st.session_state:
        st.session_state.df_reporte = None

    orden_usuario = st.chat_input("Dicte o escriba aquí...")

    if orden_usuario:
        st.session_state.ultima_orden = orden_usuario
        st.session_state.df_reporte = None

        with st.spinner("Procesando en el depósito..."):
            inventario = obtener_inventario_completo() or []

            respuesta_json = procesar_orden_voz(orden_usuario, inventario) or {}
            if not isinstance(respuesta_json, dict):
                respuesta_json = {}

            accion = respuesta_json.get("accion")
            texto_ia = respuesta_json.get("respuesta", "Lo siento, no entendí bien la orden.")

            if accion == "actualizar_ubicacion":
                termino = str(respuesta_json.get("termino", ""))
                pas = respuesta_json.get("pasillo")
                pis = respuesta_json.get("piso")
                mod = respuesta_json.get("modulo")
                fil = respuesta_json.get("fila")

                terminos_busqueda = normalizar_para_busqueda(termino).split()
                encontrados = []
                for p in inventario:
                    texto_item = f"{p.get('descripcion', '')} {p.get('codigo', '')} {p.get('vehiculo', '')}"
                    texto_norm = normalizar_para_busqueda(texto_item)
                    if all(t in texto_norm for t in terminos_busqueda):
                        encontrados.append(p)

                maestros_unicos = {p.get('id_maestro', p.get('codigo')) for p in encontrados}

                if len(maestros_unicos) == 1 and encontrados:
                    id_ref = encontrados[0].get('id_maestro') or encontrados[0].get('codigo')
                    exito, msj_db = actualizar_ubicacion_relevamiento(id_ref, pas, pis, mod, fil)
                    st.session_state.ultima_respuesta = f"✅ Ubicación guardada. {msj_db}" if exito else f"❌ Error: {msj_db}"
                    st.session_state.ultimo_estado = "success" if exito else "error"
                elif len(encontrados) > 1:
                    st.session_state.ultima_respuesta = (
                        f"⚠️ Hay {len(encontrados)} variantes para '{termino}'. "
                        f"Dictá el código maestro exacto o el ID variante (CODIGO_MARCA)."
                    )
                    st.session_state.ultimo_estado = "normal"
                else:
                    st.session_state.ultima_respuesta = f"❌ No encontré ningún producto que coincida con '{termino}'."
                    st.session_state.ultimo_estado = "error"

            elif accion == "reporte_stock":
                raw_cant = respuesta_json.get("cantidad")
                cant_limite = int(raw_cant) if raw_cant is not None and str(raw_cant).isdigit() else 3
                operador = respuesta_json.get("operador", "menor_o_igual")

                texto_usuario_norm = normalizar_para_busqueda(orden_usuario)
                if operador != "exacto" and re.search(r'\b(mas de|al menos|mayor que|mayor a|o mas|>=|\+)\b', texto_usuario_norm):
                    operador = "mayor_o_igual"

                if operador == "exacto":
                    bajo_stock = [p for p in inventario if int(p.get('stock', 0)) == cant_limite]
                    msg_op = f"exactamente {cant_limite}"
                elif operador == "mayor_o_igual":
                    bajo_stock = [p for p in inventario if int(p.get('stock', 0)) >= cant_limite]
                    msg_op = f"{cant_limite} o más"
                else:
                    bajo_stock = [p for p in inventario if int(p.get('stock', 0)) <= cant_limite]
                    msg_op = f"{cant_limite} o menos"

                if bajo_stock:
                    st.session_state.ultima_respuesta = f"📉 **Reporte de stock:** {len(bajo_stock)} variantes con {msg_op} unidades."
                    df_r = pd.DataFrame(bajo_stock)
                    cols_rep = ['codigo', 'descripcion', 'vehiculo', 'marca', 'stock']
                    st.session_state.df_reporte = df_r[[c for c in cols_rep if c in df_r.columns]]
                    st.session_state.ultimo_estado = "normal"
                else:
                    st.session_state.ultima_respuesta = f"✅ Excelente. No hay variantes con {msg_op} unidades."
                    st.session_state.ultimo_estado = "success"

            elif accion == "set_cliente":
                nombre_det = respuesta_json.get("nombre_cliente", "").upper()
                clientes_db = obtener_clientes() or {}
                cliente_encontrado = next(
                    (c for c in clientes_db.values() if nombre_det in str(c.get('nombre', '')).upper()),
                    None
                )

                if cliente_encontrado:
                    st.session_state.cliente_activo = {
                        "nombre": cliente_encontrado['nombre'],
                        "descuento": float(cliente_encontrado.get('descuento', 0.0))
                    }
                    st.session_state.ultima_respuesta = (
                        f"✅ Listo. Cliente {cliente_encontrado['nombre']} activado "
                        f"({cliente_encontrado['descuento']}% descuento)."
                    )
                    st.session_state.ultimo_estado = "success"
                else:
                    st.session_state.cliente_activo = {"nombre": nombre_det, "descuento": 0.0}
                    st.session_state.ultima_respuesta = (
                        f"⚠️ Activado. (Nota: '{nombre_det}' no está en la base de datos, aplicará 0% descuento)."
                    )
                    st.session_state.ultimo_estado = "normal"

            elif accion == "agregar_carrito":
                termino = str(respuesta_json.get("termino", ""))
                cant_raw = respuesta_json.get("cantidad")
                cant = int(cant_raw) if cant_raw is not None and str(cant_raw).isdigit() else 1
                terminos_busqueda = normalizar_para_busqueda(termino).split()

                encontrados = []
                for p in inventario:
                    texto_item = f"{p.get('descripcion', '')} {p.get('codigo', '')} {p.get('vehiculo', '')} {p.get('marca', '')}"
                    texto_norm = normalizar_para_busqueda(texto_item)
                    if all(t in texto_norm for t in terminos_busqueda):
                        encontrados.append(p)

                if len(encontrados) == 1:
                    exito, msj_db = agregar_al_carrito("Caja Principal", encontrados[0]['id'], cant)
                    st.session_state.ultima_respuesta = "🛒 Listo. Agregado al carrito." if exito else f"❌ Error: {msj_db}"
                    st.session_state.ultimo_estado = "success" if exito else "error"
                elif len(encontrados) > 1:
                    lista_alt = "\n".join([
                        f"- {p.get('descripcion')} | Marca {p.get('marca')} ({p.get('id')})"
                        for p in encontrados
                    ])
                    st.session_state.ultima_respuesta = (
                        f"⚠️ Hay múltiples variantes para '{termino}'. Indicá marca o ID CODIGO_MARCA:\n{lista_alt}"
                    )
                    st.session_state.ultimo_estado = "normal"
                else:
                    st.session_state.ultima_respuesta = f"❌ No encontré ningún producto que coincida con '{termino}'."
                    st.session_state.ultimo_estado = "error"

            elif accion in ["baja", "alta"]:
                termino = str(respuesta_json.get("termino", ""))
                cant_raw = respuesta_json.get("cantidad")
                cant = int(cant_raw) if cant_raw is not None and str(cant_raw).isdigit() else 1
                terminos_busqueda = normalizar_para_busqueda(termino).split()

                encontrados = []
                for p in inventario:
                    texto_item = f"{p.get('descripcion', '')} {p.get('codigo', '')} {p.get('vehiculo', '')} {p.get('marca', '')} {p.get('id', '')}"
                    texto_norm = normalizar_para_busqueda(texto_item)
                    if all(t in texto_norm for t in terminos_busqueda):
                        encontrados.append(p)

                if len(encontrados) == 1:
                    if accion == "alta":
                        exito, msj_db = registrar_aumento_stock(encontrados[0]['id'], cant)
                    else:
                        exito, msj_db = registrar_merma(encontrados[0]['id'], cant)

                    st.session_state.ultima_respuesta = "✅ Listo. Operación registrada." if exito else f"❌ Error: {msj_db}"
                    st.session_state.ultimo_estado = "success" if exito else "error"
                elif len(encontrados) > 1:
                    st.session_state.ultima_respuesta = (
                        f"⚠️ Hay {len(encontrados)} variantes para '{termino}'. "
                        f"Dictá el ID exacto (ej: CODIGO_MARCA) para operar sobre una marca."
                    )
                    st.session_state.ultimo_estado = "normal"
                else:
                    st.session_state.ultima_respuesta = f"❌ No existe '{termino}' en el sistema."
                    st.session_state.ultimo_estado = "error"

            elif accion == "agregar_descripcion":
                codigo = str(respuesta_json.get("codigo", "")).strip()
                texto_nuevo = str(respuesta_json.get("texto", "")).strip()
                if not codigo or not texto_nuevo:
                    st.session_state.ultima_respuesta = "❌ No detecté el código o el texto a agregar."
                    st.session_state.ultimo_estado = "error"
                else:
                    exito, msj_db = agregar_texto_descripcion(codigo, texto_nuevo)
                    st.session_state.ultima_respuesta = f"✅ {msj_db}" if exito else f"❌ {msj_db}"
                    st.session_state.ultimo_estado = "success" if exito else "error"

            elif accion == "filtrar_proveedor":
                prov_buscado = str(respuesta_json.get("proveedor", "")).strip()
                provs_catalogo = obtener_proveedores() or {}
                cuits_match, terminos_clave = _cuits_proveedor_en_catalogo(prov_buscado, provs_catalogo)

                encontrados = []
                for p in inventario:
                    if not isinstance(p, dict):
                        continue
                    cuit_p = "".join(filter(str.isdigit, str(p.get("cuit_proveedor", ""))))
                    if cuit_p and cuit_p in cuits_match:
                        encontrados.append(p)
                        continue
                    if terminos_clave and _proveedor_coincide_busqueda(
                        terminos_clave, str(p.get("proveedor", ""))
                    ):
                        encontrados.append(p)

                if encontrados:
                    st.session_state.ultima_respuesta = (
                        f"🏭 **Filtro por proveedor:** {len(encontrados)} variantes de '{prov_buscado}'."
                    )
                    df_r = pd.DataFrame(encontrados)
                    cols_mostrar = ['codigo', 'descripcion', 'vehiculo', 'marca', 'stock', 'precio_venta']
                    cols_ok = [c for c in cols_mostrar if c in df_r.columns]
                    st.session_state.df_reporte = df_r[cols_ok]
                    st.session_state.ultimo_estado = "normal"
                else:
                    st.session_state.ultima_respuesta = f"⚠️ No encontré repuestos del proveedor '{prov_buscado}'."
                    st.session_state.ultimo_estado = "normal"

            elif accion == "buscar" or accion == "consulta" or accion == "ubicacion":
                termino = str(respuesta_json.get("termino", ""))
                if not termino:
                    termino = orden_usuario

                terminos_busqueda = normalizar_para_busqueda(termino).split()

                encontrados = []
                for p in inventario:
                    if isinstance(p, dict):
                        marca_p = p.get('marca', p.get('condicion', ''))
                        texto_item = f"{p.get('descripcion', '')} {p.get('vehiculo', '')} {marca_p} {p.get('codigo', '')}"
                        texto_norm = normalizar_para_busqueda(texto_item)
                        if all(t in texto_norm for t in terminos_busqueda):
                            encontrados.append(p)

                if encontrados:
                    st.session_state.ultima_respuesta = texto_resultados_agrupados(encontrados, termino)
                    st.session_state.ultimo_estado = "normal"
                else:
                    st.session_state.ultima_respuesta = f"No hay stock de nada relacionado con '{termino}'."
                    st.session_state.ultimo_estado = "error"

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

            if st.session_state.get("df_reporte") is not None:
                st.dataframe(st.session_state.df_reporte, hide_index=True, use_container_width=True)

# --- CONFIGURACIÓN ---
elif pagina == "config":
    titulo_seccion("Configuración", "Ctrl+C")

    with st.expander("Backup y restauración de stock", expanded=False):
        col_down, col_up = st.columns(2)
        with col_down:
            st.caption("Descargar inventario actual (CSV).")
            csv_data = exportar_inventario_csv()
            if csv_data:
                st.download_button(
                    "Descargar CSV",
                    data=csv_data,
                    file_name=f"backup_inventario_{datetime.now().strftime('%Y%m%d')}.csv",
                    mime="text/csv",
                    type="primary",
                )
            else:
                st.download_button(
                    "Descargar CSV",
                    data="",
                    file_name="vacio.csv",
                    disabled=True,
                    help="El inventario está vacío",
                )

        with col_up:
            st.caption("Restaurar o sumar stock desde CSV.")
            archivo_csv = st.file_uploader("Restaurar stock (CSV)", type=["csv"])
            if archivo_csv:
                df_upload = pd.read_csv(archivo_csv)
                st.write(f"Vista previa: {len(df_upload)} filas.")
                modo_restauracion = st.radio(
                    "Modo",
                    ["sumar_stock", "sobreescribir"],
                    format_func=lambda x: "Sumar stock" if x == "sumar_stock" else "Sobreescribir todo (peligro)",
                )
                if st.button("Ejecutar restauración", type="primary"):
                    with st.spinner("Procesando archivo..."):
                        exito, msg_rest = restaurar_inventario_csv(df_upload, modo=str(modo_restauracion))
                        if exito:
                            st.success(msg_rest)
                            st.rerun()

    with st.expander("Zona de peligro — borrar base de datos"):
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
        st.subheader("Directorio de Proveedores")
        st.caption("Editá recargos y descuentos en la grilla. Usá el formulario de abajo solo para **dar de alta** uno nuevo.")

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
                    "Descuento (%)": float(datos_prov.get('descuento', 0)),
                    "Recargo Contado (%)": float(condiciones.get('Contado', 0)),
                    "Recargo 30 Días (%)": float(condiciones.get('30 Días', 0)),
                })

            df_prov = pd.DataFrame(datos_tabla)

            df_prov_edit = st.data_editor(
                df_prov,
                use_container_width=True,
                hide_index=True,
                column_config={
                    "Proveedor": st.column_config.TextColumn("Proveedor", disabled=True),
                    "CUIT": st.column_config.TextColumn("CUIT", disabled=True),
                    "Descuento (%)": st.column_config.NumberColumn("Descuento (%)", min_value=0.0, step=1.0),
                    "Recargo Contado (%)": st.column_config.NumberColumn("Rec. Contado (%)", min_value=0.0, step=1.0),
                    "Recargo 30 Días (%)": st.column_config.NumberColumn("Rec. 30 Días (%)", min_value=0.0, step=1.0),
                },
                key="grilla_proveedores"
            )

            if st.button("💾 Guardar Cambios de Proveedores", type="primary", use_container_width=True):
                guardados = 0
                filas = df_prov_edit.to_dict('records')
                originales = {r['CUIT']: r for r in datos_tabla}

                for fila in filas:
                    cuit = str(fila.get('CUIT', ''))
                    orig = originales.get(cuit, {})
                    cambio = (
                        float(fila.get('Descuento (%)', 0)) != float(orig.get('Descuento (%)', 0))
                        or float(fila.get('Recargo Contado (%)', 0)) != float(orig.get('Recargo Contado (%)', 0))
                        or float(fila.get('Recargo 30 Días (%)', 0)) != float(orig.get('Recargo 30 Días (%)', 0))
                    )
                    if cambio:
                        configurar_proveedor(
                            fila.get('Proveedor', orig.get('Proveedor', '')),
                            cuit,
                            float(fila.get('Recargo Contado (%)', 0)),
                            float(fila.get('Recargo 30 Días (%)', 0)),
                            float(fila.get('Descuento (%)', 0)),
                        )
                        guardados += 1

                if guardados:
                    st.success(f"✅ {guardados} proveedor(es) actualizado(s).")
                    st.rerun()
                else:
                    st.info("No hubo cambios para guardar.")

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
        else:
            st.info("Aún no hay proveedores cargados.")

        st.divider()
        st.subheader("➕ Alta de Proveedor Nuevo")
        with st.form("conf_prov"):
            col1, col2 = st.columns(2)
            nombre_prov = col1.text_input("Nombre Proveedor (Ej: Filtros Juan)").upper()
            cuit_prov = col2.text_input("CUIT (Solo números)")

            st.write("Recargos Financieros y Descuentos (%)")
            col3, col4, col5 = st.columns(3)
            rec_contado = col3.number_input("Recargo Contado (%)", min_value=0.0, value=0.0, step=1.0)
            rec_30 = col4.number_input("Recargo 30 Días (%)", min_value=0.0, value=15.0, step=1.0)
            desc_prov = col5.number_input("Descuento Factura (%)", min_value=0.0, value=0.0, step=1.0)

            if st.form_submit_button("Guardar Proveedor Nuevo"):
                if nombre_prov and cuit_prov:
                    configurar_proveedor(nombre_prov, cuit_prov, rec_contado, rec_30, desc_prov)
                    st.success(f"Proveedor {nombre_prov} guardado.")
                    st.rerun()
                else:
                    st.error("El nombre y el CUIT son obligatorios.")

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
