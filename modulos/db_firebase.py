import firebase_admin
from firebase_admin import credentials, firestore
import os
from dotenv import load_dotenv
from datetime import datetime, timezone
import math
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
            cred = credentials.Certificate(ruta_json)
        firebase_admin.initialize_app(cred)
    return firestore.client()

db = inicializar_firebase()

# --- BACKUP Y RESTAURACIÓN ---
def exportar_inventario_csv():
    inv = obtener_inventario_completo()
    if not inv:
        return None
    df = pd.DataFrame(inv)
    
    # Ordenar columnas para que 'id' sea la primera
    cols = df.columns.tolist()
    if 'id' in cols:
        cols.insert(0, cols.pop(cols.index('id')))
    df = df[cols]
    
    csv_buffer = df.to_csv(index=False)
    return csv_buffer.encode('utf-8')

def restaurar_inventario_csv(df_csv, modo="sobreescribir"):
    batch = db.batch()
    operaciones = 0
    ahora = datetime.now(timezone.utc)
    
    maestros = {}
    for _, row in df_csv.iterrows():
        id_original = str(row.get('id', row.get('codigo', ''))).strip()
        if not id_original or pd.isna(id_original) or id_original == "nan":
            continue
            
        id_m = id_original.split("_")[0] # Extraemos la raíz (código maestro)
        marca = str(row.get('marca', 'GENERICO')).strip().upper()
        
        if id_m not in maestros:
            maestros[id_m] = {
                "codigo": id_m,
                "descripcion": str(row.get('descripcion', '')),
                "vehiculo": str(row.get('vehiculo', 'UNIVERSAL')).upper(),
                "ubicacion": {
                    "pasillo": int(row.get('pasillo', 0) if pd.notna(row.get('pasillo')) else 0),
                    "piso": int(row.get('piso', 0) if pd.notna(row.get('piso')) else 0),
                    "modulo": int(row.get('modulo', 0) if pd.notna(row.get('modulo')) else 0),
                    "fila": int(row.get('fila', 0) if pd.notna(row.get('fila')) else 0)
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
        ref_prod = db.collection("productos").document(id_m)
        
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
        # Límite de batch en Firestore es 500
        if operaciones % 400 == 0:
            batch.commit()
            batch = db.batch()
    
    if operaciones % 400 != 0:
        batch.commit()
        
    return True, f"Procesados {operaciones} repuestos agrupados en modo '{modo}'."

# --- GESTIÓN DE CLIENTES ---
def obtener_clientes() -> dict:
    docs = db.collection("clientes").get()
    return {d.id: d.to_dict() or {} for d in docs}

def configurar_cliente(nombre, cuit_dni, descuento=0.0):
    id_cli = "".join(filter(str.isdigit, str(cuit_dni)))
    if not id_cli:
        return False, "CUIT/DNI inválido."
    db.collection("clientes").document(id_cli).set({
        "nombre": str(nombre).upper(),
        "cuit_dni": id_cli,
        "descuento": float(descuento),
        "actualizado": datetime.now(timezone.utc)
    }, merge=True)
    return True, "Cliente configurado."

def eliminar_cliente(cuit_dni):
    id_cli = "".join(filter(str.isdigit, str(cuit_dni)))
    db.collection("clientes").document(id_cli).delete()
    return True

# --- GESTIÓN DE MARCAS ---
def obtener_marcas() -> list:
    docs = db.collection("marcas").get()
    return [d.id for d in docs]

def agregar_marca(nombre):
    id_marca = str(nombre).upper().strip()
    db.collection("marcas").document(id_marca).set({
        "creado": datetime.now(timezone.utc)
    })

def eliminar_marca(nombre):
    id_marca = str(nombre).upper().strip()
    db.collection("marcas").document(id_marca).delete()
    return True

# --- GESTIÓN DE PROVEEDORES ---
def obtener_proveedores() -> dict:
    docs = db.collection("proveedores").get()
    return {d.id: d.to_dict() or {} for d in docs}

def configurar_proveedor(nombre, cuit, recargo_contado=0.0, recargo_30_dias=15.0, descuento=0.0):
    id_prov = "".join(filter(str.isdigit, str(cuit)))
    db.collection("proveedores").document(id_prov).set({
        "nombre": str(nombre).upper(),
        "cuit": id_prov,
        "descuento": float(descuento),
        "condiciones": {
            "Contado": float(recargo_contado),
            "30 Días": float(recargo_30_dias)
        }
    }, merge=True)

def eliminar_proveedor(cuit):
    id_prov = "".join(filter(str.isdigit, str(cuit)))
    db.collection("proveedores").document(id_prov).delete()
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
    docs = db.collection("productos").where("codigo", "==", cod_limpio).limit(1).get()
    if docs:
        datos = docs[0].to_dict() or {}
        datos['id'] = docs[0].id
        return datos
    return None

# --- CARGA DE MERCADERÍA REFORZADA (LÓGICA MAESTRO -> VARIANTE) ---
def registrar_ingreso_inteligente(datos_ia, condicion_pago, imagen_url=None):
    prov_id = "".join(filter(str.isdigit, str(datos_ia.get('cuit_proveedor', '0'))))
    if not prov_id: prov_id = "0"
        
    pv = str(datos_ia.get('punto_venta', '0')).zfill(5)
    num = str(datos_ia.get('numero_comprobante', '0')).zfill(8)
    id_factura = f"FACT_{prov_id}_{pv}_{num}"
    
    doc_factura = db.collection("facturas_procesadas").document(id_factura).get()
    if doc_factura.exists:
        datos_fac = doc_factura.to_dict() or {}
        fecha_bd = datos_fac.get("fecha_carga")
        if fecha_bd:
            fecha_str = fecha_bd.strftime("%d/%m/%Y a las %H:%M hs")
        else:
            fecha_str = "una fecha desconocida"
        return False, f"La factura {pv}-{num} ya fue cargada previamente el {fecha_str}."

    prov_doc = db.collection("proveedores").document(prov_id).get()
    if not prov_doc.exists:
        return False, "Proveedor no configurado (CUIT inexistente)."
    
    datos_prov = prov_doc.to_dict() or {}
    condiciones = datos_prov.get("condiciones", {})
    recargo = float(condiciones.get(condicion_pago, 0.0))
    descuento_prov = float(datos_prov.get("descuento", 0.0))
    
    ahora = datetime.now(timezone.utc)
    batch = db.batch()

    for art in datos_ia.get('articulos', []):
        codigo_base = str(art.get('codigo', '')).strip().upper().replace("/", "-")
        
        if not codigo_base or codigo_base == "NONE":
            codigo_base = str(art.get('descripcion', 'ART')).replace(' ', '_').upper()[:15].replace("/", "-")
            
        marca_rep = str(art.get('marca', art.get('condicion', 'GENERICO'))).strip().upper()
        vehiculo_rep = str(art.get('vehiculo', 'UNIVERSAL')).strip().upper()
        proveedor = str(art.get('proveedor', 'DESCONOCIDO')).upper()
        cuit_proveedor = prov_id
        precio_unitario = float(art.get('precio_unitario', 0.0))
        cantidad = int(art.get('cantidad', 0))
        
        calculos = calcular_cascada_precios(precio_unitario, recargo, descuento_prov)
        ref_prod = db.collection("productos").document(codigo_base)
        
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
        }, merge=True) # merge=True solo inserta/actualiza la variante en cuestión sin borrar las demás

    batch.set(db.collection("facturas_procesadas").document(id_factura), {
        "proveedor_id": prov_id,
        "pv": pv,
        "num": num,
        "fecha_carga": ahora,
        "factura_imagen": imagen_url
    })

    batch.commit()
    return True, "Mercadería cargada y agrupada correctamente."

