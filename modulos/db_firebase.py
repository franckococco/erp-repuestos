import firebase_admin
from firebase_admin import credentials, firestore
import os
from dotenv import load_dotenv
from datetime import datetime, timezone
import math
import re
import streamlit as st
import pandas as pd

from modulos.util_fechas import formatear_fecha_ar
from modulos.util_busqueda import termino_en_texto, normalizar_para_busqueda as _norm_busqueda
from modulos.util_vehiculos import (
    normalizar_lista_vehiculos,
    vehiculos_a_texto,
    vehiculos_en_busqueda,
    combinar_vehiculos,
)

load_dotenv(override=True)

def inicializar_firebase():
    if not firebase_admin._apps: # type: ignore
        if "firebase_key" in st.secrets:
            f_key = st.secrets["firebase_key"]
            creds_dict = dict(f_key)
            creds_dict["private_key"] = str(creds_dict["private_key"]).replace("\\n", "\n")
            cred = credentials.Certificate(creds_dict)
        else:
            ruta_json = os.getenv("FIREBASE_CREDENTIALS_PATH", "firebase_claves.json")
            if not os.path.isfile(ruta_json):
                raise RuntimeError(
                    "Firebase no configurado. En Streamlit Cloud agregá `firebase_key` en "
                    "Settings → Secrets. En local, colocá `firebase_claves.json` en la raíz."
                )
            cred = credentials.Certificate(ruta_json)
        firebase_admin.initialize_app(cred)
    return firestore.client()


_db_client = None


def get_db():
    global _db_client
    if _db_client is None:
        _db_client = inicializar_firebase()
    return _db_client


_BATCH_LIMIT = 400


def _commit_batch_si_lleno(batch, operaciones):
    if operaciones > 0 and operaciones % _BATCH_LIMIT == 0:
        batch.commit()
        return get_db().batch()
    return batch


def _celda_vacia(val) -> bool:
    if val is None:
        return True
    if isinstance(val, float) and math.isnan(val):
        return True
    return False


def _entero_fila(row, key, default=0) -> int:
    val = row.get(key, default)
    if _celda_vacia(val):
        return default
    try:
        return int(val)
    except (TypeError, ValueError):
        return default


def _float_fila(row, key, default=0.0) -> float:
    val = row.get(key, default)
    if _celda_vacia(val):
        return default
    try:
        return float(val)
    except (TypeError, ValueError):
        return default


def _ubicacion_desde_fila(row):
    ubi = row.get("ubicacion")
    if isinstance(ubi, dict):
        return ubi
    pasillo = row.get("pasillo")
    if not _celda_vacia(pasillo):
        return {
            "pasillo": _entero_fila(row, "pasillo"),
            "piso": _entero_fila(row, "piso"),
            "modulo": _entero_fila(row, "modulo"),
            "fila": _entero_fila(row, "fila"),
        }
    return {"pasillo": 0, "piso": 0, "modulo": 0, "fila": 0}


def _borrar_subcoleccion(ref_doc, nombre_sub):
    batch = get_db().batch()
    operaciones = 0
    for doc in ref_doc.collection(nombre_sub).stream():
        batch.delete(doc.reference)
        operaciones += 1
        batch = _commit_batch_si_lleno(batch, operaciones)
    if operaciones % _BATCH_LIMIT != 0:
        batch.commit()


def _limpiar_cache_streamlit(func):
    limpiar = getattr(func, "clear", None)
    if callable(limpiar):
        limpiar()


def normalizar_codigo_proveedor(codigo):
    c = str(codigo or "").strip().upper().replace("/", "-")
    return c if c and c not in ("NONE", "NULL", "NAN") else ""


def sanitizar_clave_marca(marca):
    """Clave válida para Firestore dentro de variantes.{marca}."""
    m = str(marca or "GENERICO").strip().upper()
    m = re.sub(r'[\.\/\[\]\*]', "_", m)
    m = re.sub(r"_+", "_", m).strip("_")
    if not m or m.startswith("__"):
        m = "GENERICO"
    return m[:60]


_CAMPOS_FORMATO_ANTIGUO = (
    "marca", "condicion", "stock", "precio_venta", "precio_interno",
    "ultimo_costo_base", "proveedor", "cuit_proveedor",
)


def _extraer_variantes_producto(datos):
    """Obtiene variantes del documento; reconstruye desde formato antiguo si hace falta."""
    variantes = datos.get("variantes")
    if variantes:
        return variantes
    marca_ant = sanitizar_clave_marca(datos.get("marca", datos.get("condicion", "GENERICO")))
    return {
        marca_ant: {
            "stock": int(datos.get("stock", 0)),
            "ultimo_costo_base": float(datos.get("ultimo_costo_base", 0.0)),
            "precio_interno": float(datos.get("precio_interno", 0.0)),
            "precio_venta": float(datos.get("precio_venta", 0.0)),
            "proveedor": str(datos.get("proveedor", "DESCONOCIDO")),
            "cuit_proveedor": str(datos.get("cuit_proveedor", "0")),
        }
    }


def _asegurar_formato_variantes(ref_prod, datos):
    """Migra producto antiguo (campos planos) al esquema maestro + variantes."""
    if datos.get("variantes"):
        return datos["variantes"]
    variantes = _extraer_variantes_producto(datos)
    updates = {
        "variantes": variantes,
        "ultima_actualizacion": datetime.now(timezone.utc),
    }
    for k in _CAMPOS_FORMATO_ANTIGUO:
        if k in datos:
            updates[k] = firestore.DELETE_FIELD  # type: ignore
    ref_prod.update(updates)
    invalidar_cache_datos()
    return variantes


def clave_linea_factura(codigo_o_art, marca=None):
    """Clave estable para emparejar filas de factura con metadatos de vinculación."""
    if isinstance(codigo_o_art, dict):
        cod = normalizar_codigo_proveedor(
            codigo_o_art.get("codigo_proveedor") or codigo_o_art.get("codigo", "")
        )
        marca = sanitizar_clave_marca(codigo_o_art.get("marca", "GENERICO"))
    else:
        cod = normalizar_codigo_proveedor(codigo_o_art)
        marca = sanitizar_clave_marca(str(marca or "GENERICO"))
    return f"{cod}|{marca}"


def formatear_id_variante(id_maestro, marca):
    return f"{str(id_maestro).strip().upper().replace('/', '-')}_{sanitizar_clave_marca(marca)}"


# --- BACKUP Y RESTAURACIÓN ---
def exportar_inventario_csv():
    inv = obtener_inventario_completo()
    if not inv:
        return None
    filas = []
    for item in inv:
        row = dict(item)
        ubi = row.pop("ubicacion", {}) or {}
        if isinstance(ubi, dict):
            row["pasillo"] = ubi.get("pasillo", 0)
            row["piso"] = ubi.get("piso", 0)
            row["modulo"] = ubi.get("modulo", 0)
            row["fila"] = ubi.get("fila", 0)
        filas.append(row)

    df = pd.DataFrame(filas)
    cols = df.columns.tolist()
    if "id" in cols:
        cols.insert(0, cols.pop(cols.index("id")))
    df = df[cols]

    csv_buffer = df.to_csv(index=False)
    return csv_buffer.encode("utf-8")

