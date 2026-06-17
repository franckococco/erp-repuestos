import streamlit as st
import os
import traceback

# Debe ser la primera llamada a Streamlit (evita pantalla genérica "Oh, no")
st.set_page_config(page_title="Hafid Repuestos", layout="wide", initial_sidebar_state="expanded")

if "_firebase_ok" not in st.session_state:
    st.session_state._firebase_ok = False

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
    "util_busqueda.py",
    "util_imagen.py",
    "util_branding.py",
    "factura_borrador.py",
    "ui_carga_factura.py",
    "carga_producto_voz.py",
    "factura_arca_client.py",
    "factura_arca_pdf.py",
    "ui_mostrador.py",
    "ia_mostrador.py",
    "mostrador_voz_flujo.py",
    "mostrador_session.py",
    "util_pdf.py",
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
        cliente_consumidor_final,
        cliente_db_a_activo,
        actualizar_ubicacion_relevamiento,
        actualizar_producto_desde_grilla,
        obtener_producto_por_codigo,
        exportar_inventario_csv,
        restaurar_inventario_csv,
        agregar_texto_descripcion,
        cambiar_marca_por_codigo,
        cambiar_vehiculos_por_codigo,
        edicion_masiva_descripcion,
        edicion_masiva_marca,
        sanitizar_clave_marca,
        formatear_id_variante,
    )
    from modulos.carga_producto_voz import (
        validar_y_preparar_carga_producto_voz,
        ejecutar_carga_producto_voz,
    )
    from modulos.generador_qr import generar_qr_producto
    from modulos.ui_estilos import aplicar_estilos_globales, render_sidebar, titulo_seccion, ayuda, metricas_inventario
    from modulos.util_branding import ruta_logo_hafid
    from modulos.util_vehiculos import OPCIONES_VEHICULO, normalizar_lista_vehiculos, vehiculos_a_texto
    from modulos.util_busqueda import (
        normalizar_para_busqueda,
        filtrar_por_busqueda,
        texto_item_inventario,
    )

    if not st.session_state._firebase_ok:
        get_db()
        st.session_state._firebase_ok = True
    else:
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

# Atajos Ctrl+S/I/M/A/C: desactivados en Cloud (evita components.html al arrancar).

# --- Búsqueda de proveedores (normalización en util_busqueda) ---
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


def buscar_en_inventario(items, termino):
    return filtrar_por_busqueda(items, termino, texto_item_inventario)


def filtrar_inventario(items, termino_busqueda):
    return buscar_en_inventario(items, termino_busqueda)


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


# --- FUNCIÓN PARA EL GENERADOR DE PDF (presupuesto → modulos/presupuesto_pdf.py) ---

if "temp_datos" not in st.session_state:
    st.session_state.temp_datos = None
if "borrador_id" not in st.session_state:
    st.session_state.borrador_id = None
if "cliente_activo" not in st.session_state:
    st.session_state.cliente_activo = cliente_consumidor_final()
if "factura_arca_reciente" not in st.session_state:
    st.session_state.factura_arca_reciente = None
if "presupuesto_cargado_id" not in st.session_state:
    st.session_state.presupuesto_cargado_id = None
if "mostrador_accion_pendiente" not in st.session_state:
    st.session_state.mostrador_accion_pendiente = None
if "resultados_ia_mostrador" not in st.session_state:
    st.session_state.resultados_ia_mostrador = None
if "msg_ia_mostrador" not in st.session_state:
    st.session_state.msg_ia_mostrador = None
if "mostrador_listo_para_ticket" not in st.session_state:
    st.session_state.mostrador_listo_para_ticket = False
if "hist_arca_resultados" not in st.session_state:
    st.session_state.hist_arca_resultados = None
