"""Flujo rápido de facturación por voz en mostrador (un solo paso)."""
import re
import time
from typing import Callable, Optional

import streamlit as st

from modulos.db_firebase import (
    obtener_carrito,
    vaciar_carrito,
    cliente_consumidor_final,
    cliente_db_a_activo,
    obtener_clientes,
    formatear_id_variante,
)
from modulos.ia_asistente import normalizar_texto_basico


def descartar_panels_operacion_anterior():
    """Quita carteles de factura/presupuesto de la operación anterior."""
    st.session_state.pop("factura_arca_reciente", None)
    st.session_state.pop("mostrador_voz_solo_ticket", None)
    st.session_state.pop("hist_arca_preview", None)


def preprocesar_texto_mostrador(texto):
    """Alias del preprocesador compartido (código + cantidad separados)."""
    from modulos.ia_asistente import preprocesar_texto_usuario
    return preprocesar_texto_usuario(texto)


def _limpiar_termino_item(termino):
    t = str(termino or "").strip().upper().replace("/", "-")
    t = re.sub(r"\s+", "-", t)
    return t.strip(",.;:")


def _id_carrito_desde_item(item):
    if not isinstance(item, dict):
        return None
    id_m = item.get("id_maestro") or item.get("codigo")
    marca = item.get("marca")
    if id_m and marca:
        return formatear_id_variante(id_m, marca)
    return item.get("id")


def _normalizar_codigo_con_inventario(termino, inventario):
    """Ajusta códigos dictados (1273 BH → 1273-BH) según inventario."""
    directo = _limpiar_termino_item(termino)
    if not directo:
        return directo
    coincidencias = _buscar_variantes_por_codigo(inventario, directo)
    if coincidencias:
        cod = _limpiar_termino_item(coincidencias[0].get("codigo", ""))
        return cod or directo
    compacto = directo.replace("-", "")
    for p in inventario or []:
        if not isinstance(p, dict):
            continue
        cod = _limpiar_termino_item(p.get("codigo", ""))
        if cod.replace("-", "") == compacto:
            return cod
    return directo


_STOPWORDS_ITEM = frozenset({
    "LISTO", "FIN", "FACTURA", "PRESUPUESTO", "CODIGO", "UNIDAD",
    "UNIDADES", "CLIENTE", "CARGAME", "HACEME", "CARGA", "CARGAR",
    "HACER", "ARME", "ARMAR", "PARA", "EL", "LA", "UN", "UNA", "DE",
})

# Palabras que suelen iniciar la descripción del repuesto (corte de nombre de cliente).
_INICIO_DESCRIPCION_VOZ = (
    "buje", "bujes", "filtro", "filtros", "pastilla", "pastillas", "aceite",
    "amortiguador", "amortiguadores", "disco", "discos", "bomba", "kit",
    "rotula", "rótula", "fuelle", "tensor", "correa", "cadena", "resorte",
    "espiral", "bujia", "bujía", "bobina", "ruleman", "rulemán", "cruceta",
    "homocinetica", "homocinética", "semieje", "palier", "terminal", "barra",
    "brazo", "optica", "óptica", "lampara", "lámpara", "faro", "escobilla",
    "liquido", "líquido", "junta", "juntas", "reten", "retén", "polea",
    "embrague", "crapodina", "crápodina", "cazoleta", "cazoletas", "soporte", "silent",
    "bieleta", "bieletas", "bielete", "bieletes", "biela", "bielas",
)


def _strip_termino_cant_de_resto(resto: str, termino: str, cantidad: int) -> str:
    """Quita del texto un ítem ya capturado (código o descripción)."""
    term = str(termino or "").lower().strip()
    if not term:
        return resto
    cant = int(cantidad)
    term_sp = r"\s+".join(re.escape(p) for p in term.split())
    patrones = (
        rf"(?:codigo|código)\s+{term_sp}\s+{cant}\s*(?:unidades?|u\.?|uds?|unidad)?\b",
        rf"(?:un|una)\s+{term_sp}\s+{cant}\s*(?:unidades?|u\.?|uds?|unidad)?\b",
        rf"\b{term_sp}\s+{cant}\s*(?:unidades?|u\.?|uds?|unidad)?\b",
        rf"\b{term_sp}\s+{cant}\b",
    )
    for patron in patrones:
        resto = re.sub(patron, " ", resto, flags=re.I)
    return re.sub(r"\s+", " ", resto).strip()


