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
from modulos.voz_repuestos import (
    es_palabra_repuesto,
    obtener_vocabulario_repuesto_voz,
)


def descartar_panels_operacion_anterior():
    """Quita carteles de factura/presupuesto de la operación anterior."""
    st.session_state.pop("factura_arca_reciente", None)
    st.session_state.pop("mostrador_voz_solo_ticket", None)
    st.session_state.pop("hist_arca_preview", None)


def limpiar_coincidencias_pendientes_mostrador():
    """Quita listas de elección y colas de ambigüedad de una búsqueda anterior."""
    st.session_state.pop("resultados_ia_mostrador", None)
    st.session_state.pop("msg_ia_mostrador", None)
    st.session_state.pop("mostrador_voz_cola_ambiguos", None)
    st.session_state.pop("mostrador_voz_cant_coincidencia", None)



def _expandir_numeros_compuestos(texto: str) -> str:
    """Compat tests: delega en capa de lenguaje natural."""
    from modulos.voz_lenguaje_natural import aplicar_lenguaje_natural_mostrador
    return aplicar_lenguaje_natural_mostrador(texto)


# Vocabulario de repuestos (sinónimos + raíces) para cortar cliente y cantidades
_INICIO_DESCRIPCION_VOZ = obtener_vocabulario_repuesto_voz()


def normalizar_orden_voz_mostrador(texto):
    """
    Convierte frases habladas del mostrador a texto estable para el parser local.
    Delega en la capa de lenguaje natural (muletillas, conectores, cantidades).
    """
    from modulos.ia_asistente import preprocesar_texto_usuario
    from modulos.voz_lenguaje_natural import aplicar_lenguaje_natural_mostrador

    if not texto:
        return ""
    base = preprocesar_texto_usuario(str(texto).strip())
    return aplicar_lenguaje_natural_mostrador(base)


def preprocesar_texto_mostrador(texto):
    """Preprocesa dictado del mostrador (código+cantidad + lenguaje natural)."""
    return normalizar_orden_voz_mostrador(texto)


def interpretar_orden_voz_mostrador(texto):
    """
    Interpreta una orden hablada/escrita sin inventario ni Groq.
    Devuelve texto normalizado, cliente, ítems e intención detectada.
    """
    from modulos.voz_lenguaje_natural import resumen_orden_natural, segmentar_orden_natural

    seg = segmentar_orden_natural(texto)
    return {
        "texto_original": seg["texto_original"],
        "texto_normalizado": seg["texto_normalizado"],
        "cliente": seg["cliente"],
        "items": seg["items"],
        "intent": seg["intent"],
        "forma_pago": seg["forma_pago"],
        "listo": seg["listo"],
        "resumen": resumen_orden_natural(seg),
    }


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
    "NECESITO", "QUIERO", "DAME", "PASAME", "AGREGAME", "METEME",
    "BUSCAME", "ANOTAME", "FICHAME", "MANDAME", "PREPARAME", "SACAME",
    "TAMBIEN", "TAMBIÉN", "ADEMAS", "ADEMÁS", "DESPUES", "DESPUÉS",
    "Y", "CON", "SIN", "DEL", "LOS", "LAS",
})

# _INICIO_DESCRIPCION_VOZ se carga arriba desde voz_repuestos.obtener_vocabulario_repuesto_voz()


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
        r"(?=\s+codigo|\s+listo|\s+factura|\s+presupuesto|"
        r"\s+de\s+(?:una?\s+)?\d{1,2}\b|"
        r"\s+de\s+(?:una?\s+)?\d{1,2}\s+(?:unidades?|u\.?|uds?|unidad)?\b|"
        r"\s+de\s+\d{1,2}\s+(?:unidades?|u\.?|uds?|unidad)?\b|"
        r"\s+(?:un|una)\s|"
        r"\b[\dA-Za-z]*\d[\dA-Za-z\-]*\s+\d{1,4}\s+unidades?\b|"
        rf"\s+(?:{prod})\b|$)"
    )


