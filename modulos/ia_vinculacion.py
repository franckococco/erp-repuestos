import os
import json
import re
import unicodedata
from groq import Groq  # type: ignore
import streamlit as st
from dotenv import load_dotenv

load_dotenv(override=True)


def normalizar_texto(texto):
    if not texto:
        return ""
    t = unicodedata.normalize("NFD", str(texto).lower())
    return re.sub(r"[^a-z0-9\s]", "", "".join(c for c in t if unicodedata.category(c) != "Mn"))


def _groq_client():
    api_key = os.getenv("GROQ_API_KEY")
    if not api_key:
        try:
            api_key = st.secrets["GROQ_API_KEY"]
        except Exception:
            api_key = None
    return Groq(api_key=api_key) if api_key else None


def prefiltrar_candidatos(articulo, inventario, limite=20):
    if not inventario:
        return []

    terminos = normalizar_texto(
        f"{articulo.get('codigo', '')} {articulo.get('descripcion', '')} "
        f"{articulo.get('marca', '')} {articulo.get('vehiculo', '')}"
    ).split()
    if not terminos:
        return inventario[:limite]

    scored = []
    for item in inventario:
        if not isinstance(item, dict):
            continue
        texto = normalizar_texto(
            f"{item.get('codigo', '')} {item.get('descripcion', '')} "
            f"{item.get('marca', '')} {item.get('vehiculo', '')} {item.get('id', '')}"
        )
        hits = sum(1 for t in terminos if t in texto)
        if hits == 0:
            continue
        scored.append((hits, item))

    scored.sort(key=lambda x: (-x[0], str(x[1].get("descripcion", ""))))
    return [item for _, item in scored[:limite]]


def _score_local(articulo, candidato):
    terminos = normalizar_texto(
        f"{articulo.get('codigo', '')} {articulo.get('descripcion', '')} {articulo.get('marca', '')}"
    ).split()
    texto = normalizar_texto(
        f"{candidato.get('codigo', '')} {candidato.get('descripcion', '')} "
        f"{candidato.get('marca', '')} {candidato.get('vehiculo', '')}"
    )
    if not terminos:
        return 0
    hits = sum(1 for t in terminos if t in texto)
    return min(100, int((hits / len(terminos)) * 100))


def sugerir_equivalencias_groq(articulo, candidatos):
    if not candidatos:
        return []

    client = _groq_client()
    if not client:
        return [
            {
                "id_maestro": c.get("id_maestro") or c.get("codigo"),
                "marca": c.get("marca", "GENERICO"),
                "descripcion": c.get("descripcion", ""),
                "score": _score_local(articulo, c),
                "motivo": "Coincidencia por texto",
            }
            for c in candidatos[:3]
        ]

    catalogo = []
    for c in candidatos:
        catalogo.append({
            "id_maestro": c.get("id_maestro") or c.get("codigo"),
            "marca": c.get("marca", "GENERICO"),
            "codigo": c.get("codigo", ""),
            "descripcion": c.get("descripcion", ""),
            "vehiculo": c.get("vehiculo", ""),
        })

    prompt = f"""
Sos experto en repuestos automotor argentinos. Compará el ítem de factura con el catálogo.
Devolvé SOLO JSON con esta forma:
{{"sugerencias": [{{"id_maestro": "...", "marca": "...", "score": 0-100, "motivo": "..."}}]}}

Reglas:
- Máximo 3 sugerencias ordenadas por score descendente.
- score 0-100 según probabilidad de ser el mismo repuesto.
- Sinónimos válidos: rulemán/rodamiento, bomba de agua/bomba agua, etc.
- Nunca inventes id_maestro que no esté en el catálogo.

ÍTEM FACTURA:
{json.dumps({
    "codigo": articulo.get("codigo_proveedor", articulo.get("codigo", "")),
    "descripcion": articulo.get("descripcion", ""),
    "marca": articulo.get("marca", ""),
    "vehiculo": articulo.get("vehiculo", ""),
}, ensure_ascii=False)}

CATÁLOGO CANDIDATO:
{json.dumps(catalogo, ensure_ascii=False)}
"""

    try:
        resp = client.chat.completions.create(
            messages=[{"role": "user", "content": prompt}],
            model="llama-3.3-70b-versatile",
            temperature=0.0,
            response_format={"type": "json_object"},
        )
        texto = resp.choices[0].message.content.strip()  # type: ignore
        texto = texto.replace("```json", "").replace("```", "").strip()
        data = json.loads(texto)
        sugerencias = data.get("sugerencias", [])
        ids_validos = {(str(c.get("id_maestro")), str(c.get("marca", "")).upper()) for c in catalogo}
        limpias = []
        for s in sugerencias:
            key = (str(s.get("id_maestro", "")), str(s.get("marca", "")).upper())
            if key not in ids_validos:
                continue
            cand = next(
                (c for c in candidatos if (c.get("id_maestro") or c.get("codigo")) == s.get("id_maestro")
                 and str(c.get("marca", "")).upper() == key[1]),
                {},
            )
            limpias.append({
                "id_maestro": s.get("id_maestro"),
                "marca": s.get("marca", "GENERICO"),
                "descripcion": cand.get("descripcion", ""),
                "score": int(s.get("score", 0)),
                "motivo": str(s.get("motivo", "")),
            })
        return limpias[:3]
    except Exception:
        return [
            {
                "id_maestro": c.get("id_maestro") or c.get("codigo"),
                "marca": c.get("marca", "GENERICO"),
                "descripcion": c.get("descripcion", ""),
                "score": _score_local(articulo, c),
                "motivo": "Coincidencia local (Groq no disponible)",
            }
            for c in candidatos[:3]
        ]


