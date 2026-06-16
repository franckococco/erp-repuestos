"""UI del mostrador: cliente, búsqueda de productos y facturación ARCA."""
import base64
import math
from datetime import date, datetime, time, timedelta, timezone
from typing import Optional

import pandas as pd
import streamlit as st
import streamlit.components.v1 as components

from modulos.util_busqueda import normalizar_para_busqueda

from modulos.db_firebase import (
    obtener_clientes,
    configurar_cliente,
    cliente_consumidor_final,
    cliente_db_a_activo,
    obtener_carrito,
    vaciar_carrito,
    eliminar_item_carrito,
    actualizar_cantidad_item_carrito,
    actualizar_precio_item_carrito,
    confirmar_venta,
    validar_carrito_para_venta,
    guardar_comprobante_arca,
    guardar_presupuesto,
    listar_presupuestos_guardados,
    listar_comprobantes_arca,
    reabrir_presupuesto_en_carrito,
    actualizar_estado_presupuesto,
    eliminar_presupuesto_guardado,
    obtener_credenciales_arca,
    guardar_credenciales_arca,
    obtener_config_ticket_mostrador,
    guardar_config_ticket_mostrador,
    obtener_presupuesto_guardado,
)
from modulos.presupuesto_pdf import crear_pdf_presupuesto, VALIDEZ_PRESUPUESTO_DIAS
from modulos.factura_arca_client import generar_factura, cargar_datos_nube
from modulos.factura_arca_pdf import crear_ticket, crear_a4
from modulos.util_fechas import formatear_fecha_ar, TZ_ARGENTINA
from modulos.ia_mostrador import (
    FORMAS_PAGO,
    procesar_orden_mostrador,
    normalizar_forma_pago,
)
from modulos.mostrador_voz_flujo import (
    inventario_cache_mostrador,
    agregar_termino_voz,
    ejecutar_flujo_factura_voz,
    extraer_items_orden_voz,
    marcar_verificacion_mostrador,
)


VENDEDOR_MOSTRADOR = "Caja Principal"


CONFIG_TICKET_DEFAULT = {
    "margen_x": 2.0,
    "margen_y": 2.0,
    "font_size": 8,
    "nombre_empresa": "HAFID AUTOPARTES",
    "direccion": "",
    "condicion_iva": "IVA Responsable Inscripto",
    "cuit_emisor": "",
    "iibb": "Ingresos Brutos: A-76154",
    "inicio_act": "Inicio de Actividades: 02/05/2023",
    "leyenda_extra": "¡Gracias por su compra!",
    "impresora_modo": "navegador",
    "impresora_nombre": "",
    "impresora_ip": "",
    "impresora_puerto": 9100,
}


def normalizar_cliente_activo(cliente: Optional[dict]) -> dict:
    base = cliente_consumidor_final()
    if not isinstance(cliente, dict):
        return base
    cbte = str(cliente.get("tipo_comprobante", cliente.get("cbte_tipo", "6"))).strip()
    if cbte not in ("1", "6"):
        cbte = "6"
    cuit = "".join(filter(str.isdigit, str(cliente.get("cuit", "00000000000")))) or "00000000000"
    return {
        "nombre": str(cliente.get("nombre", base["nombre"])).upper(),
        "cuit": cuit,
        "descuento": float(cliente.get("descuento", 0.0)),
        "tipo_comprobante": cbte,
    }


def _defaults_desde_streamlit_secrets():
    cuit = ""
    clave = ""
    try:
        cuit = str(st.secrets.get("FACTURADOR_CUIT", "") or "")
        clave = str(st.secrets.get("FACTURADOR_CLAVE_SECRETA", "") or "")
        bloque = st.secrets.get("facturador", {})
        if isinstance(bloque, dict):
            cuit = cuit or str(bloque.get("cuit", "") or "")
            clave = clave or str(bloque.get("clave", "") or "")
    except Exception:
        pass
    return cuit.strip(), clave.strip()


def init_credenciales_arca_session():
    cuit_def, clave_def = _defaults_desde_streamlit_secrets()
    try:
        fb = obtener_credenciales_arca() or {}
        cuit_def = str(fb.get("cuit", "") or cuit_def).strip()
        clave_def = str(fb.get("clave", "") or clave_def).strip()
    except Exception:
        pass

    if not st.session_state.get("facturador_cuit_ui"):
        st.session_state.facturador_cuit_ui = cuit_def
    if not st.session_state.get("facturador_clave_ui"):
        st.session_state.facturador_clave_ui = clave_def
    st.session_state._credenciales_arca_inited = True


def _merge_config_ticket(base: dict) -> dict:
    cfg = dict(CONFIG_TICKET_DEFAULT)
    cfg.update({k: v for k, v in (base or {}).items() if v is not None and v != ""})
    return cfg


def _cargar_config_ticket_persistida() -> dict:
    cfg = _merge_config_ticket({})
    try:
        fb = obtener_config_ticket_mostrador() or {}
        cfg = _merge_config_ticket(fb)
    except Exception:
        pass
    try:
        bloque = st.secrets.get("facturador", {})
        if isinstance(bloque, dict):
            sec = bloque.get("config_ticket")
            if isinstance(sec, dict):
                cfg = _merge_config_ticket({**cfg, **sec})
        sec_top = st.secrets.get("FACTURADOR_CONFIG_TICKET")
        if isinstance(sec_top, dict):
            cfg = _merge_config_ticket({**cfg, **sec_top})
    except Exception:
        pass
    return cfg


def init_config_ticket_session():
    if st.session_state.get("_ticket_cfg_inited"):
        return
    cfg = _cargar_config_ticket_persistida()
    for k, v in cfg.items():
        st.session_state[f"ticket_cfg_{k}"] = v
    st.session_state._ticket_cfg_inited = True


def _config_ticket_desde_session() -> dict:
    init_config_ticket_session()
    out = dict(CONFIG_TICKET_DEFAULT)
    for k in CONFIG_TICKET_DEFAULT:
        sk = f"ticket_cfg_{k}"
        if sk in st.session_state:
            out[k] = st.session_state[sk]
    return out


def _leer_secrets_facturador():
    init_credenciales_arca_session()
    init_config_ticket_session()
    config_ticket = _config_ticket_desde_session()
    cuit = str(st.session_state.get("facturador_cuit_ui", "") or "").strip()
    clave = str(st.session_state.get("facturador_clave_ui", "") or "").strip()
    if not cuit or not clave:
        cuit_sec, clave_sec = _defaults_desde_streamlit_secrets()
        cuit = cuit or cuit_sec
        clave = clave or clave_sec
    if cuit and not config_ticket.get("cuit_emisor"):
        config_ticket["cuit_emisor"] = cuit
    return cuit, clave, config_ticket


def render_credenciales_arca():
    init_credenciales_arca_session()
    cuit, clave, _ = _leer_secrets_facturador()
    configurado = bool(cuit and clave)

    with st.expander(
        "🔑 Facturación ARCA — CUIT emisor y clave secreta",
        expanded=not configurado,
    ):
        col_cuit, col_clave = st.columns(2)
        with col_cuit:
            st.text_input(
                "CUIT emisor (facturador)",
                key="facturador_cuit_ui",
                placeholder="30716713179",
            )
        with col_clave:
            st.text_input(
                "Clave secreta",
                key="facturador_clave_ui",
                type="password",
                placeholder="Clave del backend ARCA",
            )
        if configurado:
            mask = f"{cuit[:2]}…{cuit[-2:]}" if len(cuit) >= 4 else cuit
            st.caption(f"Listo para facturar · CUIT {mask}")
        else:
            st.warning("Completá ambos campos para habilitar «Emitir factura ARCA».")
        col_guar, col_info = st.columns([1, 2])
        with col_guar:
            if st.button("💾 Guardar credenciales", key="guardar_cred_arca", use_container_width=True):
                ok, msj = guardar_credenciales_arca(
                    st.session_state.get("facturador_cuit_ui", ""),
                    st.session_state.get("facturador_clave_ui", ""),
                )
                if ok:
                    st.success(msj)
                else:
                    st.error(msj)
        with col_info:
            st.caption(
                "Quedan guardadas en Firebase al pulsar Guardar. "
                "Alternativa permanente: Secrets en Streamlit Cloud."
            )


def _listar_impresoras_instaladas():
    """Solo funciona si la app corre en Windows local (no en Streamlit Cloud)."""
    import sys
    if sys.platform != "win32":
        return []
    try:
        import win32print  # type: ignore
        flags = win32print.PRINTER_ENUM_LOCAL | win32print.PRINTER_ENUM_CONNECTIONS
        return sorted({p[2] for p in win32print.EnumPrinters(flags) if p[2]})
    except Exception:
        return []