def _patron_fin_cliente_voz() -> str:
    prod = "|".join(re.escape(p) for p in _INICIO_DESCRIPCION_VOZ)
    return (
        r"(?=\s+codigo|\s+listo|\s+factura|\s+presupuesto|\s+(?:un|una)\s|"
        r"\b[\dA-Za-z]*\d[\dA-Za-z\-]*\s+\d{1,4}\s+unidades?\b|"
        rf"\s+(?:{prod})\b|$)"
    )


def _limpiar_texto_para_items_descripcion(texto: str) -> str:
    """Quita cliente, factura y prefijos de armado para buscar por descripción."""
    cliente_info = extraer_cliente_orden_voz(texto)
    t = normalizar_texto_basico(texto).lower()
    t = re.sub(r"\b(listo|termine|terminé|fin)\b", " ", t)
    t = re.sub(
        r"\b(carg\w*|hac\w*|arm\w*|rgame|cargame|haceme|armeme|armame)\b",
        " ",
        t,
    )
    t = re.sub(r"\bfactura\s+[ab]\b", " ", t)
    t = re.sub(r"\bpresupuesto\b", " ", t)
    t = re.sub(r"\bcliente\b", " ", t)
    t = re.sub(r"\b(codigo|código|descripcion|descripción|desc)\b", " ", t)
    if cliente_info.get("nombre_cliente"):
        nom = re.escape(str(cliente_info["nombre_cliente"]).lower())
        t = re.sub(rf"\bpara\s+(?:el\s+)?(?:cliente\s+)?{nom}\b", " ", t, flags=re.I)
        t = re.sub(rf"\b{nom}\b", " ", t, flags=re.I)
    elif cliente_info.get("consumidor_final"):
        t = re.sub(r"\b(consumidor\s+final|particular)\b", " ", t)
    t = re.sub(r"\bun\s+", " ", t)
    t = re.sub(r"\bde\s+una?\s+", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t


def _termino_descripcion_valido(termino: str) -> bool:
    term = _normalizar_termino_descripcion(termino)
    if not term or len(term) < 2:
        return False
    palabras = [p for p in term.split() if p not in _STOPWORDS_ITEM]
    if not palabras:
        return False
    if all(p.isdigit() for p in palabras):
        return False
    return True


def _normalizar_termino_descripcion(termino: str) -> str:
    t = normalizar_texto_basico(str(termino or "")).upper()
    palabras = [
        p for p in t.split()
        if p not in _STOPWORDS_ITEM and p not in ("UN", "UNA", "EL", "LA", "DE", "DEL")
    ]
    while palabras and palabras[0] in ("Y", "E", "TAMBIEN", "TAMBIÉN", "MAS", "MÁS"):
        palabras.pop(0)
    return " ".join(palabras).strip()


def extraer_items_orden_voz(texto):
    """Extrae uno o varios códigos/descripciones + cantidad desde la orden hablada/escrita."""
    if not texto:
        return []

    def _extraer_de_fragmento(fragmento, acumulado, vistos):
        t = normalizar_texto_basico(fragmento).lower()
        t = re.sub(r"\b(listo|termine|terminé|fin)\b", " ", t)
        t = re.sub(r"\s+", " ", t).strip()
        if not t:
            return
        cod_pat = r"([\dA-Za-z]+(?:-[\dA-Za-z]+)*)"
        from modulos.voz_repuestos import extraer_vehiculos_de_texto, patron_repuesto_para_vehiculo
        from modulos.util_busqueda import normalizar_para_busqueda as _norm_busq

        vehs_frag = extraer_vehiculos_de_texto(fragmento)
        veh_set = {_norm_busq(v) for v in vehs_frag}

        def agregar(termino, cantidad, es_descripcion=False):
            if es_descripcion:
                term = _normalizar_termino_descripcion(termino)
            else:
                term = _limpiar_termino_item(termino)
            try:
                cant = max(1, int(cantidad))
            except (TypeError, ValueError):
                return
            if not es_descripcion and _norm_busq(term) in veh_set and re.match(r"^\d{3,4}$", _norm_busq(term)):
                return
            if es_descripcion:
                if not _termino_descripcion_valido(term):
                    return
            elif not term or not re.search(r"[A-Z0-9]", term):
                return
            if not es_descripcion and term in _STOPWORDS_ITEM:
                return
            if not es_descripcion and not re.search(r"\d", term) and len(term) < 4:
                return
            clave = (term, cant)
            if clave in vistos:
                return
            vistos.add(clave)
            acumulado.append({"termino": term, "cantidad": cant})

        patrones_codigo = [
            (rf"(?:codigo|código)\s+{cod_pat}\s+(\d{{1,4}})\s*(?:unidades?|u\.?|uds?|unidad)?\b", False),
            (rf"(?:agreg\w*|sum\w*|pon\w*)\s+(?:codigo|código\s+)?{cod_pat}\s+(\d{{1,4}})\s*(?:unidades?|u\.?|unidad)?\b", False),
            (rf"(?:codigo|código)\s+{cod_pat}\s*(?:por|x|\*|con)\s*(\d{{1,4}})\b", False),
            (rf"\b([\dA-Za-z]*\d[\dA-Za-z\-]*)\s+(\d{{1,4}})\s*(?:unidades?|u\.?|uds?|unidad)\b", False),
            (rf"\b([\dA-Za-z]*\d[\dA-Za-z\-]*)\s+(\d{{1,4}})\b", False),
            (rf"(?:descripcion|descripción|desc)\s+(.+?)\s+(\d{{1,4}})\s*(?:unidades?|u\.?|uds?|unidad)?\b", True),
            (rf"(?:descripcion|descripción|desc)\s+(.+?)\s*(?:por|x|\*|con)\s*(\d{{1,4}})\b", True),
        ]
        n_antes = len(acumulado)

        resto = _limpiar_texto_para_items_descripcion(fragmento)
        pat_repuesto_veh = patron_repuesto_para_vehiculo()
        patrones_desc = [
            pat_repuesto_veh,
            r"(?:un|una)\s+(.+?)\s+(\d{1,4})\s*(?:unidades?|u\.?|uds?|unidad)\b",
            r"(?:^|\s)([a-z0-9][a-z0-9\s\-]{2,}?)\s+(\d{1,4})\s*(?:unidades?|u\.?|uds?|unidad)\b",
            r"(?:^|\s)([a-z0-9][a-z0-9\s\-]{2,}?)\s+(\d{1,4})\b",
        ]
        while resto:
            encontrado = False
            for patron in patrones_desc:
                if hasattr(patron, "search"):
                    m = patron.search(resto)
                else:
                    m = re.search(patron, resto)
                if not m:
                    continue
                n_desc = len(acumulado)
                if hasattr(patron, "search") and m.lastindex == 3:
                    agregar(m.group(1), m.group(3), es_descripcion=True)
                    if len(acumulado) > n_desc:
                        acumulado[-1]["vehiculo"] = m.group(2)
                else:
                    agregar(m.group(1), m.group(2), es_descripcion=True)
                if len(acumulado) <= n_desc:
                    continue
                item = acumulado[-1]
                resto_nuevo = _strip_termino_cant_de_resto(
                    resto,
                    str(item.get("termino", "")),
                    int(item.get("cantidad", 1)),
                )
                if resto_nuevo == resto:
                    resto = (resto[: m.start()] + " " + resto[m.end() :]).strip()
                else:
                    resto = resto_nuevo
                encontrado = True
                break
            if not encontrado:
                break

        for patron, es_desc in patrones_codigo:
            for m in re.finditer(patron, t):
                agregar(m.group(1), m.group(2), es_descripcion=es_desc)

        if len(acumulado) == n_antes:
            for m in re.finditer(rf"(?:codigo|código)\s+{cod_pat}\b", t):
                agregar(m.group(1), 1, es_descripcion=False)

        resto = _limpiar_texto_para_items_descripcion(fragmento)
        for item in acumulado[n_antes:]:
            resto = _strip_termino_cant_de_resto(
                resto,
                str(item.get("termino", "")),
                int(item.get("cantidad", 1)),
            )

        patrones_desc_extra = [
            r"(?:un|una)\s+(.+?)\s+(\d{1,4})\s*(?:unidades?|u\.?|uds?|unidad)\b",
            r"(?:^|\s)([a-z0-9][a-z0-9\s\-]{2,}?)\s+(\d{1,4})\s*(?:unidades?|u\.?|uds?|unidad)\b",
            r"(?:^|\s)([a-z0-9][a-z0-9\s\-]{2,}?)\s+(\d{1,4})\b",
        ]
        for patron in patrones_desc_extra:
            for m in re.finditer(patron, resto):
                agregar(m.group(1), m.group(2), es_descripcion=True)

    items = []
    vistos = set()
    raw = str(texto).strip()
    _extraer_de_fragmento(raw, items, vistos)

    if len(items) <= 1:
        t_full = normalizar_texto_basico(raw).lower()
        prod_pat = "|".join(re.escape(p) for p in _INICIO_DESCRIPCION_VOZ)
        separadores = (
            r"\s+y\s+|\s*,\s*|\s+también\s+|\s+tambien\s+|\s+más\s+|\s+mas\s+"
            r"|\s+después\s+|\s+despues\s+"
            rf"|\bunidad(?:es)?\s+(?=(?:{prod_pat})\b)"
            r"|\b(?:codigo|código)\s+"
        )
        if re.search(separadores, t_full):
            segmentos = re.split(separadores, t_full)
            if len(segmentos) > 1:
                items.clear()
                vistos.clear()
                for seg in segmentos:
                    seg = seg.strip()
                    if seg:
                        _extraer_de_fragmento(seg, items, vistos)
    elif len(re.findall(r"\b\d{1,4}\s+unidades?\b", normalizar_texto_basico(raw).lower())) > len(items):
        t_full = normalizar_texto_basico(raw).lower()
        prod_pat = "|".join(re.escape(p) for p in _INICIO_DESCRIPCION_VOZ)
        separadores = (
            r"\s+y\s+|\s*,\s*|\s+también\s+|\s+tambien\s+"
            rf"|\bunidad(?:es)?\s+(?=(?:{prod_pat})\b)"
            r"|\b(?:codigo|código)\s+"
        )
        if re.search(separadores, t_full):
            items.clear()
            vistos.clear()
            for seg in re.split(separadores, t_full):
                seg = seg.strip()
                if seg:
                    _extraer_de_fragmento(seg, items, vistos)

    from modulos.voz_repuestos import enriquecer_items_con_vehiculo

    return enriquecer_items_con_vehiculo(items, raw)


def _limpiar_nombre_cliente_voz(nombre: str) -> str:
    nombre = re.sub(r"^(nombre\s+de|el|la)\s+", "", str(nombre or "").strip(), flags=re.I)
    nombre = re.sub(r"\s+(el|la|del|de|una|un)$", "", nombre.strip(" ,."), flags=re.I)
    nombre = re.sub(r"^(el|la)\s+", "", nombre, flags=re.I)
    nombre = re.sub(r"\s+\d{1,2}\s+unidades?\s*$", "", nombre, flags=re.I)
    nombre = re.sub(r"\s+", " ", nombre).strip()
    if len(nombre) < 2:
        return ""
    if nombre.lower() in ("factura", "presupuesto", "consumidor final", "particular", "el", "la"):
        return ""
    primera = nombre.split()[0].lower()
    if primera in _INICIO_DESCRIPCION_VOZ:
        return ""
    return nombre.upper()


def _palabra_parece_nombre_cliente(palabra: str) -> bool:
    from modulos.voz_repuestos import es_referencia_vehiculo

    p = str(palabra or "").strip().lower()
    if len(p) < 3 or len(p) > 20:
        return False
    if p in (
        "factura", "presupuesto", "cliente", "codigo", "código", "listo",
        "consumidor", "particular", "contado", "transferencia",
        "para", "por", "de", "del", "una", "uno", "un", "el", "la", "los", "las",
        "hacer", "haceme", "haceme", "cargame", "armame",
    ):
        return False
    if es_referencia_vehiculo(p):
        return False
    if p in _INICIO_DESCRIPCION_VOZ:
        return False
    return True


def extraer_cliente_orden_voz(texto):
    """Extrae nombre de cliente o consumidor final desde la orden."""
    if not texto:
        return {}
    t = normalizar_texto_basico(texto).lower()
    if re.search(r"consumidor\s+final|particular", t):
        return {"consumidor_final": True}

    fin = _patron_fin_cliente_voz()
    m_directo = re.search(
        r"\bpresupuesto\s+para\s+([a-záéíóúñ]{3,20})\s+(?:de\s+)?(?:una?\s+)?"
        r"(?:codigo|código|\d|[a-záéíóúñ]{4,})",
        t,
    )
    if m_directo:
        nombre = _limpiar_nombre_cliente_voz(m_directo.group(1))
        if nombre and _palabra_parece_nombre_cliente(nombre.split()[0]):
            return {"nombre_cliente": nombre}

    m_directo2 = re.search(
        r"\bpresupuesto\s+(?!para\b)([a-záéíóúñ]{3,20})\s+(?:codigo|código|\d|[a-záéíóúñ]{4,})",
        t,
    )
    if m_directo2:
        nombre = _limpiar_nombre_cliente_voz(m_directo2.group(1))
        if nombre and _palabra_parece_nombre_cliente(nombre.split()[0]):
            return {"nombre_cliente": nombre}

    m_final = re.search(r"\bpresupuesto\s+(?:para\s+)?([a-záéíóúñ]{3,20})\s*$", t)
    if m_final:
        nombre = _limpiar_nombre_cliente_voz(m_final.group(1))
        if nombre and _palabra_parece_nombre_cliente(nombre.split()[0]):
            return {"nombre_cliente": nombre}

    m_factura = re.search(
        r"\bfactura\s+[ab]\s+(?:para\s+)?([a-záéíóúñ]{3,20})\s*$", t
    )
    if m_factura:
        nombre = _limpiar_nombre_cliente_voz(m_factura.group(1))
        if nombre:
            return {"nombre_cliente": nombre}

    m_cli = re.search(r"\bcliente\s+([a-záéíóúñ]{3,20})\s*$", t)
    if m_cli and _palabra_parece_nombre_cliente(m_cli.group(1)):
        nombre = _limpiar_nombre_cliente_voz(m_cli.group(1))
        if nombre:
            return {"nombre_cliente": nombre}

    m_para_nombre = re.search(
        r"\bpara\s+(?!el\s+\d{3,4}\b)(?!el\s+cliente\b)"
        r"([a-záéíóúñ]{3,20})\s+(?:de\s+)?(?:una?\s+)?",
        t,
    )
    if m_para_nombre and _palabra_parece_nombre_cliente(m_para_nombre.group(1)):
        nombre = _limpiar_nombre_cliente_voz(m_para_nombre.group(1))
        if nombre:
            return {"nombre_cliente": nombre}

    patrones = (
        rf"(?:hacer\s+)?factura\s+[ab]\s+(?:al\s+nombre\s+de|para\s+el\s+cliente|para\s+cliente|para\s+el|a\s+el|a)\s+(.+?){fin}",
        rf"factura\s+[ab]\s+(?:al\s+nombre\s+de|para\s+el\s+cliente|para\s+cliente|para\s+el|a\s+el|a)\s+(.+?){fin}",
        rf"presupuesto\s+(?:al\s+nombre\s+de|para\s+el\s+cliente|para\s+cliente|para\s+el|a\s+el|a)\s+(.+?){fin}",
        rf"para\s+el\s+cliente\s+(.+?){fin}",
        rf"cliente\s+(.+?){fin}",
    )
    for patron in patrones:
        m = re.search(patron, t)
        if not m:
            continue
        nombre = _limpiar_nombre_cliente_voz(m.group(1))
        if nombre:
            primera = nombre.split()[0]
            if _palabra_parece_nombre_cliente(primera):
                return {"nombre_cliente": primera}
    return {}


def marcar_verificacion_mostrador(intent_sugerido=None):
    st.session_state.mostrador_listo_para_ticket = True
    if intent_sugerido:
        st.session_state.mostrador_intent_sugerido = intent_sugerido


def _guardar_intent_voz_pendiente(intent):
    if intent:
        st.session_state.mostrador_voz_intent_pendiente = intent
        st.session_state.mostrador_intent_sugerido = intent


def _finalizar_cola_voz_mostrador(vendedor):
    """Cierra la cola de ambiguos y aplica presupuesto/factura pendiente."""
    intent = st.session_state.pop("mostrador_voz_intent_pendiente", None)
    st.session_state.pop("mostrador_voz_cola_ambiguos", None)
    st.session_state.pop("mostrador_voz_cant_coincidencia", None)
    if intent and (obtener_carrito(str(vendedor)) or []):
        marcar_verificacion_mostrador(intent)
    return intent


def inventario_cache_mostrador(obtener_inventario_fn, ttl_seg=120):
    ahora = time.time()
    if (
        "_inv_cache_mostrador" in st.session_state
        and ahora - float(st.session_state.get("_inv_cache_mostrador_ts", 0)) < ttl_seg
    ):
        return st.session_state["_inv_cache_mostrador"]
    inv = obtener_inventario_fn() or []
    st.session_state["_inv_cache_mostrador"] = inv
    st.session_state["_inv_cache_mostrador_ts"] = ahora
    return inv


def invalidar_cache_inventario_mostrador():
    st.session_state.pop("_inv_cache_mostrador", None)
    st.session_state.pop("_inv_cache_mostrador_ts", None)


def _parece_codigo(termino: str) -> bool:
    from modulos.util_busqueda import parece_codigo_producto

    return parece_codigo_producto(termino)


def _buscar_variantes_por_codigo(inventario, termino):
    t = _limpiar_termino_item(termino)
    if not t:
        return []
    exactos = []
    for p in inventario:
        if not isinstance(p, dict):
            continue
        cod = _limpiar_termino_item(p.get("codigo", ""))
        pid = _limpiar_termino_item(p.get("id", ""))
        id_m = _limpiar_termino_item(p.get("id_maestro", ""))
        if t in (cod, pid, id_m):
            exactos.append(p)
        elif pid.startswith(f"{t}_") or id_m.startswith(f"{t}_"):
            exactos.append(p)
    return exactos


def agregar_termino_voz(
    vendedor,
    termino,
    cantidad,
    inventario,
    buscar_en_inventario,
    agregar_al_carrito,
    vehiculo=None,
):
    cant = max(1, int(cantidad or 1))
    termino = str(termino or "").strip()
    if not termino:
        return False, "Sin término de búsqueda.", None

    from modulos.util_busqueda import _limpiar_prefijo_busqueda, buscar_en_inventario_con_vehiculo
    from modulos.voz_repuestos import corregir_termino_repuesto

    termino = _limpiar_prefijo_busqueda(termino)
    termino = corregir_termino_repuesto(termino)
    if not termino:
        return False, "Sin término de búsqueda.", None

    veh = str(vehiculo).strip() if vehiculo else None
    id_limpio = _normalizar_codigo_con_inventario(termino, inventario)

    if _parece_codigo(id_limpio):
        ok, msj = agregar_al_carrito(str(vendedor), id_limpio, cant)
        if ok:
            return True, msj, None
        coincidencias = _buscar_variantes_por_codigo(inventario, id_limpio)
        if len(coincidencias) == 1:
            id_cart = _id_carrito_desde_item(coincidencias[0])
            ok2, msj2 = agregar_al_carrito(str(vendedor), id_cart, cant)
            return ok2, msj2, None
        if len(coincidencias) > 1:
            return (
                False,
                f"Hay {len(coincidencias)} variantes para '{id_limpio}'. Elegí en la lista.",
                coincidencias[:10],
            )

    coincidencias_cod = _buscar_variantes_por_codigo(inventario, id_limpio)
    if len(coincidencias_cod) == 1:
        id_cart = _id_carrito_desde_item(coincidencias_cod[0])
        ok, msj = agregar_al_carrito(str(vendedor), id_cart, cant)
        return ok, msj, None
    if len(coincidencias_cod) > 1:
        return (
            False,
            f"Hay {len(coincidencias_cod)} variantes para '{id_limpio}'. Decí el código exacto.",
            coincidencias_cod[:10],
        )

    encontrados = buscar_en_inventario_con_vehiculo(inventario, termino, veh)
    if len(encontrados) == 1:
        ok, msj = agregar_al_carrito(str(vendedor), encontrados[0]["id"], cant)
        return ok, msj, None
    if len(encontrados) > 1:
        hint = f" ({veh})" if veh else ""
        return False, f"Varias similitudes para '{termino}'{hint}. Elegí en la lista.", encontrados[:10]
    return False, f"No encontré '{termino}'" + (f" para {veh}" if veh else "") + ". Probá con código.", None


def limpiar_cola_voz_mostrador():
    """Cancela cola de ambiguos sin marcar listo para cerrar."""
    st.session_state.pop("mostrador_voz_cola_ambiguos", None)
    st.session_state.pop("mostrador_voz_cant_coincidencia", None)
    st.session_state.pop("mostrador_voz_intent_pendiente", None)


def continuar_cola_voz_mostrador(
    vendedor,
    inventario,
    buscar_en_inventario,
    agregar_al_carrito,
):
    """
    Tras elegir una coincidencia, agrega automáticamente el resto de ítems pendientes.
    Devuelve (terminado, coincidencias_siguientes, mensaje).
    """
    cola = list(st.session_state.get("mostrador_voz_cola_ambiguos") or [])
    if not cola:
        _finalizar_cola_voz_mostrador(vendedor)
        return True, None, None

    agregados = []
    errores = []
    while cola:
        item = cola[0]
        termino = item.get("termino", "")
        cant = int(item.get("cantidad", 1))
        veh = item.get("vehiculo")
        ok, msj, ambiguos = agregar_termino_voz(
            vendedor, termino, cant, inventario, buscar_en_inventario, agregar_al_carrito,
            vehiculo=veh,
        )
        if ok:
            agregados.append(msj)
            cola.pop(0)
            continue
        if ambiguos:
            st.session_state.mostrador_voz_cola_ambiguos = cola
            st.session_state.mostrador_voz_cant_coincidencia = cant
            msg = str(item.get("msj") or msj)
            if agregados:
                msg = f"Agregados {len(agregados)} ítem(s) más. {msg}"
            if len(cola) > 1:
                msg += f" (quedan {len(cola)} por elegir)"
            return False, ambiguos, msg
        errores.append(msj or f"No se pudo agregar '{termino}'.")
        cola.pop(0)

    intent = _finalizar_cola_voz_mostrador(vendedor)

    if errores and agregados:
        return True, None, " · ".join(agregados) + " · " + " · ".join(errores)
    if errores:
        return True, None, "\n".join(errores)
    if agregados:
        return True, None, " · ".join(agregados)
    return True, None, None


def activar_cliente_voz(nombre_cliente=None, consumidor_final=False, tipo_comprobante=None):
    descartar_panels_operacion_anterior()
    if consumidor_final:
        cli = cliente_consumidor_final()
    elif nombre_cliente:
        nombre_up = str(nombre_cliente).upper()
        clientes_db = obtener_clientes() or {}
        encontrado = None
        mejor_len = 0
        for c in clientes_db.values():
            cn = str(c.get("nombre", "")).upper().strip()
            if not cn:
                continue
            if cn == nombre_up or nombre_up.startswith(cn) or cn.startswith(nombre_up):
                if len(cn) > mejor_len:
                    encontrado = c
                    mejor_len = len(cn)
        if encontrado:
            cli = cliente_db_a_activo(encontrado)
        else:
            cli = {
                "nombre": nombre_up,
                "cuit": "00000000000",
                "descuento": 0.0,
                "tipo_comprobante": "6",
            }
    else:
        return None

    if tipo_comprobante in ("1", "6", "A", "B", "a", "b"):
        t = str(tipo_comprobante).upper()
        cli["tipo_comprobante"] = "1" if t in ("1", "A") else "6"
    st.session_state.cliente_activo = cli
    return cli


def ejecutar_flujo_factura_voz(
    vendedor,
    flujo: dict,
    inventario,
    buscar_en_inventario,
    agregar_al_carrito,
    emitir_factura_fn: Callable,
    texto_orden=None,
):
    """
    Ejecuta en un solo paso: cliente, ítems, pago e impresión ticket.
    emitir_factura_fn(vendedor, carrito, total_final, desc_porc, forma_pago, solo_ticket)
    """
    descartar_panels_operacion_anterior()
    pasos_ok = []
    errores = []

    if flujo.get("vaciar_antes", flujo.get("carrito_nuevo", False)):
        vaciar_carrito(str(vendedor))

    tipo = flujo.get("tipo_comprobante")
    if tipo in (None, "") and flujo.get("factura_b"):
        tipo = "6"
    if tipo in (None, "") and flujo.get("factura_a"):
        tipo = "1"

    if flujo.get("consumidor_final"):
        activar_cliente_voz(consumidor_final=True, tipo_comprobante=tipo)
        pasos_ok.append("Consumidor final")
    elif flujo.get("nombre_cliente"):
        activar_cliente_voz(
            nombre_cliente=flujo.get("nombre_cliente"),
            tipo_comprobante=tipo,
        )
        pasos_ok.append(f"Cliente {flujo.get('nombre_cliente')}")
    elif tipo:
        cli = dict(st.session_state.get("cliente_activo") or cliente_consumidor_final())
        t = str(tipo).upper()
        cli["tipo_comprobante"] = "1" if t in ("1", "A") else "6"
        st.session_state.cliente_activo = cli
        pasos_ok.append(f"Factura {'A' if cli['tipo_comprobante'] == '1' else 'B'}")

    items = flujo.get("items") or []
    if isinstance(items, dict):
        items = [items]
    if not items and texto_orden:
        items = extraer_items_orden_voz(texto_orden)
    if not items and texto_orden and flujo.get("termino"):
        items = [{"termino": flujo.get("termino"), "cantidad": flujo.get("cantidad", 1)}]

    errores_items = []
    items_agregados = 0
    cola_ambiguos = []
    for raw in items:
        if not isinstance(raw, dict):
            continue
        termino = raw.get("termino") or raw.get("codigo") or raw.get("descripcion")
        cant = raw.get("cantidad", 1)
        veh = raw.get("vehiculo")
        ok, msj, ambiguos = agregar_termino_voz(
            vendedor, termino, cant, inventario, buscar_en_inventario, agregar_al_carrito,
            vehiculo=veh,
        )
        if ok:
            pasos_ok.append(msj)
            items_agregados += 1
        elif ambiguos:
            cola_ambiguos.append({
                "termino": termino,
                "cantidad": cant,
                "vehiculo": veh,
                "coincidencias": ambiguos,
                "msj": msj,
            })
            errores.append(msj)
        else:
            errores.append(msj)
            errores_items.append(msj)

    if cola_ambiguos:
        intent = flujo.get("intent_sugerido")
        _guardar_intent_voz_pendiente(intent)
        st.session_state.mostrador_voz_cola_ambiguos = cola_ambiguos
        first = cola_ambiguos[0]
        st.session_state.mostrador_voz_cant_coincidencia = int(first.get("cantidad", 1))
        msg = str(first.get("msj", "Elegí el producto exacto."))
        if items_agregados:
            msg = (
                f"Agregados {items_agregados} ítem(s). {msg} "
                f"(faltan {len(cola_ambiguos)} por elegir)"
            )
        elif len(cola_ambiguos) > 1:
            msg += f" (faltan {len(cola_ambiguos)} por elegir)"
        return False, msg, first.get("coincidencias")

    if items and items_agregados == 0 and errores_items:
        if len(errores_items) == 1:
            return False, errores_items[0], None
        return False, "No se pudo agregar ningún producto:\n" + "\n".join(errores_items), None

    if flujo.get("forma_pago"):
        from modulos.ia_mostrador import normalizar_forma_pago

        fp = normalizar_forma_pago(flujo.get("forma_pago"))
        st.session_state[f"mostrador_forma_pago_{vendedor}"] = fp
        pasos_ok.append(f"Pago {fp}")

    imprimir = bool(
        flujo.get("imprimir_ticket")
        or flujo.get("imprimir")
        or flujo.get("accion") == "imprimir_ticket"
    )
    ir_verificacion = bool(flujo.get("ir_verificacion") or imprimir)
    intent = flujo.get("intent_sugerido")
    if intent:
        st.session_state.mostrador_intent_sugerido = intent

    if errores:
        if ir_verificacion and (obtener_carrito(str(vendedor)) or []):
            marcar_verificacion_mostrador(intent)
        return False, "Flujo parcial:\n" + "\n".join(errores), None

    if ir_verificacion:
        carrito = obtener_carrito(str(vendedor)) or []
        if not carrito:
            if not items:
                return (
                    False,
                    "No detecté producto ni cantidad. Ejemplo: «buje de directa 3 unidades» "
                    "o «código 111 3 unidades».",
                    None,
                )
            return False, "No hay ítems en el carrito. Revisá el código.", None
        pending = st.session_state.pop("mostrador_voz_intent_pendiente", None)
        st.session_state.pop("mostrador_voz_cola_ambiguos", None)
        st.session_state.pop("mostrador_voz_cant_coincidencia", None)
        marcar_verificacion_mostrador(intent or pending)
        pasos_ok.append(
            "Listo para verificar. Revisá la grilla arriba y elegí "
            "Facturar ARCA o Presupuesto en el panel derecho."
        )
    elif intent in ("presupuesto", "factura_b", "factura_a") and (flujo.get("nombre_cliente") or flujo.get("consumidor_final")):
        carrito = obtener_carrito(str(vendedor)) or []
        if not carrito and not items:
            return (
                False,
                "No detecté producto ni cantidad en la orden. "
                "Ejemplo: «presupuesto para Juan, buje de directa 3 unidades».",
                None,
            )

    resumen = " · ".join(pasos_ok) if pasos_ok else "Listo."
    if ir_verificacion:
        return True, resumen, None
    return True, resumen, None