def _texto_vinculado(id_maestro, marca, descripcion=""):
    base = f"{id_maestro} / {marca}"
    if descripcion:
        return f"{base} — {descripcion}"
    return base


def resolver_articulo_factura(cuit, articulo, inventario, buscar_equivalencia_fn, usar_groq=False):
    from modulos.db_firebase import normalizar_codigo_proveedor

    codigo_prov = normalizar_codigo_proveedor(
        articulo.get("codigo_proveedor") or articulo.get("codigo", "")
    )
    marca_prov = str(articulo.get("marca", "GENERICO")).strip().upper()
    articulo["codigo_proveedor"] = codigo_prov
    articulo.setdefault("codigo", codigo_prov)

    eq = buscar_equivalencia_fn(cuit, codigo_prov)
    if eq:
        id_m = eq.get("id_maestro")
        marca_v = eq.get("marca_variante", marca_prov)
        articulo["estado_vinculacion"] = "auto"
        articulo["id_maestro"] = id_m
        articulo["marca_variante"] = marca_v
        articulo["vinculado_a"] = _texto_vinculado(id_m, marca_v, eq.get("descripcion_maestro", ""))
        articulo["sugerencias"] = []
        return articulo

    candidatos = prefiltrar_candidatos(articulo, inventario)
    sugerencias = sugerir_equivalencias_groq(articulo, candidatos) if usar_groq and candidatos else []

    if sugerencias and sugerencias[0].get("score", 0) >= 85:
        top = sugerencias[0]
        articulo["estado_vinculacion"] = "sugerido"
        articulo["sugerencias"] = sugerencias
        articulo["id_maestro"] = None
        articulo["marca_variante"] = None
        articulo["vinculado_a"] = f"Sugerido: {_texto_vinculado(top['id_maestro'], top['marca'], top.get('descripcion', ''))} ({top['score']}%)"
    elif sugerencias:
        articulo["estado_vinculacion"] = "sugerido"
        articulo["sugerencias"] = sugerencias
        articulo["id_maestro"] = None
        articulo["marca_variante"] = None
        articulo["vinculado_a"] = "Revisar sugerencias"
    else:
        articulo["estado_vinculacion"] = "pendiente"
        articulo["sugerencias"] = []
        articulo["id_maestro"] = None
        articulo["marca_variante"] = None
        articulo["vinculado_a"] = "Sin vincular"

    return articulo


def resolver_articulos_factura(cuit, articulos, inventario, buscar_equivalencia_fn, usar_groq=False):
    resueltos = []
    for art in articulos or []:
        if not isinstance(art, dict):
            continue
        resueltos.append(
            resolver_articulo_factura(cuit, dict(art), inventario, buscar_equivalencia_fn, usar_groq)
        )
    return resueltos


