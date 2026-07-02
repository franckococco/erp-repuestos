import streamlit as st
import pandas as pd
from io import BytesIO
import zipfile

from modulos.ia_vision import procesar_factura_con_ia
from modulos.util_imagen import mejorar_imagen_documento
from modulos.factura_borrador import (
    guardar_borrador_factura,
    listar_borradores_factura,
    eliminar_borrador_factura,
    titulo_borrador,
)
from modulos.util_vehiculos import OPCIONES_VEHICULO, normalizar_lista_vehiculos, vehiculos_a_texto
from modulos.util_codigos import normalizar_codigos_en_articulos
from modulos.db_firebase import (
    registrar_ingreso_inteligente,
    obtener_proveedores,
    obtener_producto_por_codigo,
    sanitizar_clave_marca,
    formatear_id_variante,
)
from modulos.precios_proveedor import calcular_cascada_desde_proveedor
from modulos.generador_qr import generar_qr_producto
from modulos.ui_estilos import ayuda


def _datos_proveedor_factura(cuit, provs):
    cuit_l = "".join(filter(str.isdigit, str(cuit or "")))
    if cuit_l in (provs or {}) and isinstance(provs[cuit_l], dict):
        return provs[cuit_l]
    return {}


def _tabla_precios_calculados(df_editado, datos_prov, condicion_pago):
    filas = []
    for row in df_editado.to_dict("records"):
        precio = float(row.get("precio_unitario", 0) or 0)
        calc = calcular_cascada_desde_proveedor(precio, datos_prov, condicion_pago)
        filas.append({
            "Código": row.get("codigo", ""),
            "Marca": row.get("marca", ""),
            "P. lista": precio,
            "P. venta calc.": calc["precio_venta"],
        })
    return pd.DataFrame(filas)


def _vehiculos_por_codigo(articulos):
    """Unifica vehículos del maestro cuando el mismo código aparece en varias filas."""
    mapa = {}
    for art in articulos or []:
        cod = str(art.get("codigo", "")).strip()
        if not cod:
            continue
        vehs = normalizar_lista_vehiculos(art.get("vehiculos") or art.get("vehiculo"))
        if cod not in mapa:
            mapa[cod] = set()
        mapa[cod].update(vehs)
    resultado = {}
    for cod, conjunto in mapa.items():
        ordenados = [v for v in OPCIONES_VEHICULO if v in conjunto]
        resultado[cod] = ordenados or ["UNIVERSAL"]
    return resultado


def _articulos_desde_grilla(df_editado):
    filas = df_editado.to_dict("records") if df_editado is not None and not df_editado.empty else []
    resultado = []
    for row in filas:
        if not isinstance(row, dict):
            continue
        art = dict(row)
        art["descripcion"] = str(art.get("descripcion", "")).strip().upper()
        art["codigo"] = str(art.get("codigo", "")).strip().upper().replace("/", "-")
        vehs = normalizar_lista_vehiculos(art.get("vehiculos") or art.get("vehiculo"))
        art["vehiculos"] = vehs
        art["vehiculo"] = vehiculos_a_texto(vehs)
        art["marca"] = str(art.get("marca", "GENERICO")).strip().upper()
        resultado.append(art)

    mapa = _vehiculos_por_codigo(resultado)
    for art in resultado:
        cod = str(art.get("codigo", "")).strip()
        if cod in mapa:
            art["vehiculos"] = mapa[cod]
            art["vehiculo"] = vehiculos_a_texto(mapa[cod])
    return resultado


def _cargar_borrador_en_sesion(borrador):
    datos = {
        "proveedor": borrador.get("proveedor", ""),
        "cuit_proveedor": borrador.get("cuit_proveedor", ""),
        "punto_venta": borrador.get("punto_venta", ""),
        "numero_comprobante": borrador.get("numero_comprobante", ""),
        "articulos": borrador.get("articulos", []),
    }
    for art in datos["articulos"]:
        if isinstance(art, dict):
            art["vehiculos"] = normalizar_lista_vehiculos(art.get("vehiculos") or art.get("vehiculo"))
            art["vehiculo"] = vehiculos_a_texto(art["vehiculos"])
    st.session_state.temp_datos = datos
    st.session_state.borrador_id = borrador.get("id")
    st.session_state.condicion_pago_borrador = borrador.get("condicion_pago", "Contado")
    if "grilla_validacion" in st.session_state:
        del st.session_state["grilla_validacion"]