def _nombre_cliente_valido(nombre: str) -> bool:
    nombre = str(nombre or "").strip()
    if len(nombre) < 2:
        return False
    primera = nombre.split()[0]
    return _palabra_parece_nombre_cliente(primera)


def _extraer_nombre_multipalabra(texto_norm: str, prefijos: tuple[str, ...]) -> str:
    """Captura nombre completo (varias palabras) hasta el fin lógico del bloque cliente."""
    fin = _patron_fin_cliente_voz()
    for pref in prefijos:
        m = re.search(rf"{pref}\s+(.+?){fin}", texto_norm, flags=re.I)
        if not m:
            continue
        nombre = _limpiar_nombre_cliente_voz(m.group(1))
        if nombre and _nombre_cliente_valido(nombre):
            return nombre
    return ""


def _limpiar_texto_para_items_descripcion(texto: str) -> str:
    """Quita cliente, factura y prefijos de armado para buscar por descripción."""
    from modulos.voz_lenguaje_natural import quitar_muletillas_residuales

    cliente_info = extraer_cliente_orden_voz(texto)
    t = quitar_muletillas_residuales(texto)
    t = re.sub(r"\b(listo|termine|terminé|fin|dale)\b", " ", t)
    t = re.sub(r"\bfactura\s+[ab]\b", " ", t)
    t = re.sub(r"\bpresupuesto\b", " ", t)
    t = re.sub(r"\bcliente\b", " ", t)
    t = re.sub(r"\b(codigo|código|descripcion|descripción|desc)\b", " ", t)
    if cliente_info.get("nombre_cliente"):
        nom_completo = re.escape(str(cliente_info["nombre_cliente"]).lower())
        t = re.sub(rf"\bpara\s+(?:el\s+)?(?:cliente\s+)?{nom_completo}\b", " ", t, flags=re.I)
        t = re.sub(rf"\b{nom_completo}\b", " ", t, flags=re.I)
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
    from modulos.voz_repuestos import corregir_termino_repuesto

    t = normalizar_texto_basico(str(termino or "")).upper()
    palabras = [
        p for p in t.split()
        if p not in _STOPWORDS_ITEM and p not in ("UN", "UNA", "EL", "LA", "DE", "DEL")
    ]
    while palabras and palabras[0] in ("Y", "E", "TAMBIEN", "TAMBIÉN", "MAS", "MÁS"):
        palabras.pop(0)
    while palabras and len(palabras[0]) == 1:
        palabras.pop(0)
    base = " ".join(palabras).strip()
    if not base:
        return ""
    return corregir_termino_repuesto(base).upper()


def _segmentar_orden_items(texto: str) -> list:
    """Parte órdenes compuestas (código + descripción unidos con «y»)."""
    raw = str(texto or "").strip()
    if not raw:
        return []
    t = normalizar_texto_basico(raw).lower()
    if re.search(r"\s+y\s+", t):
        segs = [s.strip() for s in re.split(r"\s+y\s+", raw, flags=re.I) if s.strip()]
        if len(segs) > 1:
            return segs
    return [raw]


def _remover_bloques_codigo_explicito(texto: str) -> str:
    """Quita del texto los bloques «codigo X N unidades» ya parseados."""
    cod_pat = r"([\dA-Za-z]+(?:-[\dA-Za-z]+)*)"
    t = str(texto or "")
    t = re.sub(
        rf"(?:codigo|código)\s+{cod_pat}\s+\d{{1,4}}\s*(?:unidades?|u\.?|uds?|unidad)?\b",
        " ",
        t,
        flags=re.I,
    )
    return re.sub(r"\s+", " ", t).strip()