def restaurar_inventario_csv(df_csv, modo="sobreescribir"):
    batch = get_db().batch()
    operaciones = 0
    ahora = datetime.now(timezone.utc)
    
    maestros = {}
    for _, row in df_csv.iterrows():
        id_original = str(row.get('id', row.get('codigo', ''))).strip()
        if not id_original or pd.isna(id_original) or id_original == "nan":
            continue
            
        id_m = id_original.split("_")[0] # Extraemos la raíz (código maestro)
        marca = sanitizar_clave_marca(row.get('marca', 'GENERICO'))
        
        if id_m not in maestros:
            ubi = _ubicacion_desde_fila(row)
            maestros[id_m] = {
                "codigo": id_m,
                "descripcion": str(row.get('descripcion', '')),
                "vehiculo": str(row.get('vehiculo', 'UNIVERSAL')).upper(),
                "ubicacion": {
                    "pasillo": int(ubi.get("pasillo", 0)),
                    "piso": int(ubi.get("piso", 0)),
                    "modulo": int(ubi.get("modulo", 0)),
                    "fila": int(ubi.get("fila", 0)),
                },
                "ultima_actualizacion": ahora,
                "variantes": {}
            }
        
        maestros[id_m]["variantes"][marca] = {
            "stock": _entero_fila(row, "stock"),
            "precio_venta": _float_fila(row, "precio_venta"),
            "precio_interno": _float_fila(row, "precio_interno"),
            "ultimo_costo_base": _float_fila(row, "ultimo_costo_base"),
            "proveedor": str(row.get('proveedor', '')),
            "cuit_proveedor": str(row.get('cuit_proveedor', ''))
        }

    for id_m, data in maestros.items():
        ref_prod = get_db().collection("productos").document(id_m)
        
        if modo == "sumar_stock":
            updates = {"ultima_actualizacion": ahora}
            for mrc, vdata in data["variantes"].items():
                if vdata["stock"] != 0:
                    updates[f"variantes.{mrc}.stock"] = firestore.Increment(vdata["stock"]) # type: ignore
            if len(updates) > 1:
                batch.set(ref_prod, updates, merge=True)
        else:
            batch.set(ref_prod, data, merge=True)
            
        operaciones += 1
        batch = _commit_batch_si_lleno(batch, operaciones)

    if operaciones % _BATCH_LIMIT != 0:
        batch.commit()

    invalidar_cache_datos()
    return True, f"Procesados {operaciones} repuestos agrupados en modo '{modo}'."

# --- GESTIÓN DE CLIENTES ---
def obtener_clientes() -> dict:
    docs = get_db().collection("clientes").get()
    return {d.id: d.to_dict() or {} for d in docs}

def cliente_consumidor_final() -> dict:
    return {
        "nombre": "CONSUMIDOR FINAL",
        "cuit": "00000000000",
        "descuento": 0.0,
        "tipo_comprobante": "6",
    }


def configurar_cliente(nombre, cuit_dni, descuento=0.0, tipo_comprobante="6"):
    id_cli = "".join(filter(str.isdigit, str(cuit_dni)))
    if not id_cli:
        return False, "CUIT/DNI inválido."
    cbte = str(tipo_comprobante).strip()
    if cbte not in ("1", "6"):
        cbte = "6"
    get_db().collection("clientes").document(id_cli).set({
        "nombre": str(nombre).upper(),
        "cuit_dni": id_cli,
        "descuento": float(descuento),
        "tipo_comprobante": cbte,
        "actualizado": datetime.now(timezone.utc)
    }, merge=True)
    return True, "Cliente configurado."


def cliente_db_a_activo(datos: dict) -> dict:
    if not isinstance(datos, dict):
        return cliente_consumidor_final()
    cbte = str(datos.get("tipo_comprobante", "6")).strip()
    if cbte not in ("1", "6"):
        cbte = "6"
    cuit = str(datos.get("cuit_dni") or datos.get("cuit") or "00000000000")
    cuit = "".join(filter(str.isdigit, cuit)) or "00000000000"
    return {
        "nombre": str(datos.get("nombre", "CONSUMIDOR FINAL")).upper(),
        "cuit": cuit,
        "descuento": float(datos.get("descuento", 0.0)),
        "tipo_comprobante": cbte,
    }


def guardar_comprobante_arca(vendedor, cliente, respuesta_arca, items, forma_pago, total):
    ref = get_db().collection("comprobantes_arca").document()
    ref.set({
        "vendedor": str(vendedor),
        "cliente": cliente,
        "cae": respuesta_arca.get("cae"),
        "vencimiento_cae": respuesta_arca.get("vencimiento_cae"),
        "punto_venta": respuesta_arca.get("punto_venta"),
        "numero_factura": respuesta_arca.get("numero_factura"),
        "nombre_empresa": respuesta_arca.get("nombre_empresa"),
        "direccion_empresa": respuesta_arca.get("direccion_empresa"),
        "items": items,
        "forma_pago": forma_pago,
        "total": float(total),
        "fecha": datetime.now(timezone.utc),
    })
    return ref.id


def listar_comprobantes_arca(limite=40):
    try:
        docs = list(get_db().collection("comprobantes_arca").limit(200).stream())
    except Exception:
        return []
    items = [{"id": d.id, **(d.to_dict() or {})} for d in docs]
    items.sort(
        key=lambda x: x.get("fecha") or datetime.min.replace(tzinfo=timezone.utc),
        reverse=True,
    )
    return items[:limite]


def eliminar_item_carrito(vendedor, item_id):
    id_item = str(item_id).replace("/", "-")
    ref = (
        get_db().collection("presupuestos_activos")
        .document(str(vendedor))
        .collection("items")
        .document(id_item)
    )
    if not ref.get().exists:
        return False, "Ítem no encontrado en el presupuesto."
    ref.delete()
    return True, "Ítem quitado del presupuesto."


def actualizar_cantidad_item_carrito(vendedor, item_id, nueva_cantidad):
    cant = int(nueva_cantidad)
    id_item = str(item_id).replace("/", "-")
    if cant <= 0:
        return eliminar_item_carrito(vendedor, id_item)

    ref_item = (
        get_db().collection("presupuestos_activos")
        .document(str(vendedor))
        .collection("items")
        .document(id_item)
    )
    doc = ref_item.get()
    if not doc.exists:
        return False, "Ítem no encontrado en el presupuesto."

    ref_prod, id_m, marca, stock_disp, err = _resolver_producto_y_stock(id_item)
    err_val = _validar_resolucion_producto(ref_prod, id_m, marca, stock_disp, err)
    if err_val:
        return False, err_val
    assert stock_disp is not None

    if cant > stock_disp:
        return False, (
            f"Stock insuficiente. Disponible: {stock_disp} u. (pediste {cant})."
        )

    ref_item.update({"cantidad": cant})
    return True, f"Cantidad actualizada a {cant} u."


def actualizar_precio_item_carrito(vendedor, item_id, nuevo_precio):
    precio = float(nuevo_precio)
    if precio < 0:
        return False, "El precio no puede ser negativo."

    id_item = str(item_id).replace("/", "-")
    ref_item = (
        get_db().collection("presupuestos_activos")
        .document(str(vendedor))
        .collection("items")
        .document(id_item)
    )
    if not ref_item.get().exists:
        return False, "Ítem no encontrado en el presupuesto."

    ref_item.update({"precio_unitario": precio})
    return True, f"Precio actualizado a ${precio:,.2f}."


def obtener_comprobante_arca(comp_id):
    if not comp_id:
        return None
    doc = get_db().collection("comprobantes_arca").document(str(comp_id)).get()
    if not doc.exists:
        return None
    return {"id": doc.id, **(doc.to_dict() or {})}


# --- PRESUPUESTOS GUARDADOS (mostrador) ---
def guardar_presupuesto(vendedor, cliente_activo, nota=""):
    carrito = obtener_carrito(vendedor)
    if not carrito:
        return False, "El carrito está vacío.", None

    total_bruto = sum(float(i.get("subtotal", 0)) for i in carrito)
    desc_porc = float((cliente_activo or {}).get("descuento", 0.0))
    total_final = total_bruto * (1 - desc_porc / 100.0)
    cli = cliente_db_a_activo(cliente_activo if isinstance(cliente_activo, dict) else {})

    items_snap = []
    for item in carrito:
        items_snap.append({
            "id": item.get("id"),
            "id_maestro": item.get("id_maestro"),
            "marca": item.get("marca"),
            "descripcion": item.get("descripcion"),
            "precio_unitario": float(item.get("precio_unitario", 0)),
            "cantidad": int(item.get("cantidad", 0)),
            "subtotal": float(item.get("subtotal", 0)),
        })

    ahora = datetime.now(timezone.utc)
    ref = get_db().collection("presupuestos_guardados").document()
    ref.set({
        "vendedor": str(vendedor),
        "cliente": cli,
        "items": items_snap,
        "total_bruto": total_bruto,
        "total_final": total_final,
        "descuento_pct": desc_porc,
        "estado": "abierto",
        "nota": str(nota or "").strip(),
        "creado": ahora,
        "actualizado": ahora,
    })
    return True, f"Presupuesto guardado ({ref.id[:8]}…).", ref.id