def _guardar_borrador_actual(d, df_editado, condicion_pago):
    arts_save = _articulos_desde_grilla(df_editado)
    payload = {**d, "articulos": arts_save, "condicion_pago": condicion_pago}
    ok, msg, bid = guardar_borrador_factura(payload, st.session_state.borrador_id)
    if ok:
        st.session_state.borrador_id = bid
        st.success(msg)
    else:
        st.error(msg)


def _enriquecer_articulos_factura(datos):
    for art in datos.get("articulos", []):
        if not isinstance(art, dict):
            continue
        cod = str(art.get("codigo", "")).strip()
        art["codigo_proveedor"] = cod
        prod_db = obtener_producto_por_codigo(cod)
        if prod_db:
            art["descripcion"] = prod_db.get("descripcion", art.get("descripcion"))
            vehs = prod_db.get("vehiculos") or prod_db.get("vehiculo", "UNIVERSAL")
            art["vehiculos"] = normalizar_lista_vehiculos(vehs)
        else:
            art["vehiculos"] = normalizar_lista_vehiculos(
                art.get("vehiculos") or art.get("vehiculo", "UNIVERSAL")
            )
        art["vehiculo"] = vehiculos_a_texto(art["vehiculos"])
        art["marca"] = art.get("marca", art.get("condicion", "GENERICO"))
        if "condicion" in art:
            del art["condicion"]
    return datos


def _archivo_en_memoria(nombre, contenido):
    buf = BytesIO(contenido)
    buf.name = nombre
    return buf


def _inicializar_cola_facturas():
    if "cola_facturas_pendientes" not in st.session_state:
        st.session_state.cola_facturas_pendientes = []
    if "cola_facturas_uid" not in st.session_state:
        st.session_state.cola_facturas_uid = 0


def _agregar_archivo_a_cola(archivo):
    nombre = getattr(archivo, "name", "factura")
    contenido = archivo.getvalue() if hasattr(archivo, "getvalue") else archivo.read()
    if not contenido:
        return False, "El archivo está vacío."
    cola = st.session_state.cola_facturas_pendientes
    if any(item.get("nombre") == nombre for item in cola):
        return False, f"«{nombre}» ya está en la lista."
    st.session_state.cola_facturas_uid += 1
    cola.append({
        "uid": st.session_state.cola_facturas_uid,
        "nombre": nombre,
        "bytes": contenido,
        "tamano_kb": round(len(contenido) / 1024, 1),
    })
    return True, f"Agregado: {nombre}"


def _quitar_archivo_de_cola(uid):
    st.session_state.cola_facturas_pendientes = [
        item for item in st.session_state.cola_facturas_pendientes if item.get("uid") != uid
    ]


def _limpiar_cola_facturas():
    st.session_state.cola_facturas_pendientes = []


def _archivos_desde_cola(cola):
    return [_archivo_en_memoria(item["nombre"], item["bytes"]) for item in cola]


def _render_cola_facturas():
    cola = st.session_state.cola_facturas_pendientes
    if not cola:
        return 0
    st.caption(f"**{len(cola)}** factura(s) en lista — procesá todas juntas cuando termines de agregar.")
    for item in cola:
        c1, c2 = st.columns([5, 1])
        c1.write(f"📄 {item['nombre']} ({item['tamano_kb']} KB)")
        if c2.button("Quitar", key=f"cola_quitar_{item['uid']}", help="Sacar de la lista"):
            _quitar_archivo_de_cola(item["uid"])
            st.rerun()
    return len(cola)


def _procesar_upload_factura(archivo, mejorar_img):
    from modulos.util_imagen import imagen_desde_upload

    img = imagen_desde_upload(archivo)
    img_proc = mejorar_imagen_documento(img.copy()) if mejorar_img else img
    datos = procesar_factura_con_ia(img_proc, mejorar_imagen=False)
    if not datos:
        raise ValueError("La IA no devolvió datos de la factura.")
    return datos, img, img_proc


def _cargar_datos_en_sesion(datos):
    st.session_state.temp_datos = datos
    st.session_state.borrador_id = None
    if "grilla_validacion" in st.session_state:
        del st.session_state["grilla_validacion"]