def render_config_ticket_mostrador():
    """Encabezados del ticket e impresora preferida."""
    init_config_ticket_session()

    with st.expander("🧾 Configuración del ticket", expanded=False):
        st.caption(
            "Editá los textos que salen arriba y abajo del ticket fiscal. "
            "Los cambios aplican al próximo comprobante que emitas."
        )

        impresoras = _listar_impresoras_instaladas()
        en_nube = not impresoras

        with st.form("form_config_ticket", clear_on_submit=False):
            st.markdown("**Encabezado del comercio**")
            nombre = st.text_input(
                "Nombre / razón social",
                key="ticket_cfg_nombre_empresa",
                placeholder="HAFID AUTOPARTES",
            )
            direccion = st.text_area(
                "Dirección",
                key="ticket_cfg_direccion",
                height=68,
                placeholder="Calle, localidad, provincia",
            )
            c1, c2 = st.columns(2)
            with c1:
                cuit_t = st.text_input("CUIT emisor (en ticket)", key="ticket_cfg_cuit_emisor")
                iibb = st.text_input("Ingresos brutos", key="ticket_cfg_iibb")
            with c2:
                inicio = st.text_input("Inicio de actividades", key="ticket_cfg_inicio_act")
                cond_iva = st.text_input("Condición IVA", key="ticket_cfg_condicion_iva")

            st.markdown("**Pie de ticket**")
            leyenda = st.text_input(
                "Leyenda final",
                key="ticket_cfg_leyenda_extra",
                placeholder="¡Gracias por su compra!",
            )

            st.markdown("**Impresora**")
            if en_nube:
                st.info(
                    "La app en la nube **no puede ver las impresoras de tu PC**. "
                    "Al imprimir se abre el diálogo del navegador y elegís la impresora ahí. "
                    "Podés guardar acá el nombre o la IP para referencia."
                )
            else:
                st.success("Impresoras detectadas en esta PC.")

            modo_opts = {
                "navegador": "Diálogo del navegador (recomendado)",
                "red": "Impresora térmica por red (IP)",
            }
            st.radio(
                "Modo de impresión",
                ["navegador", "red"],
                format_func=lambda x: modo_opts[x],
                key="ticket_cfg_impresora_modo",
                horizontal=True,
            )
            modo = st.session_state.get("ticket_cfg_impresora_modo", "navegador")

            if modo == "navegador":
                if impresoras:
                    nombre_guardado = str(st.session_state.get("ticket_cfg_impresora_nombre", "") or "")
                    opciones = list(impresoras)
                    if nombre_guardado and nombre_guardado not in opciones:
                        opciones = [nombre_guardado] + opciones
                    st.selectbox(
                        "Impresora instalada en esta PC",
                        opciones,
                        key="ticket_cfg_impresora_nombre",
                    )
                else:
                    st.text_input(
                        "Nombre de impresora (referencia)",
                        key="ticket_cfg_impresora_nombre",
                        placeholder="Ej: EPSON TM-T20",
                        help="Configurala como predeterminada en Windows. Al imprimir, elegila en el diálogo del navegador.",
                    )
            else:
                col_ip, col_puerto = st.columns([2, 1])
                with col_ip:
                    st.text_input(
                        "IP de la impresora",
                        key="ticket_cfg_impresora_ip",
                        placeholder="192.168.1.100",
                    )
                with col_puerto:
                    st.number_input(
                        "Puerto",
                        key="ticket_cfg_impresora_puerto",
                        min_value=1,
                        max_value=65535,
                        step=1,
                    )
                st.text_input(
                    "Nombre (opcional)",
                    key="ticket_cfg_impresora_nombre",
                    placeholder="Caja 1 — térmica depósito",
                )
                st.caption(
                    "Impresión directa por red estará disponible en una próxima versión. "
                    "Por ahora usá el modo navegador."
                )

            guardar = st.form_submit_button("💾 Guardar configuración del ticket", type="primary")

        if guardar:
            payload = _config_ticket_desde_session()
            ok, msj = guardar_config_ticket_mostrador(payload)
            if ok:
                st.success(msj)
            else:
                st.error(msj)

        cfg = _config_ticket_desde_session()
        with st.container(border=True):
            st.markdown("**Vista previa del encabezado**")
            prev = [
                str(cfg.get("nombre_empresa") or "—"),
                str(cfg.get("direccion") or "").strip(),
                f"CUIT: {cfg.get('cuit_emisor') or '—'}",
                str(cfg.get("iibb") or ""),
                str(cfg.get("inicio_act") or ""),
                str(cfg.get("condicion_iva") or ""),
                "────────────────",
                "FACTURA B Nro: 0001-00000001",
                "────────────────",
                f"… {cfg.get('leyenda_extra') or ''}",
            ]
            st.code("\n".join(l for l in prev if l), language=None)


def _tipo_comprobante_label(cbte: str) -> str:
    return "Factura A" if str(cbte) == "1" else "Factura B"


def _tipo_comprobante_label_largo(cbte: str) -> str:
    if str(cbte) == "1":
        return "Factura A — Responsable inscripto"
    return "Factura B — Consumidor final"


def _label_cliente_listado(id_cli: str, datos: dict) -> str:
    datos = datos or {}
    nombre = str(datos.get("nombre", "—")).strip()
    tipo = _tipo_comprobante_label_largo(datos.get("tipo_comprobante", "6"))
    desc = float(datos.get("descuento", 0) or 0)
    extra = f" · {desc:g}% desc." if desc > 0 else ""
    return f"{nombre} · CUIT/DNI {id_cli} · {tipo}{extra}"


def _filtrar_clientes(clientes_db: dict, termino: str, max_resultados: int = 30) -> list:
    """Retorna [(id_cli, datos), ...] ordenados por nombre."""
    if not clientes_db:
        return []
    t_norm = normalizar_para_busqueda(termino)
    t_digitos = "".join(filter(str.isdigit, str(termino or "")))
    out = []
    for id_cli, datos in clientes_db.items():
        if not isinstance(datos, dict):
            continue
        nombre = str(datos.get("nombre", ""))
        if not t_norm and not t_digitos:
            continue
        hit = False
        if t_norm and t_norm in normalizar_para_busqueda(nombre):
            hit = True
        if t_digitos and t_digitos in str(id_cli):
            hit = True
        if t_norm and t_norm in normalizar_para_busqueda(_tipo_comprobante_label(datos.get("tipo_comprobante", "6"))):
            hit = True
        if hit:
            out.append((id_cli, datos))
    out.sort(key=lambda x: str((x[1] or {}).get("nombre", "")).upper())
    return out[:max_resultados]


def _cerrar_presupuesto_cargado(estado: str):
    pres_id = st.session_state.get("presupuesto_cargado_id")
    if pres_id:
        actualizar_estado_presupuesto(pres_id, estado)
        st.session_state.presupuesto_cargado_id = None


def limpiar_venta_mostrador(vendedor, reset_cliente=True):
    """Vacía carrito y flags de sesión tras cerrar una venta."""
    vaciar_carrito(str(vendedor))
    st.session_state.mostrador_listo_para_ticket = False
    st.session_state.mostrador_accion_pendiente = None
    st.session_state.pop("mostrador_intent_sugerido", None)
    st.session_state.factura_arca_reciente = None
    st.session_state.pop("presupuesto_pdf_descarga", None)
    st.session_state.pop("presupuesto_pdf_nombre", None)
    st.session_state[f"mostrador_cart_rev_{vendedor}"] = (
        int(st.session_state.get(f"mostrador_cart_rev_{vendedor}", 0)) + 1
    )
    if reset_cliente:
        st.session_state.cliente_activo = cliente_consumidor_final()


def _numero_presupuesto_en_sesion():
    pres_id = st.session_state.get("presupuesto_cargado_id")
    if not pres_id:
        return None
    pres = obtener_presupuesto_guardado(pres_id)
    if not pres:
        return None
    n = pres.get("numero_presupuesto")
    return int(n) if n is not None else None


def _nombre_archivo_presupuesto(numero, cliente_nombre):
    nro = f"{int(numero):04d}" if numero else "BORRADOR"
    safe = "".join(c if c.isalnum() else "_" for c in str(cliente_nombre).upper())[:24] or "CLIENTE"
    return f"Presupuesto_{nro}_{safe}.pdf"


def _preparar_pdf_presupuesto_borrador(vendedor, carrito, total_bruto):
    """Genera PDF borrador listo para descargar (sin descuento en el PDF)."""
    cli_nom = st.session_state.cliente_activo.get("nombre", "CLIENTE")
    pdf = generar_pdf_presupuesto_mostrador(
        vendedor, carrito, float(total_bruto), 0.0, numero=None
    )
    st.session_state.presupuesto_pdf_descarga = pdf
    st.session_state.presupuesto_pdf_nombre = _nombre_archivo_presupuesto(None, cli_nom)
    return pdf


def render_descarga_presupuesto_prominente(vendedor):
    """Botón grande de descarga si hay PDF listo."""
    pdf_ready = st.session_state.get("presupuesto_pdf_descarga")
    if not pdf_ready:
        return
    st.download_button(
        "⬇️ DESCARGAR / IMPRIMIR PRESUPUESTO",
        pdf_ready,
        st.session_state.get("presupuesto_pdf_nombre", "Presupuesto.pdf"),
        "application/pdf",
        type="primary",
        use_container_width=True,
        key=f"dl_pres_top_{vendedor}",
    )


def generar_pdf_presupuesto_mostrador(vendedor, carrito, total_bruto, desc_porc, numero=None, nota=""):
    _, _, cfg = _leer_secrets_facturador()
    cli = normalizar_cliente_activo(st.session_state.cliente_activo)
    if numero is None:
        numero = _numero_presupuesto_en_sesion()
    return crear_pdf_presupuesto(
        str(vendedor),
        carrito,
        float(total_bruto),
        cli,
        0.0,
        numero,
        cfg,
        nota,
    )


def _agregar_items_voz(vendedor, items, inventario, buscar_en_inventario, agregar_al_carrito):
    """Agrega varios ítems; devuelve (ok_count, mensajes, ambiguos)."""
    ok_count = 0
    mensajes = []
    errores = []
    ambiguos_finales = None

    for raw in items or []:
        if not isinstance(raw, dict):
            continue
        termino = raw.get("termino") or raw.get("codigo") or raw.get("descripcion")
        cant = raw.get("cantidad", 1)
        ok, msj, ambiguos = agregar_termino_voz(
            vendedor, termino, cant, inventario, buscar_en_inventario, agregar_al_carrito
        )
        if ok:
            ok_count += 1
            mensajes.append(msj)
        elif ambiguos:
            ambiguos_finales = ambiguos
            return ok_count, f"Varias opciones para '{termino}'. Elegí en la lista.", ambiguos
        else:
            errores.append(msj)

    if ok_count and errores:
        return ok_count, "\n".join(mensajes + errores), None
    if ok_count:
        return ok_count, "\n".join(mensajes), None
    if errores:
        return 0, "\n".join(errores), None
    return 0, "No se detectaron productos.", None


def _set_ia_feedback(vendedor, tipo, mensaje):
    st.session_state[f"ia_feedback_{vendedor}"] = {
        "tipo": str(tipo),
        "mensaje": str(mensaje),
    }


def _render_ia_feedback(vendedor):
    fb = st.session_state.pop(f"ia_feedback_{vendedor}", None)
    if not fb:
        return
    tipo = fb.get("tipo", "info")
    mensaje = fb.get("mensaje", "")
    if tipo == "ok":
        st.success(mensaje)
    elif tipo == "error":
        st.error(mensaje)
    elif tipo == "warning":
        st.warning(mensaje)
    else:
        st.info(mensaje)