def listar_presupuestos_guardados(solo_abiertos=False, limite=40):
    fetch_lim = limite * 5 if solo_abiertos else limite
    docs = (
        get_db().collection("presupuestos_guardados")
        .order_by("creado", direction=firestore.Query.DESCENDING)  # type: ignore
        .limit(fetch_lim)
        .stream()
    )
    out = []
    for d in docs:
        data = d.to_dict() or {}
        if solo_abiertos and data.get("estado") != "abierto":
            continue
        out.append({"id": d.id, **data})
        if len(out) >= limite:
            break
    return out


def obtener_presupuesto_guardado(pres_id):
    if not pres_id:
        return None
    doc = get_db().collection("presupuestos_guardados").document(str(pres_id)).get()
    if not doc.exists:
        return None
    return {"id": doc.id, **(doc.to_dict() or {})}


def actualizar_estado_presupuesto(pres_id, estado):
    estados_ok = ("abierto", "vendido", "facturado", "anulado")
    est = str(estado).strip().lower()
    if est not in estados_ok:
        return False, "Estado inválido."
    ref = get_db().collection("presupuestos_guardados").document(str(pres_id))
    if not ref.get().exists:
        return False, "Presupuesto no encontrado."
    ref.update({"estado": est, "actualizado": datetime.now(timezone.utc)})
    return True, "Estado actualizado."


def eliminar_presupuesto_guardado(pres_id):
    ref = get_db().collection("presupuestos_guardados").document(str(pres_id))
    if not ref.get().exists:
        return False, "Presupuesto no encontrado."
    ref.delete()
    return True, "Presupuesto eliminado."


def reabrir_presupuesto_en_carrito(vendedor, pres_id, reemplazar=True):
    pres = obtener_presupuesto_guardado(pres_id)
    if not pres:
        return False, "Presupuesto no encontrado.", None
    if pres.get("estado") not in ("abierto", None, ""):
        return False, f"El presupuesto ya está marcado como {pres.get('estado')}.", None

    if reemplazar:
        vaciar_carrito(vendedor)

    errores = []
    for item in pres.get("items") or []:
        id_prod = item.get("id")
        cant = int(item.get("cantidad", 1))
        if not id_prod:
            continue
        ok, msg = agregar_al_carrito(vendedor, id_prod, cant)
        if not ok:
            errores.append(f"{item.get('descripcion', id_prod)}: {msg}")

    cliente = pres.get("cliente") or cliente_consumidor_final()
    if errores:
        return True, "Cargado con advertencias de stock:\n" + "\n".join(errores), cliente
    return True, "Presupuesto cargado en el carrito.", cliente

def eliminar_cliente(cuit_dni):
    id_cli = "".join(filter(str.isdigit, str(cuit_dni)))
    get_db().collection("clientes").document(id_cli).delete()
    return True

# --- GESTIÓN DE MARCAS ---
def obtener_marcas() -> list:
    docs = get_db().collection("marcas").get()
    return [d.id for d in docs]

def agregar_marca(nombre):
    id_marca = str(nombre).upper().strip()
    get_db().collection("marcas").document(id_marca).set({
        "creado": datetime.now(timezone.utc)
    })

def eliminar_marca(nombre):
    id_marca = str(nombre).upper().strip()
    get_db().collection("marcas").document(id_marca).delete()
    return True

# --- GESTIÓN DE PROVEEDORES ---
@st.cache_data(ttl=45)
def obtener_proveedores() -> dict:
    docs = get_db().collection("proveedores").get()
    return {d.id: d.to_dict() or {} for d in docs}

def configurar_proveedor(nombre, cuit, recargo_contado=0.0, recargo_30_dias=15.0, descuento=0.0):
    id_prov = "".join(filter(str.isdigit, str(cuit)))
    get_db().collection("proveedores").document(id_prov).set({
        "nombre": str(nombre).upper(),
        "cuit": id_prov,
        "descuento": float(descuento),
        "condiciones": {
            "Contado": float(recargo_contado),
            "30 Días": float(recargo_30_dias)
        }
    }, merge=True)
    invalidar_cache_datos()

def eliminar_proveedor(cuit):
    id_prov = "".join(filter(str.isdigit, str(cuit)))
    get_db().collection("proveedores").document(id_prov).delete()
    invalidar_cache_datos()
    return True

# --- MOTOR DE CÁLCULO DE PRECIOS ---
def calcular_cascada_precios(precio_base, recargo_financiero, descuento_proveedor=0.0) -> dict:
    base = float(precio_base)
    base_con_descuento = base * (1 - (float(descuento_proveedor) / 100.0))
    costo_iva = base_con_descuento * 1.21
    costo_final = costo_iva * (1 + (float(recargo_financiero) / 100.0))
    precio_interno = costo_final * 1.40
    precio_venta = math.ceil(precio_interno / 10.0) * 10
    
    return {
        "costo_iva": round(costo_iva, 2),
        "costo_final": round(costo_final, 2),
        "precio_interno": round(precio_interno, 2),
        "precio_venta": int(precio_venta)
    }

# --- AYUDANTE PARA MEMORIA DE DESCRIPCIONES ---
def obtener_producto_por_codigo(codigo_base):
    cod_limpio = str(codigo_base).strip().upper().replace("/", "-").split("_")[0]
    docs = get_db().collection("productos").where("codigo", "==", cod_limpio).limit(1).get()
    if docs:
        datos = docs[0].to_dict() or {}
        datos['id'] = docs[0].id
        return datos
    return None


def _descomponer_id_variante(id_producto):
    """
    Separa ID variante (CODIGO_MARCA) probando prefijos contra Firebase.
    Soporta códigos maestros que contienen guion bajo (ej: ABC_123 + SKF).
    Retorna (id_maestro, marca_o_None, ref_prod_o_None).
    """
    id_full = str(id_producto or "").strip().upper().replace("/", "-")
    if not id_full:
        return None, None, None

    def doc_existe(id_cand):
        ref = get_db().collection("productos").document(id_cand)
        return ref, ref.get().exists

    ref, ok = doc_existe(id_full)
    if ok:
        return id_full, None, ref

    if "_" not in id_full:
        docs = get_db().collection("productos").where("codigo", "==", id_full).limit(1).get()
        if docs:
            ref = get_db().collection("productos").document(docs[0].id)
            return docs[0].id, None, ref
        return id_full, None, None

    partes = id_full.split("_")
    for i in range(len(partes) - 1, 0, -1):
        id_cand = "_".join(partes[:i])
        marca_cand = "_".join(partes[i:])
        ref, ok = doc_existe(id_cand)
        if ok:
            datos = ref.get().to_dict() or {}
            variantes = datos.get("variantes", {})
            if variantes and marca_cand in variantes:
                return id_cand, marca_cand, ref
            if not variantes:
                return id_cand, marca_cand, ref

        docs = get_db().collection("productos").where("codigo", "==", id_cand).limit(1).get()
        if docs:
            ref = get_db().collection("productos").document(docs[0].id)
            datos = ref.get().to_dict() or {}
            variantes = datos.get("variantes", {})
            if not variantes or marca_cand in variantes:
                return docs[0].id, marca_cand, ref

    id_m = partes[0]
    marca = "_".join(partes[1:]) if len(partes) > 1 else None
    ref, ok = doc_existe(id_m)
    if ok:
        return id_m, marca, ref
    return id_m, marca, None


def id_equivalencia(cuit, codigo_proveedor):
    cuit_l = "".join(filter(str.isdigit, str(cuit)))
    cod = normalizar_codigo_proveedor(codigo_proveedor)
    return f"{cuit_l}_{cod}" if cuit_l and cod else ""


def buscar_equivalencia(cuit, codigo_proveedor):
    eq_id = id_equivalencia(cuit, codigo_proveedor)
    if not eq_id:
        return None
    doc = get_db().collection("equivalencias").document(eq_id).get()
    if doc.exists:
        data = doc.to_dict() or {}
        data["id"] = doc.id
        return data
    return None


def guardar_equivalencia(cuit, codigo_proveedor, id_maestro, marca_variante,
                         descripcion_proveedor="", marca_proveedor="", origen="manual"):
    eq_id = id_equivalencia(cuit, codigo_proveedor)
    id_m = str(id_maestro).strip().upper().replace("/", "-")
    marca_v = str(marca_variante).strip().upper()
    if not eq_id or not id_m:
        return False

    prod = get_db().collection("productos").document(id_m).get()
    desc_maestro = ""
    if prod.exists:
        desc_maestro = (prod.to_dict() or {}).get("descripcion", "")

    get_db().collection("equivalencias").document(eq_id).set({
        "cuit_proveedor": "".join(filter(str.isdigit, str(cuit))),
        "codigo_proveedor": normalizar_codigo_proveedor(codigo_proveedor),
        "id_maestro": id_m,
        "marca_variante": marca_v,
        "descripcion_proveedor": str(descripcion_proveedor),
        "marca_proveedor": str(marca_proveedor).upper(),
        "descripcion_maestro": desc_maestro,
        "origen": origen,
        "actualizado": datetime.now(timezone.utc),
    }, merge=True)
    return True


