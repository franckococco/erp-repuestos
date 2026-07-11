"""UI del mostrador: cliente, búsqueda de productos y facturación ARCA."""
import math
import re
from datetime import date, datetime, time, timedelta, timezone
from typing import Optional

import pandas as pd
import streamlit as st

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
    obtener_config_ticket_mostrador,
    guardar_config_ticket_mostrador,
    obtener_presupuesto_guardado,
)
from modulos.presupuesto_pdf import crear_pdf_presupuesto, VALIDEZ_PRESUPUESTO_DIAS
from modulos.factura_arca_client import generar_factura, cargar_datos_nube
from modulos.factura_arca_pdf import crear_a4
from modulos.factura_arca_ticket_html import crear_ticket_html
from modulos.util_fechas import formatear_fecha_ar, rango_fechas_ar_a_utc, fecha_hoy_ar
from modulos.ia_mostrador import (
    FORMAS_PAGO,
    normalizar_forma_pago,
)
from modulos.mostrador_voz_flujo import (
    inventario_cache_mostrador,
    agregar_termino_voz,
    marcar_verificacion_mostrador,
    descartar_panels_operacion_anterior,
    continuar_cola_voz_mostrador,
    limpiar_cola_voz_mostrador,
    _guardar_intent_voz_pendiente,
)


VENDEDOR_MOSTRADOR = "Caja Principal"

TIPOS_CLIENTE_NEGOCIO = {
    "ocasional": "Cliente ocasional",
    "mecanico": "Mecánico",
    "cuenta_corriente": "Cuenta corriente",
}

# Credenciales fijas del facturador ARCA (mostrador)
CUIT_EMISOR_ARCA = "20265010505"
CLAVE_EMISOR_ARCA = "111"


CONFIG_TICKET_DEFAULT = {
    "margen_x": 2.0,
    "margen_y": 2.0,
    "font_size": 8,
    "nombre_empresa": "HAFID AUTOPARTES",
    "direccion": "",
    "condicion_iva": "IVA Responsable Inscripto",
    "cuit_emisor": CUIT_EMISOR_ARCA,
    "iibb": "Ingresos Brutos: A-76154",
    "inicio_act": "Inicio de Actividades: 02/05/2023",
    "leyenda_extra": "¡Gracias por su compra!",
    "impresora_modo": "navegador",
    "impresora_nombre": "",
    "impresora_ip": "",
    "impresora_puerto": 9100,
}


def _label_tipo_cliente_negocio(tipo: str) -> str:
    return TIPOS_CLIENTE_NEGOCIO.get(str(tipo or "ocasional"), "Cliente ocasional")


def normalizar_cliente_activo(cliente: Optional[dict]) -> dict:
    base = cliente_consumidor_final()
    if not isinstance(cliente, dict):
        return base
    cbte = str(cliente.get("tipo_comprobante", cliente.get("cbte_tipo", "6"))).strip()
    if cbte not in ("1", "6"):
        cbte = "6"
    cuit = "".join(filter(str.isdigit, str(cliente.get("cuit", "00000000000")))) or "00000000000"
    etiqueta = str(cliente.get("etiqueta_descuento", "") or "").strip().upper()
    tipo_cli = str(cliente.get("tipo_cliente", "ocasional") or "ocasional").strip().lower()
    if tipo_cli not in TIPOS_CLIENTE_NEGOCIO:
        tipo_cli = "mecanico" if etiqueta and float(cliente.get("descuento", 0) or 0) > 0 else "ocasional"
    if tipo_cli != "mecanico":
        etiqueta = ""
    return {
        "nombre": str(cliente.get("nombre", base["nombre"])).upper(),
        "cuit": cuit,
        "descuento": float(cliente.get("descuento", 0.0)),
        "tipo_comprobante": cbte,
        "etiqueta_descuento": etiqueta,
        "tipo_cliente": tipo_cli,
    }


def _credenciales_arca_emisor():
    """CUIT y clave del facturador (fijos en mostrador)."""
    return CUIT_EMISOR_ARCA, CLAVE_EMISOR_ARCA


def _defaults_desde_streamlit_secrets():
    return _credenciales_arca_emisor()


def init_credenciales_arca_session():
    from modulos.mostrador_session import init_credenciales_arca_session as _init

    _init()


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

    with st.expander("🔑 Facturación ARCA", expanded=False):
        st.caption(f"CUIT emisor **{cuit}** · listo para facturar")


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


def render_config_ticket_mostrador(en_pagina_config=False):
    """Encabezados del ticket e impresora preferida."""
    init_config_ticket_session()

    if en_pagina_config:
        st.subheader("Ticket y factura — textos e impresora")
        contenedor = st.container()
    else:
        contenedor = st.expander("🧾 Configuración del ticket", expanded=False)

    with contenedor:
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
    etq = str(datos.get("etiqueta_descuento", "") or "").strip().upper()
    extra = f" · {desc:g}% desc." if desc > 0 else ""
    if etq:
        extra += f" · {etq}"
    tipo_neg = _label_tipo_cliente_negocio(datos.get("tipo_cliente", "ocasional"))
    return f"{nombre} · CUIT/DNI {id_cli} · {tipo} · {tipo_neg}{extra}"


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


def _invalidar_pdf_presupuesto_mostrador():
    """Quita PDF en caché cuando el carrito o el cliente ya no coinciden."""
    st.session_state.mostrador_listo_para_ticket = False
    st.session_state.pop("presupuesto_pdf_descarga", None)
    st.session_state.pop("presupuesto_pdf_nombre", None)
    st.session_state.pop("presupuesto_pdf_fingerprint", None)
    st.session_state.pop("presupuesto_emitido_ok", None)
    st.session_state.pop("presupuesto_cerrado_resumen", None)


def agregar_al_carrito_mostrador(vendedor, id_producto, cantidad=1):
    """Agrega al carrito y descarta carteles de la operación anterior."""
    descartar_panels_operacion_anterior()
    _invalidar_pdf_presupuesto_mostrador()
    from modulos.db_firebase import agregar_al_carrito
    from modulos.mostrador_voz_flujo import invalidar_cache_inventario_mostrador
    exito, msj = agregar_al_carrito(str(vendedor), id_producto, cantidad)
    if exito:
        invalidar_cache_inventario_mostrador()
    return exito, msj


def _limpiar_inputs_mostrador(vendedor):
    """Resetea campos de búsqueda y orden rápida en la UI."""
    vid = str(vendedor)
    for key in (
        f"ia_most_{vid}",
        f"busq_most_{vid}",
        f"busq_voz_proc_{vid}",
        f"auto_run_ia_{vid}",
        f"coinc_radio_{vid}",
        f"coinc_cant_{vid}",
        f"coinc_multi_{vid}",
        f"coinc_modo_{vid}",
        f"coinc_add_{vid}",
        f"coinc_add_multi_{vid}",
        f"cerrar_coinc_most_{vid}",
        f"busq_radio_{vid}",
        f"busq_multi_{vid}",
        f"busq_modo_{vid}",
        f"busq_add_multi_{vid}",
        f"busq_add_{vid}",
    ):
        st.session_state.pop(key, None)


