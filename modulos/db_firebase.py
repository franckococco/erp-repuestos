import firebase_admin
from firebase_admin import credentials, firestore
import os
from dotenv import load_dotenv
from datetime import datetime, timezone
import uuid
import streamlit as st

load_dotenv(override=True)

def inicializar_firebase():
    """Maneja la conexión tanto en local como en la nube de Streamlit"""
    if not firebase_admin._apps:
        if "firebase_key" in st.secrets:
            f_key = st.secrets["firebase_key"]
            creds_dict = {
                "type": f_key["type"],
                "project_id": f_key["project_id"],
                "private_key_id": f_key["private_key_id"],
                "private_key": f_key["private_key"].replace("\\n", "\n"),
                "client_email": f_key["client_email"],
                "client_id": f_key["client_id"],
                "auth_uri": f_key["auth_uri"],
                "token_uri": f_key["token_uri"],
                "auth_provider_x509_cert_url": f_key["auth_provider_x509_cert_url"],
                "client_x509_cert_url": f_key["client_x509_cert_url"]
            }
            cred = credentials.Certificate(creds_dict)
        else:
            ruta_json = os.getenv("FIREBASE_CREDENTIALS_PATH", "firebase_claves.json")
            cred = credentials.Certificate(ruta_json)
        
        firebase_admin.initialize_app(cred)
    return firestore.client()

db = inicializar_firebase()

# --- FUNCIONES DE CONFIGURACIÓN Y CONSULTA ---

def guardar_descuento_proveedor(nombre_proveedor, descuento):
    db.collection("configuracion").document("descuentos").set({
        nombre_proveedor.upper().strip(): float(descuento)
    }, merge=True)

def obtener_todos_los_descuentos():
    doc = db.collection("configuracion").document("descuentos").get()
    return doc.to_dict() or {} if doc.exists else {}

def obtener_reglas_proveedor(nombre_proveedor):
    descuentos = obtener_todos_los_descuentos()
    for prov_clave, desc in descuentos.items():
        if prov_clave in nombre_proveedor.upper(): return desc
    return 0.0

def obtener_inventario_completo():
    docs = db.collection("productos").get()
    inventario = []
    for d in docs:
        item = d.to_dict() or {}
        item['codigo'] = d.id
        precios = item.get("precios_por_proveedor", {}).values()
        item['precio_max'] = max(precios) if precios else 0
        inventario.append(item)
    return inventario

# --- LÓGICA DE CONTROL Y SEGURIDAD ---

def factura_ya_existe(cuit, punto_venta, numero):
    """Evita la carga duplicada de documentos fiscales"""
    id_factura = f"{cuit}_{punto_venta}_{numero}"
    doc = db.collection("facturas_procesadas").document(id_factura).get()
    return doc.exists

def registrar_ingreso_mercaderia(datos_ia):
    """Registra stock y previene duplicados usando una transacción Batch"""
    proveedor = datos_ia['proveedor']
    cuit = datos_ia.get('cuit_proveedor', '00-00000000-0')
    pv = datos_ia.get('punto_venta', 0)
    num = datos_ia.get('numero_comprobante', 0)
    
    if factura_ya_existe(cuit, pv, num):
        return False, f"Error: La factura {pv}-{num} de {proveedor} ya fue cargada."

    dto_proveedor = obtener_reglas_proveedor(proveedor)
    ahora = datetime.now(timezone.utc)
    batch = db.batch()

    for art in datos_ia['articulos']:
        codigo = str(art.get('codigo', '')).strip()
        if not codigo or codigo.lower() in ["null", "none"]:
            desc = art.get('descripcion', '').strip()
            codigo = desc.replace(' ', '_').upper() if desc else f"ID_{str(uuid.uuid4())[:6]}"
        
        costo_neto = float(art.get('precio_unitario', 0)) * (1 - (dto_proveedor / 100))
        ref_prod = db.collection("productos").document(codigo)
        doc_prod = ref_prod.get()
        
        cantidad = int(art.get('cantidad', 0))
        descripcion = art.get('descripcion', 'Sin descripción')
        
        if doc_prod.exists:
            datos = doc_prod.to_dict() or {}
            historial = datos.get("precios_por_proveedor", {})
            historial[proveedor] = costo_neto
            batch.update(ref_prod, {
                "descripcion": descripcion,
                "stock": int(datos.get("stock", 0)) + cantidad,
                "precios_por_proveedor": historial,
                "ultima_actualizacion": ahora
            })
        else:
            batch.set(ref_prod, {
                "descripcion": descripcion,
                "stock": cantidad,
                "precios_por_proveedor": {proveedor: costo_neto},
                "ultima_actualizacion": ahora
            })

    # Registramos la factura para el historial de auditoría
    id_factura = f"{cuit}_{pv}_{num}"
    ref_factura = db.collection("facturas_procesadas").document(id_factura)
    batch.set(ref_factura, {
        "proveedor": proveedor,
        "cuit": cuit,
        "punto_venta": pv,
        "numero": num,
        "fecha_carga": ahora,
        "total_articulos": len(datos_ia['articulos'])
    })

    batch.commit()
    return True, "Ingreso registrado correctamente."

# --- LÓGICA DE VENTAS Y CARRITO ---

def agregar_al_carrito(vendedor, codigo_bruto, cantidad=1):
    codigo = codigo_bruto.split("\n")[0].replace("COD:", "").strip()
    ref_prod = db.collection("productos").document(codigo)
    doc_prod = ref_prod.get()
    
    if not doc_prod.exists:
        return False, f"El código {codigo} no existe."
    
    datos = doc_prod.to_dict() or {}
    stock_actual = int(datos.get("stock", 0))
    if stock_actual < cantidad:
        return False, f"Stock insuficiente ({stock_actual})."

    precios = datos.get("precios_por_proveedor", {}).values()
    precio_venta = max(precios) if precios else 0.0

    ref_item = db.collection("presupuestos_activos").document(vendedor).collection("items").document(codigo)
    doc_item = ref_item.get()
    
    if doc_item.exists:
        item_data = doc_item.to_dict() or {}
        nueva_cant = item_data.get("cantidad", 0) + cantidad
        if nueva_cant > stock_actual:
            return False, f"No podés agregar más. Límite: {stock_actual}."
        ref_item.update({"cantidad": nueva_cant, "subtotal": nueva_cant * precio_venta})
    else:
        ref_item.set({
            "descripcion": datos.get("descripcion", "Repuesto"),
            "precio_unitario": precio_venta,
            "cantidad": cantidad,
            "subtotal": precio_venta * cantidad
        })
    return True, "Agregado."

def obtener_carrito(vendedor):
    docs = db.collection("presupuestos_activos").document(vendedor).collection("items").get()
    return [{"codigo": d.id, **(d.to_dict() or {})} for d in docs]

def vaciar_carrito(vendedor):
    docs = db.collection("presupuestos_activos").document(vendedor).collection("items").get()
    for d in docs:
        d.reference.delete()

def confirmar_venta(vendedor):
    items = db.collection("presupuestos_activos").document(vendedor).collection("items").get()
    if not items: return False, "El carrito está vacío."
    
    batch = db.batch()
    for d in items:
        item = d.to_dict()
        ref_prod = db.collection("productos").document(d.id)
        batch.update(ref_prod, {"stock": firestore.Increment(-item['cantidad'])}) # type: ignore
        batch.delete(d.reference)
    
    batch.commit()
    return True, "Venta realizada con éxito."