def listar_maestros_para_busqueda(termino="", limite=40):
    inv = obtener_inventario_completo()
    grupos = {}
    termino_norm = _norm_busqueda(termino) if termino else ""
    terminos = [t for t in termino_norm.split() if len(t) >= 2]

    for item in inv:
        if not isinstance(item, dict):
            continue
        id_m = item.get("id_maestro") or item.get("codigo")
        if not id_m:
            continue
        if id_m not in grupos:
            grupos[id_m] = {
                "id_maestro": id_m,
                "codigo": item.get("codigo", id_m),
                "descripcion": item.get("descripcion", ""),
                "vehiculo": item.get("vehiculo", "UNIVERSAL"),
                "marcas": [],
            }
        marca = item.get("marca", "GENERICO")
        if marca not in grupos[id_m]["marcas"]:
            grupos[id_m]["marcas"].append(marca)

    resultado = list(grupos.values())
    if terminos:
        filtrados = []
        for g in resultado:
            texto = _norm_busqueda(
                f"{g['codigo']} {g['descripcion']} {g['vehiculo']} {' '.join(g['marcas'])}"
            )
            if all(termino_en_texto(t, texto) for t in terminos):
                filtrados.append(g)
        resultado = filtrados

    return resultado[:limite]


def guardar_control_remito(cuit, proveedor, num_factura, num_remito, resultado):
    cuit_l = "".join(filter(str.isdigit, str(cuit)))
    ts = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    ctrl_id = f"CTRL_{cuit_l}_{ts}"
    get_db().collection("controles_remito").document(ctrl_id).set({
        "cuit_proveedor": cuit_l,
        "proveedor": str(proveedor).upper(),
        "numero_factura": str(num_factura),
        "numero_remito": str(num_remito),
        "fecha": datetime.now(timezone.utc),
        "resumen": resultado.get("resumen", {}),
        "coinciden": resultado.get("coinciden", []),
        "dif_cantidad": resultado.get("dif_cantidad", []),
        "faltan_en_remito": resultado.get("faltan_en_remito", []),
        "sobran_en_remito": resultado.get("sobran_en_remito", []),
    })
    return ctrl_id


# --- CARGA DE MERCADERÍA REFORZADA (LÓGICA MAESTRO -> VARIANTE) ---
def registrar_ingreso_inteligente(datos_ia, condicion_pago, imagen_url=None):
    prov_id = "".join(filter(str.isdigit, str(datos_ia.get('cuit_proveedor', '0'))))
    if not prov_id: prov_id = "0"
        
    pv = str(datos_ia.get('punto_venta', '0')).zfill(5)
    num = str(datos_ia.get('numero_comprobante', '0')).zfill(8)
    id_factura = f"FACT_{prov_id}_{pv}_{num}"
    
    doc_factura = get_db().collection("facturas_procesadas").document(id_factura).get()
    if doc_factura.exists:
        datos_fac = doc_factura.to_dict() or {}
        fecha_str = formatear_fecha_ar(datos_fac.get("fecha_carga")) or "una fecha desconocida"
        return False, f"La factura {pv}-{num} ya fue cargada previamente el {fecha_str} (hora Argentina)."

    prov_doc = get_db().collection("proveedores").document(prov_id).get()
    if not prov_doc.exists:
        return False, "Proveedor no configurado (CUIT inexistente)."
    
    datos_prov = prov_doc.to_dict() or {}
    condiciones = datos_prov.get("condiciones", {})
    recargo = float(condiciones.get(condicion_pago, 0.0))
    descuento_prov = float(datos_prov.get("descuento", 0.0))

    articulos = datos_ia.get('articulos', [])
    if not articulos:
        return False, "La factura no tiene artículos."

    ahora = datetime.now(timezone.utc)
    batch = get_db().batch()
    operaciones = 0
    nuevos = 0
    actualizados = 0

    for art in articulos:
        if not isinstance(art, dict):
            continue
        codigo_prov = normalizar_codigo_proveedor(art.get('codigo', ''))
        codigo_base = codigo_prov
        if not codigo_base:
            codigo_base = str(art.get('descripcion', 'ART')).replace(' ', '_').upper()[:15].replace("/", "-")
        if not codigo_base:
            return False, "Hay artículos sin código ni descripción."

        marca_rep = sanitizar_clave_marca(art.get('marca', art.get('condicion', 'GENERICO')))
        vehiculos_rep = normalizar_lista_vehiculos(art.get('vehiculos') or art.get('vehiculo'))
        vehiculo_rep = vehiculos_a_texto(vehiculos_rep)
        proveedor = str(art.get('proveedor', 'DESCONOCIDO')).upper()
        cuit_proveedor = prov_id
        precio_unitario = float(art.get('precio_unitario', 0.0))
        cantidad = int(art.get('cantidad', 0))

        calculos = calcular_cascada_precios(precio_unitario, recargo, descuento_prov)
        ref_prod = get_db().collection("productos").document(codigo_base)

        snap_existente = ref_prod.get()
        datos_existentes = snap_existente.to_dict() if snap_existente.exists else None

        payload = {
            "codigo": codigo_base,
            "ultima_actualizacion": ahora,
            "variantes": {
                marca_rep: {
                    "stock": firestore.Increment(cantidad), # type: ignore
                    "ultimo_costo_base": precio_unitario,
                    "precio_interno": calculos['precio_interno'],
                    "precio_venta": calculos['precio_venta'],
                    "proveedor": proveedor,
                    "cuit_proveedor": cuit_proveedor
                }
            }
        }
        if datos_existentes:
            actualizados += 1
        else:
            nuevos += 1
            payload["descripcion"] = str(art.get('descripcion', 'Repuesto'))
            payload["vehiculos"] = vehiculos_rep
            payload["vehiculo"] = vehiculo_rep

        batch.set(ref_prod, payload, merge=True)
        operaciones += 1
        batch = _commit_batch_si_lleno(batch, operaciones)

    batch.set(get_db().collection("facturas_procesadas").document(id_factura), {
        "proveedor_id": prov_id,
        "pv": pv,
        "num": num,
        "fecha_carga": ahora,
        "factura_imagen": imagen_url
    })
    operaciones += 1

    if operaciones % _BATCH_LIMIT != 0:
        batch.commit()
    invalidar_cache_datos()
    partes = ["Mercadería cargada correctamente."]
    if actualizados:
        partes.append(
            f"{actualizados} código(s) ya existían: se actualizó stock/precio "
            f"(descripción y vehículos conservados)."
        )
    if nuevos:
        partes.append(f"{nuevos} código(s) nuevos.")
    return True, " ".join(partes)

def alta_manual_producto(codigo, condicion, vehiculo, descripcion, cuit_proveedor, precio_base, recargo, stock, pasillo, piso, modulo, fila):
    codigo_base = str(codigo).strip().upper().replace("/", "-")
    marca_limpia = sanitizar_clave_marca(condicion)
    vehiculos_limpios = normalizar_lista_vehiculos(vehiculo)
    veh_limpio = vehiculos_a_texto(vehiculos_limpios)

    if not codigo_base: return False, "Código de producto inválido."

    ref_prod = get_db().collection("productos").document(codigo_base)

    cuit_prov_limpio = "".join(filter(str.isdigit, str(cuit_proveedor)))
    if not cuit_prov_limpio: cuit_prov_limpio = "0"
        
    prov_doc = get_db().collection("proveedores").document(cuit_prov_limpio).get()
    datos_proveedor_db = prov_doc.to_dict() or {} 
    nombre_proveedor = datos_proveedor_db.get("nombre", "DESCONOCIDO") if prov_doc.exists else "DESCONOCIDO"
    descuento_prov = float(datos_proveedor_db.get("descuento", 0.0))

    calculos = calcular_cascada_precios(float(precio_base), float(recargo), descuento_prov)
    ahora = datetime.now(timezone.utc)

    ref_prod.set({
        "codigo": codigo_base,
        "descripcion": str(descripcion).strip(),
        "vehiculos": vehiculos_limpios,
        "vehiculo": veh_limpio,
        "ubicacion": {
            "pasillo": int(pasillo),
            "piso": int(piso),
            "modulo": int(modulo),
            "fila": int(fila)
        },
        "ultima_actualizacion": ahora,
        "variantes": {
            marca_limpia: {
                "stock": int(stock),
                "ultimo_costo_base": float(precio_base),
                "precio_interno": calculos['precio_interno'],
                "precio_venta": calculos['precio_venta'],
                "proveedor": nombre_proveedor,
                "cuit_proveedor": cuit_prov_limpio
            }
        }
    }, merge=True)

    invalidar_cache_datos()
    return True, f"Repuesto {codigo_base} guardado bajo la variante {marca_limpia}."