def limpiar_venta_mostrador(vendedor, reset_cliente=True, conservar_pdf_presupuesto=False):
    """Vacía carrito y flags de sesión tras cerrar una venta."""
    descartar_panels_operacion_anterior()
    vaciar_carrito(str(vendedor))
    reset_estado_orden_mostrador(
        vendedor,
        reset_cliente=reset_cliente,
        conservar_pdf_presupuesto=conservar_pdf_presupuesto,
    )
    _limpiar_inputs_mostrador(vendedor)
    st.session_state[f"mostrador_cart_rev_{vendedor}"] = (
        int(st.session_state.get(f"mostrador_cart_rev_{vendedor}", 0)) + 1
    )


def cancelar_operacion_mostrador(vendedor, reset_cliente=True):
    """Cancela presupuesto/factura en curso, vacía carrito y deja la pantalla en blanco."""
    from modulos.mostrador_estado import limpiar_pantalla_mostrador

    limpiar_venta_mostrador(
        vendedor,
        reset_cliente=reset_cliente,
        conservar_pdf_presupuesto=False,
    )
    limpiar_pantalla_mostrador(vendedor)


def reset_estado_orden_mostrador(vendedor, reset_cliente=False, conservar_pdf_presupuesto=False):
    """Limpia verificación, PDF pendiente y mensajes de voz."""
    st.session_state.mostrador_listo_para_ticket = False
    st.session_state.mostrador_accion_pendiente = None
    st.session_state.pop("mostrador_intent_sugerido", None)
    st.session_state.pop(f"ia_feedback_{vendedor}", None)
    st.session_state.resultados_ia_mostrador = None
    st.session_state.pop("msg_ia_mostrador", None)
    if not conservar_pdf_presupuesto:
        _invalidar_pdf_presupuesto_mostrador()
    if reset_cliente:
        st.session_state.cliente_activo = cliente_consumidor_final()


def _al_modificar_carrito_mostrador(vendedor):
    """Tras quitar ítems: invalidar PDF y verificación."""
    _invalidar_pdf_presupuesto_mostrador()
    if not (obtener_carrito(str(vendedor)) or []):
        reset_estado_orden_mostrador(vendedor, reset_cliente=False)


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


def _fingerprint_presupuesto(vendedor, carrito=None):
    """Huella del carrito + cliente para detectar PDF desactualizado."""
    cli = normalizar_cliente_activo(st.session_state.get("cliente_activo"))
    if carrito is None:
        carrito = carrito_efectivo_mostrador(vendedor, obtener_carrito(str(vendedor)) or [])
    parts = [
        str(cli.get("nombre", "")),
        str(cli.get("cuit", cli.get("cuit_dni", ""))),
        f"{float(cli.get('descuento', 0)):.4f}",
    ]
    for item in sorted(carrito, key=lambda x: str(x.get("id", ""))):
        parts.append(
            f"{item.get('id')}|{int(item.get('cantidad', 1))}|"
            f"{float(item.get('precio_unitario', 0)):.4f}"
        )
    return "|".join(parts)


def _carrito_para_presupuesto(vendedor):
    """Sincroniza la grilla con Firebase y devuelve ítems efectivos para el PDF."""
    carrito_base = obtener_carrito(str(vendedor)) or []
    if carrito_base:
        sincronizar_grilla_carrito_firebase(vendedor, carrito_base)
        carrito_base = obtener_carrito(str(vendedor)) or []
    return carrito_efectivo_mostrador(vendedor, carrito_base)


def _preparar_pdf_presupuesto_borrador(vendedor, carrito, total_bruto):
    """Genera PDF y vacía el carrito en Firebase (queda solo la descarga pendiente)."""
    cli = normalizar_cliente_activo(st.session_state.cliente_activo)
    cli_nom = cli.get("nombre", "CLIENTE")
    desc_porc = float(cli.get("descuento", 0))
    _, total_final = calcular_totales_carrito(carrito, desc_porc)
    pdf = generar_pdf_presupuesto_mostrador(
        vendedor, carrito, float(total_bruto), desc_porc, numero=None
    )
    st.session_state.presupuesto_pdf_descarga = pdf
    st.session_state.presupuesto_pdf_nombre = _nombre_archivo_presupuesto(None, cli_nom)
    st.session_state.presupuesto_pdf_fingerprint = _fingerprint_presupuesto(vendedor, carrito)
    st.session_state.presupuesto_emitido_ok = True
    st.session_state.presupuesto_cerrado_resumen = {
        "cliente": cli_nom,
        "n_items": len(carrito),
        "total": float(total_final),
    }
    _vaciar_carrito_tras_presupuesto_emitido(vendedor, reset_cliente=True)
    return pdf


def _vaciar_carrito_tras_presupuesto_emitido(vendedor, reset_cliente=True):
    """Vacía carrito en Firebase tras emitir presupuesto; conserva PDF en sesión."""
    from modulos.mostrador_estado import limpiar_mensaje_chat

    descartar_panels_operacion_anterior()
    vaciar_carrito(str(vendedor))
    st.session_state.mostrador_listo_para_ticket = False
    st.session_state.mostrador_accion_pendiente = None
    st.session_state.pop("mostrador_intent_sugerido", None)
    st.session_state.pop(f"ia_feedback_{vendedor}", None)
    st.session_state.resultados_ia_mostrador = None
    st.session_state.pop("msg_ia_mostrador", None)
    st.session_state.pop("mostrador_voz_cola_ambiguos", None)
    st.session_state.pop("mostrador_voz_cant_coincidencia", None)
    st.session_state.pop("mostrador_voz_intent_pendiente", None)
    _limpiar_inputs_mostrador(vendedor)
    limpiar_mensaje_chat()
    if reset_cliente:
        st.session_state.cliente_activo = cliente_consumidor_final()
    st.session_state[f"mostrador_cart_rev_{vendedor}"] = (
        int(st.session_state.get(f"mostrador_cart_rev_{vendedor}", 0)) + 1
    )


def _cerrar_presupuesto_mostrador(vendedor, reset_cliente=True):
    """Cierra presupuesto tras descargar/imprimir: vacía todo incluido el PDF."""
    limpiar_venta_mostrador(
        str(vendedor), reset_cliente=reset_cliente, conservar_pdf_presupuesto=False
    )


def _finalizar_presupuesto_impreso(vendedor):
    """Tras descargar/imprimir: limpia PDF pendiente y deja el mostrador en blanco."""
    _invalidar_pdf_presupuesto_mostrador()
    _limpiar_inputs_mostrador(vendedor)


