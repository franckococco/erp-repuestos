import firebase_admin
from firebase_admin import credentials, firestore
import os
from dotenv import load_dotenv
from datetime import datetime, timezone
import math
import re
import streamlit as st
import pandas as pd

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


def _ubicacion_desde_fila(row):
    ubi = row.get("ubicacion")
    if isinstance(ubi, dict):
        return ubi
    pasillo = row.get("pasillo")
    if pd.notna(pasillo):
        return {
            "pasillo": int(pasillo if pd.notna(pasillo) else 0),
            "piso": int(row.get("piso", 0) if pd.notna(row.get("piso")) else 0),
            "modulo": int(row.get("modulo", 0) if pd.notna(row.get("modulo")) else 0),
            "fila": int(row.get("fila", 0) if pd.notna(row.get("fila")) else 0),
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
            "stock": int(row.get('stock', 0) if pd.notna(row.get('stock')) else 0),
            "precio_venta": float(row.get('precio_venta', 0) if pd.notna(row.get('precio_venta')) else 0),
            "precio_interno": float(row.get('precio_interno', 0) if pd.notna(row.get('precio_interno')) else 0),
            "ultimo_costo_base": float(row.get('ultimo_costo_base', 0) if pd.notna(row.get('ultimo_costo_base')) else 0),
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

def configurar_cliente(nombre, cuit_dni, descuento=0.0):
    id_cli = "".join(filter(str.isdigit, str(cuit_dni)))
    if not id_cli:
        return False, "CUIT/DNI inválido."
    get_db().collection("clientes").document(id_cli).set({
        "nombre": str(nombre).upper(),
        "cuit_dni": id_cli,
        "descuento": float(descuento),
        "actualizado": datetime.now(timezone.utc)
    }, merge=True)
    return True, "Cliente configurado."

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
    termino_norm = re.sub(r"[^a-z0-9\s]", "", str(termino).lower()) if termino else ""
    terminos = [t for t in termino_norm.split() if t]

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
            texto = re.sub(
                r"[^a-z0-9\s]", "",
                f"{g['codigo']} {g['descripcion']} {g['vehiculo']} {' '.join(g['marcas'])}".lower(),
            )
            if all(t in texto for t in terminos):
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
        fecha_bd = datos_fac.get("fecha_carga")
        if fecha_bd:
            fecha_str = fecha_bd.strftime("%d/%m/%Y a las %H:%M hs")
        else:
            fecha_str = "una fecha desconocida"
        return False, f"La factura {pv}-{num} ya fue cargada previamente el {fecha_str}."

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
        vehiculo_rep = str(art.get('vehiculo', 'UNIVERSAL')).strip().upper()
        proveedor = str(art.get('proveedor', 'DESCONOCIDO')).upper()
        cuit_proveedor = prov_id
        precio_unitario = float(art.get('precio_unitario', 0.0))
        cantidad = int(art.get('cantidad', 0))

        calculos = calcular_cascada_precios(precio_unitario, recargo, descuento_prov)
        ref_prod = get_db().collection("productos").document(codigo_base)

        batch.set(ref_prod, {
            "codigo": codigo_base,
            "descripcion": str(art.get('descripcion', 'Repuesto')),
            "vehiculo": vehiculo_rep,
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
        }, merge=True)
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
    return True, "Mercadería cargada y agrupada correctamente."

def alta_manual_producto(codigo, condicion, vehiculo, descripcion, cuit_proveedor, precio_base, recargo, stock, pasillo, piso, modulo, fila):
    codigo_base = str(codigo).strip().upper().replace("/", "-")
    marca_limpia = sanitizar_clave_marca(condicion)
    veh_limpio = str(vehiculo).strip().upper()

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

    if not marca_req:
        if len(variantes) == 1:
            marca_req = list(variantes.keys())[0]
        else:
            return None, None, None, None, (
                "Múltiples marcas para este repuesto. Indicá el ID exacto (CODIGO_MARCA)."
            )

    if marca_req not in variantes:
        return None, None, None, None, f"La marca '{marca_req}' no se encontró en este repuesto."

    return ref_prod, id_m, marca_req, int(variantes[marca_req].get("stock", 0)), None


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
    variantes = datos.get("variantes", {})
    ahora = datetime.now(timezone.utc)

    if campo == "Marca":
        if not variantes:
            return False, "Producto sin variantes (formato antiguo)."
        nueva_marca = str(nuevo_valor).strip().upper()
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

    mapa_campos = {
        "Descripción": "descripcion",
        "Vehículo": "vehiculo",
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
    elif campo == "Vehículo":
        nuevo_valor = str(nuevo_valor).upper()
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
        
        if "variantes" not in master:
            # Producto formato viejo, lo aplanamos para que no rompa app.py
            marca_ant = master.get("marca", master.get("condicion", "GENERICO"))
            item = {
                "id": f"{master_id}_{marca_ant}",
                "id_maestro": master_id,
                "codigo": master.get("codigo", master_id),
                "descripcion": master.get("descripcion", ""),
                "vehiculo": master.get("vehiculo", "UNIVERSAL"),
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
                    "vehiculo": master.get("vehiculo", "UNIVERSAL"),
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

def confirmar_venta(vendedor):
    items = obtener_carrito(vendedor)

    if not items:
        return False, "Vacío."

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
        return False, "No se confirmó la venta:\n" + "\n".join(errores)

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
    return True, "Venta confirmada."

def borrar_toda_la_base_de_datos():
    for doc in get_db().collection("presupuestos_activos").stream():
        _borrar_subcoleccion(doc.reference, "items")
        doc.reference.delete()

    for doc in get_db().collection("pedidos").stream():
        _borrar_subcoleccion(doc.reference, "items")
        doc.reference.delete()

    for col in ["productos", "facturas_procesadas", "auditoria_mermas", "auditoria_ingresos", "clientes", "proveedores", "marcas", "equivalencias", "controles_remito"]:
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


def invalidar_cache_datos():
    """Limpia caches de Streamlit tras cambios en inventario o proveedores."""
    _limpiar_cache_streamlit(obtener_inventario_completo)
    _limpiar_cache_streamlit(obtener_proveedores)