def alta_manual_producto(codigo, condicion, vehiculo, descripcion, cuit_proveedor, precio_base, recargo, stock, pasillo, piso, modulo, fila):
    codigo_base = str(codigo).strip().upper().replace("/", "-")
    marca_limpia = str(condicion).strip().upper()
    veh_limpio = str(vehiculo).strip().upper()

    if not codigo_base: return False, "Código de producto inválido."

    ref_prod = db.collection("productos").document(codigo_base)

    cuit_prov_limpio = "".join(filter(str.isdigit, str(cuit_proveedor)))
    if not cuit_prov_limpio: cuit_prov_limpio = "0"
        
    prov_doc = db.collection("proveedores").document(cuit_prov_limpio).get()
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
    
    return True, f"Repuesto {codigo_base} guardado bajo la variante {marca_limpia}."

def actualizar_ubicacion_relevamiento(id_producto, pasillo=None, piso=None, modulo=None, fila=None):
    id_limpio = str(id_producto).strip().upper().replace("/", "-").split("_")[0] # Toma el maestro
    ref_prod = db.collection("productos").document(id_limpio)
    
    if not ref_prod.get().exists:
        docs_codigo = db.collection("productos").where("codigo", "==", id_limpio).get()
        if docs_codigo:
            ref_prod = db.collection("productos").document(docs_codigo[0].id)
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
        return True, "Ubicación de inventario actualizada."
    return False, "No se detectaron datos de ubicación válidos en la orden."