def render_presupuesto_pdf_pendiente(vendedor):
    """Banner de descarga cuando el presupuesto ya se emitió y el carrito está vacío."""
    if not st.session_state.get("presupuesto_emitido_ok"):
        return
    pdf_ready = st.session_state.get("presupuesto_pdf_descarga")
    if not pdf_ready:
        return

    resumen = st.session_state.get("presupuesto_cerrado_resumen") or {}
    cliente = resumen.get("cliente", "CLIENTE")
    total = resumen.get("total")
    n_items = resumen.get("n_items", "")
    detalle = f" · {n_items} ítem(s)" if n_items else ""
    total_txt = f" · ${total:,.2f}" if total is not None else ""

    st.success(
        f"**Presupuesto listo** — {cliente}{detalle}{total_txt}. "
        "Descargá el PDF para imprimir."
    )
    col_dl, col_ok, col_x = st.columns([3, 1, 1])
    with col_dl:
        st.download_button(
            "⬇️ Descargar / imprimir presupuesto",
            pdf_ready,
            st.session_state.get("presupuesto_pdf_nombre", "Presupuesto.pdf"),
            "application/pdf",
            type="primary",
            use_container_width=True,
            key=f"dl_pres_pend_{vendedor}",
            on_click=_finalizar_presupuesto_impreso,
            args=(vendedor,),
        )
    with col_ok:
        if st.button(
            "✅ Listo",
            use_container_width=True,
            key=f"cerrar_pres_pend_{vendedor}",
            help="Cerrar sin volver a descargar.",
        ):
            _finalizar_presupuesto_impreso(vendedor)
            st.rerun()
    with col_x:
        if st.button(
            "❌ Cancelar",
            use_container_width=True,
            key=f"cancelar_pres_pend_{vendedor}",
            help="Descartar presupuesto y limpiar pantalla.",
        ):
            cancelar_operacion_mostrador(vendedor, reset_cliente=True)
            st.rerun()


def _render_cierre_presupuesto_mostrador(vendedor, carrito, desc_porc):
    """Acciones de cierre mientras aún hay ítems en la grilla (antes de emitir)."""
    if not carrito:
        return

    if st.button(
        "⬇️ Generar presupuesto PDF",
        use_container_width=True,
        type="primary",
        key=f"btn_pdf_pres_{vendedor}",
    ):
        carrito_sync = _carrito_para_presupuesto(vendedor)
        if not carrito_sync:
            st.error("El carrito está vacío.")
        else:
            _, tb = calcular_totales_carrito(carrito_sync, desc_porc)
            _preparar_pdf_presupuesto_borrador(vendedor, carrito_sync, tb)
            st.rerun()

    if st.button(
        "💾 Guardar presupuesto numerado",
        use_container_width=True,
        key=f"btn_guardar_pres_{vendedor}",
    ):
        carrito_sync = _carrito_para_presupuesto(vendedor)
        if not carrito_sync:
            st.error("El carrito está vacío.")
        else:
            ok, msj, nuevo_id = guardar_presupuesto(
                str(vendedor), st.session_state.cliente_activo, ""
            )
            if ok:
                pres = obtener_presupuesto_guardado(nuevo_id) or {}
                nro = pres.get("numero_presupuesto")
                _, tb = calcular_totales_carrito(carrito_sync, desc_porc)
                cli = normalizar_cliente_activo(st.session_state.cliente_activo)
                cli_nom = cli.get("nombre", "CLIENTE")
                _, tf = calcular_totales_carrito(carrito_sync, desc_porc)
                pdf = generar_pdf_presupuesto_mostrador(
                    vendedor, carrito_sync, tb, desc_porc, numero=nro,
                )
                st.session_state.presupuesto_pdf_descarga = pdf
                st.session_state.presupuesto_pdf_nombre = _nombre_archivo_presupuesto(
                    nro, cli_nom,
                )
                st.session_state.presupuesto_pdf_fingerprint = _fingerprint_presupuesto(
                    vendedor, carrito_sync
                )
                st.session_state.presupuesto_emitido_ok = True
                st.session_state.presupuesto_cerrado_resumen = {
                    "cliente": cli_nom,
                    "n_items": len(carrito_sync),
                    "total": float(tf),
                }
                st.session_state.presupuesto_cargado_id = nuevo_id
                _vaciar_carrito_tras_presupuesto_emitido(vendedor, reset_cliente=True)
                st.success(msj)
                st.rerun()
            else:
                st.error(msj)


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
        float(desc_porc),
        numero,
        cfg,
        nota,
    )


def _agregar_items_voz(vendedor, items, inventario, buscar_en_inventario, agregar_al_carrito):
    """Agrega varios ítems; devuelve (ok_count, mensajes, ambiguos)."""
    ok_count = 0
    mensajes = []
    errores = []
    cola_ambiguos = []

    for raw in items or []:
        if not isinstance(raw, dict):
            continue
        termino = raw.get("termino") or raw.get("codigo") or raw.get("descripcion")
        cant = raw.get("cantidad", 1)
        ok, msj, ambiguos = agregar_termino_voz(
            vendedor, termino, cant, inventario, buscar_en_inventario, agregar_al_carrito,
            vehiculo=raw.get("vehiculo"),
        )
        if ok:
            ok_count += 1
            mensajes.append(msj)
        elif ambiguos:
            cola_ambiguos.append({
                "termino": termino,
                "cantidad": cant,
                "vehiculo": raw.get("vehiculo"),
                "coincidencias": ambiguos,
                "msj": msj,
            })
            errores.append(msj)
        else:
            errores.append(msj)

    if cola_ambiguos:
        st.session_state.mostrador_voz_cola_ambiguos = cola_ambiguos
        intent_pend = (
            st.session_state.get("mostrador_voz_intent_pendiente")
            or st.session_state.get("mostrador_intent_sugerido")
        )
        if intent_pend:
            _guardar_intent_voz_pendiente(intent_pend)
        first = cola_ambiguos[0]
        st.session_state.mostrador_voz_cant_coincidencia = int(first.get("cantidad", 1))
        msg = str(first.get("msj", f"Varias opciones para '{first.get('termino', '')}'."))
        if ok_count:
            msg = f"Agregados {ok_count} ítem(s). {msg} (faltan {len(cola_ambiguos)} por elegir)"
        return ok_count, msg, first.get("coincidencias")

    if ok_count and errores:
        return ok_count, "\n".join(mensajes + errores), None
    if ok_count:
        return ok_count, "\n".join(mensajes), None
    if errores:
        return 0, "\n".join(errores), None
    return 0, "No se detectaron productos.", None


def _finalizar_revision_si_listo(vendedor):
    """Tras completar cola de ambiguos: la grilla de revisión queda activa vía marcar_verificacion_mostrador."""
    return


def _tras_agregar_coincidencia_voz(vendedor, buscar_en_inventario, obtener_inventario, agregar_al_carrito):
    """Tras elegir una variante, sigue con el resto de ítems pendientes de la orden."""
    from modulos.mostrador_estado import guardar_mensaje_chat

    cola = st.session_state.get("mostrador_voz_cola_ambiguos")
    if cola:
        cola.pop(0)
        st.session_state.mostrador_voz_cola_ambiguos = cola
    inv = inventario_cache_mostrador(obtener_inventario)
    _, ambiguos, msg = continuar_cola_voz_mostrador(
        vendedor, inv, buscar_en_inventario, agregar_al_carrito
    )
    if ambiguos:
        st.session_state.resultados_ia_mostrador = ambiguos
        st.session_state.msg_ia_mostrador = msg or "Elegí el producto exacto:"
        return
    st.session_state.resultados_ia_mostrador = None
    st.session_state.msg_ia_mostrador = None
    _finalizar_revision_si_listo(vendedor)
    if msg:
        guardar_mensaje_chat(
            st.session_state.get("venta_chat_orden", "Orden"),
            msg,
            "ok",
        )