def _label_intent_sugerido(intent):
    if intent == "presupuesto":
        return "Presupuesto"
    if intent == "factura_a":
        return "Factura A"
    return "Factura B"


def _render_banner_armado_voz(vendedor):
    carrito = carrito_efectivo_mostrador(vendedor, obtener_carrito(str(vendedor)) or [])
    cli = normalizar_cliente_activo(st.session_state.cliente_activo)
    desc = float(cli.get("descuento", 0))
    _, total = calcular_totales_carrito(carrito, desc)
    n_items = len(carrito)
    if st.session_state.get("mostrador_listo_para_ticket"):
        intent = st.session_state.get("mostrador_intent_sugerido", "factura_b")
        st.success(
            f"**Verificación** · Cliente: {cli['nombre']} · {n_items} ítem(s) · "
            f"Total **${total:,.2f}** · Sugerido: **{_label_intent_sugerido(intent)}**. "
            f"Elegí abajo **Facturar ARCA** o **Presupuesto**."
        )
    else:
        st.info(
            f"**Modo armado** · Cliente: {cli['nombre']}"
            + (f" ({desc:g}% dto.)" if desc else "")
            + f" · {n_items} ítem(s) · Total **${total:,.2f}** · "
            f"Seguí dictando o decí **listo** para verificar."
        )


def render_presupuestos_guardados(vendedor):
    with st.expander("📁 Presupuestos guardados", expanded=False):
        solo_abiertos = st.checkbox("Solo abiertos", value=True, key="pres_solo_abiertos")
        lista = listar_presupuestos_guardados(solo_abiertos=solo_abiertos, limite=30)

        if not lista:
            st.info("No hay presupuestos guardados.")
            return

        filas = []
        for p in lista:
            cli = p.get("cliente") or {}
            filas.append({
                "Nº": f"{int(p.get('numero_presupuesto', 0)):04d}" if p.get("numero_presupuesto") else "—",
                "ID": p.get("id", "")[:8],
                "Fecha": formatear_fecha_ar(p.get("creado")),
                "Cliente": cli.get("nombre", "—"),
                "Total": f"${float(p.get('total_final', 0)):,.2f}",
                "Estado": p.get("estado", "abierto"),
                "Vendedor": p.get("vendedor", "—"),
            })
        st.dataframe(filas, use_container_width=True, hide_index=True)

        opciones = {p["id"]: p for p in lista}
        sel_id = st.selectbox(
            "Seleccionar presupuesto",
            options=list(opciones.keys()),
            format_func=lambda x: (
                f"Nº {int((opciones[x].get('numero_presupuesto') or 0)):04d} · "
                f"{x[:8]}… · {(opciones[x].get('cliente') or {}).get('nombre', '')} · "
                f"${float(opciones[x].get('total_final', 0)):,.0f} · {opciones[x].get('estado', '')}"
            ) if opciones[x].get("numero_presupuesto") else (
                f"{x[:8]}… · {(opciones[x].get('cliente') or {}).get('nombre', '')} · "
                f"${float(opciones[x].get('total_final', 0)):,.0f} · {opciones[x].get('estado', '')}"
            ),
            key="pres_sel_detalle",
        )
        pres = opciones.get(sel_id) or {}
        if pres.get("nota"):
            st.caption(f"Nota: {pres['nota']}")

        col_r, col_pdf, col_anu, col_del = st.columns(4)
        if col_r.button("↩️ Reabrir en carrito", use_container_width=True, key="pres_reabrir"):
            ok, msj, cliente = reabrir_presupuesto_en_carrito(str(vendedor), sel_id, reemplazar=True)
            if ok:
                st.session_state.cliente_activo = normalizar_cliente_activo(cliente)
                st.session_state.presupuesto_cargado_id = sel_id
                if "advertencias" in msj.lower() or "stock" in msj.lower():
                    st.warning(msj)
                else:
                    st.success(msj)
                st.rerun()
            else:
                st.error(msj)

        items_pres = pres.get("items") or []
        cli_pres = pres.get("cliente") or {}
        desc_pres = float(cli_pres.get("descuento", 0))
        total_bruto_pres = float(pres.get("total_bruto", 0))
        pdf_pres = generar_pdf_presupuesto_mostrador(
            pres.get("vendedor", vendedor),
            items_pres,
            total_bruto_pres,
            desc_pres,
            numero=pres.get("numero_presupuesto"),
            nota=pres.get("nota", ""),
        )
        nro_pres = pres.get("numero_presupuesto")
        col_pdf.download_button(
            "📄 PDF",
            pdf_pres,
            _nombre_archivo_presupuesto(nro_pres, cli_pres.get("nombre", "CLIENTE")),
            "application/pdf",
            use_container_width=True,
            key="pres_dl_pdf",
        )

        if col_anu.button("Anular", use_container_width=True, key="pres_anular"):
            ok, msj = actualizar_estado_presupuesto(sel_id, "anulado")
            if ok:
                if st.session_state.get("presupuesto_cargado_id") == sel_id:
                    st.session_state.presupuesto_cargado_id = None
                st.success(msj)
                st.rerun()
            else:
                st.error(msj)

        if col_del.button("🗑️ Eliminar", use_container_width=True, key="pres_eliminar"):
            ok, msj = eliminar_presupuesto_guardado(sel_id)
            if ok:
                if st.session_state.get("presupuesto_cargado_id") == sel_id:
                    st.session_state.presupuesto_cargado_id = None
                st.success(msj)
                st.rerun()
            else:
                st.error(msj)


def render_seccion_cliente_mostrador():
    st.session_state.cliente_activo = normalizar_cliente_activo(
        st.session_state.get("cliente_activo")
    )
    cli = st.session_state.cliente_activo
    clientes_db = obtener_clientes() or {}

    col_info, col_cf, col_lim = st.columns([4, 1, 1])
    with col_info:
        st.markdown(f"**Cliente:** {cli['nombre']}")
        st.caption(
            f"CUIT/DNI: {cli['cuit']} · {_tipo_comprobante_label_largo(cli['tipo_comprobante'])}"
            + (f" · Descuento: {cli['descuento']}%" if cli["descuento"] > 0 else "")
        )
    with col_cf:
        if st.button("Consumidor final", use_container_width=True):
            st.session_state.cliente_activo = cliente_consumidor_final()
            st.rerun()
    with col_lim:
        if st.button("Limpiar cliente", use_container_width=True):
            st.session_state.cliente_activo = cliente_consumidor_final()
            st.rerun()

    with st.expander("🔍 Buscar o cargar cliente", expanded=True):
        if clientes_db:
            st.markdown("**Buscador de clientes**")
            buscar_cli = st.text_input(
                "Nombre, CUIT o DNI",
                key="mostrador_buscar_cliente",
                placeholder="Ej: García, 30716, López…",
            )
            termino = (buscar_cli or "").strip()
            if len(termino) < 2:
                st.info("Escribí al menos 2 caracteres para buscar.")
            else:
                encontrados = _filtrar_clientes(clientes_db, termino)
                if not encontrados:
                    st.warning("No hay clientes que coincidan.")
                else:
                    st.caption(f"{len(encontrados)} resultado(s)")
                    ids = [x[0] for x in encontrados]
                    sel_id = st.selectbox(
                        "Resultados",
                        options=ids,
                        format_func=lambda x: _label_cliente_listado(x, clientes_db.get(x, {})),
                        key="mostrador_sel_cliente",
                        label_visibility="collapsed",
                    )
                    if st.button("Usar cliente seleccionado", key="mostrador_usar_cliente", type="primary"):
                        if sel_id:
                            st.session_state.cliente_activo = cliente_db_a_activo(
                                clientes_db.get(sel_id, {})
                            )
                            st.rerun()
                        else:
                            st.warning("Seleccioná un cliente de la lista.")

        with st.form("mostrador_alta_cliente_rapida"):
            c1, c2, c3 = st.columns([3, 2, 1])
            nombre_nuevo = c1.text_input("Nombre / Razón Social")
            cuit_nuevo = c2.text_input("DNI o CUIT")
            desc_nuevo = c3.number_input("% Desc.", min_value=0.0, step=1.0, value=0.0)
            st.markdown("**Tipo de factura**")
            tipo_nuevo = st.radio(
                "Tipo de factura",
                options=["6", "1"],
                format_func=_tipo_comprobante_label_largo,
                horizontal=False,
                key="mostrador_tipo_fc_nuevo",
                label_visibility="collapsed",
            )
            if st.form_submit_button("Guardar y usar"):
                if nombre_nuevo and cuit_nuevo:
                    ok, msj = configurar_cliente(
                        nombre_nuevo.upper(), cuit_nuevo, desc_nuevo, tipo_nuevo
                    )
                    if ok:
                        id_cli = "".join(filter(str.isdigit, str(cuit_nuevo)))
                        st.session_state.cliente_activo = {
                            "nombre": nombre_nuevo.upper(),
                            "cuit": id_cli,
                            "descuento": float(desc_nuevo),
                            "tipo_comprobante": tipo_nuevo,
                        }
                        st.success(msj)
                        st.rerun()
                    else:
                        st.error(msj)
                else:
                    st.error("Nombre y CUIT/DNI son obligatorios.")


