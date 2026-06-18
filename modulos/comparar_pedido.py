"""Comparación pedido vs factura o remito (por código proveedor)."""
from modulos.db_firebase import normalizar_codigo_proveedor, sanitizar_clave_marca, clave_linea_factura
from modulos.normalizar_carga_producto import extraer_marca_desde_texto


def _clave_codigo_proveedor(codigo, marca):
    cod = normalizar_codigo_proveedor(codigo)
    mar = sanitizar_clave_marca(marca)
    return clave_linea_factura(cod, mar) if cod else ""


def _marca_linea(art: dict) -> str:
    marca = sanitizar_clave_marca(art.get("marca", "GENERICO"))
    if marca != "GENERICO":
        return marca
    desc = str(art.get("descripcion", "") or "")
    return extraer_marca_desde_texto(desc, "GENERICO")


def preparar_lineas_pedido(items):
    lineas = []
    for art in items or []:
        if not isinstance(art, dict):
            continue
        cod = normalizar_codigo_proveedor(art.get("codigo_proveedor") or art.get("codigo", ""))
        marca = _marca_linea(art)
        if not cod:
            continue
        try:
            cant = int(art.get("cantidad_pedida", art.get("cantidad", 0)))
        except (TypeError, ValueError):
            cant = 0
        clave = _clave_codigo_proveedor(cod, marca)
        if not clave:
            continue
        lineas.append({
            "clave": clave,
            "codigo_proveedor": cod,
            "marca": marca,
            "descripcion": str(art.get("descripcion", "")),
            "cantidad": cant,
        })
    return lineas


def preparar_lineas_documento(items):
    """Factura o remito: empareja por código proveedor + marca (inferida si falta)."""
    lineas = []
    for art in items or []:
        if not isinstance(art, dict):
            continue
        cod = normalizar_codigo_proveedor(art.get("codigo_proveedor") or art.get("codigo", ""))
        marca = _marca_linea(art)
        if not cod:
            desc = str(art.get("descripcion", ""))
            cod = desc.replace(" ", "_").upper()[:15] if desc else ""
        if not cod:
            continue
        try:
            cant = int(art.get("cantidad", 0))
        except (TypeError, ValueError):
            cant = 0
        clave = _clave_codigo_proveedor(cod, marca)
        if not clave:
            continue
        lineas.append({
            "clave": clave,
            "codigo_proveedor": cod,
            "marca": marca,
            "descripcion": str(art.get("descripcion", "")),
            "cantidad": cant,
        })
    return lineas


def _agrupar_por_clave(lineas):
    grupos = {}
    for ln in lineas:
        k = ln["clave"]
        if k not in grupos:
            grupos[k] = {**ln, "cantidad": 0}
        grupos[k]["cantidad"] += ln["cantidad"]
    return grupos


def _indice_por_codigo(grupos):
    """codigo_proveedor -> lista de claves (todas las marcas)."""
    idx = {}
    for clave, ln in grupos.items():
        cod = ln["codigo_proveedor"]
        idx.setdefault(cod, []).append(clave)
    return idx


def _emparejar_linea_pedido(p, doc, idx_doc):
    """Busca línea en documento: clave exacta o mismo código si hay GENERICO."""
    clave = p["clave"]
    if clave in doc:
        return doc[clave], clave

    cod = p["codigo_proveedor"]
    candidatos = idx_doc.get(cod, [])
    if len(candidatos) == 1:
        k = candidatos[0]
        return doc[k], k

    if p["marca"] == "GENERICO":
        for k in candidatos:
            return doc[k], k

    for k in candidatos:
        if doc[k]["marca"] == "GENERICO":
            return doc[k], k

    return None, None