def render_presupuestos_guardados(vendedor):
    with st.expander("📁 Presupuestos guardados", expanded=False):
        solo_abiertos = st.checkbox("Solo abiertos", value=True, key="pres_solo_abiertos")
        col_load, _ = st.columns([1, 3])
        with col_load:
            refrescar = st.button("↻ Cargar lista", key="pres_cargar_lista", use_container_width=True)

        cache_key = f"pres_lista_{solo_abiertos}"
        if refrescar or cache_key not in st.session_state:
            if refrescar:
                st.session_state[cache_key] = listar_presupuestos_guardados(
                    solo_abiertos=solo_abiertos, limite=30
                )
            elif cache_key not in st.session_state:
                st.caption("Pulsá **Cargar lista** para ver presupuestos guardados.")
                return

        lista = st.session_state.get(cache_key) or []

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
                descartar_panels_operacion_anterior()
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
        nro_pres = pres.get("numero_presupuesto")
        pdf_cache_key = f"pres_pdf_bytes_{sel_id}"
        if col_pdf.button("📄 Preparar PDF", use_container_width=True, key="pres_gen_pdf"):
            st.session_state[pdf_cache_key] = generar_pdf_presupuesto_mostrador(
                pres.get("vendedor", vendedor),
                items_pres,
                total_bruto_pres,
                desc_pres,
                numero=pres.get("numero_presupuesto"),
                nota=pres.get("nota", ""),
            )
        pdf_pres = st.session_state.get(pdf_cache_key)
        if pdf_pres:
            col_pdf.download_button(
                "⬇ Descargar PDF",
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
            + (f" · {_label_tipo_cliente_negocio(cli.get('tipo_cliente'))}" if cli.get("tipo_cliente") else "")
        )
    with col_cf:
        if st.button("Consumidor final", use_container_width=True):
            descartar_panels_operacion_anterior()
            _invalidar_pdf_presupuesto_mostrador()
            st.session_state.cliente_activo = cliente_consumidor_final()
            st.rerun()
    with col_lim:
        if st.button("Limpiar cliente", use_container_width=True):
            descartar_panels_operacion_anterior()
            _invalidar_pdf_presupuesto_mostrador()
            st.session_state.cliente_activo = cliente_consumidor_final()
            st.rerun()

    with st.expander("🔍 Buscar o cargar cliente", expanded=False):
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
                            descartar_panels_operacion_anterior()
                            _invalidar_pdf_presupuesto_mostrador()
                            st.session_state.cliente_activo = cliente_db_a_activo(
                                clientes_db.get(sel_id, {})
                            )
                            st.rerun()
                        else:
                            st.warning("Seleccioná un cliente de la lista.")
                    datos_sel = clientes_db.get(sel_id or "", {}) if sel_id else {}
                    if datos_sel:
                        with st.form("mostrador_edit_cliente_negocio"):
                            tipo_edit = st.radio(
                                "Tipo en el negocio",
                                options=list(TIPOS_CLIENTE_NEGOCIO.keys()),
                                format_func=_label_tipo_cliente_negocio,
                                index=list(TIPOS_CLIENTE_NEGOCIO.keys()).index(
                                    str(datos_sel.get("tipo_cliente", "ocasional") or "ocasional")
                                    if str(datos_sel.get("tipo_cliente", "ocasional") or "ocasional")
                                    in TIPOS_CLIENTE_NEGOCIO
                                    else "ocasional"
                                ),
                                horizontal=True,
                            )
                            etq_edit = ""
                            if tipo_edit == "mecanico":
                                etq_edit = st.text_input(
                                    "Sigla en comprobante (ej. MEC)",
                                    value=str(datos_sel.get("etiqueta_descuento", "") or ""),
                                    help="Discreta, al pie del CAE o en la última línea del presupuesto.",
                                )
                            if st.form_submit_button("Guardar datos del cliente"):
                                ok, msj = configurar_cliente(
                                    datos_sel.get("nombre", ""),
                                    sel_id,
                                    float(datos_sel.get("descuento", 0)),
                                    datos_sel.get("tipo_comprobante", "6"),
                                    etiqueta_descuento=etq_edit if tipo_edit == "mecanico" else "",
                                    tipo_cliente=tipo_edit,
                                )
                                if ok:
                                    st.session_state.cliente_activo = cliente_db_a_activo({
                                        **datos_sel,
                                        "tipo_cliente": tipo_edit,
                                        "etiqueta_descuento": str(etq_edit or "").strip().upper()
                                        if tipo_edit == "mecanico" else "",
                                    })
                                    st.success(msj)
                                    st.rerun()
                                else:
                                    st.error(msj)

        with st.form("mostrador_alta_cliente_rapida"):
            c1, c2 = st.columns(2)
            nombre_nuevo = c1.text_input("Nombre / Razón Social")
            cuit_nuevo = c2.text_input("DNI o CUIT")
            c3, c4 = st.columns(2)
            desc_nuevo = c3.number_input("% Desc.", min_value=0.0, step=1.0, value=0.0)
            tipo_negocio = c4.radio(
                "Tipo en el negocio",
                options=list(TIPOS_CLIENTE_NEGOCIO.keys()),
                format_func=_label_tipo_cliente_negocio,
                horizontal=True,
            )
            etiqueta_nuevo = ""
            if tipo_negocio == "mecanico":
                etiqueta_nuevo = st.text_input(
                    "Sigla en comprobante (ej. MEC)",
                    placeholder="MEC",
                    help="Solo para mecánicos. No muestra el Dto en el PDF; la sigla va discreta al pie.",
                )
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
                        nombre_nuevo.upper(),
                        cuit_nuevo,
                        desc_nuevo,
                        tipo_nuevo,
                        etiqueta_descuento=etiqueta_nuevo if tipo_negocio == "mecanico" else "",
                        tipo_cliente=tipo_negocio,
                    )
                    if ok:
                        id_cli = "".join(filter(str.isdigit, str(cuit_nuevo)))
                        descartar_panels_operacion_anterior()
                        _invalidar_pdf_presupuesto_mostrador()
                        st.session_state.cliente_activo = normalizar_cliente_activo({
                            "nombre": nombre_nuevo.upper(),
                            "cuit": id_cli,
                            "descuento": float(desc_nuevo),
                            "tipo_comprobante": tipo_nuevo,
                            "etiqueta_descuento": str(etiqueta_nuevo or "").strip().upper()
                            if tipo_negocio == "mecanico" else "",
                            "tipo_cliente": tipo_negocio,
                        })
                        st.success(msj)
                        st.rerun()
                    else:
                        st.error(msj)
                else:
                    st.error("Nombre y CUIT/DNI son obligatorios.")


