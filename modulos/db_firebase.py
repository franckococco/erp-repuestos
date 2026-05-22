import firebase_admin
from firebase_admin import credentials, firestore
import os
from dotenv import load_dotenv
from datetime import datetime, timezone
import math
import streamlit as st

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
    """Busca si el código ya existe en el inventario para rescatar su descripción original."""
    cod_limpio = str(codigo_base).strip().upper().replace("/", "-")
    docs = db.collection("productos").where("codigo", "==", cod_limpio).limit(1).get()
    if docs:
        datos = docs[0].to_dict() or {}
        datos['id'] = docs[0].id
        return datos
    return None

# --- CARGA DE MERCADERÍA REFORZADA ---
def registrar_ingreso_inteligente(datos_ia, condicion_pago, imagen_url=None):
    prov_id = "".join(filter(str.isdigit, str(datos_ia.get('cuit_proveedor', '0'))))
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
        return False, "Proveedor no configurado."
    
    datos_prov = prov_doc.to_dict() or {}
    condiciones = datos_prov.get("condiciones", {})
    recargo = float(condiciones.get(condicion_pago, 0.0))
    descuento_prov = float(datos_prov.get("descuento", 0.0))
    
    ahora = datetime.now(timezone.utc)
    batch = db.batch()

    for art in datos_ia.get('articulos', []):
        codigo_base = str(art.get('codigo', '')).strip().upper().replace("/", "-")
        condicion_rep = str(art.get('condicion', 'GENERICO')).strip().upper()
        vehiculo_rep = str(art.get('vehiculo', 'UNIVERSAL')).strip().upper()
        proveedor = str(art.get('proveedor', 'DESCONOCIDO')).upper()
        cuit_proveedor = str(art.get('cuit_proveedor', '0'))
        
        if not codigo_base or codigo_base == "NONE":
            codigo_base = str(art.get('descripcion', 'ART')).replace(' ', '_').upper()[:15].replace("/", "-")
            
        id_producto = f"{codigo_base}_{condicion_rep}".replace("/", "-").strip()
        precio_unitario = float(art.get('precio_unitario', 0.0))
        
        calculos = calcular_cascada_precios(precio_unitario, recargo, descuento_prov)
        
        ref_prod = db.collection("productos").document(id_producto)
        doc_prod = ref_prod.get()
        cantidad = int(art.get('cantidad', 0))
        
        if doc_prod.exists:
            batch.update(ref_prod, {
                "stock": firestore.Increment(cantidad), # type: ignore
                "ultimo_costo_base": precio_unitario,
                "precio_interno": calculos['precio_interno'],
                "precio_venta": calculos['precio_venta'],
                "proveedor": proveedor,
                "cuit_proveedor": cuit_proveedor,
                "ultima_actualizacion": ahora
            })
        else:
            batch.set(ref_prod, {
                "codigo": codigo_base,
                "marca": condicion_rep, 
                "condicion": condicion_rep,
                "vehiculo": vehiculo_rep,
                "descripcion": str(art.get('descripcion', 'Repuesto')),
                "stock": cantidad,
                "ultimo_costo_base": precio_unitario,
                "precio_interno": calculos['precio_interno'],
                "precio_venta": calculos['precio_venta'],
                "proveedor": proveedor,
                "cuit_proveedor": cuit_proveedor,
                "ubicacion": {
                    "pasillo": 0,
                    "piso": 0,
                    "modulo": 0,
                    "fila": 0
                },
                "ultima_actualizacion": ahora
            })

    batch.set(db.collection("facturas_procesadas").document(id_factura), {
        "proveedor_id": prov_id,
        "pv": pv,
        "num": num,
        "fecha_carga": ahora,
        "factura_imagen": imagen_url
    })

    batch.commit()
    return True, "Mercadería cargada correctamente."

def alta_manual_producto(codigo, condicion, vehiculo, descripcion, cuit_proveedor, precio_base, recargo, stock, pasillo, piso, modulo, fila):
    codigo_base = str(codigo).strip().upper().replace("/", "-")
    cond_limpia = str(condicion).strip().upper()
    veh_limpio = str(vehiculo).strip().upper()
    id_producto = f"{codigo_base}_{cond_limpia}".replace("/", "-")

    ref_prod = db.collection("productos").document(id_producto)
    if ref_prod.get().exists:
        return False, f"El producto {codigo_base} ({cond_limpia}) ya existe."

    prov_doc = db.collection("proveedores").document(str(cuit_proveedor)).get()
    datos_proveedor_db = prov_doc.to_dict() or {} 
    nombre_proveedor = datos_proveedor_db.get("nombre", "DESCONOCIDO") if prov_doc.exists else "DESCONOCIDO"
    descuento_prov = float(datos_proveedor_db.get("descuento", 0.0))

    calculos = calcular_cascada_precios(float(precio_base), float(recargo), descuento_prov)
    ahora = datetime.now(timezone.utc)

    ref_prod.set({
        "codigo": codigo_base,
        "marca": cond_limpia, 
        "condicion": cond_limpia,
        "vehiculo": veh_limpio,
        "descripcion": str(descripcion).strip(),
        "stock": int(stock),
        "ultimo_costo_base": float(precio_base),
        "precio_interno": calculos['precio_interno'],
        "precio_venta": calculos['precio_venta'],
        "proveedor": nombre_proveedor,
        "cuit_proveedor": str(cuit_proveedor),
        "ubicacion": {
            "pasillo": int(pasillo),
            "piso": int(piso),
            "modulo": int(modulo),
            "fila": int(fila)
        },
        "ultima_actualizacion": ahora
    })
    
    return True, f"Producto {codigo_base} cargado exitosamente."