def render_panel_coincidencias_mostrador(vendedor, agrupar_por_maestro, agregar_al_carrito):
    """Lista compacta de variantes encontradas (IA o búsqueda)."""
    resultados = st.session_state.get("resultados_ia_mostrador")
    if not resultados:
        return

    col_msg, col_x = st.columns([11, 1])
    with col_msg:
        st.caption(st.session_state.get("msg_ia_mostrador", "Coincidencias"))
    with col_x:
        if st.button("✕", key="cerrar_coinc_most", help="Cerrar coincidencias"):
            st.session_state.resultados_ia_mostrador = None
            st.session_state.msg_ia_mostrador = None
            st.rerun()

    grupos_most = agrupar_por_maestro(resultados)
    for gkey in sorted(grupos_most.keys(), key=lambda k: grupos_most[k]["descripcion"]):
        g = grupos_most[gkey]
        titulo = f"{g['descripcion'][:45]} · {g['codigo']}"
        if g.get("vehiculo"):
            titulo += f" · {str(g['vehiculo'])[:20]}"
        st.markdown(f"<p style='margin:0.2rem 0;font-size:0.85rem;font-weight:600'>{titulo}</p>", unsafe_allow_html=True)
        for res in g["variantes"]:
            marca_res = res.get("marca", res.get("condicion", ""))
            precio_f = float(res.get("precio_venta", 0))
            stock = res.get("stock", 0)
            rid = res.get("id", "N")
            c_txt, c_btn = st.columns([6, 1])
            with c_txt:
                st.markdown(
                    f"<span style='font-size:0.8rem;color:#555'>"
                    f"{marca_res} · {stock} u. · ${precio_f:,.0f}</span>",
                    unsafe_allow_html=True,
                )
            with c_btn:
                if st.button("➕", key=f"btn_add_most_{rid}", help="Agregar al carrito"):
                    exito, msj_db = agregar_al_carrito(str(vendedor), rid, 1)
                    if exito:
                        st.session_state.resultados_ia_mostrador = None
                        st.session_state.msg_ia_mostrador = None
                        st.rerun()
                    else:
                        st.error(msj_db)


def render_buscador_productos(vendedor, inv_completo, agregar_al_carrito, filtrar_inventario):
    from modulos.ia_mostrador import parece_orden_voz_mostrador

    busqueda = st.text_input(
        "Buscar por código, descripción, vehículo o marca",
        key=f"busq_most_{vendedor}",
        placeholder="Ej: 111, filtro aceite… (órdenes de voz → pestaña Asistente IA)",
    )
    if not busqueda or len(busqueda.strip()) < 2:
        st.info("Escribí en el buscador para ver productos (no se lista todo el inventario).")
        return

    busq = busqueda.strip()
    if parece_orden_voz_mostrador(busq):
        ultima = st.session_state.get(f"busq_voz_proc_{vendedor}")
        if ultima != busq:
            st.session_state[f"ia_most_{vendedor}"] = busq
            st.session_state[f"auto_run_ia_{vendedor}"] = True
            st.session_state[f"busq_voz_proc_{vendedor}"] = busq
            st.rerun()
        return

    encontrados = filtrar_inventario(inv_completo, busq)[:40]
    if not encontrados:
        st.warning("Sin coincidencias.")
        return

    opciones_desc = {}
    for item in encontrados:
        if isinstance(item, dict):
            marca_item = item.get("marca", item.get("condicion", ""))
            desc = (
                f"{item.get('codigo', '')} | {item.get('vehiculo', '')} - "
                f"{marca_item} | {item.get('descripcion', '')} - "
                f"${item.get('precio_venta', 0)} (stock {item.get('stock', 0)})"
            )
            opciones_desc[desc] = item.get("id")

    sel_prod = st.selectbox("Resultados:", options=[""] + list(opciones_desc.keys()))
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
            st.warning("Seleccioná un producto de la lista.")


def _cart_editor_session_key(vendedor):
    rev = st.session_state.get(f"mostrador_cart_rev_{vendedor}", 0)
    return f"cart_editor_{vendedor}_{rev}"


def _float_celda(val, default=0.0):
    if val is None or (isinstance(val, float) and math.isnan(val)):
        return default
    try:
        return float(val)
    except (TypeError, ValueError):
        return default


def _int_celda(val, default=1):
    try:
        n = int(_float_celda(val, default))
        return n if n > 0 else default
    except (TypeError, ValueError):
        return default


def _dataframe_desde_editor(vendedor):
    raw = st.session_state.get(_cart_editor_session_key(vendedor))
    if raw is None:
        return None
    if isinstance(raw, pd.DataFrame):
        return raw if not raw.empty else None
    if isinstance(raw, dict):
        try:
            df = pd.DataFrame(raw)
            return df if not df.empty else None
        except (TypeError, ValueError):
            return None
    return None


def carrito_efectivo_mostrador(vendedor, carrito_base):
    """Carrito con cantidad/precio de la grilla aún no guardados en Firebase."""
    carrito = [dict(i) for i in (carrito_base or []) if isinstance(i, dict)]
    if not carrito:
        return carrito
    df_live = _dataframe_desde_editor(vendedor)
    if df_live is None:
        return carrito

    id_map = {str(i.get("id", "")): i for i in carrito}
    for _, row in df_live.iterrows():
        iid = str(row.get("_id", ""))
        if not iid or iid not in id_map:
            continue
        item = id_map[iid]
        cant = _int_celda(row.get("Cant."), int(item.get("cantidad", 1)))
        precio = _float_celda(row.get("Precio unit."), float(item.get("precio_unitario", 0)))
        item["cantidad"] = cant
        item["precio_unitario"] = precio
        item["subtotal"] = cant * precio
    return list(id_map.values())


def calcular_totales_carrito(carrito, desc_porc=0.0):
    bruto = sum(float(i.get("subtotal", 0)) for i in carrito if isinstance(i, dict))
    desc = float(desc_porc)
    return bruto, bruto * (1 - desc / 100.0)


def sincronizar_grilla_carrito_firebase(vendedor, carrito_base=None):
    """Persiste en Firebase los cambios pendientes del editor de la grilla."""
    carrito = carrito_base if carrito_base is not None else (obtener_carrito(str(vendedor)) or [])
    df_live = _dataframe_desde_editor(vendedor)
    if df_live is None:
        return 0, []
    return _aplicar_cambios_carrito(vendedor, carrito, df_live)


def obtener_carrito_listo_facturacion(vendedor, desc_porc=0.0):
    sincronizar_grilla_carrito_firebase(vendedor)
    carrito = obtener_carrito(str(vendedor)) or []
    bruto, final = calcular_totales_carrito(carrito, desc_porc)
    return carrito, bruto, final


def carrito_a_items_factura(carrito, descuento_pct):
    factor = 1.0 - float(descuento_pct) / 100.0
    items = []
    for item in carrito:
        if not isinstance(item, dict):
            continue
        cant = int(item.get("cantidad", 1))
        sub = float(item.get("subtotal", 0)) * factor
        items.append({
            "descripcion": str(item.get("descripcion", "Artículo"))[:120],
            "cantidad": cant,
            "precio": round(sub, 2),
        })
    return items


def _auto_imprimir_pdf(pdf_bytes):
    """Abre el diálogo de impresión del navegador (ticket tras facturar)."""
    if not pdf_bytes:
        return
    base64_pdf = base64.b64encode(pdf_bytes).decode("utf-8")
    components.html(
        f"""
        <script>
        (function() {{
            const b64 = "{base64_pdf}";
            const byteCharacters = atob(b64);
            const byteNumbers = new Array(byteCharacters.length);
            for (let i = 0; i < byteCharacters.length; i++) {{
                byteNumbers[i] = byteCharacters.charCodeAt(i);
            }}
            const blob = new Blob([new Uint8Array(byteNumbers)], {{type: 'application/pdf'}});
            const url = URL.createObjectURL(blob);
            const win = window.open(url, '_blank');
            if (win) {{ win.focus(); setTimeout(() => win.print(), 600); }}
        }})();
        </script>
        """,
        height=0,
    )


def _mostrar_boton_imprimir_pdf(pdf_bytes):
    base64_pdf = base64.b64encode(pdf_bytes).decode("utf-8")
    components.html(
        f"""
        <button onclick="imprimir()" style="
            background-color: #ff4b4b; color: white; padding: 10px;
            border-radius: 5px; width: 100%; border: none; cursor: pointer;
            font-weight: bold; font-family: sans-serif;
        ">🖨️ IMPRIMIR</button>
        <script>
        function imprimir() {{
            const b64 = "{base64_pdf}";
            const byteCharacters = atob(b64);
            const byteNumbers = new Array(byteCharacters.length);
            for (let i = 0; i < byteCharacters.length; i++) {{
                byteNumbers[i] = byteCharacters.charCodeAt(i);
            }}
            const blob = new Blob([new Uint8Array(byteNumbers)], {{type: 'application/pdf'}});
            const url = URL.createObjectURL(blob);
            const win = window.open(url, '_blank');
            if (win) {{ win.focus(); setTimeout(() => win.print(), 500); }}
        }}
        </script>
        """,
        height=60,
    )


def _formato_nro_comprobante(datos):
    try:
        return (
            f"{int(float(datos.get('punto_venta', 0))):04d}-"
            f"{int(float(datos.get('numero_factura', 0))):08d}"
        )
    except (TypeError, ValueError):
        return "—"


def _cliente_para_pdf(cliente):
    cli = cliente if isinstance(cliente, dict) else {}
    cbte = str(cli.get("cbte_tipo") or cli.get("tipo_comprobante") or "6")
    return {
        "cuit": cli.get("cuit", "00000000000"),
        "nombre": cli.get("nombre", "CONSUMIDOR FINAL"),
        "cbte_tipo": cbte,
    }


def _respuesta_para_pdf(comp):
    return {
        "cae": comp.get("cae"),
        "vencimiento_cae": comp.get("vencimiento_cae"),
        "punto_venta": comp.get("punto_venta"),
        "numero_factura": comp.get("numero_factura"),
        "nombre_empresa": comp.get("nombre_empresa"),
        "direccion_empresa": comp.get("direccion_empresa"),
    }


def regenerar_pdfs_comprobante(comp):
    _, _, cfg = _leer_secrets_facturador()
    datos_resp = _respuesta_para_pdf(comp)
    datos_cliente = _cliente_para_pdf(comp.get("cliente"))
    items = comp.get("items") or []
    ticket = crear_ticket(datos_resp, datos_cliente, items, cfg)
    a4 = crear_a4(datos_resp, datos_cliente, items, cfg)
    return ticket, a4, datos_resp


