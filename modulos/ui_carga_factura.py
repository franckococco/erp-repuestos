import streamlit as st
import pandas as pd
from PIL import Image
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
    calcular_cascada_precios,
    sanitizar_clave_marca,
    formatear_id_variante,
)
from modulos.generador_qr import generar_qr_producto
from modulos.ui_estilos import ayuda


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


def render_carga_factura():
    ayuda(
        "Ayuda — Carga de factura",
        "Editá la grilla (código, marca, **vehículos** en la misma tabla). Guardá con **Ctrl+G** "
        "o el botón *Guardar borrador*. Retomá desde *Facturas en curso*.",
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
        archivo = st.file_uploader(
            "Subir factura (PDF o imagen)",
            type=["png", "jpg", "jpeg", "pdf"],
            label_visibility="visible",
        )
        mejorar_img = st.checkbox(
            "Mejorar imagen antes de leer (contraste y nitidez)",
            value=True,
            help="Recomendado para fotos con poca luz o inclinadas.",
        )

    if archivo:
        if st.button("Procesar Factura", type="primary"):
            with st.spinner("Mejorando imagen y leyendo factura con IA..."):
                try:
                    from modulos.util_imagen import imagen_desde_upload
                    img = imagen_desde_upload(archivo)
                    img_proc = mejorar_imagen_documento(img.copy()) if mejorar_img else img
                    if mejorar_img:
                        with st.expander("Vista previa de imagen", expanded=False):
                            c1, c2 = st.columns(2)
                            c1.image(img, caption="Original", use_container_width=True)
                            c2.image(img_proc, caption="Mejorada", use_container_width=True)
                    datos = procesar_factura_con_ia(img_proc, mejorar_imagen=False)
                    if datos:
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
                        st.session_state.temp_datos = datos
                        st.session_state.borrador_id = None
                        if "grilla_validacion" in st.session_state:
                            del st.session_state["grilla_validacion"]
                        st.rerun()
                except Exception as e:
                    st.error(f"❌ Error al procesar la factura: {e}")

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
        f"**{len(articulos)}** artículos — editá todo en la grilla. "
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
        c for c in ("codigo", "descripcion", "cantidad", "precio_unitario", "marca", "vehiculos")
        if c in df_articulos.columns
    ]
    df_editor = df_articulos[cols_editor]

    df_editado = st.data_editor(
        df_editor,
        column_config={
            "codigo": st.column_config.TextColumn("Código", width="small", required=True),
            "descripcion": st.column_config.TextColumn("Descripción", width="medium", required=True),
            "cantidad": st.column_config.NumberColumn("Cant.", min_value=1, step=1, required=True),
            "precio_unitario": st.column_config.NumberColumn("Precio Base", min_value=0.0, format="$ %.2f", required=True),
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
        provs = obtener_proveedores() or {}
        recargo_prev = descuento_prev = 0.0
        if cuit_detectado in provs and isinstance(provs[cuit_detectado], dict):
            dp = provs[cuit_detectado]
            recargo_prev = float(dp.get("condiciones", {}).get(str(condicion_pago), 0.0))
            descuento_prev = float(dp.get("descuento", 0.0))
        calculos = calcular_cascada_precios(precio_bruto, recargo_prev, descuento_prev)
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
        recargo = descuento_prov = 0.0
        if prov_id in provs and isinstance(provs[prov_id], dict):
            recargo = float(provs[prov_id].get("condiciones", {}).get(str(condicion_pago), 0.0))
            descuento_prov = float(provs[prov_id].get("descuento", 0.0))

        zip_buffer = BytesIO()
        with zipfile.ZipFile(zip_buffer, "a", zipfile.ZIP_DEFLATED, False) as zip_file:
            for art in articulos_lista:
                codigo_base = str(art.get("codigo", "")).strip().upper().replace("/", "-")
                marca_rep = sanitizar_clave_marca(art.get("marca", "GENERICO"))
                if not codigo_base:
                    continue
                id_producto = formatear_id_variante(codigo_base, marca_rep)
                calc = calcular_cascada_precios(float(art.get("precio_unitario", 0)), recargo, descuento_prov)
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