def _procesar_lote_facturas(archivos, condicion_pago, mejorar_img):
    resultados = []
    total = len(archivos)
    barra = st.progress(0, text="Preparando lote…")

    for idx, archivo in enumerate(archivos, start=1):
        nombre = getattr(archivo, "name", f"archivo_{idx}")
        barra.progress((idx - 1) / total, text=f"Leyendo {idx}/{total}: {nombre}")
        fila = {"archivo": nombre, "estado": "error", "mensaje": "", "datos": None, "borrador_id": None}
        try:
            datos, _, _ = _procesar_upload_factura(archivo, mejorar_img)
            datos = _enriquecer_articulos_factura(datos)
            fila["datos"] = datos
            payload = {
                **datos,
                "condicion_pago": condicion_pago,
                "archivo_origen": nombre,
            }
            ok, msg, bid = guardar_borrador_factura(payload, None)
            if ok:
                fila["estado"] = "ok"
                fila["mensaje"] = msg
                fila["borrador_id"] = bid
            else:
                fila["estado"] = "revision"
                fila["mensaje"] = msg
        except Exception as e:
            fila["mensaje"] = str(e)
        resultados.append(fila)

    barra.progress(1.0, text=f"Lote finalizado ({total} archivo(s)).")
    return resultados


def _mostrar_resumen_lote(resultados):
    ok = sum(1 for r in resultados if r["estado"] == "ok")
    rev = sum(1 for r in resultados if r["estado"] == "revision")
    err = sum(1 for r in resultados if r["estado"] == "error")
    st.success(f"Lote procesado: {ok} guardada(s) como borrador, {rev} para revisar, {err} con error.")

    for i, r in enumerate(resultados):
        cols = st.columns([4, 2, 1])
        if r["estado"] == "ok":
            cols[0].success(f"✅ {r['archivo']} — {r['mensaje']}")
        elif r["estado"] == "revision":
            cols[0].warning(f"⚠️ {r['archivo']} — {r['mensaje']}")
        else:
            cols[0].error(f"❌ {r['archivo']} — {r['mensaje']}")

        prov = (r.get("datos") or {}).get("proveedor", "")
        n_items = len((r.get("datos") or {}).get("articulos") or [])
        if prov or n_items:
            cols[1].caption(f"{prov or '—'} · {n_items} ítems")

        if r.get("datos") and cols[2].button("Abrir", key=f"lote_abrir_{i}"):
            _cargar_datos_en_sesion(r["datos"])
            if r.get("borrador_id"):
                st.session_state.borrador_id = r["borrador_id"]
            st.session_state.condicion_pago_borrador = st.session_state.get("condicion_pago_factura", "Contado")
            st.rerun()


def _procesar_una_factura_directa(archivo, mejorar_img):
    """Una factura: abre la grilla de validación (flujo clásico)."""
    datos, img, img_proc = _procesar_upload_factura(archivo, mejorar_img)
    if mejorar_img:
        with st.expander("Vista previa de imagen", expanded=False):
            c1, c2 = st.columns(2)
            c1.image(img, caption="Original", use_container_width=True)
            c2.image(img_proc, caption="Mejorada", use_container_width=True)
    datos = _enriquecer_articulos_factura(datos)
    _cargar_datos_en_sesion(datos)
    _limpiar_cola_facturas()
    st.rerun()


