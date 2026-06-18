"""Carga de producto nuevo desde el asistente de voz (con confirmación)."""
from datetime import datetime, timezone

from modulos.util_vehiculos import normalizar_lista_vehiculos, vehiculos_a_texto
from modulos.normalizar_carga_producto import normalizar_orden_cargar_producto
from modulos.db_firebase import (
    normalizar_codigo_proveedor,
    sanitizar_clave_marca,
    obtener_producto_por_codigo,
    _extraer_variantes_producto,
    get_db,
    calcular_cascada_precios,
    invalidar_cache_datos,
    alta_manual_producto,
)


def _entero_ubicacion(val):
    if val is None:
        return 0
    try:
        return max(0, int(val))
    except (TypeError, ValueError):
        return 0


def validar_y_preparar_carga_producto_voz(datos):
    """
    Valida una orden cargar_producto del asistente.
    Retorna (ok, payload, mensaje_resumen).
    """
    if not isinstance(datos, dict):
        return False, None, "Datos inválidos."

    datos = normalizar_orden_cargar_producto(dict(datos))

    codigo = normalizar_codigo_proveedor(datos.get("codigo", ""))
    descripcion = str(datos.get("descripcion", "") or "").strip()
    if not codigo:
        return False, None, "No detecté el código del producto."
    if not descripcion:
        return False, None, "No detecté la descripción. Incluila en la orden."

    marca = sanitizar_clave_marca(datos.get("marca") or "GENERICO")
    vehiculos_raw = datos.get("vehiculos") or datos.get("vehiculo") or ["UNIVERSAL"]
    vehiculos = normalizar_lista_vehiculos(vehiculos_raw)

    stock_raw = datos.get("stock")
    try:
        stock = int(stock_raw) if stock_raw is not None else 1
    except (TypeError, ValueError):
        stock = 1
    stock = max(0, stock)

    try:
        precio = float(datos.get("precio_base") or 0)
    except (TypeError, ValueError):
        precio = 0.0
    precio = max(0.0, precio)

    payload = {
        "codigo": codigo,
        "descripcion": descripcion,
        "marca": marca,
        "vehiculos": vehiculos,
        "stock": stock,
        "precio_base": precio,
        "cuit_proveedor": "0",
        "recargo": 0.0,
        "pasillo": _entero_ubicacion(datos.get("pasillo")),
        "piso": _entero_ubicacion(datos.get("piso")),
        "modulo": _entero_ubicacion(datos.get("modulo")),
        "fila": _entero_ubicacion(datos.get("fila")),
        "solo_variante": False,
    }

    existente = obtener_producto_por_codigo(codigo)
    desc_existente = ""
    if existente:
        desc_existente = str(existente.get("descripcion", ""))
        variantes = _extraer_variantes_producto(existente)
        if marca in variantes:
            return False, None, (
                f"El código {codigo} ya existe con marca {marca}. "
                f"Usá «sumá {stock} al {codigo}» para agregar stock."
            )
        payload["solo_variante"] = True
        payload["id_maestro"] = existente.get("id", codigo)

    veh_texto = vehiculos_a_texto(vehiculos)
    tiene_ubi = any(payload[k] for k in ("pasillo", "piso", "modulo", "fila"))
    ubi_txt = (
        f"Pasillo {payload['pasillo']}, Piso {payload['piso']}, "
        f"Módulo {payload['modulo']}, Fila {payload['fila']}"
        if tiene_ubi else "Sin ubicación (0/0/0/0)"
    )
    precio_txt = f"${precio:,.0f}" if precio > 0 else "Sin precio (completar después)"

    msg = (
        f"**Confirmar carga de producto**\n\n"
        f"- **Código:** {codigo}\n"
        f"- **Descripción:** {descripcion}\n"
        f"- **Marca:** {marca}\n"
        f"- **Vehículo(s):** {veh_texto}\n"
        f"- **Stock:** {stock}\n"
        f"- **Ubicación:** {ubi_txt}\n"
        f"- **Precio:** {precio_txt}\n"
    )
    if payload["solo_variante"]:
        msg += (
            f"\n_El código ya existe ({desc_existente}). "
            f"Se agregará la variante **{marca}** sin cambiar descripción ni vehículos del maestro._\n"
        )

    return True, payload, msg


def ejecutar_carga_producto_voz(payload):
    """Graba el producto tras confirmación del usuario."""
    if not isinstance(payload, dict):
        return False, "Datos inválidos."

    codigo = payload.get("codigo", "")
    marca = payload.get("marca", "GENERICO")
    stock = int(payload.get("stock", 0))
    precio = float(payload.get("precio_base", 0))
    ahora = datetime.now(timezone.utc)

    if payload.get("solo_variante"):
        id_m = str(payload.get("id_maestro") or codigo).strip()
        ref_prod = get_db().collection("productos").document(id_m)
        if not ref_prod.get().exists:
            docs = get_db().collection("productos").where("codigo", "==", codigo).limit(1).get()
            if not docs:
                return False, f"No encontré el código {codigo}."
            ref_prod = get_db().collection("productos").document(docs[0].id)

        calculos = calcular_cascada_precios(precio, 0.0, 0.0)
        updates = {
            "ultima_actualizacion": ahora,
            "variantes": {
                marca: {
                    "stock": stock,
                    "ultimo_costo_base": precio,
                    "precio_interno": calculos["precio_interno"],
                    "precio_venta": calculos["precio_venta"],
                    "proveedor": "DESCONOCIDO",
                    "cuit_proveedor": "0",
                }
            },
        }
        ref_prod.set(updates, merge=True)

        if any(payload.get(k) for k in ("pasillo", "piso", "modulo", "fila")):
            ref_prod.update({
                "ubicacion": {
                    "pasillo": _entero_ubicacion(payload.get("pasillo")),
                    "piso": _entero_ubicacion(payload.get("piso")),
                    "modulo": _entero_ubicacion(payload.get("modulo")),
                    "fila": _entero_ubicacion(payload.get("fila")),
                },
                "ultima_actualizacion": ahora,
            })

        invalidar_cache_datos()
        return True, f"Variante {marca} agregada al código {codigo} ({stock} u.)."

    return alta_manual_producto(
        codigo=codigo,
        condicion=marca,
        vehiculo=payload.get("vehiculos", ["UNIVERSAL"]),
        descripcion=payload.get("descripcion", ""),
        cuit_proveedor=payload.get("cuit_proveedor", "0"),
        precio_base=precio,
        recargo=float(payload.get("recargo", 0)),
        stock=stock,
        pasillo=_entero_ubicacion(payload.get("pasillo")),
        piso=_entero_ubicacion(payload.get("piso")),
        modulo=_entero_ubicacion(payload.get("modulo")),
        fila=_entero_ubicacion(payload.get("fila")),
    )