def _render_acciones_pdf_compactas(nro, pdf_ticket, pdf_a4, key_prefix, solo_ticket=False):
    """Imprimir / descargar en una sola fila horizontal."""
    n_cols = 3 if (pdf_a4 and not solo_ticket) else 2
    cols = st.columns(n_cols)
    idx = 0
    if pdf_ticket:
        with cols[idx]:
            _mostrar_boton_imprimir_pdf(pdf_ticket)
        idx += 1
        with cols[idx]:
            st.download_button(
                "↓ Ticket",
                pdf_ticket,
                file_name=f"Ticket_{nro}.pdf",
                mime="application/pdf",
                use_container_width=True,
                key=f"{key_prefix}_ticket",
            )
        idx += 1
    if pdf_a4 and not solo_ticket and idx < len(cols):
        with cols[idx]:
            st.download_button(
                "↓ A4",
                pdf_a4,
                file_name=f"Factura_{nro}.pdf",
                mime="application/pdf",
                use_container_width=True,
                key=f"{key_prefix}_a4",
            )
    elif solo_ticket and pdf_a4:
        st.download_button(
            "↓ A4 (opcional)",
            pdf_a4,
            file_name=f"Factura_{nro}.pdf",
            mime="application/pdf",
            key=f"{key_prefix}_a4_opt",
        )


def render_factura_arca_exitosa(key_suffix=""):
    rec = st.session_state.get("factura_arca_reciente")
    if not rec:
        return False

    datos = rec.get("respuesta", {})
    nro = _formato_nro_comprobante(datos)
    cae = datos.get("cae", "")
    vto = datos.get("vencimiento_cae", "")
    total = rec.get("total")
    ks = key_suffix or "panel"
    solo_ticket = bool(st.session_state.get("mostrador_voz_solo_ticket"))

    with st.container(border=True):
        hdr, btn_cerrar = st.columns([5, 1])
        with hdr:
            if cae:
                st.markdown(f"**✅ CAE otorgado** · `{nro}` · vto {vto or '—'}")
            else:
                st.error("Factura sin CAE en la respuesta")
        with btn_cerrar:
            if st.button("✕", key=f"cerrar_factura_arca_{ks}", help="Cerrar"):
                st.session_state.factura_arca_reciente = None
                st.session_state.mostrador_voz_solo_ticket = False
                st.rerun()

        c1, c2, c3 = st.columns(3)
        c1.caption("CAE")
        c1.code(str(cae) if cae else "—", language=None)
        if total is not None:
            c2.metric("Total", f"${float(total):,.2f}")
        c3.caption("Comprobante")
        c3.write(nro)

        _render_acciones_pdf_compactas(
            nro,
            rec.get("pdf_ticket"),
            rec.get("pdf_a4"),
            f"fact_{ks}",
            solo_ticket=solo_ticket,
        )
    return True


def _rango_fechas_utc(fecha_desde: date, fecha_hasta: date):
    inicio = datetime.combine(fecha_desde, time.min)
    fin = datetime.combine(fecha_hasta, time.max)
    try:
        inicio = inicio.replace(tzinfo=TZ_ARGENTINA).astimezone(timezone.utc)
        fin = fin.replace(tzinfo=TZ_ARGENTINA).astimezone(timezone.utc)
    except Exception:
        inicio = inicio.replace(tzinfo=timezone.utc)
        fin = fin.replace(tzinfo=timezone.utc)
    return inicio, fin


def render_historial_facturas_arca():
    with st.expander("Facturas ARCA — reimprimir", expanded=False):
        hoy = date.today()
        col_d1, col_d2, col_f = st.columns([1, 1, 2])
        with col_d1:
            fecha_desde = st.date_input(
                "Desde",
                value=hoy - timedelta(days=30),
                key="hist_arca_desde",
                format="DD/MM/YYYY",
            )
        with col_d2:
            fecha_hasta = st.date_input(
                "Hasta",
                value=hoy,
                key="hist_arca_hasta",
                format="DD/MM/YYYY",
            )
        with col_f:
            filtro_txt = st.text_input(
                "Filtrar por nro., cliente o CAE",
                key="hist_arca_filtro",
                placeholder="0001-00000123, García, 7abc…",
            )

        if fecha_desde > fecha_hasta:
            st.error("«Desde» no puede ser posterior a «Hasta».")
            return

        try:
            ini, fin = _rango_fechas_utc(fecha_desde, fecha_hasta)
            lista = listar_comprobantes_arca(
                limite=80,
                fecha_desde=ini,
                fecha_hasta=fin,
                busqueda=filtro_txt,
            )
        except Exception as ex:
            st.error(f"No se pudo leer el historial: {ex}")
            return
        if not lista:
            st.info("No hay facturas en ese rango o filtro.")
            return

        filas = []
        for c in lista:
            cli = c.get("cliente") or {}
            cbte = str(cli.get("cbte_tipo") or cli.get("tipo_comprobante") or "6")
            filas.append({
                "Tipo": _tipo_comprobante_label(cbte),
                "Nro": _formato_nro_comprobante(c),
                "Fecha": formatear_fecha_ar(c.get("fecha")),
                "Cliente": cli.get("nombre", "—"),
                "CAE": c.get("cae", "—"),
                "Total": f"${float(c.get('total', 0)):,.2f}",
            })
        st.dataframe(filas, use_container_width=True, hide_index=True)

        opciones = {x["id"]: x for x in lista}
        sel_id = st.selectbox(
            "Elegir factura para reimprimir",
            options=list(opciones.keys()),
            format_func=lambda x: (
                f"{_tipo_comprobante_label(str((opciones[x].get('cliente') or {}).get('tipo_comprobante', '6')))} · "
                f"{_formato_nro_comprobante(opciones[x])} · "
                f"{formatear_fecha_ar(opciones[x].get('fecha'), con_hora=False)} · "
                f"{(opciones[x].get('cliente') or {}).get('nombre', '')} · "
                f"${float(opciones[x].get('total', 0)):,.2f}"
            ),
            key="hist_arca_sel",
        )
        comp = opciones.get(sel_id) or {}
        cli_sel = comp.get("cliente") or {}
        st.caption(
            f"{_tipo_comprobante_label(str(cli_sel.get('tipo_comprobante', '6')))} · "
            f"Vto. CAE: {comp.get('vencimiento_cae', '—')} · "
            f"Pago: {comp.get('forma_pago', '—')} · Vendedor: {comp.get('vendedor', '—')}"
        )

        if st.button("Cargar PDFs", key="hist_arca_reimprimir", use_container_width=True):
            pdf_t, pdf_a, datos = regenerar_pdfs_comprobante(comp)
            nro = _formato_nro_comprobante(datos)
            st.session_state.hist_arca_preview = {
                "respuesta": datos,
                "pdf_ticket": pdf_t,
                "pdf_a4": pdf_a,
                "total": comp.get("total"),
                "comprobante_id": sel_id,
                "nro": nro,
            }
            st.rerun()

        preview = st.session_state.get("hist_arca_preview")
        if preview and preview.get("comprobante_id") == sel_id:
            st.caption(f"Comprobante {preview.get('nro', '—')}")
            _render_acciones_pdf_compactas(
                preview.get("nro", "—"),
                preview.get("pdf_ticket"),
                preview.get("pdf_a4"),
                f"hist_{sel_id[:8]}",
            )


def _forma_pago_actual(vendedor):
    key = f"mostrador_forma_pago_{vendedor}"
    if key not in st.session_state:
        st.session_state[key] = "Contado"
    return st.session_state[key]


def _set_forma_pago(vendedor, forma):
    fp = normalizar_forma_pago(forma)
    st.session_state[f"mostrador_forma_pago_{vendedor}"] = fp
    return fp


def ejecutar_emitir_factura_arca(
    vendedor, carrito, total_final, desc_porc, forma_pago, solo_ticket=False
):
    cuit_fact, clave_fact, config_ticket = _leer_secrets_facturador()
    if not cuit_fact or not clave_fact:
        return False, "Completá CUIT emisor y clave secreta en «Facturación ARCA» (arriba en Mostrador).", None

    _, errores_sync = sincronizar_grilla_carrito_firebase(vendedor, carrito)
    if errores_sync:
        return False, "\n".join(errores_sync), None

    carrito = obtener_carrito(str(vendedor)) or []
    _, total_final = calcular_totales_carrito(carrito, desc_porc)

    ok_val, msg_val, _ = validar_carrito_para_venta(str(vendedor))
    if not ok_val:
        return False, msg_val, None

    cli = normalizar_cliente_activo(st.session_state.cliente_activo)
    datos_cliente = {
        "cuit": cli["cuit"],
        "nombre": cli["nombre"],
        "cbte_tipo": cli["tipo_comprobante"],
    }
    items_fc = carrito_a_items_factura(carrito, desc_porc)
    if not items_fc or sum(i["precio"] for i in items_fc) <= 0:
        return False, (
            "El total a facturar debe ser mayor a cero. "
            "Revisá el precio unitario en la grilla."
        ), None

    resultado = generar_factura(cuit_fact, clave_fact, datos_cliente, items_fc, forma_pago)
    if not resultado.get("success"):
        return False, f"Error ARCA: {resultado.get('error', 'Desconocido')}", None

    datos_resp = resultado["data"]
    cfg = dict(config_ticket)
    if cuit_fact:
        cfg["cuit_emisor"] = cfg.get("cuit_emisor") or cuit_fact
    pdf_ticket = crear_ticket(datos_resp, datos_cliente, items_fc, cfg)
    pdf_a4 = crear_a4(datos_resp, datos_cliente, items_fc, cfg)

    exito_stock, msj_stock = confirmar_venta(str(vendedor))
    if not exito_stock:
        return False, (
            f"CAE obtenido pero falló el descuento de stock: {msj_stock}. "
            "Revisá inventario manualmente."
        ), None

    comp_id = guardar_comprobante_arca(
        vendedor, datos_cliente, datos_resp, items_fc, forma_pago, total_final
    )
    _cerrar_presupuesto_cargado("facturado")
    limpiar_venta_mostrador(vendedor, reset_cliente=True)
    st.session_state.mostrador_voz_solo_ticket = bool(solo_ticket)
    nro = _formato_nro_comprobante(datos_resp)
    return True, f"Factura {nro} emitida · CAE otorgado · Total ${total_final:,.2f}", {
        "respuesta": datos_resp,
        "pdf_ticket": pdf_ticket,
        "pdf_a4": pdf_a4,
        "total": total_final,
        "comprobante_id": comp_id,
        "nro": nro,
    }