def actualizar_ubicacion_relevamiento(id_producto, pasillo=None, piso=None, modulo=None, fila=None):
    id_limpio = str(id_producto).strip().upper().replace("/", "-").split("_")[0] # Toma el maestro
    ref_prod = get_db().collection("productos").document(id_limpio)
    
    if not ref_prod.get().exists:
        docs_codigo = get_db().collection("productos").where("codigo", "==", id_limpio).get()
        if docs_codigo:
            ref_prod = get_db().collection("productos").document(docs_codigo[0].id)
        else:
            return False, f"El código '{id_limpio}' no existe en el sistema."
            
    updates = {}
    if pasillo is not None: updates["ubicacion.pasillo"] = int(pasillo)
    if piso is not None: updates["ubicacion.piso"] = int(piso)
    if modulo is not None: updates["ubicacion.modulo"] = int(modulo)
    if fila is not None: updates["ubicacion.fila"] = int(fila)
    updates["ultima_actualizacion"] = datetime.now(timezone.utc)
    
    if updates:
        ref_prod.update(updates)
        invalidar_cache_datos()
        return True, "Ubicación de inventario actualizada."
    return False, "No se detectaron datos de ubicación válidos en la orden."


def agregar_texto_descripcion(codigo, texto_a_sumar):
    """Concatena texto a la descripción del artículo maestro."""
    cod = normalizar_codigo_proveedor(codigo)
    if not cod:
        return False, "Código inválido."
    texto = str(texto_a_sumar or "").strip()
    if not texto:
        return False, "No hay texto para agregar."

    ref_prod, id_m = _obtener_ref_producto_maestro(cod, id_maestro=cod)
    if not ref_prod:
        docs = get_db().collection("productos").where("codigo", "==", cod).limit(1).get()
        if docs:
            ref_prod = get_db().collection("productos").document(docs[0].id)
            id_m = docs[0].id
        else:
            return False, f"No encontré el código '{cod}' en el inventario."

    snap = ref_prod.get()
    if not snap.exists:
        return False, f"No encontré el código '{cod}' en el inventario."

    desc_actual = str((snap.to_dict() or {}).get("descripcion", "")).strip()
    nueva = f"{desc_actual} {texto}".strip() if desc_actual else texto
    ref_prod.update({
        "descripcion": nueva,
        "ultima_actualizacion": datetime.now(timezone.utc),
    })
    invalidar_cache_datos()
    return True, f"Descripción de {id_m or cod} actualizada: \"{nueva[:100]}{'...' if len(nueva) > 100 else ''}\""


def reemplazar_descripcion_maestro(codigo, nueva_descripcion):
    """Reemplaza por completo la descripción del artículo maestro."""
    cod = normalizar_codigo_proveedor(codigo)
    if not cod:
        return False, "Código inválido."
    nueva = str(nueva_descripcion or "").strip()
    if not nueva:
        return False, "La descripción no puede estar vacía."

    ref_prod, id_m = _obtener_ref_producto_maestro(cod, id_maestro=cod)
    if not ref_prod:
        docs = get_db().collection("productos").where("codigo", "==", cod).limit(1).get()
        if docs:
            ref_prod = get_db().collection("productos").document(docs[0].id)
            id_m = docs[0].id
        else:
            return False, f"No encontré el código '{cod}' en el inventario."

    snap = ref_prod.get()
    if not snap.exists:
        return False, f"No encontré el código '{cod}' en el inventario."

    ref_prod.update({
        "descripcion": nueva,
        "ultima_actualizacion": datetime.now(timezone.utc),
    })
    invalidar_cache_datos()
    return True, f"Descripción de {id_m or cod} reemplazada."


def edicion_masiva_descripcion(items, modo, texto):
    """Agrega o reemplaza descripción en códigos maestro únicos del listado."""
    texto = str(texto or "").strip()
    if not texto:
        return False, "Indicá el texto."
    if modo not in ("agregar", "reemplazar"):
        return False, "Modo inválido."

    maestros = {}
    for item in items or []:
        if not isinstance(item, dict):
            continue
        key = str(item.get("id_maestro") or item.get("codigo") or "").strip()
        if key:
            maestros[key] = str(item.get("codigo") or key).strip()
    if not maestros:
        return False, "No hay artículos en la selección."

    ok = 0
    errores = []
    for cod in maestros.values():
        if modo == "agregar":
            success, msg = agregar_texto_descripcion(cod, texto)
        else:
            success, msg = reemplazar_descripcion_maestro(cod, texto)
        if success:
            ok += 1
        else:
            errores.append(f"{cod}: {msg}")

    if ok == 0:
        return False, errores[0] if errores else "No se pudo actualizar ninguna descripción."
    msg = f"Descripción actualizada en {ok} código(s) maestro(s)."
    if errores:
        msg += f" {len(errores)} error(es)."
    return True, msg


def edicion_masiva_marca(items, marca_nueva):
    """Cambia marca en códigos del listado (solo los que tienen una sola variante)."""
    marca = str(marca_nueva or "").strip()
    if not marca:
        return False, "Indicá la marca nueva."

    codigos = {}
    for item in items or []:
        if not isinstance(item, dict):
            continue
        cod = str(item.get("codigo") or item.get("id_maestro") or "").strip()
        if cod:
            codigos[cod] = cod
    if not codigos:
        return False, "No hay artículos en la selección."

    ok = 0
    omitidos = 0
    errores = []
    for cod in codigos.values():
        success, msg = cambiar_marca_por_codigo(cod, marca)
        if success:
            ok += 1
        elif "una sola" in msg.lower() or "exactamente" in msg.lower():
            omitidos += 1
        else:
            errores.append(f"{cod}: {msg}")

    if ok == 0 and omitidos == 0:
        return False, errores[0] if errores else "No se pudo cambiar ninguna marca."
    msg = f"Marca actualizada en {ok} código(s)."
    if omitidos:
        msg += f" {omitidos} omitido(s) por tener varias marcas."
    if errores:
        msg += f" {len(errores)} error(es)."
    return True, msg


def cambiar_marca_por_codigo(codigo, marca_nueva):
    """
    Renombra la variante de un código maestro.
    Solo permitido cuando el artículo tiene exactamente una marca.
    """
    cod = normalizar_codigo_proveedor(codigo)
    if not cod:
        return False, "Código inválido."

    if not str(marca_nueva or "").strip():
        return False, "Indicá la marca nueva."

    nueva_marca = sanitizar_clave_marca(marca_nueva)
    if not nueva_marca:
        return False, "Marca nueva inválida."

    ref_prod, id_m = _obtener_ref_producto_maestro(cod, id_maestro=cod)
    if not ref_prod:
        docs = get_db().collection("productos").where("codigo", "==", cod).limit(1).get()
        if docs:
            ref_prod = get_db().collection("productos").document(docs[0].id)
            id_m = docs[0].id
        else:
            return False, f"No encontré el código '{cod}' en el inventario."

    snap = ref_prod.get()
    if not snap.exists:
        return False, f"No encontré el código '{cod}' en el inventario."

    datos = snap.to_dict() or {}
    era_antiguo = "variantes" not in datos
    variantes = _extraer_variantes_producto(datos)

    marcas = list(variantes.keys())
    if len(marcas) != 1:
        lista = ", ".join(marcas)
        return False, (
            f"El código '{cod}' tiene {len(marcas)} marcas ({lista}). "
            "El asistente solo cambia marca cuando hay una sola variante por código."
        )

    marca_actual = marcas[0]
    if nueva_marca == marca_actual:
        if era_antiguo:
            _asegurar_formato_variantes(ref_prod, datos)
        return True, f"El código {cod} ya tiene marca {marca_actual}."

    id_m = id_m or cod
    id_viejo = formatear_id_variante(id_m, marca_actual)
    id_nuevo = formatear_id_variante(id_m, nueva_marca)
    ahora = datetime.now(timezone.utc)

    if era_antiguo:
        updates = {
            "variantes": {nueva_marca: variantes[marca_actual]},
            "ultima_actualizacion": ahora,
        }
        for k in _CAMPOS_FORMATO_ANTIGUO:
            if k in datos:
                updates[k] = firestore.DELETE_FIELD  # type: ignore
    else:
        updates = {
            f"variantes.{nueva_marca}": variantes[marca_actual],
            f"variantes.{marca_actual}": firestore.DELETE_FIELD,  # type: ignore
            "ultima_actualizacion": ahora,
        }

    ref_prod.update(updates)
    invalidar_cache_datos()
    msg_extra = " (migrado a formato nuevo)" if era_antiguo else ""
    return True, (
        f"Marca de {cod} actualizada: {marca_actual} → {nueva_marca}{msg_extra}. "
        f"Nuevo ID: {id_nuevo} (antes {id_viejo})."
    )