def _deduplicar_items_orden(items: list) -> list:
    """Elimina ítems duplicados o basura (p. ej. «A 111» cuando ya hay código 111)."""
    from modulos.util_busqueda import parece_codigo_producto, _normalizar_codigo_busqueda

    out = []
    codigos_vistos = set()
    desc_vistos = set()

    for raw in items or []:
        if not isinstance(raw, dict):
            continue
        it = dict(raw)
        term = str(it.get("termino", "")).strip().upper()
        if not term:
            continue

        palabras = term.split()
        if len(palabras) == 1 and len(palabras[0]) == 1:
            continue
        if len(palabras) == 2 and len(palabras[0]) == 1 and palabras[1].isdigit():
            term = palabras[1]
            it["termino"] = term
            it["modo"] = "codigo"

        modo = str(it.get("modo") or "").lower()
        if modo == "descripcion":
            if term in desc_vistos:
                continue
            desc_vistos.add(term)
            it["modo"] = "descripcion"
            out.append(it)
            continue
        if modo != "codigo" and parece_codigo_producto(term):
            modo = "codigo"
            it["modo"] = "codigo"

        if modo == "codigo":
            cod = _normalizar_codigo_busqueda(term)
            if not cod or cod in codigos_vistos:
                continue
            codigos_vistos.add(cod)
            it["termino"] = cod
            it["modo"] = "codigo"
            out.append(it)
            continue

        if parece_codigo_producto(term) and _normalizar_codigo_busqueda(term) in codigos_vistos:
            continue
        if term in desc_vistos:
            continue
        desc_vistos.add(term)
        it["modo"] = "descripcion"
        out.append(it)

    return out


def extraer_items_orden_voz(texto):
    """Extrae uno o varios códigos/descripciones + cantidad desde la orden hablada/escrita."""
    if not texto:
        return []
    texto = normalizar_orden_voz_mostrador(texto)

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

        def agregar(termino, cantidad, es_descripcion=False, es_codigo_explicito=False):
            if es_descripcion:
                term = _normalizar_termino_descripcion(termino)
            else:
                term = _limpiar_termino_item(termino)
            try:
                cant = max(1, int(cantidad))
            except (TypeError, ValueError):
                return
            from modulos.voz_repuestos import es_referencia_vehiculo
            from modulos.util_busqueda import parece_codigo_producto

            if es_referencia_vehiculo(str(cant)):
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
            clave = (term, cant, "codigo" if es_codigo_explicito else "desc")
            if clave in vistos:
                return
            vistos.add(clave)
            item = {"termino": term, "cantidad": cant}
            if es_codigo_explicito or (not es_descripcion and parece_codigo_producto(term)):
                item["modo"] = "codigo"
            else:
                item["modo"] = "descripcion"
            acumulado.append(item)

        patrones_codigo_explicito = [
            rf"(?:codigo|código)\s+{cod_pat}\s+(\d{{1,4}})\s*(?:unidades?|u\.?|uds?|unidad)?\b",
            rf"(?:agreg\w*|sum\w*|pon\w*)\s+(?:codigo|código\s+)?{cod_pat}\s+(\d{{1,4}})\s*(?:unidades?|u\.?|unidad)?\b",
            rf"(?:codigo|código)\s+{cod_pat}\s*(?:por|x|\*|con)\s*(\d{{1,4}})\b",
        ]
        n_antes = len(acumulado)

        for patron in patrones_codigo_explicito:
            for m in re.finditer(patron, t):
                agregar(m.group(1), m.group(2), es_descripcion=False, es_codigo_explicito=True)

        t_sin_cod = _remover_bloques_codigo_explicito(t)
        resto = _limpiar_texto_para_items_descripcion(_remover_bloques_codigo_explicito(fragmento))
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

        patrones_codigo_resto = [
            (rf"\b([\dA-Za-z]*\d[\dA-Za-z\-]*)\s+(\d{{1,4}})\s*(?:unidades?|u\.?|uds?|unidad)\b", False),
            (rf"\b([\dA-Za-z]*\d[\dA-Za-z\-]*)\s+(\d{{1,4}})\b", False),
            (rf"(?:descripcion|descripción|desc)\s+(.+?)\s+(\d{{1,4}})\s*(?:unidades?|u\.?|uds?|unidad)?\b", True),
            (rf"(?:descripcion|descripción|desc)\s+(.+?)\s*(?:por|x|\*|con)\s*(\d{{1,4}})\b", True),
        ]
        for patron, es_desc in patrones_codigo_resto:
            for m in re.finditer(patron, t_sin_cod):
                agregar(m.group(1), m.group(2), es_descripcion=es_desc)

        if len(acumulado) == n_antes:
            for m in re.finditer(rf"(?:codigo|código)\s+{cod_pat}\b", t):
                agregar(m.group(1), 1, es_descripcion=False, es_codigo_explicito=True)

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

        prod_pat = "|".join(re.escape(p) for p in _INICIO_DESCRIPCION_VOZ)
        for m in re.finditer(
            rf"\b(?:un|una)\s+({prod_pat})\w*(?:\s+de\s+([a-záéíóúñ]+(?:\s+[a-záéíóúñ]+)?))?",
            resto,
            re.I,
        ):
            term_parts = [m.group(1)]
            if m.group(2):
                term_parts.append(m.group(2).strip())
            agregar(" ".join(term_parts), 1, es_descripcion=True)

    def _extraer_items_de_segmento(fragmento, acumulado, vistos):
        n_antes = len(acumulado)
        _extraer_de_fragmento(fragmento, acumulado, vistos)
        if len(acumulado) > n_antes:
            return
        fallback = _extraer_item_sin_cantidad_explicita(fragmento)
        if not fallback:
            return
        clave = (fallback["termino"], int(fallback.get("cantidad", 1)))
        if clave in vistos:
            return
        vistos.add(clave)
        acumulado.append(fallback)

    items = []
    vistos = set()
    raw = str(texto).strip()
    for segmento in _segmentar_orden_items(raw):
        _extraer_items_de_segmento(segmento, items, vistos)

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
                        _extraer_items_de_segmento(seg, items, vistos)
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
                    _extraer_items_de_segmento(seg, items, vistos)

    items = _deduplicar_items_orden(items)

    from modulos.voz_repuestos import enriquecer_items_con_vehiculo

    if not items:
        fallback = _extraer_item_sin_cantidad_explicita(raw)
        if fallback:
            items.append(fallback)

    return enriquecer_items_con_vehiculo(items, raw)