def actualizar_producto_desde_grilla(id_producto, campo, nuevo_valor, id_maestro=None, marca=None):
    id_m = str(id_maestro or "").strip().replace("/", "-")
    marca_key = str(marca or "").strip().upper()

    if not id_m:
        partes = str(id_producto).replace("/", "-").split("_", 1)
        id_m = partes[0]
        if not marca_key:
            marca_key = partes[1].upper() if len(partes) > 1 else "GENERICO"

    if not marca_key:
        partes = str(id_producto).replace("/", "-").split("_", 1)
        marca_key = partes[1].upper() if len(partes) > 1 else "GENERICO"

    ref_prod = db.collection("productos").document(id_m)
    doc = ref_prod.get()
    if not doc.exists:
        return False, "Producto no encontrado."

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
    return True, "OK"

# --- ASISTENTE DE DEPÓSITO ---
def registrar_merma(id_producto, cantidad):
    partes = str(id_producto).strip().upper().replace("/", "-").split("_", 1)
    id_m = partes[0]
    marca_req = partes[1] if len(partes) > 1 else None
    
    ref_prod = db.collection("productos").document(id_m)
    doc = ref_prod.get()
    
    if not doc.exists:
        docs_codigo = db.collection("productos").where("codigo", "==", id_m).get()
        if docs_codigo:
            ref_prod = db.collection("productos").document(docs_codigo[0].id)
            doc = ref_prod.get()
            id_m = doc.id
        else:
            return False, f"El código '{id_m}' no se encontró en el inventario."
            
    datos = doc.to_dict() or {}
    variantes = datos.get("variantes", {})
    
    if not variantes:
        # Retrocompatibilidad para repuestos viejos sin variantes
        batch = db.batch()
        batch.update(ref_prod, {"stock": firestore.Increment(-int(cantidad)), "ultima_actualizacion": datetime.now(timezone.utc)}) # type: ignore
        batch.commit()
        return True, f"Baja de {cantidad} unidades registrada."

    if not marca_req:
        if len(variantes) == 1:
            marca_req = list(variantes.keys())[0]
        else:
            return False, f"Múltiples marcas para este repuesto. Por favor, dictá el código exacto desde la pantalla o usá ingreso manual."
            
    if marca_req not in variantes:
        return False, f"La marca '{marca_req}' no se encontró en este repuesto."
        
    batch = db.batch()
    batch.update(ref_prod, {
        f"variantes.{marca_req}.stock": firestore.Increment(-int(cantidad)), # type: ignore
        "ultima_actualizacion": datetime.now(timezone.utc)
    })
    
    ref_baja = db.collection("auditoria_mermas").document()
    batch.set(ref_baja, {
        "id_producto": id_m,
        "marca": marca_req,
        "cantidad_baja": int(cantidad),
        "fecha": datetime.now(timezone.utc),
        "motivo": "Ajuste reportado vía Asistente de Voz"
    })
    
    batch.commit()
    return True, f"Baja de {cantidad} unidades en marca {marca_req} registrada."

def registrar_aumento_stock(id_producto, cantidad):
    partes = str(id_producto).strip().upper().replace("/", "-").split("_", 1)
    id_m = partes[0]
    marca_req = partes[1] if len(partes) > 1 else None
    
    ref_prod = db.collection("productos").document(id_m)
    doc = ref_prod.get()
    
    if not doc.exists:
        docs_codigo = db.collection("productos").where("codigo", "==", id_m).get()
        if docs_codigo:
            ref_prod = db.collection("productos").document(docs_codigo[0].id)
            doc = ref_prod.get()
            id_m = doc.id
        else:
            return False, f"El código '{id_m}' no existe en el sistema."
            
    datos = doc.to_dict() or {}
    variantes = datos.get("variantes", {})
    
    if not variantes:
        batch = db.batch()
        batch.update(ref_prod, {"stock": firestore.Increment(int(cantidad)), "ultima_actualizacion": datetime.now(timezone.utc)}) # type: ignore
        batch.commit()
        return True, f"Aumento de {cantidad} unidades registrado."

    if not marca_req:
        if len(variantes) == 1:
            marca_req = list(variantes.keys())[0]
        else:
            return False, f"Múltiples marcas para este repuesto. Por favor, dictá el código exacto o usá ingreso manual."
            
    if marca_req not in variantes:
        return False, f"La marca '{marca_req}' no se encontró en este repuesto."
        
    batch = db.batch()
    batch.update(ref_prod, {
        f"variantes.{marca_req}.stock": firestore.Increment(int(cantidad)), # type: ignore
        "ultima_actualizacion": datetime.now(timezone.utc)
    })
    
    ref_alta = db.collection("auditoria_ingresos").document()
    batch.set(ref_alta, {
        "id_producto": id_m,
        "marca": marca_req,
        "cantidad_ingreso": int(cantidad),
        "fecha": datetime.now(timezone.utc),
        "motivo": "Ingreso manual vía Asistente de Voz"
    })
    
    batch.commit()
    return True, f"Aumento de {cantidad} unidades en marca {marca_req} registrado exitosamente."