def _facturar_desde_carrito(vendedor, carrito, total_final, desc_porc, forma_pago, solo_ticket=False):
    with st.spinner("Solicitando CAE a AFIP…"):
        ok, msj, datos = ejecutar_emitir_factura_arca(
            vendedor, carrito, total_final, desc_porc, forma_pago, solo_ticket=solo_ticket
        )
    if ok and datos:
        _auto_imprimir_pdf(datos.get("pdf_ticket"))
    return ok, msj


def _ejecutar_accion_pendiente(vendedor, pendiente, carrito, total_final, desc_porc):
    tipo = pendiente.get("tipo")
    forma_pago = pendiente.get("forma_pago") or _forma_pago_actual(vendedor)

    if tipo == "confirmar_venta":
        exito, msj = confirmar_venta(str(vendedor))
        if exito:
            _cerrar_presupuesto_cargado("vendido")
            limpiar_venta_mostrador(vendedor, reset_cliente=True)
        return exito, msj

    if tipo == "facturar":
        with st.spinner("Solicitando CAE a ARCA/AFIP…"):
            ok, msj, datos = ejecutar_emitir_factura_arca(
                vendedor, carrito, total_final, desc_porc, forma_pago
            )
        if ok and datos:
            _auto_imprimir_pdf(datos.get("pdf_ticket"))
        return ok, msj

    if tipo == "imprimir_ticket":
        with st.spinner("Solicitando CAE e imprimiendo ticket…"):
            ok, msj, datos = ejecutar_emitir_factura_arca(
                vendedor, carrito, total_final, desc_porc, forma_pago, solo_ticket=True
            )
        if ok and datos:
            _auto_imprimir_pdf(datos.get("pdf_ticket"))
        return ok, msj

    if tipo == "guardar_presupuesto":
        ok, msj, nuevo_id = guardar_presupuesto(
            str(vendedor), st.session_state.cliente_activo, pendiente.get("nota", "")
        )
        if ok:
            st.session_state.presupuesto_cargado_id = nuevo_id
        return ok, msj

    if tipo == "vaciar_carrito":
        limpiar_venta_mostrador(vendedor, reset_cliente=False)
        return True, "Carrito vaciado."

    return False, "Acción pendiente desconocida."


def _limpiar_accion_pendiente():
    st.session_state.mostrador_accion_pendiente = None


def render_confirmacion_pendiente_mostrador(vendedor, carrito, total_final, desc_porc):
    pend = st.session_state.get("mostrador_accion_pendiente")
    if not pend:
        return

    st.warning(pend.get("mensaje", "¿Confirmás esta acción?"))
    col_ok, col_no = st.columns(2)
    if col_ok.button("✅ Confirmar", type="primary", use_container_width=True, key="most_pend_ok"):
        ok, msj = _ejecutar_accion_pendiente(vendedor, pend, carrito, total_final, desc_porc)
        _limpiar_accion_pendiente()
        if ok:
            st.success(msj)
            st.rerun()
        else:
            st.error(msj)
    if col_no.button("❌ Cancelar", use_container_width=True, key="most_pend_no"):
        _limpiar_accion_pendiente()
        st.info("Acción cancelada.")
        st.rerun()


def _marcar_listo_para_ticket(vendedor, total_final, intent_sugerido=None):
    marcar_verificacion_mostrador(intent_sugerido)
    return (
        f"Revisá la grilla (total ${total_final:,.2f}) y elegí "
        "**Facturar ARCA** o **Presupuesto** en el panel derecho."
    )


