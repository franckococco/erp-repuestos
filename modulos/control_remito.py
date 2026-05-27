import json
import os

from groq import Groq  # type: ignore
import streamlit as st
from dotenv import load_dotenv

load_dotenv(override=True)


def _clave_articulo(id_maestro, marca):
    return f"{str(id_maestro).upper()}|{str(marca).upper()}"


def preparar_articulos_comparacion(articulos, cuit, buscar_equivalencia_fn, normalizar_codigo_fn):
    preparados = []
    cuit_l = "".join(filter(str.isdigit, str(cuit)))

    for art in articulos or []:
        if not isinstance(art, dict):
            continue
        cod_prov = normalizar_codigo_fn(art.get("codigo_proveedor") or art.get("codigo", ""))
        marca = str(art.get("marca_variante") or art.get("marca", "GENERICO")).strip().upper()
        id_maestro = art.get("id_maestro")
        descripcion = str(art.get("descripcion", ""))

        if id_maestro:
            id_m = str(id_maestro).strip().upper().replace("/", "-")
        elif cuit_l and cod_prov:
            eq = buscar_equivalencia_fn(cuit_l, cod_prov)
            if eq:
                id_m = eq.get("id_maestro")
                marca = eq.get("marca_variante", marca)
                if eq.get("descripcion_maestro"):
                    descripcion = eq.get("descripcion_maestro")
            else:
                id_m = cod_prov
        elif cod_prov:
            id_m = cod_prov
        else:
            id_m = descripcion.replace(" ", "_").upper()[:15] or "SIN_CODIGO"

        try:
            cantidad = int(art.get("cantidad", 0))
        except (TypeError, ValueError):
            cantidad = 0

        preparados.append({
            "clave": _clave_articulo(id_m, marca),
            "id_maestro": id_m,
            "marca": marca,
            "descripcion": descripcion,
            "codigo_origen": cod_prov,
            "cantidad": cantidad,
        })
    return preparados


def _agrupar_por_clave(lineas):
    grupos = {}
    for linea in lineas:
        k = linea["clave"]
        if k not in grupos:
            grupos[k] = {**linea, "cantidad": 0, "codigos_origen": []}
        grupos[k]["cantidad"] += linea["cantidad"]
        cod = linea.get("codigo_origen")
        if cod and cod not in grupos[k]["codigos_origen"]:
            grupos[k]["codigos_origen"].append(cod)
    return grupos


def _indice_por_codigo_origen(lineas):
    """Índice codigo_origen → lista de claves (para emparejar códigos distintos al maestro)."""
    idx = {}
    for linea in lineas:
        cod = linea.get("codigo_origen")
        if cod:
            idx.setdefault(cod, []).append(linea["clave"])
    return idx


def comparar_factura_remito(articulos_factura, articulos_remito, cuit, buscar_equivalencia_fn, normalizar_codigo_fn):
    fac_lineas = preparar_articulos_comparacion(
        articulos_factura, cuit, buscar_equivalencia_fn, normalizar_codigo_fn
    )
    rem_lineas = preparar_articulos_comparacion(
        articulos_remito, cuit, buscar_equivalencia_fn, normalizar_codigo_fn
    )
    fac = _agrupar_por_clave(fac_lineas)
    rem = _agrupar_por_clave(rem_lineas)

    # Emparejar por código de origen cuando el maestro difiere (ej. OCR vs equivalencia)
    idx_rem_cod = _indice_por_codigo_origen(rem_lineas)
    claves_rem_usadas = set()
    alias_fac_a_rem = {}
    for clave_f, f in list(fac.items()):
        if clave_f in alias_fac_a_rem:
            continue
        for cod in f.get("codigos_origen") or []:
            for clave_r in idx_rem_cod.get(cod, []):
                if clave_r in claves_rem_usadas or clave_r == clave_f:
                    continue
                if clave_r in fac:
                    continue
                alias_fac_a_rem[clave_f] = clave_r
                claves_rem_usadas.add(clave_r)
                break
            if clave_f in alias_fac_a_rem:
                break

    coinciden = []
    dif_cantidad = []
    faltan_en_remito = []
    sobran_en_remito = []

    for clave, f in fac.items():
        clave_rem = alias_fac_a_rem.get(clave, clave)
        r = rem.get(clave_rem)
        base = {
            "id_maestro": f["id_maestro"],
            "marca": f["marca"],
            "descripcion": f["descripcion"],
            "codigos_factura": f.get("codigos_origen", []),
            "codigos_remito": r.get("codigos_origen", []) if r else [],
            "cant_factura": f["cantidad"],
            "cant_remito": r["cantidad"] if r else 0,
        }
        if r and f["cantidad"] == r["cantidad"]:
            coinciden.append({**base, "estado": "ok"})
        elif r:
            dif_cantidad.append({
                **base,
                "estado": "diferencia",
                "delta": r["cantidad"] - f["cantidad"],
            })
        else:
            faltan_en_remito.append({**base, "estado": "falta_remito"})

    for clave, r in rem.items():
        if clave in claves_rem_usadas:
            continue
        clave_fac_directa = clave
        if clave_fac_directa not in fac and clave not in alias_fac_a_rem.values():
            sobran_en_remito.append({
                "id_maestro": r["id_maestro"],
                "marca": r["marca"],
                "descripcion": r["descripcion"],
                "codigos_remito": r.get("codigos_origen", []),
                "cant_factura": 0,
                "cant_remito": r["cantidad"],
                "estado": "sobra_remito",
            })

    resumen = {
        "total_factura": len(fac),
        "total_remito": len(rem),
        "coinciden": len(coinciden),
        "diferencias": len(dif_cantidad),
        "faltan_en_remito": len(faltan_en_remito),
        "sobran_en_remito": len(sobran_en_remito),
        "ok": len(dif_cantidad) == 0 and len(faltan_en_remito) == 0 and len(sobran_en_remito) == 0,
    }

    return {
        "coinciden": coinciden,
        "dif_cantidad": dif_cantidad,
        "faltan_en_remito": faltan_en_remito,
        "sobran_en_remito": sobran_en_remito,
        "resumen": resumen,
    }