def sugerir_articulo_con_groq(articulo, inventario):
    """Ejecuta sugerencia Groq bajo demanda para un solo artículo."""
    candidatos = prefiltrar_candidatos(articulo, inventario)
    sugerencias = sugerir_equivalencias_groq(articulo, candidatos)
    articulo = dict(articulo)
    if sugerencias:
        articulo["estado_vinculacion"] = "sugerido"
        articulo["sugerencias"] = sugerencias
        articulo["id_maestro"] = None
        articulo["marca_variante"] = None
        top = sugerencias[0]
        articulo["vinculado_a"] = (
            f"Sugerido: {_texto_vinculado(top['id_maestro'], top['marca'], top.get('descripcion', ''))} "
            f"({top.get('score', 0)}%)"
        )
    else:
        articulo["estado_vinculacion"] = "pendiente"
        articulo["sugerencias"] = []
        articulo["vinculado_a"] = "Sin coincidencias IA — vincular manualmente"
    return articulo


def sugerir_pendientes_con_groq(articulos, inventario):
    """Sugerencias Groq solo para ítems pendientes (no auto-vinculados)."""
    resultado = []
    for art in articulos or []:
        if not isinstance(art, dict):
            continue
        art = dict(art)
        if art.get("estado_vinculacion") == "auto" and art.get("id_maestro"):
            resultado.append(art)
            continue
        if art.get("id_maestro"):
            resultado.append(art)
            continue
        resultado.append(sugerir_articulo_con_groq(art, inventario))
    return resultado


def mapa_vinculacion_articulos(articulos):
    """Índice clave → metadatos de vinculación para fusionar con la grilla editada."""
    mapa = {}
    for art in articulos or []:
        if not isinstance(art, dict):
            continue
        from modulos.db_firebase import clave_linea_factura
        k = clave_linea_factura(art)
        mapa[k] = {
            "estado_vinculacion": art.get("estado_vinculacion"),
            "id_maestro": art.get("id_maestro"),
            "marca_variante": art.get("marca_variante"),
            "vinculado_a": art.get("vinculado_a"),
            "sugerencias": art.get("sugerencias", []),
            "codigo_proveedor": art.get("codigo_proveedor"),
        }
    return mapa


def fusionar_grilla_con_vinculacion(df_editado, mapa_vinc):
    """Fusiona filas editadas con vinculación usando clave codigo|marca (no índice)."""
    from modulos.db_firebase import clave_linea_factura, normalizar_codigo_proveedor
    filas = []
    for _, row in df_editado.iterrows():
        art = row.to_dict()
        art["marca"] = str(art.get("marca", "GENERICO")).strip().upper()
        art["codigo_proveedor"] = normalizar_codigo_proveedor(art.get("codigo", ""))
        k = clave_linea_factura(art)
        meta = mapa_vinc.get(k, {})
        for campo in ("estado_vinculacion", "id_maestro", "marca_variante", "vinculado_a", "sugerencias"):
            if campo in meta:
                art[campo] = meta[campo]
        filas.append(art)
    return filas


def aplicar_vinculacion_manual(articulo, id_maestro, marca_variante, descripcion_maestro=""):
    articulo["estado_vinculacion"] = "manual"
    articulo["id_maestro"] = str(id_maestro).strip().upper().replace("/", "-")
    articulo["marca_variante"] = str(marca_variante).strip().upper()
    articulo["vinculado_a"] = _texto_vinculado(articulo["id_maestro"], articulo["marca_variante"], descripcion_maestro)
    articulo["sugerencias"] = []
    return articulo


def aplicar_vinculacion_sugerida(articulo, sugerencia):
    return aplicar_vinculacion_manual(
        articulo,
        sugerencia.get("id_maestro"),
        sugerencia.get("marca", "GENERICO"),
        sugerencia.get("descripcion", ""),
    )


def aplicar_articulo_nuevo(articulo):
    from modulos.db_firebase import normalizar_codigo_proveedor

    codigo = normalizar_codigo_proveedor(articulo.get("codigo_proveedor") or articulo.get("codigo", ""))
    if not codigo:
        desc = str(articulo.get("descripcion", "ART")).replace(" ", "_").upper()[:15]
        codigo = desc or "SIN_CODIGO"
    marca = str(articulo.get("marca", "GENERICO")).strip().upper()
    articulo["estado_vinculacion"] = "nuevo"
    articulo["id_maestro"] = codigo
    articulo["marca_variante"] = marca
    articulo["vinculado_a"] = f"Nuevo: {codigo} / {marca}"
    articulo["sugerencias"] = []
    return articulo