def render_panel_coincidencias_mostrador(
    vendedor,
    agrupar_por_maestro,
    agregar_al_carrito,
    buscar_en_inventario=None,
    obtener_inventario=None,
):
    """Variantes encontradas (IA o búsqueda): elegir una o varias."""
    resultados = st.session_state.get("resultados_ia_mostrador")
    if not resultados:
        return

    vid = str(vendedor)
    col_msg, col_x = st.columns([11, 1])
    with col_msg:
        st.markdown(
            f"**{st.session_state.get('msg_ia_mostrador', 'Coincidencias')}**"
        )
    with col_x:
        if st.button("✕", key=f"cerrar_coinc_most_{vid}", help="Cerrar y limpiar chat"):
            from modulos.mostrador_estado import limpiar_pantalla_mostrador

            limpiar_pantalla_mostrador(vendedor)
            st.rerun()

    flat = []
    labels = {}
    grupos_most = agrupar_por_maestro(resultados)
    for gkey in sorted(grupos_most.keys(), key=lambda k: grupos_most[k]["descripcion"]):
        g = grupos_most[gkey]
        for res in g["variantes"]:
            rid = str(res.get("id", ""))
            if not rid:
                continue
            marca_res = res.get("marca", res.get("condicion", ""))
            precio_f = float(res.get("precio_venta", 0))
            stock = res.get("stock", 0)
            labels[rid] = (
                f"{g.get('descripcion', '')[:40]} · {g.get('codigo', '')} · "
                f"{marca_res} · {stock} u. · ${precio_f:,.0f}"
            )
            flat.append(rid)

    if not flat:
        st.warning("Sin variantes para elegir.")
        return

    modo = st.radio(
        "Modo de selección",
        options=["uno", "varios"],
        format_func=lambda x: "Un artículo" if x == "uno" else "Varios artículos",
        horizontal=True,
        key=f"coinc_modo_{vid}",
    )
    cant_pend = int(st.session_state.get("mostrador_voz_cant_coincidencia") or 0)
    if cant_pend > 0 and f"coinc_cant_{vid}" not in st.session_state:
        st.session_state[f"coinc_cant_{vid}"] = cant_pend
    cant = st.number_input(
        "Cant. por ítem",
        min_value=1,
        step=1,
        value=int(st.session_state.get(f"coinc_cant_{vid}", cant_pend or 1)),
        key=f"coinc_cant_{vid}",
    )

    if modo == "uno":
        sel_id = st.radio(
            "Elegí la variante:",
            options=flat,
            format_func=lambda rid: labels.get(rid, rid),
            key=f"coinc_radio_{vid}",
            index=0,
        )
        if st.button(
            "➕ Agregar seleccionado",
            type="primary",
            use_container_width=True,
            key=f"coinc_add_{vid}",
        ):
            exito, msj_db = agregar_al_carrito(vid, sel_id, int(cant))
            if exito:
                if buscar_en_inventario and obtener_inventario:
                    _tras_agregar_coincidencia_voz(
                        vendedor, buscar_en_inventario, obtener_inventario, agregar_al_carrito
                    )
                else:
                    st.session_state.resultados_ia_mostrador = None
                    st.session_state.msg_ia_mostrador = None
                st.rerun()
            else:
                st.error(msj_db)
    else:
        sel_ids = st.multiselect(
            "Elegí una o más variantes:",
            options=flat,
            format_func=lambda rid: labels.get(rid, rid),
            key=f"coinc_multi_{vid}",
        )
        if st.button(
            "➕ Agregar seleccionados",
            type="primary",
            use_container_width=True,
            key=f"coinc_add_multi_{vid}",
        ):
            if not sel_ids:
                st.warning("Seleccioná al menos un artículo.")
            else:
                ok_n = 0
                errores = []
                for rid in sel_ids:
                    exito, msj_db = agregar_al_carrito(vid, rid, int(cant))
                    if exito:
                        ok_n += 1
                    else:
                        errores.append(msj_db)
                if ok_n:
                    if buscar_en_inventario and obtener_inventario:
                        _tras_agregar_coincidencia_voz(
                            vendedor, buscar_en_inventario, obtener_inventario, agregar_al_carrito
                        )
                    else:
                        st.session_state.resultados_ia_mostrador = None
                        st.session_state.msg_ia_mostrador = None
                    if errores:
                        st.warning(f"Agregados {ok_n}. Algunos fallaron:\n" + "\n".join(errores))
                    st.rerun()
                elif errores:
                    st.error("\n".join(errores))


def render_buscador_productos(vendedor, inv_completo, agregar_al_carrito, filtrar_inventario):
    from modulos.ia_mostrador import parece_orden_voz_mostrador

    st.markdown(
        '<div class="mostrador-buscador-box"><strong>🔍 Búsqueda manual</strong></div>',
        unsafe_allow_html=True,
    )
    busqueda = st.text_input(
        "Buscar por código, descripción, vehículo o marca",
        key=f"busq_most_{vendedor}",
        placeholder="Ej: 111, filtro aceite, descripción buje…",
    )
    if not busqueda or len(busqueda.strip()) < 2:
        st.info("Escribí al menos 2 caracteres para buscar.")
        return

    busq = busqueda.strip()
    if parece_orden_voz_mostrador(busq):
        from modulos.mostrador_voz_flujo import (
            extraer_items_orden_voz,
            normalizar_orden_voz_mostrador,
        )
        norm = normalizar_orden_voz_mostrador(busq)
        items_voz = extraer_items_orden_voz(norm)
        terminos = []
        for it in items_voz:
            if not isinstance(it, dict):
                continue
            t = str(it.get("termino") or it.get("codigo") or "").strip()
            if t:
                terminos.append(t)
        if terminos:
            busq = terminos[0]
            st.caption(f"Búsqueda del repuesto: «{busq}» (extraído de la orden).")
        else:
            st.info("Para órdenes completas (cliente + presupuesto) usá el chat de arriba.")
            return

    encontrados = filtrar_inventario(inv_completo, busq)[:25]
    if not encontrados:
        st.warning("Sin coincidencias. Probá con código, descripción u otra palabra.")
        return

    opciones = {}
    for item in encontrados:
        if isinstance(item, dict):
            marca_item = item.get("marca", item.get("condicion", ""))
            iid = str(item.get("id", ""))
            desc = (
                f"{item.get('codigo', '')} | {item.get('vehiculo', '')} - "
                f"{marca_item} | {item.get('descripcion', '')} - "
                f"${item.get('precio_venta', 0)} (stock {item.get('stock', 0)})"
            )
            opciones[iid] = desc

    ids = list(opciones.keys())
    vid = str(vendedor)
    modo_busq = st.radio(
        "Modo",
        options=["uno", "varios"],
        format_func=lambda x: "Un artículo" if x == "uno" else "Varios artículos",
        horizontal=True,
        key=f"busq_modo_{vid}",
    )
    cant_b = st.number_input("Cant. por ítem", min_value=1, step=1, key=f"cant_b_{vid}")

    if modo_busq == "uno":
        sel_id = st.radio(
            "Resultados:",
            options=ids,
            format_func=lambda x: opciones.get(x, x),
            key=f"busq_radio_{vid}",
            index=0,
        )
        if st.button("➕ Agregar al carrito", use_container_width=True, type="primary", key=f"busq_add_{vid}"):
            exito, msj = agregar_al_carrito(vid, sel_id, int(cant_b))
            if exito:
                st.success(msj)
                st.rerun()
            else:
                st.error(msj)
    else:
        sel_ids = st.multiselect(
            "Resultados (elegí uno o más):",
            options=ids,
            format_func=lambda x: opciones.get(x, x),
            key=f"busq_multi_{vid}",
        )
        if st.button(
            "➕ Agregar seleccionados",
            use_container_width=True,
            type="primary",
            key=f"busq_add_multi_{vid}",
        ):
            if not sel_ids:
                st.warning("Seleccioná al menos un artículo.")
            else:
                ok_n = 0
                for iid in sel_ids:
                    exito, _ = agregar_al_carrito(vid, iid, int(cant_b))
                    if exito:
                        ok_n += 1
                if ok_n:
                    st.success(f"Agregados {ok_n} artículo(s).")
                    st.rerun()
                else:
                    st.error("No se pudo agregar ningún artículo.")