def _lookup_maestro_por_codigo(cod):
    """Busca documento maestro por código. Retorna (ref, id_m, datos) o (None, None, None)."""
    ref_prod, id_m = _obtener_ref_producto_maestro(cod, id_maestro=cod)
    if not ref_prod:
        docs = get_db().collection("productos").where("codigo", "==", cod).limit(1).get()
        if docs:
            ref_prod = get_db().collection("productos").document(docs[0].id)
            id_m = docs[0].id
        else:
            return None, None, None
    snap = ref_prod.get()
    if not snap.exists:
        return None, None, None
    return ref_prod, id_m, snap.to_dict() or {}


def cambiar_vehiculos_por_codigo(codigo, vehiculos, modo="reemplazar"):
    """
    Actualiza vehículos compatibles del artículo maestro.
    modo: reemplazar | agregar | quitar
    """
    cod = normalizar_codigo_proveedor(codigo)
    if not cod:
        return False, "Código inválido."

    modo_l = str(modo or "reemplazar").strip().lower()
    if modo_l not in ("reemplazar", "agregar", "quitar"):
        modo_l = "reemplazar"

    if vehiculos is None or (isinstance(vehiculos, list) and len(vehiculos) == 0):
        if modo_l == "reemplazar":
            return False, "Indicá al menos un vehículo."
        vehiculos = []

    ref_prod, id_m, datos = _lookup_maestro_por_codigo(cod)
    if not ref_prod or datos is None:
        return False, f"No encontré el código '{cod}' en el inventario."

    if not datos.get("variantes"):
        _asegurar_formato_variantes(ref_prod, datos)
        snap = ref_prod.get()
        datos = snap.to_dict() or {}

    actuales = datos.get("vehiculos") or datos.get("vehiculo")
    resultado = combinar_vehiculos(actuales, vehiculos, modo_l)
    texto = vehiculos_a_texto(resultado)

    if normalizar_lista_vehiculos(actuales) == resultado:
        return True, f"El código {cod} ya tiene vehículos: {texto}."

    ref_prod.update({
        "vehiculos": resultado,
        "vehiculo": texto,
        "ultima_actualizacion": datetime.now(timezone.utc),
    })
    invalidar_cache_datos()

    verbos = {"reemplazar": "actualizados", "agregar": "agregados", "quitar": "quitados"}
    return True, f"Código {cod}: vehículos {verbos.get(modo_l, 'actualizados')} → {texto}."


def _obtener_ref_producto_maestro(id_producto, id_maestro=None):
    """Resuelve el documento Firebase del artículo maestro (el ID del doc puede ≠ campo codigo)."""
    vistos = set()
    id_full = str(id_producto or "").strip().replace("/", "-")

    def probar(id_cand):
        id_c = str(id_cand or "").strip().replace("/", "-")
        if not id_c or id_c in vistos:
            return None, None
        vistos.add(id_c)
        ref = get_db().collection("productos").document(id_c)
        if ref.get().exists:
            return ref, id_c
        return None, None

    for candidato in (id_maestro, id_full):
        ref, id_m = probar(candidato)
        if ref:
            return ref, id_m

    if "_" in id_full:
        ref, id_m = probar(id_full.rsplit("_", 1)[0])
        if ref:
            return ref, id_m

    codigo_buscar = str(id_maestro or "").strip().replace("/", "-")
    if not codigo_buscar and "_" in id_full:
        codigo_buscar = id_full.rsplit("_", 1)[0]
    if codigo_buscar:
        docs = get_db().collection("productos").where("codigo", "==", codigo_buscar).limit(1).get()
        if docs:
            return get_db().collection("productos").document(docs[0].id), docs[0].id

    return None, None


def _extraer_marca_variante(id_producto, id_maestro):
    id_m, marca, _ = _descomponer_id_variante(id_producto)
    if id_maestro:
        prefijo = f"{str(id_maestro).strip().replace('/', '-')}_"
        id_full = str(id_producto or "").strip().replace("/", "-")
        if id_full.startswith(prefijo):
            return id_full[len(prefijo):].upper()
    if marca:
        return str(marca).upper()
    return "GENERICO"


def _normalizar_marca_en_variantes(marca_req, variantes, id_producto=None):
    """Corrige marcas mal parseadas (ej. GENERICO_GENERICO → GENERICO)."""
    if not variantes:
        return marca_req
    if marca_req and marca_req in variantes:
        return marca_req
    if not marca_req:
        if len(variantes) == 1:
            return list(variantes.keys())[0]
        return marca_req

    id_up = str(id_producto or "").upper().replace("/", "-")
    coincidencias_sufijo = []
    for clave in variantes:
        suf = f"_{str(clave).upper()}"
        if id_up.endswith(suf):
            coincidencias_sufijo.append(clave)
    if len(coincidencias_sufijo) == 1:
        return coincidencias_sufijo[0]

    for parte in reversed(str(marca_req).split("_")):
        if parte in variantes:
            return parte

    marca_up = str(marca_req).upper()
    for clave in variantes:
        if str(clave).upper() in marca_up or marca_up in str(clave).upper():
            return clave
    return marca_req


def _resolver_producto_y_stock(id_producto):
    """
    Resuelve documento Firebase y stock disponible para un ID variante (CODIGO_MARCA).
    Retorna (ref_prod, id_m, marca, stock, error). error es None si todo OK.
    """
    id_m, marca_req, ref_prod = _descomponer_id_variante(id_producto)

    if not ref_prod:
        docs_codigo = get_db().collection("productos").where("codigo", "==", id_m).limit(1).get()
        if docs_codigo:
            ref_prod = get_db().collection("productos").document(docs_codigo[0].id)
            id_m = docs_codigo[0].id
        else:
            return None, None, None, None, f"El código '{id_m}' no se encontró en el inventario."

    doc = ref_prod.get()
    if not doc.exists:
        return None, None, None, None, f"El código '{id_m}' no se encontró en el inventario."

    datos = doc.to_dict() or {}
    variantes = datos.get("variantes", {})

    if not variantes:
        marca_ant = datos.get("marca", datos.get("condicion", "GENERICO"))
        return ref_prod, id_m, marca_ant, int(datos.get("stock", 0)), None

    marca_req = _normalizar_marca_en_variantes(marca_req, variantes, id_producto)

    if not marca_req:
        if len(variantes) == 1:
            marca_req = list(variantes.keys())[0]
        else:
            return None, None, None, None, (
                "Múltiples marcas para este repuesto. Indicá el ID exacto (CODIGO_MARCA)."
            )

    if marca_req not in variantes:
        return None, None, None, None, f"La marca '{marca_req}' no se encontró en este repuesto."

    stock = int(variantes[marca_req].get("stock", 0))
    if stock <= 0:
        return None, None, None, None, (
            f"Sin stock disponible para '{id_m}' ({marca_req}). Quedan 0 unidades."
        )

    return ref_prod, id_m, marca_req, stock, None


def _validar_resolucion_producto(ref_prod, id_m, marca, stock, err):
    if err:
        return err
    if ref_prod is None or id_m is None or marca is None or stock is None:
        return "Producto no encontrado."
    return None