def _extraer_item_sin_cantidad_explicita(texto_original: str):
    """Repuesto + vehículo sin «N unidades» (ej. bieleta de suspension 207 → cant 1)."""
    resto = _limpiar_texto_para_items_descripcion(texto_original)
    if not resto:
        return None
    from modulos.voz_repuestos import extraer_vehiculos_de_texto, corregir_termino_repuesto

    t = normalizar_texto_basico(resto).lower().strip()
    t = re.sub(r"\b(?:de|del)\s+(?:una?|el|la)\s+", " ", t)
    vehs = extraer_vehiculos_de_texto(resto)
    veh = vehs[0] if vehs else None
    if veh:
        t = re.sub(rf"\b(?:para\s+el\s+)?{re.escape(veh)}\b", " ", t, flags=re.I)
    prod_pat = "|".join(re.escape(p) for p in _INICIO_DESCRIPCION_VOZ)
    m = re.search(
        rf"\b({prod_pat})\w*(?:\s+de\s+([a-záéíóúñ]+(?:\s+[a-záéíóúñ]+)?))?",
        t,
        re.I,
    )
    if not m:
        return None
    partes = [corregir_termino_repuesto(m.group(1))]
    if m.group(2):
        partes.append(m.group(2).strip())
    term = corregir_termino_repuesto(" ".join(partes)).upper()
    if not _termino_descripcion_valido(term):
        return None
    item = {"termino": term, "cantidad": 1}
    if veh:
        item["vehiculo"] = veh
    return item


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
    if es_palabra_repuesto(primera):
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
        "hacer", "haceme", "cargame", "armame", "necesito", "quiero", "dame",
        "pasame", "agregame", "poneme", "sumame", "meteme", "buscame", "anotame",
        "fichame", "mandame", "preparame", "sacame", "tirame", "dejame",
        "cotiza", "cotizame", "facturame", "presu",
    ):
        return False
    if es_palabra_repuesto(p):
        return False
    if es_referencia_vehiculo(p):
        return False
    return True


