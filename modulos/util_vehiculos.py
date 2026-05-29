"""Vehículos compatibles — lista múltiple por artículo maestro."""

OPCIONES_VEHICULO = [
    "UNIVERSAL",
    "VOLKSWAGEN",
    "PEUGEOT",
    "CITROEN",
    "FIAT",
    "FORD",
    "RENAULT",
    "CHEVROLET",
]


def normalizar_lista_vehiculos(valor):
    """Acepta str, lista o None → lista única en mayúsculas."""
    if valor is None:
        return ["UNIVERSAL"]
    if isinstance(valor, list):
        items = [str(v).strip().upper() for v in valor if str(v).strip()]
    else:
        texto = str(valor).strip().upper()
        if not texto:
            return ["UNIVERSAL"]
        separadores = texto.replace(";", ",").replace("|", ",")
        items = [p.strip() for p in separadores.split(",") if p.strip()]
    if not items:
        return ["UNIVERSAL"]
    if "TODOS" in items:
        return list(OPCIONES_VEHICULO)
    vistos = []
    for v in items:
        if v not in vistos and v in OPCIONES_VEHICULO:
            vistos.append(v)
    return vistos or ["UNIVERSAL"]


def vehiculos_a_texto(vehiculos):
    lista = normalizar_lista_vehiculos(vehiculos)
    if set(lista) >= set(OPCIONES_VEHICULO):
        return "TODOS"
    return ", ".join(lista)


def vehiculos_en_busqueda(vehiculos):
    return " ".join(normalizar_lista_vehiculos(vehiculos))