def render_ia_mostrador(
    vendedor,
    obtener_inventario_completo,
    buscar_en_inventario,
    agrupar_por_maestro,
    agregar_al_carrito,
):
    _render_ia_feedback(vendedor)

    st.caption(
        "Una frase alcanza: «cargame presupuesto para cliente Juan, código 111 3 unidades, listo» "
        "o «cargame factura B… listo». Revisás la grilla y descargás abajo."
    )

    render_descarga_presupuesto_prominente(vendedor)

    if obtener_carrito(str(vendedor)) or st.session_state.get("mostrador_listo_para_ticket"):
        _render_banner_armado_voz(vendedor)

    col_ia1, col_ia2 = st.columns([4, 1])
    orden = col_ia1.text_input("Orden rápida (voz o texto):", key=f"ia_most_{vendedor}")
    submit_ia = col_ia2.button("▶ Ejecutar", use_container_width=True, type="primary")
    if st.session_state.pop(f"auto_run_ia_{vendedor}", False) and orden:
        submit_ia = True

    if submit_ia and orden:
        fb_tipo = None
        fb_msg = None
        do_rerun = False
        with st.spinner("Procesando orden…"):
            resp = procesar_orden_mostrador(orden) or {}
            accion = resp.get("accion")
            inventario = inventario_cache_mostrador(obtener_inventario_completo)
            carrito = obtener_carrito(str(vendedor)) or []
            carrito_ui = carrito_efectivo_mostrador(vendedor, carrito)
            desc_porc = float(st.session_state.cliente_activo.get("descuento", 0))
            total_bruto, total_final = calcular_totales_carrito(carrito_ui, desc_porc)

            if accion == "flujo_factura":
                if not resp.get("items"):
                    items_extra = extraer_items_orden_voz(orden)
                    if items_extra:
                        resp["items"] = items_extra
                ok, msj, ambiguos = ejecutar_flujo_factura_voz(
                    vendedor,
                    resp,
                    inventario,
                    buscar_en_inventario,
                    agregar_al_carrito,
                    ejecutar_emitir_factura_arca,
                    texto_orden=orden,
                )
                if ok:
                    fb_tipo, fb_msg, do_rerun = "ok", msj, True
                    st.session_state.resultados_ia_mostrador = None
                    if resp.get("intent_sugerido") == "presupuesto" and resp.get("ir_verificacion"):
                        carrito_n = obtener_carrito(str(vendedor)) or []
                        if carrito_n:
                            _, tb = calcular_totales_carrito(
                                carrito_efectivo_mostrador(vendedor, carrito_n), desc_porc
                            )
                            _preparar_pdf_presupuesto_borrador(vendedor, carrito_n, tb)
                            fb_msg = (
                                f"{msj} PDF listo — usá el botón "
                                "**DESCARGAR / IMPRIMIR PRESUPUESTO**."
                            )
                elif ambiguos:
                    fb_tipo, fb_msg = "warning", msj
                    st.session_state.resultados_ia_mostrador = ambiguos
                    st.session_state.msg_ia_mostrador = "Elegí el producto exacto:"
                else:
                    fb_tipo, fb_msg = "error", msj or "No se pudo completar la orden."

            elif accion == "imprimir_ticket":
                if not carrito:
                    st.error("El carrito está vacío.")
                else:
                    ok_val, msg_val, _ = validar_carrito_para_venta(str(vendedor))
                    if not ok_val:
                        st.error(msg_val)
                    else:
                        st.success(_marcar_listo_para_ticket(vendedor, total_final))
                        st.session_state.resultados_ia_mostrador = None
                        st.rerun()

            elif accion == "confirmar_pendiente":
                pend = st.session_state.get("mostrador_accion_pendiente")
                if pend:
                    ok, msj = _ejecutar_accion_pendiente(
                        vendedor, pend, carrito, total_final, desc_porc
                    )
                    _limpiar_accion_pendiente()
                    if ok:
                        st.success(msj)
                        st.session_state.resultados_ia_mostrador = None
                        st.rerun()
                    else:
                        st.error(msj)
                else:
                    st.info("No hay ninguna acción pendiente de confirmación.")

            elif accion == "cancelar_pendiente":
                _limpiar_accion_pendiente()
                st.info("Acción cancelada.")
                st.session_state.resultados_ia_mostrador = None
                st.rerun()

            elif accion == "agregar_items":
                n, msj, ambiguos = _agregar_items_voz(
                    vendedor,
                    resp.get("items"),
                    inventario,
                    buscar_en_inventario,
                    agregar_al_carrito,
                )
                if ambiguos:
                    st.warning(msj)
                    st.session_state.resultados_ia_mostrador = ambiguos
                    st.session_state.msg_ia_mostrador = "Elegí el producto exacto:"
                elif n:
                    carrito_n = carrito_efectivo_mostrador(
                        vendedor, obtener_carrito(str(vendedor)) or []
                    )
                    _, tf = calcular_totales_carrito(carrito_n, desc_porc)
                    st.toast(f"{n} ítem(s) · Total ${tf:,.2f}. Seguí dictando.")
                    st.success(msj)
                    st.rerun()
                else:
                    st.error(msj)

            elif accion == "presupuesto_pdf":
                sincronizar_grilla_carrito_firebase(vendedor, carrito_ui)
                carrito_n = obtener_carrito(str(vendedor)) or []
                if not carrito_n:
                    st.error("El carrito está vacío.")
                else:
                    _, tb = calcular_totales_carrito(carrito_n, desc_porc)
                    pdf = generar_pdf_presupuesto_mostrador(
                        vendedor, carrito_n, tb, desc_porc
                    )
                    cli_nom = st.session_state.cliente_activo.get("nombre", "CLIENTE")
                    st.session_state.presupuesto_pdf_descarga = pdf
                    st.session_state.presupuesto_pdf_nombre = _nombre_archivo_presupuesto(
                        None, cli_nom
                    )
                    _, tf = calcular_totales_carrito(carrito_n, desc_porc)
                    st.success(
                        f"Presupuesto BORRADOR (${tf:,.2f}). "
                        f"Validez {VALIDEZ_PRESUPUESTO_DIAS} días. Descargalo abajo."
                    )
                    st.rerun()

            elif accion == "listo_armado":
                carrito_n = carrito_efectivo_mostrador(
                    vendedor, obtener_carrito(str(vendedor)) or []
                )
                if not carrito_n:
                    fb_tipo, fb_msg = "error", "Carrito vacío."
                else:
                    _, tf = calcular_totales_carrito(carrito_n, desc_porc)
                    intent = resp.get("intent_sugerido")
                    fb_msg = _marcar_listo_para_ticket(vendedor, tf, intent)
                    if intent == "presupuesto":
                        _, tb = calcular_totales_carrito(carrito_n, desc_porc)
                        _preparar_pdf_presupuesto_borrador(vendedor, carrito_n, tb)
                        fb_msg = (
                            f"PDF listo (${tf:,.2f}). "
                            "Usá **DESCARGAR / IMPRIMIR PRESUPUESTO**."
                        )
                    fb_tipo, do_rerun = "ok", True
                    st.session_state.resultados_ia_mostrador = None

            elif accion == "agregar_carrito":
                termino = str(resp.get("termino", ""))
                cant_raw = resp.get("cantidad")
                cant = int(cant_raw) if cant_raw is not None and str(cant_raw).isdigit() else 1
                ok, msj, ambiguos = agregar_termino_voz(
                    vendedor, termino, cant, inventario,
                    buscar_en_inventario, agregar_al_carrito,
                )

                if ok:
                    carrito_n = carrito_efectivo_mostrador(
                        vendedor, obtener_carrito(str(vendedor)) or []
                    )
                    _, tf = calcular_totales_carrito(carrito_n, desc_porc)
                    st.toast(f"Agregado · Total ${tf:,.2f}. Seguí dictando.")
                    st.success(f"🛒 {msj}")
                    st.rerun()
                elif ambiguos:
                    st.warning(f"Encontré {len(ambiguos)} alternativas para '{termino}'.")
                    st.session_state.resultados_ia_mostrador = ambiguos
                    st.session_state.msg_ia_mostrador = (
                        f"Elegí qué variante de '{termino}' querés agregar:"
                    )
                else:
                    st.error(f"❌ {msj}")

            elif accion == "set_cliente":
                nombre_det = str(resp.get("nombre_cliente", "")).upper()
                clientes_db = obtener_clientes() or {}
                cliente_encontrado = next(
                    (c for c in clientes_db.values()
                     if nombre_det in str(c.get("nombre", "")).upper()),
                    None,
                )
                if cliente_encontrado:
                    st.session_state.cliente_activo = cliente_db_a_activo(cliente_encontrado)
                    tipo = resp.get("tipo_comprobante")
                    if tipo in ("1", "6", "A", "B", "a", "b"):
                        t = str(tipo).upper()
                        st.session_state.cliente_activo["tipo_comprobante"] = (
                            "1" if t in ("1", "A") else "6"
                        )
                    st.success(f"✅ Cliente {cliente_encontrado['nombre']} activado.")
                    st.session_state.resultados_ia_mostrador = None
                    st.rerun()
                else:
                    st.warning(f"⚠️ '{nombre_det}' no está en la base de datos.")

            elif accion == "set_tipo_factura":
                tipo = resp.get("tipo_comprobante", "6")
                cli = dict(st.session_state.cliente_activo or cliente_consumidor_final())
                t = str(tipo).upper()
                cli["tipo_comprobante"] = "1" if t in ("1", "A") else "6"
                st.session_state.cliente_activo = cli
                st.success(f"✅ Factura {_tipo_comprobante_label(cli['tipo_comprobante'])}.")
                st.session_state.resultados_ia_mostrador = None
                st.rerun()

            elif accion == "consumidor_final":
                tipo = resp.get("tipo_comprobante")
                cli = cliente_consumidor_final()
                if tipo in ("1", "6", "A", "B", "a", "b"):
                    t = str(tipo).upper()
                    cli["tipo_comprobante"] = "1" if t in ("1", "A") else "6"
                st.session_state.cliente_activo = cli
                st.success("✅ Consumidor final activado.")
                st.session_state.resultados_ia_mostrador = None
                st.rerun()

            elif accion == "set_forma_pago":
                fp = _set_forma_pago(vendedor, resp.get("forma_pago", "Contado"))
                st.success(f"✅ Forma de pago: {fp}")
                st.session_state.resultados_ia_mostrador = None
                st.rerun()

            elif accion == "guardar_presupuesto":
                if not carrito:
                    st.error("El carrito está vacío.")
                else:
                    nota = str(resp.get("nota", "") or "")
                    st.session_state.mostrador_accion_pendiente = {
                        "tipo": "guardar_presupuesto",
                        "nota": nota,
                        "mensaje": f"¿Guardar presupuesto de ${total_final:,.2f} para "
                        f"{st.session_state.cliente_activo.get('nombre', 'CONSUMIDOR FINAL')}?",
                    }
                    st.rerun()

            elif accion == "confirmar_venta":
                if not carrito:
                    st.error("El carrito está vacío.")
                else:
                    ok_val, msg_val, _ = validar_carrito_para_venta(str(vendedor))
                    if not ok_val:
                        st.error(msg_val)
                    else:
                        st.session_state.mostrador_accion_pendiente = {
                            "tipo": "confirmar_venta",
                            "mensaje": (
                                f"¿Confirmar venta por ${total_final:,.2f} "
                                f"(sin factura fiscal) y descontar stock?"
                            ),
                        }
                        st.rerun()

            elif accion == "facturar":
                if not carrito:
                    st.error("El carrito está vacío.")
                else:
                    ok_val, msg_val, _ = validar_carrito_para_venta(str(vendedor))
                    if not ok_val:
                        st.error(msg_val)
                    else:
                        st.success(
                            _marcar_listo_para_ticket(
                                vendedor, total_final, "factura_b"
                            )
                        )
                        st.session_state.resultados_ia_mostrador = None
                        st.rerun()

            elif accion == "vaciar_carrito":
                if not carrito:
                    st.info("El carrito ya está vacío.")
                else:
                    st.session_state.mostrador_accion_pendiente = {
                        "tipo": "vaciar_carrito",
                        "mensaje": "¿Vaciar el carrito actual?",
                    }
                    st.rerun()

            elif accion == "buscar" or accion == "consulta":
                termino = str(resp.get("termino", "") or orden)
                if termino:
                    encontrados = buscar_en_inventario(inventario, termino)
                    if encontrados:
                        st.session_state.resultados_ia_mostrador = encontrados[:10]
                        st.session_state.msg_ia_mostrador = (
                            f"🔍 Encontré estas opciones para '{termino}':"
                        )
                    else:
                        st.warning(f"No encontré coincidencias para '{termino}'.")
                        st.session_state.resultados_ia_mostrador = None
                else:
                    st.warning("No detecté qué producto querés buscar.")
                    st.session_state.resultados_ia_mostrador = None

            elif accion == "error":
                fb_tipo = "error"
                fb_msg = resp.get("respuesta", "Error de IA.")
                st.session_state.resultados_ia_mostrador = None

            elif not accion:
                fb_tipo = "error"
                fb_msg = "No se pudo interpretar la orden."

            else:
                fb_tipo = "info"
                fb_msg = resp.get("respuesta") or "Orden no reconocida para el mostrador."
                st.session_state.resultados_ia_mostrador = None

        if fb_tipo and fb_msg:
            if do_rerun:
                _set_ia_feedback(vendedor, fb_tipo, fb_msg)
                st.rerun()
            elif fb_tipo == "ok":
                st.success(fb_msg)
            elif fb_tipo == "error":
                st.error(fb_msg)
            elif fb_tipo == "warning":
                st.warning(fb_msg)
            else:
                st.info(fb_msg)

    render_panel_coincidencias_mostrador(vendedor, agrupar_por_maestro, agregar_al_carrito)


def _carrito_a_dataframe(carrito):
    filas = []
    for item in carrito:
        if not isinstance(item, dict):
            continue
        cant = int(item.get("cantidad", 1))
        precio = float(item.get("precio_unitario", 0))
        filas.append({
            "_id": str(item.get("id", "")),
            "Código": str(item.get("id", "")),
            "Descripción": str(item.get("descripcion", "")),
            "Cant.": cant,
            "Precio unit.": precio,
        })
    return pd.DataFrame(filas)


def _aplicar_cambios_carrito(vendedor, carrito, df_editado):
    """Sincroniza cantidad/precio editados en la grilla con Firebase."""
    orig_map = {str(i.get("id", "")): i for i in carrito if isinstance(i, dict)}
    errores = []
    cambios = 0

    for _, row in df_editado.iterrows():
        iid = str(row.get("_id", ""))
        if not iid or iid not in orig_map:
            continue
        orig = orig_map[iid]
        cant_n = _int_celda(row.get("Cant."), int(orig.get("cantidad", 1)))
        cant_o = int(orig.get("cantidad", 1))
        precio_n = _float_celda(row.get("Precio unit."), float(orig.get("precio_unitario", 0)))
        precio_o = float(orig.get("precio_unitario", 0))

        if cant_n != cant_o:
            ok, msj = actualizar_cantidad_item_carrito(str(vendedor), iid, cant_n)
            if ok:
                cambios += 1
            else:
                errores.append(msj)
        if precio_n != precio_o:
            ok, msj = actualizar_precio_item_carrito(str(vendedor), iid, precio_n)
            if ok:
                cambios += 1
            else:
                errores.append(msj)

    return cambios, errores