if "hist_arca_preview" not in st.session_state:
    st.session_state.hist_arca_preview = None
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
            term_busqueda = str(busqueda_inv or "").strip()

            if not term_busqueda:
                metricas_inventario(inv)
                st.info(
                    "Escribí en el buscador para ver repuestos. "
                    "Podés buscar por código, descripción, marca, vehículo o proveedor."
                )
            else:
                inv_filtrado = filtrar_inventario(inv, busqueda_inv)
                metricas_inventario(inv_filtrado)

                if not inv_filtrado:
                    st.info(f"Sin coincidencias para «{term_busqueda}».")
                else:
                    with st.expander("Edición masiva (resultados filtrados)", expanded=False):
                        n_maestros = len({
                            str(i.get("id_maestro") or i.get("codigo") or "")
                            for i in inv_filtrado
                        })
                        st.caption(
                            f"{len(inv_filtrado)} variantes · {n_maestros} código(s) maestro(s)"
                        )
                        col_desc, col_marca = st.columns(2)
                        with col_desc:
                            modo_desc = st.radio(
                                "Descripción",
                                ["Agregar texto", "Reemplazar descripción"],
                                horizontal=True,
                                key="mass_desc_mode",
                            )
                            texto_desc = st.text_input("Texto", key="mass_desc_text", label_visibility="collapsed")
                            if st.button("Aplicar descripción al filtro", key="mass_desc_btn"):
                                modo = "agregar" if modo_desc == "Agregar texto" else "reemplazar"
                                exito, msg = edicion_masiva_descripcion(inv_filtrado, modo, texto_desc)
                                if exito:
                                    st.success(msg)
                                    st.rerun()
                                else:
                                    st.error(msg)
                        with col_marca:
                            st.caption("Marca: solo códigos con una sola variante.")
                            marca_nueva = st.text_input("Nueva marca", key="mass_marca_text")
                            if st.button("Cambiar marca en filtro", key="mass_marca_btn"):
                                exito, msg = edicion_masiva_marca(inv_filtrado, marca_nueva)
                                if exito:
                                    st.success(msg)
                                    st.rerun()
                                else:
                                    st.error(msg)

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
                                with st.expander(titulo, expanded=True):
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

                    elif vista_inv == "Editar variantes":
                        ayuda(
                            "Ayuda — Edición",
                            "Editá descripción, vehículo (varios separados por coma), marca, stock, precio y ubicación. "
                            "El vehículo se aplica al código maestro (todas las variantes). Luego **Guardar cambios**.",
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
    from modulos.mostrador_session import init_credenciales_arca_session

    init_credenciales_arca_session()
    from modulos.ui_mostrador import (
        render_seccion_cliente_mostrador,
        render_credenciales_arca,
        render_buscador_productos,
        render_carrito_grilla,
        render_presupuestos_guardados,
        render_ia_mostrador,
        render_panel_coincidencias_mostrador,
        render_mostrador_venta_actual,
        render_mostrador_accion_pendiente,
        render_descarga_presupuesto_prominente,
        render_factura_arca_exitosa,
        render_historial_facturas_arca,
        VENDEDOR_MOSTRADOR,
    )

    from modulos.ui_estilos import aplicar_estilos_mostrador
    from modulos.mostrador_voz_flujo import inventario_cache_mostrador

    aplicar_estilos_mostrador()
    titulo_seccion("Mostrador / Presupuesto", "Ctrl+M")

    vista_mostrador = st.radio(
        "Vista mostrador",
        ["🛒 Caja / Presupuesto", "🧾 Facturas ARCA"],
        horizontal=True,
        label_visibility="collapsed",
        key="mostrador_vista_principal",
    )

    if vista_mostrador.startswith("🧾"):
        render_historial_facturas_arca()
    else:
        bar_cli, bar_cred = st.columns([3, 2])
        with bar_cli:
            render_seccion_cliente_mostrador()
        with bar_cred:
            render_credenciales_arca()

        vendedor = VENDEDOR_MOSTRADOR

        if render_factura_arca_exitosa("top"):
            st.divider()

        carrito_full = obtener_carrito(str(vendedor)) or []
        if carrito_full:
            render_carrito_grilla(vendedor, carrito_full)
            render_descarga_presupuesto_prominente(vendedor)
            st.divider()

        col_izq, col_der = st.columns([3, 2], gap="large")
        inv_mostrador = inventario_cache_mostrador(obtener_inventario_completo, ttl_seg=300)

        with col_izq:
            render_ia_mostrador(
                vendedor,
                obtener_inventario_completo,
                buscar_en_inventario,
                agrupar_por_maestro,
                agregar_al_carrito,
            )
            st.divider()
            render_presupuestos_guardados(vendedor)

            t_buscar, t_manual, t_qr = st.tabs(
                ["🔍 Buscador", "⌨️ Pistola / Manual", "📷 Escáner QR"]
            )

            with t_buscar:
                if inv_mostrador:
                    render_buscador_productos(vendedor, inv_mostrador, agregar_al_carrito, filtrar_inventario)
                    render_panel_coincidencias_mostrador(vendedor, agrupar_por_maestro, agregar_al_carrito)
                else:
                    st.info("El inventario está vacío. Agregue productos primero.")

            with t_manual:
                with st.form("form_carga_rapida", clear_on_submit=True):
                    col_scan1, col_scan2 = st.columns([4, 1])
                    codigo_manual_scan = col_scan1.text_input(
                        "Ingreso Manual o Pistola (ID variante CODIGO_MARCA):",
                        key=f"scan_{vendedor}",
                    )
                    submit_btn = col_scan2.form_submit_button("➕ Agregar", use_container_width=True)

                    if submit_btn and codigo_manual_scan:
                        exito, msj = agregar_al_carrito(str(vendedor), codigo_manual_scan)
                        if exito:
                            st.success(msj)
                            st.rerun()
                        else:
                            st.error(msj)

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

            if st.session_state.get("mostrador_accion_pendiente"):
                render_mostrador_accion_pendiente(vendedor)

        with col_der:
            render_mostrador_venta_actual(vendedor)

# --- ASISTENTE ---
elif pagina == "asistente":
    titulo_seccion("Asistente de depósito", "Ctrl+A")
    ayuda(
        "Ayuda — Comandos",
        "Búsqueda de stock (acepta plural/singular: *bujes* = *buje*), reportes (ej. *menos de 3*), "
        "ubicación, **altas/bajas de stock** (ej. *sumá 5 al código 1491*), "
        "**cargar producto nuevo** (ej. *cargame el 25412 buje amortiguador para gol, 4 unidades, pasillo 2*), "
        "filtro por proveedor, completar descripciones por código, **cambiar marca** (código con una sola variante), "
        "**cambiar vehículos** (reemplazar, agregar o quitar por código). "
        "Resultados agrupados por artículo maestro.",
    )

    if "ultima_orden" not in st.session_state:
        st.session_state.ultima_orden = None
    if "ultima_respuesta" not in st.session_state:
        st.session_state.ultima_respuesta = None
    if "ultimo_estado" not in st.session_state:
        st.session_state.ultimo_estado = None
    if "df_reporte" not in st.session_state:
        st.session_state.df_reporte = None
    if "producto_pendiente_voz" not in st.session_state:
        st.session_state.producto_pendiente_voz = None

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

            if accion != "cargar_producto":
                st.session_state.producto_pendiente_voz = None

            if accion == "actualizar_ubicacion":
                termino = str(respuesta_json.get("termino", ""))
                pas = respuesta_json.get("pasillo")
                pis = respuesta_json.get("piso")
                mod = respuesta_json.get("modulo")
                fil = respuesta_json.get("fila")

                encontrados = buscar_en_inventario(inventario, termino)

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
                    st.session_state.cliente_activo = cliente_db_a_activo(cliente_encontrado)
                    st.session_state.ultima_respuesta = (
                        f"✅ Listo. Cliente {cliente_encontrado['nombre']} activado "
                        f"({cliente_encontrado.get('descuento', 0)}% descuento)."
                    )
                    st.session_state.ultimo_estado = "success"
                else:
                    st.session_state.cliente_activo = {
                        "nombre": nombre_det,
                        "cuit": "00000000000",
                        "descuento": 0.0,
                        "tipo_comprobante": "6",
                    }
                    st.session_state.ultima_respuesta = (
                        f"⚠️ Activado. (Nota: '{nombre_det}' no está en la base de datos, aplicará 0% descuento)."
                    )
                    st.session_state.ultimo_estado = "normal"

            elif accion == "agregar_carrito":
                termino = str(respuesta_json.get("termino", ""))
                cant_raw = respuesta_json.get("cantidad")
                cant = int(cant_raw) if cant_raw is not None and str(cant_raw).isdigit() else 1
                encontrados = buscar_en_inventario(inventario, termino)

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
                encontrados = buscar_en_inventario(inventario, termino)

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

            elif accion == "cambiar_marca":
                codigo = str(respuesta_json.get("codigo", "")).strip()
                marca_nueva = str(respuesta_json.get("marca_nueva", "")).strip()
                if not codigo or not marca_nueva:
                    st.session_state.ultima_respuesta = "❌ No detecté el código o la marca nueva."
                    st.session_state.ultimo_estado = "error"
                else:
                    exito, msj_db = cambiar_marca_por_codigo(codigo, marca_nueva)
                    st.session_state.ultima_respuesta = f"✅ {msj_db}" if exito else f"❌ {msj_db}"
                    st.session_state.ultimo_estado = "success" if exito else "error"

            elif accion == "cambiar_vehiculos":
                codigo = str(respuesta_json.get("codigo", "")).strip()
                modo = str(respuesta_json.get("modo", "reemplazar")).strip().lower()
                vehs = respuesta_json.get("vehiculos", [])
                if isinstance(vehs, str):
                    vehs = [vehs]
                if not codigo:
                    st.session_state.ultima_respuesta = "❌ No detecté el código."
                    st.session_state.ultimo_estado = "error"
                else:
                    exito, msj_db = cambiar_vehiculos_por_codigo(codigo, vehs, modo=modo)
                    st.session_state.ultima_respuesta = f"✅ {msj_db}" if exito else f"❌ {msj_db}"
                    st.session_state.ultimo_estado = "success" if exito else "error"

            elif accion == "cargar_producto":
                ok_prep, payload, msg_prep = validar_y_preparar_carga_producto_voz(respuesta_json)
                if ok_prep and payload:
                    st.session_state.producto_pendiente_voz = {
                        "payload": payload,
                        "mensaje": msg_prep,
                    }
                    st.session_state.ultima_respuesta = (
                        f"{msg_prep}\n\n**Confirmá o cancelá la carga con los botones de abajo.**"
                    )
                    st.session_state.ultimo_estado = "normal"
                else:
                    st.session_state.producto_pendiente_voz = None
                    st.session_state.ultima_respuesta = f"❌ {msg_prep}"
                    st.session_state.ultimo_estado = "error"

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

                encontrados = buscar_en_inventario(inventario, termino)

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

            if st.session_state.producto_pendiente_voz:
                pend = st.session_state.producto_pendiente_voz
                col_ok, col_no = st.columns(2)
                if col_ok.button("Confirmar carga", type="primary", use_container_width=True, key="btn_conf_prod_voz"):
                    exito, msj_db = ejecutar_carga_producto_voz(pend.get("payload"))
                    st.session_state.producto_pendiente_voz = None
                    st.session_state.ultima_orden = "Confirmar carga de producto"
                    st.session_state.ultima_respuesta = f"✅ {msj_db}" if exito else f"❌ {msj_db}"
                    st.session_state.ultimo_estado = "success" if exito else "error"
                    st.rerun()
                if col_no.button("Cancelar", use_container_width=True, key="btn_cancel_prod_voz"):
                    st.session_state.producto_pendiente_voz = None
                    st.session_state.ultima_orden = "Cancelar carga de producto"
                    st.session_state.ultima_respuesta = "Carga cancelada."
                    st.session_state.ultimo_estado = "normal"
                    st.rerun()

            if st.session_state.get("df_reporte") is not None:
                st.dataframe(st.session_state.df_reporte, hide_index=True, use_container_width=True)

# --- CONFIGURACIÓN ---
elif pagina == "config":
    from modulos.mostrador_session import init_credenciales_arca_session

    init_credenciales_arca_session()
    from modulos.ui_mostrador import render_config_ticket_mostrador

    titulo_seccion("Configuración", "Ctrl+C")

    render_config_ticket_mostrador(en_pagina_config=True)

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
            tipo_cli = st.radio(
                "Tipo de factura",
                options=["6", "1"],
                format_func=lambda x: (
                    "Factura B — Consumidor final"
                    if x == "6"
                    else "Factura A — Responsable inscripto"
                ),
                horizontal=False,
                key="conf_tipo_fc_cliente",
            )

            if st.form_submit_button("Guardar Cliente"):
                if nombre_cli and cuit_cli:
                    configurar_cliente(nombre_cli, cuit_cli, desc_cli, tipo_cli)
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
                    "Descuento": f"{d_cli.get('descuento', 0)}%",
                    "Comprobante": "A" if str(d_cli.get("tipo_comprobante", "6")) == "1" else "B",
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