def _filas_carrito_desde_inputs(vendedor, carrito):
    """Lee cantidad/precio editados en la grilla por filas."""
    rev = int(st.session_state.get(f"mostrador_cart_rev_{vendedor}", 0))
    vid = str(vendedor)
    items = [i for i in (carrito or []) if isinstance(i, dict)]
    filas = []
    for idx, item in enumerate(items):
        iid = str(item.get("id", ""))
        if not iid:
            continue
        safe = re.sub(r"[^\w]", "_", iid)[:36]
        qkey = f"cart_q_{vid}_{rev}_{idx}_{safe}"
        pkey = f"cart_p_{vid}_{rev}_{idx}_{safe}"
        cant = st.session_state.get(qkey, item.get("cantidad", 1))
        precio = st.session_state.get(pkey, item.get("precio_unitario", 0))
        filas.append((iid, cant, precio))
    return filas


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


def carrito_efectivo_mostrador(vendedor, carrito_base):
    """Carrito con cantidad/precio de la grilla aún no guardados en Firebase."""
    carrito = [dict(i) for i in (carrito_base or []) if isinstance(i, dict)]
    if not carrito:
        return carrito

    id_map = {str(i.get("id", "")): i for i in carrito}
    for iid, cant, precio in _filas_carrito_desde_inputs(vendedor, carrito):
        if not iid or iid not in id_map:
            continue
        item = id_map[iid]
        cant = _int_celda(cant, int(item.get("cantidad", 1)))
        precio = _float_celda(precio, float(item.get("precio_unitario", 0)))
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
    filas = _filas_carrito_desde_inputs(vendedor, carrito)
    if not filas:
        return 0, []
    return _aplicar_cambios_carrito_filas(vendedor, carrito, filas)


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
        cant = max(1, int(item.get("cantidad", 1)))
        precio_u = float(item.get("precio_unitario", 0) or 0) * factor
        sub = float(item.get("subtotal", precio_u * cant) or 0) * factor
        if sub <= 0 and precio_u > 0:
            sub = precio_u * cant
        codigo = str(item.get("codigo") or item.get("id_maestro") or "").strip()
        desc = str(item.get("descripcion", "Artículo")).strip() or "Artículo"
        items.append({
            "codigo": codigo,
            "id_maestro": codigo or str(item.get("id", "")).split("_")[0],
            "descripcion": desc[:120],
            "cantidad": cant,
            "precio_unitario": round(precio_u, 2),
            "precio": round(sub, 2),
        })
    return items


def _auto_imprimir_ticket(html_ticket):
    """Marca ticket HTML para abrir diálogo de impresión al mostrar el panel."""
    if html_ticket:
        st.session_state["_ticket_auto_print_html"] = html_ticket


def _formato_nro_comprobante(datos):
    try:
        return (
            f"{int(float(datos.get('punto_venta', 0))):04d}-"
            f"{int(float(datos.get('numero_factura', 0))):08d}"
        )
    except (TypeError, ValueError):
        return "—"