def extraer_cliente_orden_voz(texto):
    """Extrae nombre de cliente o consumidor final desde la orden."""
    if not texto:
        return {}
    t = normalizar_orden_voz_mostrador(texto).lower()
    if re.search(r"consumidor\s+final|particular", t):
        return {"consumidor_final": True}

    fin = _patron_fin_cliente_voz()

    nombre = _extraer_nombre_multipalabra(
        t,
        (
            r"\bpara\s+el\s+cliente",
            r"\bpresupuesto\s+para",
            r"\bpresupuesto\s+(?!para\b)",
            r"\bpresupuesto\s+(?:al\s+nombre\s+de|para\s+el\s+cliente|para\s+cliente|para\s+el|a\s+el|a)",
            r"(?:hacer\s+)?factura\s+[ab]\s+(?:al\s+nombre\s+de|para\s+el\s+cliente|para\s+cliente|para\s+el|a\s+el|a)",
            r"\bfactura\s+[ab]\s+(?:al\s+nombre\s+de|para\s+el\s+cliente|para\s+cliente|para\s+el|a\s+el|a)",
        ),
    )
    if nombre:
        return {"nombre_cliente": nombre}

    m_antes_presu = re.search(
        r"^([a-záéíóúñ]+(?:\s+[a-záéíóúñ]+){0,3})\s+presupuesto\b",
        t,
        flags=re.I,
    )
    if m_antes_presu:
        nombre = _limpiar_nombre_cliente_voz(m_antes_presu.group(1))
        if nombre and _nombre_cliente_valido(nombre):
            return {"nombre_cliente": nombre}

    m_para = re.search(
        rf"\bpara\s+(?!el\s+\d{{3,4}}\b)(?!el\s+cliente\b)(.+?){fin}",
        t,
        flags=re.I,
    )
    if m_para:
        nombre = _limpiar_nombre_cliente_voz(m_para.group(1))
        if nombre and _nombre_cliente_valido(nombre):
            return {"nombre_cliente": nombre}

    m_presu_final = re.search(rf"\bpresupuesto\s+(?:para\s+)?(.+?){fin}", t)
    if m_presu_final:
        nombre = _limpiar_nombre_cliente_voz(m_presu_final.group(1))
        if nombre and _nombre_cliente_valido(nombre):
            return {"nombre_cliente": nombre}

    m_factura_final = re.search(r"\bfactura\s+[ab]\s+(?:para\s+)?(.+?)\s*$", t)
    if m_factura_final:
        nombre = _limpiar_nombre_cliente_voz(m_factura_final.group(1))
        if nombre and _nombre_cliente_valido(nombre):
            return {"nombre_cliente": nombre}

    m_cli = re.search(rf"\bcliente\s+(.+?){fin}", t)
    if m_cli:
        nombre = _limpiar_nombre_cliente_voz(m_cli.group(1))
        if nombre and _nombre_cliente_valido(nombre):
            return {"nombre_cliente": nombre}

    m_para_final = re.search(r"\bpara\s+(.+?)\s*$", t)
    if m_para_final:
        nombre = _limpiar_nombre_cliente_voz(m_para_final.group(1))
        if nombre and _nombre_cliente_valido(nombre):
            return {"nombre_cliente": nombre}

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
    modo=None,
):
    cant = max(1, int(cantidad or 1))
    termino = str(termino or "").strip()
    if not termino:
        return False, "Sin término de búsqueda.", None

    from modulos.util_busqueda import (
        _limpiar_prefijo_busqueda,
        buscar_codigo_exacto_inventario,
        buscar_en_inventario_con_vehiculo,
        parece_codigo_producto,
    )
    from modulos.voz_repuestos import corregir_termino_repuesto

    modo_codigo = str(modo or "").lower() == "codigo"
    modo_descripcion = str(modo or "").lower() == "descripcion"
    termino = _limpiar_prefijo_busqueda(termino)
    if not modo_codigo:
        termino = corregir_termino_repuesto(termino)
    else:
        termino = termino.upper()
    if not termino:
        return False, "Sin término de búsqueda.", None

    veh = str(vehiculo).strip() if vehiculo else None
    id_limpio = _normalizar_codigo_con_inventario(termino, inventario)

    if modo_codigo or (not modo_descripcion and _parece_codigo(id_limpio)):
        coincidencias = buscar_codigo_exacto_inventario(inventario, id_limpio)
        if not coincidencias:
            coincidencias = _buscar_variantes_por_codigo(inventario, id_limpio)
        if len(coincidencias) == 1:
            id_cart = _id_carrito_desde_item(coincidencias[0])
            ok, msj = agregar_al_carrito(str(vendedor), id_cart, cant)
            return ok, msj, None
        if len(coincidencias) > 1:
            return (
                False,
                f"Hay {len(coincidencias)} variantes para '{id_limpio}'. Elegí en la lista.",
                coincidencias[:10],
            )
        ok, msj = agregar_al_carrito(str(vendedor), id_limpio, cant)
        if ok:
            return True, msj, None
        if modo_codigo or parece_codigo_producto(id_limpio):
            return False, f"No encontré código '{id_limpio}'.", None

    encontrados = buscar_en_inventario_con_vehiculo(inventario, termino, veh)
    if len(encontrados) == 1:
        ok, msj = agregar_al_carrito(str(vendedor), encontrados[0]["id"], cant)
        return ok, msj, None
    if len(encontrados) > 1:
        hint = f" ({veh})" if veh else ""
        return False, f"Varias similitudes para '{termino}'{hint}. Elegí en la lista.", encontrados[:10]
    st.session_state[f"manual_add_ctx_{vendedor}"] = {
        "termino": termino,
        "vehiculo": veh,
        "cantidad": cant,
    }
    return False, f"No encontré '{termino}'" + (f" para {veh}" if veh else "") + ". Probá con código o agregá manual.", None


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
            modo=item.get("modo"),
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
        from modulos.cliente_resolver import (
            clientes_cache_mostrador,
            corregir_nombre_con_clientes,
            resolver_cliente_por_nombre,
        )

        nombre_corregido = corregir_nombre_con_clientes(nombre_cliente)
        clientes_db = clientes_cache_mostrador()
        encontrado, score, metodo = resolver_cliente_por_nombre(
            nombre_corregido, clientes_db
        )
        if not encontrado and nombre_corregido != str(nombre_cliente).strip().upper():
            encontrado, score, metodo = resolver_cliente_por_nombre(
                nombre_cliente, clientes_db
            )
        if encontrado and score >= 0.68:
            cli = cliente_db_a_activo(encontrado)
            st.session_state.pop("cliente_pendiente_confirmar", None)
        else:
            from modulos.cliente_resolver import sugerencias_clientes

            sugerencias = sugerencias_clientes(nombre_corregido, 5)
            st.session_state.cliente_pendiente_confirmar = {
                "nombre_dictado": nombre_corregido,
                "nombre_original": str(nombre_cliente).strip().upper(),
                "sugerencias": sugerencias,
            }
            cli = {
                "nombre": nombre_corregido,
                "cuit": "00000000000",
                "descuento": 0.0,
                "tipo_comprobante": "6",
                "pendiente_confirmar": True,
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
    if flujo.get("vaciar_antes", flujo.get("carrito_nuevo", False)):
        limpiar_coincidencias_pendientes_mostrador()
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
        modo = raw.get("modo")
        ok, msj, ambiguos = agregar_termino_voz(
            vendedor, termino, cant, inventario, buscar_en_inventario, agregar_al_carrito,
            vehiculo=veh,
            modo=modo,
        )
        if ok:
            pasos_ok.append(msj)
            items_agregados += 1
        elif ambiguos:
            cola_ambiguos.append({
                "termino": termino,
                "cantidad": cant,
                "vehiculo": veh,
                "modo": modo,
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
