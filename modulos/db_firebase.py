import firebase_admin
from firebase_admin import credentials, firestore
import os
from dotenv import load_dotenv
from datetime import datetime, timezone
import math
import streamlit as st

load_dotenv(override=True)

def inicializar_firebase():
    """Conexión híbrida local/nube"""
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

# --- GESTIÓN DE MARCAS ---
def obtener_marcas():
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
def obtener_proveedores():
    docs = db.collection("proveedores").get()
    return {d.id: d.to_dict() for d in docs}

def configurar_proveedor(nombre, cuit, recargo_contado=0.0, recargo_30_dias=15.0):
    id_prov = "".join(filter(str.isdigit, str(cuit)))
    db.collection("proveedores").document(id_prov).set({
        "nombre": str(nombre).upper(),
        "cuit": id_prov,
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
def calcular_cascada_precios(precio_base, recargo_financiero):
    base = float(precio_base)
    costo_iva = base * 1.21
    costo_final = costo_iva * (1 + (float(recargo_financiero) / 100.0))
    precio_interno = costo_final * 1.40
    precio_venta = math.ceil(precio_interno / 10.0) * 10
    
    return {
        "costo_iva": round(costo_iva, 2),
        "costo_final": round(costo_final, 2),
        "precio_interno": round(precio_interno, 2),
        "precio_venta": int(precio_venta)
    }

# --- CARGA DE MERCADERÍA REFORZADA ---
def registrar_ingreso_inteligente(datos_ia, condicion_pago, imagen_url=None):
    prov_id = "".join(filter(str.isdigit, str(datos_ia.get('cuit_proveedor', '0'))))
    pv = str(datos_ia.get('punto_venta', '0')).zfill(5)
    num = str(datos_ia.get('numero_comprobante', '0')).zfill(8)
    id_factura = f"FACT_{prov_id}_{pv}_{num}"
    
    if db.collection("facturas_procesadas").document(id_factura).get().exists:
        return False, f"La factura {pv}-{num} ya fue cargada."

    prov_doc = db.collection("proveedores").document(prov_id).get()
    if not prov_doc.exists:
        return False, "Proveedor no configurado."
    
    datos_prov = prov_doc.to_dict() or {}
    condiciones = datos_prov.get("condiciones", {})
    recargo = float(condiciones.get(condicion_pago, 0.0))
    
    ahora = datetime.now(timezone.utc)
    batch = db.batch()

    for art in datos_ia.get('articulos', []):
        codigo_base = str(art.get('codigo', '')).strip().upper()
        marca = str(art.get('marca', 'GENERICO')).strip().upper()
        
        if not codigo_base or codigo_base == "NONE":
            codigo_base = str(art.get('descripcion', 'ART')).replace(' ', '_').upper()[:15]
            
        id_producto = f"{codigo_base}_{marca}"
        precio_unitario = float(art.get('precio_unitario', 0.0))
        calculos = calcular_cascada_precios(precio_unitario, recargo)
        
        ref_prod = db.collection("productos").document(id_producto)
        doc_prod = ref_prod.get()
        cantidad = int(art.get('cantidad', 0))
        
        if doc_prod.exists:
            batch.update(ref_prod, {
                "stock": firestore.Increment(cantidad), # type: ignore
                "ultimo_costo_base": precio_unitario,
                "precio_interno": calculos['precio_interno'],
                "precio_venta": calculos['precio_venta'],
                "ultima_actualizacion": ahora
            })
        else:
            batch.set(ref_prod, {
                "codigo": codigo_base,
                "marca": marca,
                "descripcion": str(art.get('descripcion', 'Repuesto')),
                "stock": cantidad,
                "ultimo_costo_base": precio_unitario,
                "precio_interno": calculos['precio_interno'],
                "precio_venta": calculos['precio_venta'],
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

# --- ASISTENTE DE DEPÓSITO ---
def registrar_merma(id_producto, cantidad):
    ref_prod = db.collection("productos").document(id_producto)
    
    if not ref_prod.get().exists:
        return False, "Producto no existe."
        
    batch = db.batch()
    batch.update(ref_prod, {
        "stock": firestore.Increment(-cantidad), # type: ignore
        "ultima_actualizacion": datetime.now(timezone.utc)
    })
    
    # Registro de auditoría para la baja
    ref_baja = db.collection("auditoria_mermas").document()
    batch.set(ref_baja, {
        "id_producto": id_producto,
        "cantidad_baja": cantidad,
        "fecha": datetime.now(timezone.utc),
        "motivo": "Ajuste reportado vía Asistente de Voz"
    })
    
    batch.commit()
    return True, f"Baja de {cantidad} unidades registrada."

# --- INVENTARIO Y VENTAS ---
def obtener_inventario_completo():
    docs = db.collection("productos").get()
    return [{"id": d.id, **(d.to_dict() or {})} for d in docs]

def agregar_al_carrito(vendedor, id_producto, cantidad=1):
    ref_prod = db.collection("productos").document(id_producto)
    doc = ref_prod.get()
    
    if not doc.exists:
        return False, "No existe."
    
    datos = doc.to_dict() or {}
    precio = float(datos.get('precio_venta', 0.0))
    ref_item = db.collection("presupuestos_activos").document(vendedor).collection("items").document(id_producto)
    
    ref_item.set({
        "descripcion": f"{datos.get('descripcion')} ({datos.get('marca')})",
        "precio_unitario": precio,
        "cantidad": firestore.Increment(cantidad) # type: ignore
    }, merge=True)
    
    return True, "Agregado."

def obtener_carrito(vendedor):
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
        ref_prod = db.collection("productos").document(item['id'])
        ref_item = db.collection("presupuestos_activos").document(vendedor).collection("items").document(item['id'])
        
        batch.update(ref_prod, {
            "stock": firestore.Increment(-item['cantidad']) # type: ignore
        })
        batch.delete(ref_item)
        
    batch.commit()
    return True, "Venta confirmada."

def borrar_toda_la_base_de_datos():
    for col in ["productos", "facturas_procesadas", "presupuestos_activos", "auditoria_mermas"]:
        docs = db.collection(col).get()
        for d in docs:
            d.reference.delete()
    return True, "Base de datos limpia."