def _cliente_para_pdf(cliente):
    cli = normalizar_cliente_activo(cliente if isinstance(cliente, dict) else {})
    cbte = str(cli.get("tipo_comprobante") or "6")
    return {
        "cuit": cli.get("cuit", "00000000000"),
        "nombre": cli.get("nombre", "CONSUMIDOR FINAL"),
        "cbte_tipo": cbte,
        "descuento": cli.get("descuento", 0.0),
        "etiqueta_descuento": cli.get("etiqueta_descuento", ""),
        "tipo_cliente": cli.get("tipo_cliente", "ocasional"),
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


def regenerar_comprobantes_arca(comp):
    _, _, cfg = _leer_secrets_facturador()
    datos_resp = _respuesta_para_pdf(comp)
    datos_cliente = _cliente_para_pdf(comp.get("cliente"))
    items = comp.get("items") or []
    forma_pago = str(comp.get("forma_pago") or "Contado")
    html_ticket = crear_ticket_html(
        datos_resp, datos_cliente, items, cfg, forma_pago=forma_pago
    )
    a4 = crear_a4(datos_resp, datos_cliente, items, cfg)
    return html_ticket, a4, datos_resp


def regenerar_pdfs_comprobante(comp):
    """Compatibilidad: devuelve (html_ticket, a4, datos)."""
    return regenerar_comprobantes_arca(comp)


def _render_vista_previa_ticket_html(html_ticket: str, key_prefix: str):
    if not html_ticket:
        return
    with st.expander("Vista previa ticket", expanded=False):
        import streamlit.components.v1 as components
        components.html(html_ticket, height=420, scrolling=True)


def _render_acciones_comprobante(nro, html_ticket, pdf_a4, key_prefix, solo_ticket=False):
    """Ticket HTML imprimible + factura A4 en PDF."""
    n_cols = 3 if (pdf_a4 and not solo_ticket and html_ticket) else 2
    cols = st.columns(n_cols)
    idx = 0
    if html_ticket:
        html_bytes = html_ticket.encode("utf-8")
        with cols[idx]:
            st.download_button(
                "🖨️ Ticket HTML",
                html_bytes,
                file_name=f"Ticket_{nro}.html",
                mime="text/html",
                use_container_width=True,
                key=f"{key_prefix}_ticket_html",
            )
        idx += 1
        if idx < len(cols):
            with cols[idx]:
                if st.button(
                    "🖨️ Imprimir ticket",
                    use_container_width=True,
                    key=f"{key_prefix}_ticket_print",
                ):
                    st.session_state[f"{key_prefix}_ticket_print_html"] = html_ticket
        idx += 1
    if pdf_a4 and not solo_ticket and idx < len(cols):
        with cols[idx]:
            st.download_button(
                "↓ Factura A4",
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

    print_html = st.session_state.pop(f"{key_prefix}_ticket_print_html", None)
    if print_html:
        import streamlit.components.v1 as components
        components.html(print_html, height=0, scrolling=False)


def _render_acciones_pdf_compactas(nro, html_ticket, pdf_a4, key_prefix, solo_ticket=False):
    _render_acciones_comprobante(nro, html_ticket, pdf_a4, key_prefix, solo_ticket=solo_ticket)


def render_factura_arca_exitosa(key_suffix=""):
    rec = st.session_state.get("factura_arca_reciente")
    if not rec:
        return False

    auto_print = st.session_state.pop("_ticket_auto_print_html", None)
    if auto_print:
        import streamlit.components.v1 as components
        components.html(auto_print, height=0, scrolling=False)

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

        _render_acciones_comprobante(
            nro,
            rec.get("html_ticket"),
            rec.get("pdf_a4"),
            f"fact_{ks}",
            solo_ticket=solo_ticket,
        )
        _render_vista_previa_ticket_html(rec.get("html_ticket"), f"fact_{ks}")
    return True


def render_historial_facturas_arca():
    """Buscar y reimprimir facturas ARCA (pestaña dedicada; carga bajo demanda)."""
    st.markdown("#### Facturas ARCA — buscar y reimprimir")
    st.caption("Consultá comprobantes emitidos por fecha, número, cliente o CAE.")

    hoy = fecha_hoy_ar()
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

    buscar = st.button("🔍 Buscar facturas", key="hist_arca_buscar", type="primary")
    if buscar:
        st.session_state.hist_arca_resultados = None
        st.session_state.hist_arca_preview = None

    if not buscar and not st.session_state.get("hist_arca_resultados"):
        st.info("Elegí el rango de fechas y pulsá **Buscar facturas**.")
        return

    lista = st.session_state.get("hist_arca_resultados")
    if lista is None or buscar:
        try:
            ini, fin = rango_fechas_ar_a_utc(fecha_desde, fecha_hasta)
            lista = listar_comprobantes_arca(
                limite=80,
                fecha_desde=ini,
                fecha_hasta=fin,
                busqueda=filtro_txt,
            )
            st.session_state.hist_arca_resultados = lista
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

    if st.button("Cargar comprobantes", key="hist_arca_reimprimir", use_container_width=True):
        html_t, pdf_a, datos = regenerar_comprobantes_arca(comp)
        nro = _formato_nro_comprobante(datos)
        st.session_state.hist_arca_preview = {
            "respuesta": datos,
            "html_ticket": html_t,
            "pdf_a4": pdf_a,
            "total": comp.get("total"),
            "comprobante_id": sel_id,
            "nro": nro,
        }
        st.rerun()

    preview = st.session_state.get("hist_arca_preview")
    if preview and preview.get("comprobante_id") == sel_id:
        st.caption(f"Comprobante {preview.get('nro', '—')}")
        _render_acciones_comprobante(
            preview.get("nro", "—"),
            preview.get("html_ticket"),
            preview.get("pdf_a4"),
            f"hist_{sel_id[:8]}",
        )
        _render_vista_previa_ticket_html(preview.get("html_ticket"), f"hist_{sel_id[:8]}")


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

    _, errores_sync = sincronizar_grilla_carrito_firebase(vendedor)
    if errores_sync:
        return False, "\n".join(errores_sync), None

    carrito, _, total_final = obtener_carrito_listo_facturacion(vendedor, desc_porc)

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
    html_ticket = crear_ticket_html(
        datos_resp, datos_cliente, items_fc, cfg, forma_pago=forma_pago
    )
    pdf_a4 = crear_a4(datos_resp, datos_cliente, items_fc, cfg)

    exito_stock, msj_stock = confirmar_venta(str(vendedor))
    if not exito_stock:
        return False, (
            f"CAE obtenido pero falló el descuento de stock: {msj_stock}. "
            "Revisá inventario manualmente."
        ), None

    from modulos.mostrador_voz_flujo import invalidar_cache_inventario_mostrador
    invalidar_cache_inventario_mostrador()

    comp_id = guardar_comprobante_arca(
        vendedor, datos_cliente, datos_resp, items_fc, forma_pago, total_final
    )
    nro = _formato_nro_comprobante(datos_resp)
    try:
        from modulos.puntos_vendedor import registrar_venta_puntos, asegurar_vendedor
        asegurar_vendedor(str(vendedor), nombre=str(vendedor))
        ok_pt, msg_pt, pts = registrar_venta_puntos(str(vendedor), float(total_final), comp_id)
    except Exception:
        ok_pt, msg_pt, pts = True, "", 0

    try:
        from modulos.auditoria_app import registrar_auditoria
        registrar_auditoria(
            "mostrador",
            "facturar_arca",
            f"Factura {nro} · ${total_final:,.2f} · {datos_cliente.get('nombre', '')}",
            detalle={
                "comprobante_id": comp_id,
                "nro": nro,
                "cae": datos_resp.get("cae"),
                "total": total_final,
                "cliente": datos_cliente.get("nombre"),
                "forma_pago": forma_pago,
                "vendedor": str(vendedor),
                "items": len(items_fc),
                "puntos_msg": msg_pt if ok_pt else None,
            },
            exito=True,
            ref_id=comp_id,
            vendedor_id=str(vendedor),
        )
    except Exception:
        pass

    datos_panel = {
        "respuesta": datos_resp,
        "html_ticket": html_ticket,
        "pdf_a4": pdf_a4,
        "total": total_final,
        "comprobante_id": comp_id,
        "nro": nro,
    }
    _cerrar_presupuesto_cargado("facturado")
    limpiar_venta_mostrador(vendedor, reset_cliente=True)
    st.session_state.factura_arca_reciente = datos_panel
    st.session_state.mostrador_voz_solo_ticket = bool(solo_ticket)
    return True, f"Factura {nro} emitida · CAE otorgado · Total ${total_final:,.2f}", datos_panel


def _facturar_desde_carrito(vendedor, carrito, total_final, desc_porc, forma_pago, solo_ticket=False):
    with st.spinner("Solicitando CAE a AFIP…"):
        ok, msj, datos = ejecutar_emitir_factura_arca(
            vendedor, carrito, total_final, desc_porc, forma_pago, solo_ticket=solo_ticket
        )
    if ok and datos:
        _auto_imprimir_ticket(datos.get("html_ticket"))
    return ok, msj


def _audit_mostrador(accion, resumen, detalle=None, exito=True, ref_id=None, error_msg=None):
    try:
        from modulos.auditoria_app import registrar_auditoria
        registrar_auditoria(
            "mostrador", accion, resumen, detalle=detalle,
            exito=exito, ref_id=ref_id, error_msg=error_msg,
        )
    except Exception:
        pass


def _ejecutar_accion_pendiente(vendedor, pendiente, carrito, total_final, desc_porc):
    tipo = pendiente.get("tipo")
    forma_pago = pendiente.get("forma_pago") or _forma_pago_actual(vendedor)

    if tipo == "confirmar_venta":
        exito, msj = confirmar_venta(str(vendedor))
        if exito:
            _cerrar_presupuesto_cargado("vendido")
            limpiar_venta_mostrador(vendedor, reset_cliente=True)
        _audit_mostrador(
            "confirmar_venta",
            f"Venta sin factura · ${total_final:,.2f}",
            detalle={"vendedor": str(vendedor), "total": total_final},
            exito=exito,
            error_msg=None if exito else msj,
        )
        return exito, msj

    if tipo == "facturar":
        with st.spinner("Solicitando CAE a ARCA/AFIP…"):
            ok, msj, datos = ejecutar_emitir_factura_arca(
                vendedor, carrito, total_final, desc_porc, forma_pago
            )
        if ok and datos:
            _auto_imprimir_ticket(datos.get("html_ticket"))
        return ok, msj

    if tipo == "imprimir_ticket":
        with st.spinner("Solicitando CAE e imprimiendo ticket…"):
            ok, msj, datos = ejecutar_emitir_factura_arca(
                vendedor, carrito, total_final, desc_porc, forma_pago, solo_ticket=True
            )
        if ok and datos:
            _auto_imprimir_ticket(datos.get("html_ticket"))
        return ok, msj

    if tipo == "guardar_presupuesto":
        ok, msj, nuevo_id = guardar_presupuesto(
            str(vendedor), st.session_state.cliente_activo, pendiente.get("nota", "")
        )
        if ok:
            st.session_state.presupuesto_cargado_id = nuevo_id
        _audit_mostrador(
            "guardar_presupuesto",
            f"Presupuesto guardado · ${total_final:,.2f}",
            detalle={"presupuesto_id": nuevo_id, "vendedor": str(vendedor), "total": total_final},
            exito=ok,
            ref_id=nuevo_id,
            error_msg=None if ok else msj,
        )
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
        f"Revisá la grilla (total ${total_final:,.2f}) y usá los botones de abajo."
    )


def _aplicar_cambios_carrito_filas(vendedor, carrito, filas_editadas):
    """Sincroniza cantidad/precio editados en filas del carrito con Firebase."""
    orig_map = {str(i.get("id", "")): i for i in carrito if isinstance(i, dict)}
    errores = []
    cambios = 0

    for iid, cant_n, precio_n in filas_editadas:
        if not iid or iid not in orig_map:
            continue
        orig = orig_map[iid]
        cant_n = max(1, int(cant_n))
        precio_n = max(0.0, float(precio_n))
        cant_o = int(orig.get("cantidad", 1))
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
    """Grilla editable con tachito por fila para quitar ítems."""
    st.markdown("**Ítems**")
    st.caption("Editá cantidad o precio; 🗑️ para quitar. **Aplicar cambios** si modificás la grilla.")

    items = [i for i in carrito if isinstance(i, dict)]
    if not items:
        return

    rev = int(st.session_state.get(f"mostrador_cart_rev_{vendedor}", 0))
    vid = str(vendedor)

    hc, hd, hq, hp, hx = st.columns([2.1, 3.6, 0.75, 1.05, 0.45])
    hc.markdown("**Código**")
    hd.markdown("**Descripción**")
    hq.markdown("**Cant.**")
    hp.markdown("**Precio unit.**")
    hx.markdown("**🗑️**")

    filas_editadas = []
    for idx, item in enumerate(items):
        iid = str(item.get("id", ""))
        if not iid:
            continue
        safe = re.sub(r"[^\w]", "_", iid)[:36]

        c1, c2, c3, c4, c5 = st.columns([2.1, 3.6, 0.75, 1.05, 0.45])
        cod_txt = iid if len(iid) <= 34 else f"{iid[:31]}…"
        c1.markdown(f"<span style='font-size:0.82rem'>{cod_txt}</span>", unsafe_allow_html=True)
        desc = str(item.get("descripcion", ""))
        if len(desc) > 52:
            desc = desc[:49] + "…"
        c2.markdown(f"<span style='font-size:0.82rem;color:#475569'>{desc}</span>", unsafe_allow_html=True)
        cant = c3.number_input(
            "Cant.",
            min_value=0,
            max_value=9999,
            value=int(item.get("cantidad", 1)),
            step=1,
            key=f"cart_q_{vid}_{rev}_{idx}_{safe}",
            label_visibility="collapsed",
        )
        precio = c4.number_input(
            "Precio",
            min_value=0.0,
            value=float(item.get("precio_unitario", 0)),
            step=1.0,
            format="%.0f",
            key=f"cart_p_{vid}_{rev}_{idx}_{safe}",
            label_visibility="collapsed",
        )
        if c5.button(
            "🗑️",
            key=f"cart_x_{vid}_{rev}_{idx}_{safe}",
            help=f"Quitar {iid}",
            use_container_width=True,
        ):
            ok, msj = eliminar_item_carrito(vid, iid)
            if ok:
                _al_modificar_carrito_mostrador(vendedor)
                st.rerun()
            else:
                st.error(msj)
        filas_editadas.append((iid, cant, precio))

    if st.button(
        "✅ Aplicar cambios",
        type="primary",
        use_container_width=True,
        key=f"cart_apply_{vendedor}",
    ):
        cambios, errores = _aplicar_cambios_carrito_filas(vendedor, carrito, filas_editadas)
        if errores:
            st.error("\n".join(errores))
        elif cambios:
            _invalidar_pdf_presupuesto_mostrador()
            st.rerun()
        else:
            st.toast("Sin cambios.")


def render_panel_cobro_mostrador(
    vendedor, carrito, total_bruto, total_final, desc_porc
):
    """Totales, pago y botones de facturación (columna lateral)."""
    listo_ticket = bool(st.session_state.get("mostrador_listo_para_ticket"))
    intent = st.session_state.get("mostrador_intent_sugerido", "factura_b")
    listo_para_cerrar = listo_ticket or (
        bool(carrito) and intent in ("factura_b", "factura_a", "presupuesto")
    )

    with st.container(border=True):
        st.markdown('<div class="mostrador-cobro-panel">', unsafe_allow_html=True)

        if desc_porc > 0:
            st.metric("Subtotal", f"${total_bruto:,.2f}")
            st.caption(f"Dto ${total_bruto * desc_porc / 100:,.2f}")
        st.metric("Total", f"${total_final:,.2f}")

        if listo_para_cerrar:
            intent = st.session_state.get("mostrador_intent_sugerido", "factura_b")
            if intent == "presupuesto":
                _render_cierre_presupuesto_mostrador(vendedor, carrito, desc_porc)
            else:
                forma_pago = st.selectbox(
                    "Forma de pago",
                    list(FORMAS_PAGO),
                    index=list(FORMAS_PAGO).index(_forma_pago_actual(vendedor)),
                    key=f"pago_arca_{vendedor}",
                )
                _set_forma_pago(vendedor, forma_pago)
                if st.button(
                    "🧾 FACTURAR E IMPRIMIR",
                    type="primary",
                    use_container_width=True,
                    key=f"btn_verif_arca_{vendedor}",
                ):
                    ok, msj = _facturar_desde_carrito(
                        vendedor, carrito, total_final, desc_porc, forma_pago, solo_ticket=False
                    )
                    if ok:
                        st.rerun()
                    else:
                        st.error(msj)
        else:
            st.caption("Decí **listo** en la orden rápida para cerrar.")

        with st.expander("Más acciones", expanded=False):
            if intent != "presupuesto" or not listo_para_cerrar:
                nota_pres = st.text_input("Nota presupuesto", key=f"nota_pres_{vendedor}")
                if st.button("💾 Guardar presupuesto", key=f"guardar_pres_{vendedor}", use_container_width=True):
                    ok, msj, nuevo_id = guardar_presupuesto(
                        str(vendedor), st.session_state.cliente_activo, nota_pres
                    )
                    if ok:
                        st.session_state.presupuesto_cargado_id = nuevo_id
                        st.success(msj)
                        st.rerun()
                    else:
                        st.error(msj)
            if st.button("✅ Venta sin factura", key=f"venta_sin_fc_{vendedor}", use_container_width=True):
                _, err_sync = sincronizar_grilla_carrito_firebase(vendedor)
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
    if carrito:
        carrito_ui = carrito_efectivo_mostrador(vendedor, carrito)
        desc_porc = float(st.session_state.cliente_activo.get("descuento", 0))
        total_bruto, total_final = calcular_totales_carrito(carrito_ui, desc_porc)
        render_acciones_carrito(
            vendedor, carrito_ui, total_bruto, total_final, desc_porc
        )
    else:
        if st.session_state.get("mostrador_listo_para_ticket"):
            reset_estado_orden_mostrador(vendedor, reset_cliente=False)
        st.caption("Carrito vacío.")


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