# --- INVENTARIO Y VENTAS ---
def obtener_inventario_completo() -> list:
    docs = db.collection("productos").get()
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
    partes = str(id_producto).strip().upper().replace("/", "-").split("_", 1)
    id_m = partes[0]
    marca_req = partes[1] if len(partes) > 1 else None
    
    ref_prod = db.collection("productos").document(id_m)
    doc = ref_prod.get()

    if not doc.exists:
        docs_codigo = db.collection("productos").where("codigo", "==", id_m).get()
        if docs_codigo:
            doc = docs_codigo[0]
            id_m = doc.id
        else:
            return False, f"El código '{id_m}' no se encontró en el inventario."
            
    datos = doc.to_dict() or {}
    variantes = datos.get("variantes", {})
    
    if not variantes:
        # Modo viejo
        precio = float(datos.get('precio_venta', 0.0))
        marca_mostrar = datos.get('marca', datos.get('condicion', ''))
        ref_item = db.collection("presupuestos_activos").document(vendedor).collection("items").document(f"{id_m}_{marca_mostrar}")
        ref_item.set({
            "id_maestro": id_m, "marca": marca_mostrar,
            "descripcion": f"{datos.get('descripcion')} ({marca_mostrar})",
            "precio_unitario": precio,
            "cantidad": firestore.Increment(int(cantidad)) # type: ignore
        }, merge=True)
        return True, f"Agregado: {datos.get('descripcion')}"
        
    if not marca_req:
        if len(variantes) == 1:
            marca_req = list(variantes.keys())[0]
        else:
            return False, f"Múltiples marcas disponibles. Selecciona una específica."
            
    if marca_req not in variantes:
        return False, f"Marca '{marca_req}' sin stock."
        
    precio = float(variantes[marca_req].get('precio_venta', 0.0))
    ref_item = db.collection("presupuestos_activos").document(vendedor).collection("items").document(f"{id_m}_{marca_req}")
    
    ref_item.set({
        "id_maestro": id_m,
        "marca": marca_req,
        "descripcion": f"{datos.get('descripcion')} ({marca_req})",
        "precio_unitario": precio,
        "cantidad": firestore.Increment(int(cantidad)) # type: ignore
    }, merge=True)
    
    return True, f"Agregado: {datos.get('descripcion')} ({marca_req})"

def obtener_carrito(vendedor) -> list:
    docs = db.collection("presupuestos_activos").document(vendedor).collection("items").get()
    carrito = []
    
    for d in docs:
        item = d.to_dict() or {}
        item['id'] = d.id
        item['subtotal'] = float(item.get('precio_unitario', 0)) * int(item.get('cantidad', 0))
        carrito.append(item)
        
    return carrito

def vaciar_carrito(vendedor):
    docs = db.collection("presupuestos_activos").document(vendedor).collection("items").get()
    for d in docs:
        d.reference.delete()

def confirmar_venta(vendedor):
    items = obtener_carrito(vendedor)
    
    if not items:
        return False, "Vacío."
        
    batch = db.batch()
    
    for item in items:
        id_completo = item['id'].replace("/", "-")
        partes = id_completo.split("_", 1)
        id_m = item.get('id_maestro') or partes[0]
        marca = item.get('marca') or (partes[1] if len(partes) > 1 else 'GENERICO')
        
        ref_prod = db.collection("productos").document(id_m)
        ref_item = db.collection("presupuestos_activos").document(vendedor).collection("items").document(item['id'])
        
        doc_prod = ref_prod.get()
        if doc_prod.exists and "variantes" in (doc_prod.to_dict() or {}):
            batch.update(ref_prod, {
                f"variantes.{marca}.stock": firestore.Increment(-item['cantidad']) # type: ignore
            })
        else:
            batch.update(ref_prod, {
                "stock": firestore.Increment(-item['cantidad']) # type: ignore
            })
            
        batch.delete(ref_item)
        
    batch.commit()
    return True, "Venta confirmada."

def borrar_toda_la_base_de_datos():
    for col in ["productos", "facturas_procesadas", "presupuestos_activos", "auditoria_mermas", "auditoria_ingresos", "clientes"]:
        docs = db.collection(col).get()
        for d in docs:
            d.reference.delete()
    return True, "Base de datos limpia."