def actualizar_producto_desde_grilla(id_producto, campo, nuevo_valor, id_maestro=None, marca=None):
    ref_prod, id_m = _obtener_ref_producto_maestro(id_producto, id_maestro)
    if not ref_prod:
        return False, "Producto no encontrado."

    marca_key = str(marca or "").strip().upper()
    if not marca_key:
        marca_key = _extraer_marca_variante(id_producto, id_m)

    doc = ref_prod.get()

    datos = doc.to_dict() or {}
    if not datos.get("variantes"):
        variantes = _asegurar_formato_variantes(ref_prod, datos)
    else:
        variantes = datos.get("variantes", {})
    ahora = datetime.now(timezone.utc)

    if campo == "Marca":
        nueva_marca = sanitizar_clave_marca(nuevo_valor)
        marca_key = sanitizar_clave_marca(marca_key)
        if nueva_marca == marca_key:
            return True, "OK"
        if marca_key not in variantes:
            return False, f"La variante '{marca_key}' no existe."
        if nueva_marca in variantes:
            return False, f"Ya existe la marca '{nueva_marca}' en este artículo."
        ref_prod.update({
            f"variantes.{nueva_marca}": variantes[marca_key],
            f"variantes.{marca_key}": firestore.DELETE_FIELD,  # type: ignore
            "ultima_actualizacion": ahora
        })
        invalidar_cache_datos()
        return True, "OK"

    if campo == "Vehículo":
        cod_maestro = str(datos.get("codigo") or id_m or "").strip()
        if not cod_maestro:
            return False, "No se pudo resolver el código maestro."
        return cambiar_vehiculos_por_codigo(cod_maestro, nuevo_valor, modo="reemplazar")

    mapa_campos = {
        "Descripción": "descripcion",
        "Pasillo": "ubicacion.pasillo",
        "Piso": "ubicacion.piso",
        "Módulo": "ubicacion.modulo",
        "Fila": "ubicacion.fila",
        "Stock": f"variantes.{marca_key}.stock",
        "Precio Final": f"variantes.{marca_key}.precio_venta",
    }

    campo_db = mapa_campos.get(campo)
    if not campo_db:
        return False, "Campo no editable."

    if campo in ["Stock", "Pasillo", "Piso", "Módulo", "Fila", "Precio Final"]:
        nuevo_valor = int(nuevo_valor)
    else:
        nuevo_valor = str(nuevo_valor)

    if campo in ("Stock", "Precio Final") and variantes and marca_key not in variantes:
        return False, f"La variante '{marca_key}' no existe en este artículo."

    updates = {campo_db: nuevo_valor, "ultima_actualizacion": ahora}
    ref_prod.update(updates)
    invalidar_cache_datos()
    return True, "OK"

# --- ASISTENTE DE DEPÓSITO ---
def registrar_merma(id_producto, cantidad):
    cant = int(cantidad)
    if cant <= 0:
        return False, "La cantidad debe ser mayor a cero."

    ref_prod, id_m, marca_req, stock_disp, err = _resolver_producto_y_stock(id_producto)
    err_val = _validar_resolucion_producto(ref_prod, id_m, marca_req, stock_disp, err)
    if err_val:
        return False, err_val
    assert ref_prod is not None and id_m is not None and marca_req is not None and stock_disp is not None

    if cant > stock_disp:
        return False, (
            f"Stock insuficiente para dar de baja {cant} u. "
            f"(disponible: {stock_disp}, marca {marca_req})."
        )

    datos = ref_prod.get().to_dict() or {}
    variantes = datos.get("variantes", {})

    if not variantes:
        batch = get_db().batch()
        batch.update(ref_prod, {"stock": firestore.Increment(-cant), "ultima_actualizacion": datetime.now(timezone.utc)}) # type: ignore
        batch.commit()
        invalidar_cache_datos()
        return True, f"Baja de {cant} unidades registrada."

    batch = get_db().batch()
    batch.update(ref_prod, {
        f"variantes.{marca_req}.stock": firestore.Increment(-cant), # type: ignore
        "ultima_actualizacion": datetime.now(timezone.utc)
    })

    ref_baja = get_db().collection("auditoria_mermas").document()
    batch.set(ref_baja, {
        "id_producto": id_m,
        "marca": marca_req,
        "cantidad_baja": cant,
        "fecha": datetime.now(timezone.utc),
        "motivo": "Ajuste reportado vía Asistente de Voz"
    })

    batch.commit()
    invalidar_cache_datos()
    return True, f"Baja de {cant} unidades en marca {marca_req} registrada."

def registrar_aumento_stock(id_producto, cantidad):
    partes = str(id_producto).strip().upper().replace("/", "-").split("_", 1)
    id_m = partes[0]
    marca_req = partes[1] if len(partes) > 1 else None
    
    ref_prod = get_db().collection("productos").document(id_m)
    doc = ref_prod.get()
    
    if not doc.exists:
        docs_codigo = get_db().collection("productos").where("codigo", "==", id_m).get()
        if docs_codigo:
            ref_prod = get_db().collection("productos").document(docs_codigo[0].id)
            doc = ref_prod.get()
            id_m = doc.id
        else:
            return False, f"El código '{id_m}' no existe en el sistema."
            
    datos = doc.to_dict() or {}
    variantes = datos.get("variantes", {})
    
    if not variantes:
        batch = get_db().batch()
        batch.update(ref_prod, {"stock": firestore.Increment(int(cantidad)), "ultima_actualizacion": datetime.now(timezone.utc)}) # type: ignore
        batch.commit()
        invalidar_cache_datos()
        return True, f"Aumento de {cantidad} unidades registrado."

    if not marca_req:
        if len(variantes) == 1:
            marca_req = list(variantes.keys())[0]
        else:
            return False, f"Múltiples marcas para este repuesto. Por favor, dictá el código exacto o usá ingreso manual."
            
    if marca_req not in variantes:
        return False, f"La marca '{marca_req}' no se encontró en este repuesto."
        
    batch = get_db().batch()
    batch.update(ref_prod, {
        f"variantes.{marca_req}.stock": firestore.Increment(int(cantidad)), # type: ignore
        "ultima_actualizacion": datetime.now(timezone.utc)
    })
    
    ref_alta = get_db().collection("auditoria_ingresos").document()
    batch.set(ref_alta, {
        "id_producto": id_m,
        "marca": marca_req,
        "cantidad_ingreso": int(cantidad),
        "fecha": datetime.now(timezone.utc),
        "motivo": "Ingreso manual vía Asistente de Voz"
    })
    
    batch.commit()
    invalidar_cache_datos()
    return True, f"Aumento de {cantidad} unidades en marca {marca_req} registrado exitosamente."

# --- INVENTARIO Y VENTAS ---
@st.cache_data(ttl=45)
def obtener_inventario_completo() -> list:
    docs = get_db().collection("productos").get()
    inventario = []
    for d in docs:
        master = d.to_dict() or {}
        master_id = d.id
        vehs_master = normalizar_lista_vehiculos(master.get("vehiculos") or master.get("vehiculo"))
        veh_texto = vehiculos_a_texto(vehs_master)
        veh_busqueda = vehiculos_en_busqueda(vehs_master)

        if "variantes" not in master:
            # Producto formato viejo, lo aplanamos para que no rompa app.py
            marca_ant = master.get("marca", master.get("condicion", "GENERICO"))
            item = {
                "id": f"{master_id}_{marca_ant}",
                "id_maestro": master_id,
                "codigo": master.get("codigo", master_id),
                "descripcion": master.get("descripcion", ""),
                "vehiculos": vehs_master,
                "vehiculo": veh_texto,
                "vehiculos_busqueda": veh_busqueda,
                "marca": marca_ant,
                "stock": master.get("stock", 0),
                "precio_venta": master.get("precio_venta", 0.0),
                "precio_interno": master.get("precio_interno", 0.0),
                "ultimo_costo_base": master.get("ultimo_costo_base", 0.0),
                "proveedor": master.get("proveedor", "DESCONOCIDO"),
                "cuit_proveedor": master.get("cuit_proveedor", "0"),
                "ubicacion": master.get("ubicacion", {"pasillo": 0, "piso": 0, "modulo": 0, "fila": 0})
            }
            inventario.append(item)
        else:
            # Producto maestro con variantes, generamos una fila por cada variante
            for marca, v_data in master["variantes"].items():
                item = {
                    "id": f"{master_id}_{marca}",
                    "id_maestro": master_id,
                    "codigo": master.get("codigo", master_id),
                    "descripcion": master.get("descripcion", ""),
                    "vehiculos": vehs_master,
                    "vehiculo": veh_texto,
                    "vehiculos_busqueda": veh_busqueda,
                    "marca": marca,
                    "stock": v_data.get("stock", 0),
                    "precio_venta": v_data.get("precio_venta", 0.0),
                    "precio_interno": v_data.get("precio_interno", 0.0),
                    "ultimo_costo_base": v_data.get("ultimo_costo_base", 0.0),
                    "proveedor": v_data.get("proveedor", ""),
                    "cuit_proveedor": v_data.get("cuit_proveedor", ""),
                    "ubicacion": master.get("ubicacion", {"pasillo": 0, "piso": 0, "modulo": 0, "fila": 0})
                }
                inventario.append(item)
                
    return inventario