def render_carrito_grilla(vendedor, carrito):
    """Grilla ancha editable (cantidad y precio) — PC y móvil."""
    st.markdown("**Ítems del presupuesto**")
    st.caption("Editá **Cant.** y **Precio unit.** en la tabla y pulsá «Aplicar cambios».")

    df = _carrito_a_dataframe(carrito)
    if df.empty:
        return

    df_edit = st.data_editor(
        df,
        column_config={
            "_id": None,
            "Código": st.column_config.TextColumn("Código", disabled=True, width="medium"),
            "Descripción": st.column_config.TextColumn("Descripción", disabled=True, width="large"),
            "Cant.": st.column_config.NumberColumn(
                "Cant.", min_value=1, max_value=9999, step=1, format="%d"
            ),
            "Precio unit.": st.column_config.NumberColumn(
                "Precio unit.", min_value=0.0, step=0.01, format="$ %.2f"
            ),
        },
        use_container_width=True,
        hide_index=True,
        key=_cart_editor_session_key(vendedor),
    )

    act1, act2, act3 = st.columns([2, 2, 3])
    if act1.button("✅ Aplicar cambios", type="primary", use_container_width=True, key=f"cart_apply_{vendedor}"):
        cambios, errores = _aplicar_cambios_carrito(vendedor, carrito, df_edit)
        if errores:
            st.error("\n".join(errores))
        elif cambios:
            st.rerun()
        else:
            st.toast("Sin cambios.")

    ids = [str(i.get("id", "")) for i in carrito if isinstance(i, dict)]
    labels = {
        iid: f"{iid[:28]}…" if len(iid) > 28 else iid for iid in ids
    }
    with act2:
        st.caption("Quitar ítem")
        quitar = st.selectbox(
            "Quitar ítem",
            options=[""] + ids,
            format_func=lambda x: "— Elegir —" if not x else labels.get(x, x),
            key=f"cart_del_sel_{vendedor}",
            label_visibility="collapsed",
        )
    if act3.button("🗑️ Quitar seleccionado", use_container_width=True, key=f"cart_del_btn_{vendedor}"):
        if quitar:
            ok, msj = eliminar_item_carrito(str(vendedor), quitar)
            if ok:
                st.rerun()
            else:
                st.error(msj)
        else:
            st.warning("Elegí un ítem para quitar.")


def render_panel_cobro_mostrador(
    vendedor, carrito, total_bruto, total_final, desc_porc
):
    """Totales, pago y botones de facturación (columna lateral)."""
    listo_ticket = bool(st.session_state.get("mostrador_listo_para_ticket"))
    intent = st.session_state.get("mostrador_intent_sugerido", "factura_b")

    with st.container(border=True):
        st.markdown('<div class="mostrador-cobro-panel">', unsafe_allow_html=True)

        if desc_porc > 0:
            st.metric("Subtotal", f"${total_bruto:,.2f}")
            st.caption(f"Descuento {desc_porc}%")
        st.metric("Total", f"${total_final:,.2f}")

        if listo_ticket:
            intent = st.session_state.get("mostrador_intent_sugerido", "factura_b")
            if intent == "presupuesto":
                render_descarga_presupuesto_prominente(vendedor)
                if st.button(
                    "💾 Guardar presupuesto numerado",
                    use_container_width=True,
                    key=f"btn_guardar_pres_{vendedor}",
                ):
                    _, err_sync = sincronizar_grilla_carrito_firebase(vendedor, carrito)
                    if err_sync:
                        st.error("\n".join(err_sync))
                    else:
                        ok, msj, nuevo_id = guardar_presupuesto(
                            str(vendedor), st.session_state.cliente_activo, ""
                        )
                        if ok:
                            pres = obtener_presupuesto_guardado(nuevo_id) or {}
                            nro = pres.get("numero_presupuesto")
                            pdf = generar_pdf_presupuesto_mostrador(
                                vendedor, carrito, total_bruto, 0.0, numero=nro,
                            )
                            cli_nom = st.session_state.cliente_activo.get("nombre", "CLIENTE")
                            st.session_state.presupuesto_pdf_descarga = pdf
                            st.session_state.presupuesto_pdf_nombre = _nombre_archivo_presupuesto(
                                nro, cli_nom,
                            )
                            st.session_state.presupuesto_cargado_id = nuevo_id
                            st.success(msj)
                            st.rerun()
                        else:
                            st.error(msj)
                if st.button("↩️ Seguir armando", use_container_width=True, key=f"btn_seguir_arm_{vendedor}"):
                    st.session_state.mostrador_listo_para_ticket = False
                    st.rerun()
            else:
                forma_pago = st.selectbox(
                    "Forma de pago",
                    list(FORMAS_PAGO),
                    index=list(FORMAS_PAGO).index(_forma_pago_actual(vendedor)),
                    key=f"pago_arca_{vendedor}",
                )
                _set_forma_pago(vendedor, forma_pago)
                cuit_fact, clave_fact, _ = _leer_secrets_facturador()
                puede_facturar = bool(cuit_fact and clave_fact)
                if st.button(
                    "🧾 FACTURAR E IMPRIMIR",
                    type="primary",
                    use_container_width=True,
                    disabled=not puede_facturar,
                    key=f"btn_verif_arca_{vendedor}",
                ):
                    if puede_facturar:
                        ok, msj = _facturar_desde_carrito(
                            vendedor, carrito, total_final, desc_porc, forma_pago, solo_ticket=True
                        )
                        if ok:
                            st.success(msj)
                            st.rerun()
                        else:
                            st.error(msj)
                if not puede_facturar:
                    st.caption("Completá CUIT y clave en «Facturación ARCA».")
                if st.button("↩️ Seguir armando", use_container_width=True, key=f"btn_seguir_arm_{vendedor}"):
                    st.session_state.mostrador_listo_para_ticket = False
                    st.rerun()
        else:
            st.caption(
                "El total incluye cambios de la grilla. Decí **listo** en la IA o usá los botones."
            )
            forma_pago = st.selectbox(
                "Forma de pago",
                list(FORMAS_PAGO),
                index=list(FORMAS_PAGO).index(_forma_pago_actual(vendedor)),
                key=f"pago_arca_{vendedor}",
            )
            _set_forma_pago(vendedor, forma_pago)

            cuit_fact, clave_fact, _ = _leer_secrets_facturador()
            puede_facturar = bool(cuit_fact and clave_fact)

            if st.button(
                "✅ Listo — revisar",
                type="primary",
                use_container_width=True,
                key=f"btn_listo_rev_{vendedor}",
            ):
                marcar_verificacion_mostrador(
                    st.session_state.get("mostrador_intent_sugerido", "factura_b")
                )
                st.rerun()

            if st.button(
                "🖨️ Facturar e imprimir ticket",
                use_container_width=True,
                disabled=not puede_facturar,
                key=f"btn_ticket_{vendedor}",
            ):
                if puede_facturar:
                    ok, msj = _facturar_desde_carrito(
                        vendedor, carrito, total_final, desc_porc, forma_pago, solo_ticket=True
                    )
                    if ok:
                        st.success(msj)
                        st.rerun()
                    else:
                        st.error(msj)

            pdf_bytes = generar_pdf_presupuesto_mostrador(
                str(vendedor), carrito, total_bruto, desc_porc,
            )
            st.download_button(
                "📄 Presupuesto borrador",
                pdf_bytes,
                _nombre_archivo_presupuesto(
                    _numero_presupuesto_en_sesion(),
                    st.session_state.cliente_activo.get("nombre", "CLIENTE"),
                ),
                "application/pdf",
                use_container_width=True,
                key=f"dl_pres_borrador_{vendedor}",
            )

            if not puede_facturar:
                st.caption("Completá CUIT y clave en «Facturación ARCA».")

        with st.expander("Más acciones", expanded=False):
            nota_pres = st.text_input("Nota presupuesto", key=f"nota_pres_{vendedor}")
            if st.button("💾 Guardar presupuesto", key=f"guardar_pres_{vendedor}", use_container_width=True):
                ok, msj, nuevo_id = guardar_presupuesto(
                    str(vendedor), st.session_state.cliente_activo, nota_pres
                )
                if ok:
                    st.session_state.presupuesto_cargado_id = nuevo_id
                    st.success(msj)
                else:
                    st.error(msj)
            if st.button("✅ Venta sin factura", key=f"venta_sin_fc_{vendedor}", use_container_width=True):
                _, err_sync = sincronizar_grilla_carrito_firebase(vendedor, carrito)
                if err_sync:
                    st.error("\n".join(err_sync))
                else:
                    exito, msj = confirmar_venta(str(vendedor))
                    if exito:
                        _cerrar_presupuesto_cargado("vendido")
                        limpiar_venta_mostrador(vendedor, reset_cliente=True)
                        st.success(msj)
                        st.rerun()
                    else:
                        st.error(msj)
            if st.button("🗑️ Vaciar carrito", key=f"vaciar_{vendedor}", use_container_width=True):
                limpiar_venta_mostrador(vendedor, reset_cliente=False)
                st.rerun()

        st.markdown("</div>", unsafe_allow_html=True)


def render_mostrador_accion_pendiente(vendedor):
    if not st.session_state.get("mostrador_accion_pendiente"):
        return
    carrito_pend = carrito_efectivo_mostrador(vendedor, obtener_carrito(str(vendedor)) or [])
    dp = float(st.session_state.cliente_activo.get("descuento", 0))
    _, tf = calcular_totales_carrito(carrito_pend, dp)
    render_confirmacion_pendiente_mostrador(vendedor, carrito_pend, tf, dp)


def render_mostrador_venta_actual(vendedor):
    st.markdown("#### Venta actual")
    carrito = obtener_carrito(str(vendedor)) or []
    listo = bool(st.session_state.get("mostrador_listo_para_ticket"))
    if carrito:
        carrito_ui = carrito_efectivo_mostrador(vendedor, carrito)
        desc_porc = float(st.session_state.cliente_activo.get("descuento", 0))
        total_bruto, total_final = calcular_totales_carrito(carrito_ui, desc_porc)
        render_acciones_carrito(
            vendedor, carrito_ui, total_bruto, total_final, desc_porc
        )
    elif listo:
        st.warning("Orden procesada pero el carrito quedó vacío. Revisá el código del producto.")
    else:
        st.info("Carrito vacío — buscá productos o usá la IA de voz.")
    render_historial_facturas_arca()


def render_acciones_carrito(vendedor, carrito, total_bruto, total_final, desc_porc):
    """Compat: panel lateral de cobro (la grilla va aparte a ancho completo)."""
    render_panel_cobro_mostrador(
        vendedor, carrito, total_bruto, total_final, desc_porc
    )


def sincronizar_config_ticket_desde_nube():
    """Opcional: cargar config del ticket desde el backend (sidebar)."""
    cuit, clave, config_local = _leer_secrets_facturador()
    if not cuit or not clave:
        return config_local
    res = cargar_datos_nube(cuit, clave)
    if res.get("success"):
        data = res.get("data") or {}
        cfg = data.get("configuracion")
        if isinstance(cfg, dict):
            config_local.update(cfg)
    return config_local