def render_carga_factura():
    _inicializar_cola_facturas()
    ayuda(
        "Ayuda — Carga de factura",
        "Subí **de a una** factura y usá *Agregar a la lista* para armar el lote. "
        "Cuando termines, *Procesar todas*. También podés *Procesar ahora* sin agregar a la lista. "
        "Editá la grilla (código, marca, **vehículos**). Guardá con **Ctrl+G** "
        "o *Guardar borrador*. Retomá desde *Facturas en curso*.",
    )

    if "borrador_id" not in st.session_state:
        st.session_state.borrador_id = None

    with st.expander("📂 Facturas en curso (borradores)", expanded=False):
        borradores = listar_borradores_factura()
        if not borradores:
            st.caption("No hay borradores guardados.")
        else:
            for b in borradores:
                c1, c2, c3 = st.columns([5, 1, 1])
                c1.caption(titulo_borrador(b))
                if c2.button("Abrir", key=f"abrir_b_{b['id']}"):
                    _cargar_borrador_en_sesion(b)
                    st.rerun()
                if c3.button("🗑️", key=f"del_b_{b['id']}", help="Eliminar borrador"):
                    eliminar_borrador_factura(b["id"])
                    if st.session_state.borrador_id == b["id"]:
                        st.session_state.borrador_id = None
                        st.session_state.temp_datos = None
                    st.rerun()

    cond_default = st.session_state.pop("condicion_pago_borrador", None)
    col_cond, col_arch = st.columns([1, 2])
    with col_cond:
        condicion_pago = st.radio(
            "Condición de pago",
            ["Contado", "30 Días"],
            horizontal=True,
            index=0 if cond_default != "30 Días" else 1,
            key="condicion_pago_factura",
        )
    with col_arch:
        archivo_nuevo = st.file_uploader(
            "Elegir factura (PDF o imagen)",
            type=["png", "jpg", "jpeg", "pdf"],
            accept_multiple_files=False,
            label_visibility="visible",
            key="upload_factura_individual",
        )
        mejorar_img = st.checkbox(
            "Mejorar imagen antes de leer (contraste y nitidez)",
            value=True,
            help="Recomendado para fotos con poca luz o inclinadas.",
        )

        if archivo_nuevo:
            btn_agregar, btn_ahora = st.columns(2)
            if btn_agregar.button("➕ Agregar a la lista", use_container_width=True):
                ok, msg = _agregar_archivo_a_cola(archivo_nuevo)
                if ok:
                    st.success(msg)
                    st.rerun()
                else:
                    st.warning(msg)
            if btn_ahora.button("⚡ Procesar ahora", type="primary", use_container_width=True):
                with st.spinner("Mejorando imagen y leyendo factura con IA..."):
                    try:
                        _procesar_una_factura_directa(archivo_nuevo, mejorar_img)
                    except Exception as e:
                        st.error(f"❌ Error al procesar la factura: {e}")

        n_cola = _render_cola_facturas()
        if n_cola:
            btn_lote, btn_vaciar = st.columns([2, 1])
            if btn_lote.button(
                f"🚀 Procesar todas ({n_cola})",
                type="primary",
                use_container_width=True,
            ):
                archivos_cola = _archivos_desde_cola(st.session_state.cola_facturas_pendientes)
                if n_cola == 1:
                    with st.spinner("Mejorando imagen y leyendo factura con IA..."):
                        try:
                            _procesar_una_factura_directa(archivos_cola[0], mejorar_img)
                        except Exception as e:
                            st.error(f"❌ Error al procesar la factura: {e}")
                else:
                    with st.spinner(f"Procesando {n_cola} facturas con IA..."):
                        resultados = _procesar_lote_facturas(archivos_cola, condicion_pago, mejorar_img)
                    _limpiar_cola_facturas()
                    _mostrar_resumen_lote(resultados)
            if btn_vaciar.button("Vaciar lista", use_container_width=True):
                _limpiar_cola_facturas()
                st.rerun()

    if not st.session_state.get("temp_datos"):
        return

    d = st.session_state.temp_datos or {}
    if not isinstance(d, dict):
        return

    col_prov, col_cuit = st.columns(2)
    prov_editado = col_prov.text_input("Proveedor detectado:", value=d.get("proveedor", "DESCONOCIDO"))
    cuit_editado = col_cuit.text_input(
        "CUIT (11 dígitos):",
        value="".join(filter(str.isdigit, str(d.get("cuit_proveedor", "")))),
        max_chars=11,
    )

    col_pv, col_num, col_comp = st.columns(3)
    pv_edit = col_pv.text_input(
        "Punto de venta (5 díg.)",
        value=str(d.get("punto_venta", "")).strip(),
        max_chars=5,
    )
    num_edit = col_num.text_input(
        "Nº comprobante (8 díg.)",
        value=str(d.get("numero_comprobante", "")).strip(),
        max_chars=8,
    )
    pv_fmt = str(pv_edit or "0").zfill(5)
    num_fmt = str(num_edit or "0").zfill(8)
    col_comp.info(f"**Comprobante:** {pv_fmt}-{num_fmt}")

    d["proveedor"] = prov_editado
    d["cuit_proveedor"] = cuit_editado
    d["punto_venta"] = pv_edit
    d["numero_comprobante"] = num_edit

    cuit_detectado = "".join(filter(str.isdigit, cuit_editado))
    if len(cuit_detectado) != 11 and cuit_detectado not in ("", "0"):
        st.warning("⚠️ El CUIT debe tener 11 dígitos.")

    provs_check = obtener_proveedores() or {}
    if cuit_detectado and cuit_detectado not in provs_check:
        st.error("⚠️ CUIT no registrado. Configuralo en Proveedores antes de confirmar.")

    cuit_valido = len(cuit_detectado) == 11 and cuit_detectado in provs_check

    articulos = d.get("articulos", [])
    mapa_veh = _vehiculos_por_codigo(articulos)
    for art in articulos:
        if not isinstance(art, dict):
            continue
        art.setdefault("codigo_proveedor", art.get("codigo", ""))
        art["marca"] = art.get("marca", art.get("condicion", "GENERICO"))
        cod = str(art.get("codigo", "")).strip()
        vehs = mapa_veh.get(cod) or normalizar_lista_vehiculos(art.get("vehiculos") or art.get("vehiculo"))
        art["vehiculos"] = vehs
        art["vehiculo"] = vehiculos_a_texto(vehs)

    st.caption(
        f"**{len(articulos)}** artículos — editá costos, descripción, cantidad y marca antes de confirmar. "
        "Columna *Vehículos*: elegí varios por fila. **Ctrl+G** guarda borrador."
    )

    if not articulos:
        st.warning("No hay artículos en la factura.")
        return

    df_articulos = pd.DataFrame(articulos)
    if "vehiculos" not in df_articulos.columns:
        df_articulos["vehiculos"] = [["UNIVERSAL"]] * len(df_articulos)
    df_articulos["vehiculos"] = df_articulos["vehiculos"].apply(normalizar_lista_vehiculos)

    cols_editor = [
        c for c in (
            "codigo", "codigo_proveedor", "descripcion", "cantidad",
            "precio_unitario", "marca", "vehiculos",
        )
        if c in df_articulos.columns
    ]
    if "codigo_proveedor" not in df_articulos.columns:
        df_articulos["codigo_proveedor"] = (
            df_articulos["codigo"] if "codigo" in df_articulos.columns else ""
        )
        if "codigo_proveedor" not in cols_editor:
            cols_editor.insert(1, "codigo_proveedor")
    df_editor = df_articulos[cols_editor]

    df_editado = st.data_editor(
        df_editor,
        column_config={
            "codigo": st.column_config.TextColumn("Código", width="small", required=True),
            "codigo_proveedor": st.column_config.TextColumn(
                "Cód. proveedor", width="small", help="Código impreso en la factura del proveedor."
            ),
            "descripcion": st.column_config.TextColumn("Descripción", width="medium", required=True),
            "cantidad": st.column_config.NumberColumn("Cant.", min_value=1, step=1, required=True),
            "precio_unitario": st.column_config.NumberColumn(
                "Costo / Precio base", min_value=0.0, format="$ %.2f", required=True
            ),
            "marca": st.column_config.TextColumn("Marca (variante)", width="small", required=True),
            "vehiculos": st.column_config.MultiselectColumn(
                "Vehículos",
                help="Varios por fila (mismo código maestro comparte vehículos al guardar).",
                options=OPCIONES_VEHICULO,
                default=["UNIVERSAL"],
                width="medium",
                required=True,
            ),
        },
        use_container_width=True,
        num_rows="dynamic",
        key="grilla_validacion",
    )

    datos_prov_fact = _datos_proveedor_factura(cuit_detectado, provs_check) if cuit_valido else {}
    if not df_editado.empty and datos_prov_fact:
        st.caption("Precios de venta calculados (según márgenes del proveedor y condición de pago):")
        st.dataframe(
            _tabla_precios_calculados(df_editado, datos_prov_fact, condicion_pago),
            use_container_width=True,
            hide_index=True,
        )
        st.caption(
            "Al confirmar ingreso, las descripciones nuevas se guardan en **MAYÚSCULAS**."
        )

    col_btn1, col_btn2, col_btn3 = st.columns(3)
    if col_btn1.button("💾 Guardar borrador (Ctrl+G)", type="secondary", use_container_width=True):
        _guardar_borrador_actual(d, df_editado, condicion_pago)

    if col_btn2.button("🔤 Normalizar códigos", use_container_width=True):
        n = normalizar_codigos_en_articulos(articulos)
        d["articulos"] = articulos
        st.session_state.temp_datos = d
        st.success(f"Normalizados {n} código(s).")
        st.rerun()

    if col_btn3.button("Descartar factura", use_container_width=True):
        if st.session_state.borrador_id:
            eliminar_borrador_factura(st.session_state.borrador_id)
        st.session_state.temp_datos = None
        st.session_state.borrador_id = None
        if "grilla_validacion" in st.session_state:
            del st.session_state["grilla_validacion"]
        st.rerun()

    st.divider()
    tamano_qr = st.slider("Tamaño QR (10 estándar)", min_value=5, max_value=20, value=10)

    if not df_editado.empty:
        art_ej = df_editado.iloc[0].to_dict()
        cod_ej = str(art_ej.get("codigo", "DEMO")).strip().upper().replace("/", "-") or "DEMO"
        marca_ej = sanitizar_clave_marca(art_ej.get("marca", "GENERICO"))
        id_qr_ej = formatear_id_variante(cod_ej, marca_ej)
        precio_bruto = float(art_ej.get("precio_unitario", 0))
        calculos = calcular_cascada_desde_proveedor(precio_bruto, datos_prov_fact, condicion_pago)
        qr_preview = generar_qr_producto(
            id_qr_ej, f"{art_ej.get('descripcion', 'Repuesto')} ({marca_ej})",
            calculos["precio_venta"], tamano_caja=tamano_qr,
        )
        st.image(qr_preview, caption=f"Vista previa — {id_qr_ej}", width=150)

    if st.button(
        "💾 Confirmar ingreso y generar TODOS los QR",
        type="primary",
        use_container_width=True,
        disabled=not cuit_valido,
    ):
        if not cuit_valido:
            st.error("CUIT inválido o no registrado.")
            return

        articulos_lista = _articulos_desde_grilla(df_editado)
        nombre_prov = d.get("proveedor", "DESCONOCIDO")
        for art in articulos_lista:
            art["codigo_proveedor"] = str(art.get("codigo", "")).strip()
            art["proveedor"] = nombre_prov
            art["cuit_proveedor"] = cuit_detectado

        d["articulos"] = articulos_lista
        d["condicion_pago"] = condicion_pago

        exito, msg = registrar_ingreso_inteligente(d, str(condicion_pago))
        if not exito:
            st.error(msg)
            return

        from modulos.auditoria_app import registrar_auditoria
        registrar_auditoria(
            "carga",
            "confirmar_factura_proveedor",
            f"Ingreso factura {nombre_prov} · {len(articulos_lista)} artículos",
            detalle={
                "proveedor": nombre_prov,
                "cuit": cuit_detectado,
                "comprobante": f"{pv_fmt}-{num_fmt}",
                "articulos": len(articulos_lista),
                "condicion_pago": condicion_pago,
            },
            exito=True,
            ref_id=f"{cuit_detectado}_{pv_fmt}_{num_fmt}",
        )

        if st.session_state.borrador_id:
            eliminar_borrador_factura(st.session_state.borrador_id)
            st.session_state.borrador_id = None

        prov_id = cuit_detectado
        provs = obtener_proveedores() or {}
        datos_prov_zip = _datos_proveedor_factura(prov_id, provs)

        zip_buffer = BytesIO()
        with zipfile.ZipFile(zip_buffer, "a", zipfile.ZIP_DEFLATED, False) as zip_file:
            for art in articulos_lista:
                codigo_base = str(art.get("codigo", "")).strip().upper().replace("/", "-")
                marca_rep = sanitizar_clave_marca(art.get("marca", "GENERICO"))
                if not codigo_base:
                    continue
                id_producto = formatear_id_variante(codigo_base, marca_rep)
                calc = calcular_cascada_desde_proveedor(
                    float(art.get("precio_unitario", 0)), datos_prov_zip, condicion_pago,
                )
                desc_qr = f"{art.get('descripcion', 'Repuesto')} ({marca_rep})"
                qr_bytes = generar_qr_producto(id_producto, desc_qr, calc["precio_venta"], tamano_caja=tamano_qr)
                zip_file.writestr(f"QR_{id_producto}.png", qr_bytes)

        st.session_state.zip_listo = zip_buffer.getvalue()
        st.session_state.zip_nombre = f"Etiquetas_{prov_id}.zip"
        st.session_state.temp_datos = None
        if "grilla_validacion" in st.session_state:
            del st.session_state["grilla_validacion"]
        st.success(msg)
        st.rerun()

    if "zip_listo" in st.session_state:
        st.success("📦 Etiquetas listas.")
        st.download_button(
            "⬇️ DESCARGAR ZIP",
            data=st.session_state.zip_listo,
            file_name=st.session_state.zip_nombre,
            mime="application/zip",
            type="primary",
            use_container_width=True,
        )
        if st.button("Limpiar pantalla"):
            del st.session_state.zip_listo
            st.rerun()