def actualizar_ubicacion_relevamiento(id_producto, pasillo=None, piso=None, modulo=None, fila=None):
    id_limpio = str(id_producto).strip().upper().replace("/", "-")
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

def actualizar_producto_desde_grilla(id_producto, campo, nuevo_valor):
    id_limpio = str(id_producto).replace("/", "-")
    ref_prod = db.collection("productos").document(id_limpio)
    if not ref_prod.get().exists:
        return False, "Producto no encontrado."
    
    mapa_campos = {
        "Descripción": "descripcion",
        "Stock": "stock",
        "Precio Final": "precio_venta",
        "Vehículo": "vehiculo",
        "Condición": "condicion",
        "Pasillo": "ubicacion.pasillo",
        "Piso": "ubicacion.piso",
        "Módulo": "ubicacion.modulo",
        "Fila": "ubicacion.fila"
    }
    
    campo_db = mapa_campos.get(campo)
    if not campo_db:
        return False, f"Campo no editable."
        
    if campo in ["Stock", "Pasillo", "Piso", "Módulo", "Fila", "Precio Final"]:
        nuevo_valor = int(nuevo_valor)
    else:
        nuevo_valor = str(nuevo_valor).upper() if campo in ["Vehículo", "Condición"] else str(nuevo_valor)
        
    updates = {campo_db: nuevo_valor, "ultima_actualizacion": datetime.now(timezone.utc)}
    if campo == "Condición":
        updates["marca"] = nuevo_valor
        
    ref_prod.update(updates)
    return True, "OK"

# --- ASISTENTE DE DEPÓSITO ---
def registrar_merma(id_producto, cantidad):
    id_limpio = str(id_producto).strip().upper().replace("/", "-")
    ref_prod = db.collection("productos").document(id_limpio)
    
    if not ref_prod.get().exists:
        docs_codigo = db.collection("productos").where("codigo", "==", id_limpio).get()
        if docs_codigo:
            ref_prod = db.collection("productos").document(docs_codigo[0].id)
            id_limpio = docs_codigo[0].id
        else:
            return False, f"El código '{id_limpio}' no se encontró en el inventario."
        
    batch = db.batch()
    batch.update(ref_prod, {
        "stock": firestore.Increment(-int(cantidad)), # type: ignore
        "ultima_actualizacion": datetime.now(timezone.utc)
    })
    
    ref_baja = db.collection("auditoria_mermas").document()
    batch.set(ref_baja, {
        "id_producto": id_limpio,
        "cantidad_baja": int(cantidad),
        "fecha": datetime.now(timezone.utc),
        "motivo": "Ajuste reportado vía Asistente de Voz"
    })
    
    batch.commit()
    return True, f"Baja de {cantidad} unidades registrada."

def registrar_aumento_stock(id_producto, cantidad):
    id_limpio = str(id_producto).strip().upper().replace("/", "-")
    ref_prod = db.collection("productos").document(id_limpio)
    
    if not ref_prod.get().exists:
        docs_codigo = db.collection("productos").where("codigo", "==", id_limpio).get()
        if docs_codigo:
            ref_prod = db.collection("productos").document(docs_codigo[0].id)
            id_limpio = docs_codigo[0].id
        else:
            return False, f"El código '{id_limpio}' no existe en el sistema."
        
    batch = db.batch()
    batch.update(ref_prod, {
        "stock": firestore.Increment(int(cantidad)), # type: ignore
        "ultima_actualizacion": datetime.now(timezone.utc)
    })
    
    ref_alta = db.collection("auditoria_ingresos").document()
    batch.set(ref_alta, {
        "id_producto": id_limpio,
        "cantidad_ingreso": int(cantidad),
        "fecha": datetime.now(timezone.utc),
        "motivo": "Ingreso manual vía Asistente de Voz"
    })
    
    batch.commit()
    return True, f"Aumento de {cantidad} unidades registrado exitosamente."

# --- INVENTARIO Y VENTAS ---
def obtener_inventario_completo() -> list:
    docs = db.collection("productos").get()
    inventario = []
    for d in docs:
        datos = d.to_dict() or {}
        datos['id'] = d.id
        
        if 'condicion' not in datos:
            datos['condicion'] = datos.get('marca', 'GENERICO')
        if 'vehiculo' not in datos:
            datos['vehiculo'] = 'UNIVERSAL'
            
        inventario.append(datos)
    return inventario

def agregar_al_carrito(vendedor, id_producto, cantidad=1):
    id_limpio = str(id_producto).strip().upper().replace("/", "-")
    ref_prod = db.collection("productos").document(id_limpio)
    doc = ref_prod.get()
    
    datos = None
    id_real = id_limpio

    if doc.exists:
        datos = doc.to_dict() or {}
    else:
        docs_codigo = db.collection("productos").where("codigo", "==", id_limpio).get()
        if docs_codigo:
            doc = docs_codigo[0]
            datos = doc.to_dict() or {}
            id_real = doc.id
        else:
            return False, f"El código '{id_limpio}' no se encontró en el inventario."
    
    precio = float(datos.get('precio_venta', 0.0))
    ref_item = db.collection("presupuestos_activos").document(vendedor).collection("items").document(id_real)
    
    marca_mostrar = datos.get('condicion', datos.get('marca', ''))
    ref_item.set({
        "descripcion": f"{datos.get('descripcion')} ({marca_mostrar})",
        "precio_unitario": precio,
        "cantidad": firestore.Increment(int(cantidad)) # type: ignore
    }, merge=True)
    
    return True, f"Agregado: {datos.get('descripcion')}"

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
        ref_prod = db.collection("productos").document(item['id'].replace("/", "-"))
        ref_item = db.collection("presupuestos_activos").document(vendedor).collection("items").document(item['id'])
        
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