def agregar_al_carrito(vendedor, id_producto, cantidad=1):
    cant = int(cantidad)
    if cant <= 0:
        return False, "La cantidad debe ser mayor a cero."

    ref_prod, id_m, marca_req, stock_disp, err = _resolver_producto_y_stock(id_producto)
    err_val = _validar_resolucion_producto(ref_prod, id_m, marca_req, stock_disp, err)
    if err_val:
        return False, err_val
    assert ref_prod is not None and id_m is not None and marca_req is not None and stock_disp is not None

    datos = ref_prod.get().to_dict() or {}
    variantes = datos.get("variantes", {})
    id_item = f"{id_m}_{marca_req}"
    ref_item = get_db().collection("presupuestos_activos").document(vendedor).collection("items").document(id_item)

    qty_carrito = 0
    doc_carrito = ref_item.get()
    if doc_carrito.exists:
        qty_carrito = int((doc_carrito.to_dict() or {}).get("cantidad", 0))

    if qty_carrito + cant > stock_disp:
        libre = max(0, stock_disp - qty_carrito)
        return False, (
            f"Stock insuficiente. Disponible para agregar: {libre} u. "
            f"(en carrito: {qty_carrito}, stock total: {stock_disp})."
        )

    if not variantes:
        precio = float(datos.get('precio_venta', 0.0))
        marca_mostrar = datos.get('marca', datos.get('condicion', ''))
        ref_item.set({
            "id_maestro": id_m, "marca": marca_mostrar,
            "descripcion": f"{datos.get('descripcion')} ({marca_mostrar})",
            "precio_unitario": precio,
            "cantidad": firestore.Increment(cant) # type: ignore
        }, merge=True)
        return True, f"Agregado: {datos.get('descripcion')}"

    precio = float(variantes[marca_req].get('precio_venta', 0.0))
    ref_item.set({
        "id_maestro": id_m,
        "marca": marca_req,
        "descripcion": f"{datos.get('descripcion')} ({marca_req})",
        "precio_unitario": precio,
        "cantidad": firestore.Increment(cant) # type: ignore
    }, merge=True)

    return True, f"Agregado: {datos.get('descripcion')} ({marca_req})"

def obtener_carrito(vendedor) -> list:
    docs = get_db().collection("presupuestos_activos").document(vendedor).collection("items").get()
    carrito = []
    
    for d in docs:
        item = d.to_dict() or {}
        item['id'] = d.id
        item['subtotal'] = float(item.get('precio_unitario', 0)) * int(item.get('cantidad', 0))
        carrito.append(item)
        
    return carrito

def vaciar_carrito(vendedor):
    docs = get_db().collection("presupuestos_activos").document(vendedor).collection("items").get()
    for d in docs:
        d.reference.delete()

def validar_carrito_para_venta(vendedor):
    items = obtener_carrito(vendedor)
    if not items:
        return False, "Vacío.", []

    lineas_ok = []
    errores = []

    for item in items:
        cant = int(item.get("cantidad", 0))
        id_item = str(item.get("id", "")).replace("/", "-")
        ref_prod, id_m, marca, stock_disp, err = _resolver_producto_y_stock(id_item)
        err_val = _validar_resolucion_producto(ref_prod, id_m, marca, stock_disp, err)
        if err_val:
            errores.append(f"{id_item}: {err_val}")
            continue
        assert ref_prod is not None and stock_disp is not None
        if cant <= 0:
            errores.append(f"{id_item}: cantidad inválida.")
            continue
        if cant > stock_disp:
            errores.append(
                f"{item.get('descripcion', id_item)}: stock insuficiente "
                f"(pedido {cant}, disponible {stock_disp})."
            )
            continue
        lineas_ok.append({
            "item": item,
            "ref_prod": ref_prod,
            "marca": marca,
            "cantidad": cant,
        })

    if errores:
        return False, "No se confirmó la venta:\n" + "\n".join(errores), []
    return True, "", lineas_ok


def _descontar_stock_lineas_carrito(vendedor, lineas_ok):
    batch = get_db().batch()
    operaciones = 0

    for linea in lineas_ok:
        item = linea["item"]
        ref_prod = linea["ref_prod"]
        marca = linea["marca"]
        cant = linea["cantidad"]
        ref_item = (
            get_db().collection("presupuestos_activos")
            .document(vendedor)
            .collection("items")
            .document(item["id"])
        )

        doc_prod = ref_prod.get()
        if doc_prod.exists and "variantes" in (doc_prod.to_dict() or {}):
            batch.update(ref_prod, {
                f"variantes.{marca}.stock": firestore.Increment(-cant)  # type: ignore
            })
        else:
            batch.update(ref_prod, {
                "stock": firestore.Increment(-cant)  # type: ignore
            })

        batch.delete(ref_item)
        operaciones += 2
        batch = _commit_batch_si_lleno(batch, operaciones)

    if operaciones % _BATCH_LIMIT != 0:
        batch.commit()
    invalidar_cache_datos()


def confirmar_venta(vendedor):
    ok, msg, lineas_ok = validar_carrito_para_venta(vendedor)
    if not ok:
        return False, msg
    _descontar_stock_lineas_carrito(vendedor, lineas_ok)
    vaciar_carrito(vendedor)
    return True, "Venta confirmada."

def borrar_toda_la_base_de_datos():
    for doc in get_db().collection("presupuestos_activos").stream():
        _borrar_subcoleccion(doc.reference, "items")
        doc.reference.delete()

    for doc in get_db().collection("pedidos").stream():
        _borrar_subcoleccion(doc.reference, "items")
        doc.reference.delete()

    for col in ["productos", "facturas_procesadas", "facturas_borrador", "auditoria_mermas", "auditoria_ingresos", "clientes", "proveedores", "marcas", "equivalencias", "controles_remito"]:
        batch = get_db().batch()
        operaciones = 0
        for doc in get_db().collection(col).stream():
            batch.delete(doc.reference)
            operaciones += 1
            batch = _commit_batch_si_lleno(batch, operaciones)
        if operaciones % _BATCH_LIMIT != 0:
            batch.commit()

    invalidar_cache_datos()
    return True, "Base de datos limpia."


def obtener_credenciales_arca():
    """Credenciales ARCA guardadas en Firebase (persisten entre sesiones)."""
    try:
        doc = get_db().collection("configuracion").document("mostrador_arca").get()
        if doc.exists:
            return doc.to_dict() or {}
    except Exception:
        pass
    return {}


def guardar_credenciales_arca(cuit, clave):
    """Guarda CUIT y clave del facturador para no reingresarlos."""
    cuit_l = str(cuit or "").strip()
    clave_l = str(clave or "").strip()
    if not cuit_l or not clave_l:
        return False, "Completá CUIT y clave antes de guardar."
    try:
        get_db().collection("configuracion").document("mostrador_arca").set({
            "cuit": cuit_l,
            "clave": clave_l,
            "actualizado": datetime.now(timezone.utc),
        }, merge=True)
        return True, "Credenciales ARCA guardadas."
    except Exception as e:
        return False, f"No se pudieron guardar: {e}"


def invalidar_cache_datos():
    """Limpia caches de Streamlit tras cambios en inventario o proveedores."""
    _limpiar_cache_streamlit(obtener_inventario_completo)
    _limpiar_cache_streamlit(obtener_proveedores)