def comparar_pedido_con_documento(items_pedido, items_documento, tipo_documento="documento"):
    """
    Compara pedido (base) contra factura o remito.
    tipo_documento: 'factura' | 'remito' (solo para etiquetas en UI).
    """
    ped = _agrupar_por_clave(preparar_lineas_pedido(items_pedido))
    doc = _agrupar_por_clave(preparar_lineas_documento(items_documento))
    idx_doc = _indice_por_codigo(doc)
    doc_usado = set()

    coinciden = []
    dif_cantidad = []
    faltan_en_doc = []

    for clave, p in ped.items():
        d, clave_doc = _emparejar_linea_pedido(p, doc, idx_doc)
        base = {
            "codigo_proveedor": p["codigo_proveedor"],
            "marca": p["marca"],
            "descripcion": p["descripcion"],
            "cant_pedido": p["cantidad"],
            "cant_documento": d["cantidad"] if d else 0,
        }
        if d:
            doc_usado.add(clave_doc)
            if p["cantidad"] == d["cantidad"]:
                coinciden.append({**base, "estado": "ok"})
            else:
                dif_cantidad.append({
                    **base,
                    "estado": "diferencia",
                    "delta": d["cantidad"] - p["cantidad"],
                })
        else:
            faltan_en_doc.append({**base, "estado": "falta_documento"})

    sobran_en_doc = []
    for clave, d in doc.items():
        if clave not in doc_usado:
            sobran_en_doc.append({
                "codigo_proveedor": d["codigo_proveedor"],
                "marca": d["marca"],
                "descripcion": d["descripcion"],
                "cant_pedido": 0,
                "cant_documento": d["cantidad"],
                "estado": "sobra_documento",
            })

    resumen = {
        "tipo_documento": tipo_documento,
        "total_pedido": len(ped),
        "total_documento": len(doc),
        "coinciden": len(coinciden),
        "diferencias": len(dif_cantidad),
        "faltan_en_documento": len(faltan_en_doc),
        "sobran_en_documento": len(sobran_en_doc),
        "ok": (
            len(dif_cantidad) == 0
            and len(faltan_en_doc) == 0
            and len(sobran_en_doc) == 0
        ),
    }

    return {
        "coinciden": coinciden,
        "dif_cantidad": dif_cantidad,
        "faltan_en_documento": faltan_en_doc,
        "sobran_en_documento": sobran_en_doc,
        "resumen": resumen,
    }


def resultado_a_tabla(resultado, tipo_documento="documento"):
    doc_label = "Factura" if tipo_documento == "factura" else "Remito" if tipo_documento == "remito" else "Doc."
    filas = []
    for item in resultado.get("coinciden", []):
        filas.append({
            "Estado": "✅ OK",
            "Artículo": f"{item['descripcion']} ({item['codigo_proveedor']} / {item['marca']})",
            "Pedido": item["cant_pedido"],
            doc_label: item["cant_documento"],
            "Diferencia": 0,
            "Detalle": "Coincide",
        })
    for item in resultado.get("dif_cantidad", []):
        delta = item["cant_documento"] - item["cant_pedido"]
        filas.append({
            "Estado": "⚠️ Cantidad",
            "Artículo": f"{item['descripcion']} ({item['codigo_proveedor']} / {item['marca']})",
            "Pedido": item["cant_pedido"],
            doc_label: item["cant_documento"],
            "Diferencia": delta,
            "Detalle": f"{'Sobran' if delta > 0 else 'Faltan'} {abs(delta)} u. en {doc_label.lower()}",
        })
    for item in resultado.get("faltan_en_documento", []):
        filas.append({
            "Estado": f"❌ Falta en {doc_label.lower()}",
            "Artículo": f"{item['descripcion']} ({item['codigo_proveedor']} / {item['marca']})",
            "Pedido": item["cant_pedido"],
            doc_label: 0,
            "Diferencia": -item["cant_pedido"],
            "Detalle": "Pedido pero no en documento",
        })
    for item in resultado.get("sobran_en_documento", []):
        filas.append({
            "Estado": f"❌ Sobra en {doc_label.lower()}",
            "Artículo": f"{item['descripcion']} ({item['codigo_proveedor']} / {item['marca']})",
            "Pedido": 0,
            doc_label: item["cant_documento"],
            "Diferencia": item["cant_documento"],
            "Detalle": "En documento pero no pedido",
        })
    return filas