def _groq_client():
    api_key = os.getenv("GROQ_API_KEY")
    if not api_key:
        try:
            api_key = st.secrets["GROQ_API_KEY"]
        except Exception:
            api_key = None
    return Groq(api_key=api_key) if api_key else None


def sugerir_emparejamientos_huerfanos(faltan_en_remito, sobran_en_remito):
    if not faltan_en_remito or not sobran_en_remito:
        return []

    client = _groq_client()
    if not client:
        return []

    prompt = f"""
Sos experto en repuestos automotor. Hay líneas de factura sin match en el remito y líneas del remito sin match en la factura.
Sugerí posibles pares que podrían ser el mismo producto (códigos distintos entre proveedores).
Devolvé SOLO JSON: {{"pares": [{{"idx_factura": 0, "idx_remito": 0, "score": 0-100, "motivo": "..."}}]}}
Máximo 5 pares. idx es índice en las listas provistas (0-based).

FACTURA SIN REMITO:
{json.dumps([{"i": i, "codigo": f.get("codigos_factura"), "desc": f.get("descripcion"), "cant": f.get("cant_factura")} for i, f in enumerate(faltan_en_remito)], ensure_ascii=False)}

REMito SIN FACTURA:
{json.dumps([{"i": i, "codigo": s.get("codigos_remito"), "desc": s.get("descripcion"), "cant": s.get("cant_remito")} for i, s in enumerate(sobran_en_remito)], ensure_ascii=False)}
"""
    try:
        resp = client.chat.completions.create(
            messages=[{"role": "user", "content": prompt}],
            model="llama-3.3-70b-versatile",
            temperature=0.0,
            response_format={"type": "json_object"},
        )
        texto = resp.choices[0].message.content.strip()  # type: ignore
        data = json.loads(texto.replace("```json", "").replace("```", "").strip())
        pares = []
        for p in data.get("pares", []):
            i_f = int(p.get("idx_factura", -1))
            i_r = int(p.get("idx_remito", -1))
            if 0 <= i_f < len(faltan_en_remito) and 0 <= i_r < len(sobran_en_remito):
                pares.append({
                    "factura": faltan_en_remito[i_f],
                    "remito": sobran_en_remito[i_r],
                    "score": int(p.get("score", 0)),
                    "motivo": str(p.get("motivo", "")),
                })
        return pares
    except Exception:
        return []


def resultado_a_tabla(resultado):
    filas = []
    for item in resultado.get("coinciden", []):
        filas.append({
            "Estado": "✅ OK",
            "Artículo": f"{item['descripcion']} ({item['id_maestro']} / {item['marca']})",
            "Factura": item["cant_factura"],
            "Remito": item["cant_remito"],
            "Diferencia": 0,
            "Detalle": "Coincide",
        })
    for item in resultado.get("dif_cantidad", []):
        delta = item["cant_remito"] - item["cant_factura"]
        filas.append({
            "Estado": "⚠️ Cantidad",
            "Artículo": f"{item['descripcion']} ({item['id_maestro']} / {item['marca']})",
            "Factura": item["cant_factura"],
            "Remito": item["cant_remito"],
            "Diferencia": delta,
            "Detalle": f"{'Sobran' if delta > 0 else 'Faltan'} {abs(delta)} u.",
        })
    for item in resultado.get("faltan_en_remito", []):
        filas.append({
            "Estado": "❌ Falta en remito",
            "Artículo": f"{item['descripcion']} ({item['id_maestro']} / {item['marca']})",
            "Factura": item["cant_factura"],
            "Remito": 0,
            "Diferencia": -item["cant_factura"],
            "Detalle": "Facturado pero no en remito",
        })
    for item in resultado.get("sobran_en_remito", []):
        filas.append({
            "Estado": "❌ Sobra en remito",
            "Artículo": f"{item['descripcion']} ({item['id_maestro']} / {item['marca']})",
            "Factura": 0,
            "Remito": item["cant_remito"],
            "Diferencia": item["cant_remito"],
            "Detalle": "En remito pero no facturado",
        })
    return filas
