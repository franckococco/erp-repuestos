"""Normalización de repuestos dictados: sinónimos, vehículo y búsqueda compuesta."""
import re
from typing import FrozenSet, List, Optional, Tuple

from modulos.ia_asistente import normalizar_texto_basico
from modulos.util_busqueda import normalizar_para_busqueda

# Raíces canónicas de repuestos (mostrador argentino)
_REPUESTOS_BASE = (
    "amortiguador", "pastilla", "disco", "filtro", "buje", "ruleman", "rotula",
    "homocinetica", "fuelle", "tensor", "correa", "kit", "bomba", "radiador",
    "termostato", "sensor", "bujia", "bobina", "escobilla", "optica", "faro",
    "paragolpe", "tirante", "barra", "terminal", "extremo", "cremallera",
    "trapecio", "puntal", "meseta", "cazoleta", "tope", "embrague", "crapodina",
    "collarin", "semieje", "palier", "junta", "reten", "polea", "alternador",
    "arranque", "liquido", "aceite", "refrigerante", "bieleta", "soporte",
    "silentblock", "cruceta", "espiral", "resorte", "cadena", "distribucion",
    "inyector", "carburador", "caño", "manguera", "abrazadera", "bulbo",
    "interruptor", "rele", "fusible", "modulo", "valvula", "culata", "piston",
    "camisa", "biela", "cigueñal", "cigüeñal", "empaque", "tapa", "carter",
    "volante", "piñon", "piñón", "engranaje", "sincronizador", "horquilla",
    "perno", "tuerca", "grapa", "clip", "fuelle", "guardapolvo", "capuchon",
    "capuchón", "rodamiento", "cruceta", "cardan", "cardán", "flexible",
    "maza", "roza", "pastilla", "zapata", "cilindro", "flexible", "latiguillo",
    "deposito", "depósito", "tapón", "tapon", "bulon", "bulón", "esparrago",
    "esparrago", "tornillo", "arandela", "resorte", "goma", "bandeja",
    "parrilla", "radiador", "intercooler", "turbo", "egr", "pcv", "maf",
    "pedal", "palanca", "selectora", "horquilla", "collarin", "actuador",
    "bombita", "electro", "ventilador", "electroventilador", "compresor",
    "evaporador", "condensador", "deshidratador", "polea", "tensores",
)

# Errores de voz, abreviaturas y jerga de mostrador → forma de búsqueda
_SINONIMOS_DICTADO = {
    # Bieletas / brazos
    "bielete": "bieleta", "bieletes": "bieletas", "bieletta": "bieleta",
    "biela": "bieleta", "bielas": "bieletas", "biella": "bieleta",
    "tirante": "bieleta", "tirantes": "bieletas",
    # Bujes
    "bujete": "buje", "bujetes": "bujes", "bujes": "buje", "bush": "buje", "bushes": "bujes",
    "silent": "silentblock", "silentbloc": "silentblock", "sylent": "silentblock",
    # Rótulas / dirección
    "rotula": "rotula", "rotulas": "rotulas", "rótula": "rotula", "rótulas": "rotulas",
    "rotulita": "rotula", "terminal": "terminal", "extremo": "extremo",
    "precap": "precap", "axial": "axial",
    # Amortiguadores
    "amorti": "amortiguador", "amortis": "amortiguadores", "shock": "amortiguador",
    "shocks": "amortiguadores", "amortiguadores": "amortiguador",
    # Frenos
    "ferodo": "pastilla", "ferodos": "pastillas", "pastillas": "pastilla",
    "pastilla": "pastilla", "disco": "disco", "discos": "discos",
    "zapata": "zapata", "zapatas": "zapatas", "cilindro": "cilindro",
    "latiguillo": "latiguillo", "flexible": "flexible",
    # Filtros
    "filtro": "filtro", "filtros": "filtros", "filtro de aceite": "filtro aceite",
    "filtro de aire": "filtro aire", "filtro de nafta": "filtro nafta",
    "filtro de combustible": "filtro combustible", "filtro de polen": "filtro polen",
    "filtro habitaculo": "filtro habitaculo", "filtro habitáculo": "filtro habitaculo",
    # Motor / distribución
    "kit": "kit", "kits": "kit", "kit de distribucion": "kit distribucion",
    "kit distribución": "kit distribucion", "distribucion": "distribucion",
    "distribución": "distribucion", "distri": "distribucion",
    "tensor": "tensor", "tensors": "tensor", "tensores": "tensor",
    "correa": "correa", "correas": "correas", "polyv": "correa polyv",
    "poly v": "correa polyv", "auxiliar": "correa auxiliar",
    "polea": "polea", "poleas": "poleas",
    # Homocinéticas
    "homocinetica": "homocinetica", "homocinética": "homocinetica",
    "homocineticas": "homocineticas", "homo": "homocinetica", "homos": "homocineticas",
    "tripode": "homocinetica", "trípode": "homocinetica", "tripa": "homocinetica",
    "junta homo": "junta homocinetica",
    # Rulemanes
    "ruleman": "ruleman", "rulemán": "ruleman", "ruliman": "ruleman",
    "rulimán": "ruleman", "rodamiento": "ruleman", "rodamientos": "ruleman",
    # Encendido
    "bujia": "bujia", "bujía": "bujia", "bujias": "bujias", "bujías": "bujias",
    "vela": "bujia", "velas": "bujias", "bobina": "bobina", "bobinas": "bobinas",
    # Carrocería / ópticas
    "optica": "optica", "óptica": "optica", "opticas": "opticas", "ópticas": "opticas",
    "faro": "faro", "faros": "faros", "farito": "faro", "lampara": "lampara",
    "lámpara": "lampara", "lamparas": "lamparas", "lámparas": "lamparas",
    "paragolpe": "paragolpe", "paragolpes": "paragolpe", "parachoque": "paragolpe",
    "guardabarro": "guardabarro", "capot": "capot", "espejo": "espejo",
    # Líquidos
    "liquido": "liquido", "líquido": "liquido", "lbf": "liquido frenos",
    "refrigerante": "refrigerante", "coolant": "refrigerante", "anticongelante": "refrigerante",
    "aceite": "aceite", "lubricante": "aceite",
    # Embrague
    "embrague": "embrague", "crapodina": "crapodina", "crápodina": "crapodina",
    "crapodinas": "crapodinas", "collarin": "collarin", "collarín": "collarin",
    "disco embrague": "disco embrague", "placa": "placa embrague",
    # Suspensión
    "cazoleta": "cazoleta", "cazoletas": "cazoletas", "meseta": "meseta",
    "espiral": "espiral", "espirales": "espirales", "resorte": "resorte",
    "resortes": "resortes", "tope": "tope", "topes": "topes",
    # Retenes / juntas
    "reten": "reten", "retén": "reten", "retenes": "retenes", "retén": "reten",
    "junta": "junta", "juntas": "juntas", "empaque": "empaque", "empaquetadura": "empaque",
    "culata": "culata", "tapa de cilindros": "tapa cilindros",
    # Ejes / transmisión
    "semieje": "semieje", "semiejes": "semiejes", "palier": "palier", "paliers": "paliers",
    "cardan": "cardan", "cardán": "cardan", "cruceta": "cruceta", "crucetas": "crucetas",
    "maza": "maza", "mazas": "mazas",
    # Eléctrico
    "alternador": "alternador", "arranque": "arranque", "motor de arranque": "arranque",
    "bendix": "bendix", "sensor": "sensor", "sonda": "sensor", "lambda": "sonda lambda",
    "rele": "rele", "relé": "rele", "modulo": "modulo", "módulo": "modulo",
    # Climatización
    "compresor": "compresor", "evaporador": "evaporador", "condensador": "condensador",
    "electro": "electroventilador", "electroventilador": "electroventilador",
    "ventilador": "ventilador",
    # Bombas
    "bomba": "bomba", "bombas": "bombas", "bomba de agua": "bomba agua",
    "bomba de nafta": "bomba nafta", "bomba de combustible": "bomba combustible",
    "bombita": "bomba", "bombita de nafta": "bomba nafta",
    # Escobillas
    "escobilla": "escobilla", "escobillas": "escobillas",
    "limpia": "escobilla", "limpia parabrisas": "escobilla",
    # Varios
    "fuelle": "fuelle", "fuelles": "fuelles", "guardapolvo": "guardapolvo",
    "guardapolvos": "guardapolvo", "capuchon": "capuchon", "capuchón": "capuchon",
    "radiador": "radiador", "radiadores": "radiadores", "termostato": "termostato",
    "soporte": "soporte", "soportes": "soporte", "soporte motor": "soporte motor",
    "abrazadera": "abrazadera", "manguera": "manguera", "caño": "caño",
    "cañito": "caño", "perno": "perno", "bulon": "bulon", "bulón": "bulon",
    "trapecio": "trapecio", "puntal": "puntal", "bandeja": "bandeja",
    "cremallera": "cremallera", "barra estabilizadora": "barra estabilizadora",
    "directa": "direccion directa", "bomba direccion": "bomba direccion",
    "bomba de direccion": "bomba direccion", "bomba de dirección": "bomba direccion",
    "inyector": "inyector", "inyectores": "inyectores", "valvula": "valvula",
    "válvula": "valvula", "valvulas": "valvulas",
}

# Alias coloquiales de modelos
_ALIAS_VEHICULO = {
    "golcito": "gol", "golcitos": "gol", "golito": "gol",
    "peu": "peugeot", "208": "208", "207": "207",
    "chevy": "chevrolet", "chevrolet": "chevrolet",
    "vw": "volkswagen", "volkswagen": "volkswagen",
    "fiatito": "fiat", "renaultito": "renault",
}

# Modelos / referencias frecuentes (orden largo → corto en búsqueda)
_MODELOS_VEHICULO = (
  # VW
    "gol trend", "gol power", "gol country", "gol 1.6", "gol 1.4", "gol",
    "vento", "voyage", "suran", "polo", "amarok", "up", "bora", "t cross",
    "t-cross", "taos", "nivus", "virtus",
  # Peugeot / Citroën
    "partner", "208", "207", "206", "307", "308", "301", "408", "2008", "3008",
    "5008", "147", "berlingo", "c4", "c3", "c3 aircross", "xsara", "picasso",
    "jumper", "boxer", "expert",
  # Renault
    "kangoo", "clio", "sandero", "logan", "duster", "fluence", "megane",
    "symbol", "master", "trafic", "captur", "koleos", "alaskan",
  # Chevrolet / GM
    "corsa", "onix", "prisma", "cruze", "tracker", "spin", "agile", "celta",
    "classic", "aveo", "s10", "montana",
  # Ford
    "focus", "fiesta", "ecosport", "ranger", "ka", "ka plus", "territory",
    "mondeo", "maverick",
  # Fiat
    "palio", "siena", "uno", "cronos", "argo", "toro", "strada", "mobi",
    "pulse", "fastback", "fiorino", "ducato", "doblo",
  # Toyota / Nissan / Honda
    "corolla", "hilux", "etios", "yaris", "sw4", "rav4",
    "sentra", "tiida", "march", "note", "versa", "frontier",
    "fit", "civic", "hr-v", "hrv", "city",
  # Otros
    "duster", "fluence", "megane", "symbol",
)

_PAT_PARA_VEHICULO = None
_PAT_MODELO_SUELTO = None
_PAT_REPUESTO_PARA_VEH = None
_VOCAB_REPUESTO: Optional[Tuple[str, ...]] = None
_VOCAB_REPUESTO_SET: Optional[FrozenSet[str]] = None


def _modelos_regex_ordenados() -> str:
    return "|".join(
        re.escape(m) for m in sorted(set(_MODELOS_VEHICULO), key=len, reverse=True)
    )


def _modelos_solo_digitos() -> List[str]:
    return sorted({m for m in _MODELOS_VEHICULO if re.match(r"^\d{3,4}$", m)}, key=len, reverse=True)


def _pat_para_vehiculo():
    global _PAT_PARA_VEHICULO
    if _PAT_PARA_VEHICULO is None:
        m = _modelos_regex_ordenados()
        _PAT_PARA_VEHICULO = re.compile(
            rf"\b(?:para|del|de|en|modelo)\s+(?:el|la|los|las)?\s*"
            rf"(\d{{3,4}}|{m})\b",
            re.I,
        )
    return _PAT_PARA_VEHICULO


def _pat_modelo_suelto():
    global _PAT_MODELO_SUELTO
    if _PAT_MODELO_SUELTO is None:
        nums = "|".join(re.escape(n) for n in _modelos_solo_digitos())
        if nums:
            _PAT_MODELO_SUELTO = re.compile(rf"\b({nums})\b")
        else:
            _PAT_MODELO_SUELTO = re.compile(r"(?!x)x")
    return _PAT_MODELO_SUELTO


def palabras_cantidad_repuesto_voz() -> Tuple[str, ...]:
    """Raíces de repuesto para «dos bujes» → «buje 2 unidades»."""
    return tuple(
        sorted(
            {normalizar_para_busqueda(b) for b in _REPUESTOS_BASE if len(b) >= 3},
            key=len,
            reverse=True,
        )
    )


def obtener_vocabulario_repuesto_voz() -> Tuple[str, ...]:
    """Palabras que suelen iniciar descripción de repuesto (regex / corte cliente)."""
    global _VOCAB_REPUESTO, _VOCAB_REPUESTO_SET
    if _VOCAB_REPUESTO is not None:
        return _VOCAB_REPUESTO
    words = set()
    for base in _REPUESTOS_BASE:
        b = normalizar_para_busqueda(base)
        if b:
            words.add(b)
    for k, v in _SINONIMOS_DICTADO.items():
        for w in (k, v):
            wn = normalizar_para_busqueda(str(w).split()[0])
            if wn and len(wn) >= 3:
                words.add(wn)
    _VOCAB_REPUESTO = tuple(sorted(words, key=len, reverse=True))
    _VOCAB_REPUESTO_SET = frozenset(words)
    return _VOCAB_REPUESTO


def es_palabra_repuesto(palabra: str) -> bool:
    if _VOCAB_REPUESTO_SET is None:
        obtener_vocabulario_repuesto_voz()
    p = normalizar_para_busqueda(str(palabra or ""))
    return bool(p and p in _VOCAB_REPUESTO_SET)


def es_referencia_vehiculo(palabra: str) -> bool:
    """True si la palabra es un modelo de auto (207, gol, partner…)."""
    p = normalizar_para_busqueda(str(palabra or ""))
    if not p:
        return False
    if p in _ALIAS_VEHICULO:
        p = normalizar_para_busqueda(_ALIAS_VEHICULO[p])
    if re.match(r"^\d{3,4}$", p):
        return True
    vehs = {normalizar_para_busqueda(v) for v in _MODELOS_VEHICULO}
    vehs.update(normalizar_para_busqueda(v) for v in _ALIAS_VEHICULO.values())
    return p in vehs


def patron_repuesto_para_vehiculo():
    """Patrón «repuesto para el VEHICULO cantidad» (207, Gol, Partner…)."""
    global _PAT_REPUESTO_PARA_VEH
    if _PAT_REPUESTO_PARA_VEH is None:
        modelos = _modelos_regex_ordenados()
        _PAT_REPUESTO_PARA_VEH = re.compile(
            rf"\b([a-záéíóúñ][a-záéíóúñ\-]{{2,24}})\s+para\s+(?:el|la)\s+"
            rf"(\d{{3,4}}|{modelos})\s+(\d{{1,2}})\s*(?:unidades?|u\.?|uds?)?\b",
            re.I,
        )
    return _PAT_REPUESTO_PARA_VEH


def corregir_palabra_dictada(palabra: str) -> str:
    p = normalizar_para_busqueda(palabra)
    if not p:
        return palabra
    if p in _SINONIMOS_DICTADO:
        return _SINONIMOS_DICTADO[p]
    return palabra


def corregir_termino_repuesto(termino: str) -> str:
    """Aplica sinónimos palabra a palabra (bielete → bieleta)."""
    if not termino:
        return termino
    t_low = normalizar_texto_basico(str(termino)).lower().strip()
    if t_low in _SINONIMOS_DICTADO:
        return _SINONIMOS_DICTADO[t_low]
    partes = str(termino).strip().split()
    out = [corregir_palabra_dictada(p) for p in partes]
    return " ".join(out)


def extraer_vehiculos_de_texto(texto: str) -> List[str]:
    """Lista de referencias de vehículo mencionadas (sin duplicados)."""
    if not texto:
        return []
    t = normalizar_texto_basico(texto).lower()
    found = []
    for m in _pat_para_vehiculo().finditer(t):
        v = m.group(1).strip().lower()
        if v in _ALIAS_VEHICULO:
            v = _ALIAS_VEHICULO[v]
        if v and v not in found:
            found.append(v)
    for modelo in _MODELOS_VEHICULO:
        if re.search(rf"\b{re.escape(modelo)}\b", t) and modelo not in found:
            found.append(modelo)
    for alias, canon in _ALIAS_VEHICULO.items():
        if re.search(rf"\b{re.escape(alias)}\b", t) and canon not in found:
            if canon in _MODELOS_VEHICULO or re.match(r"^\d+$", canon):
                found.append(canon)
    for m in _pat_modelo_suelto().finditer(t):
        v = m.group(1)
        if v not in found:
            found.append(v)
    return found


def extraer_vehiculo_global_orden(texto: str) -> Optional[str]:
    """Vehículo que aplica a toda la orden si se menciona una sola vez."""
    vehs = extraer_vehiculos_de_texto(texto)
    if len(vehs) == 1:
        return vehs[0]
    return None


def extraer_vehiculo_cerca_termino(texto: str, termino: str) -> Optional[str]:
    """Vehículo asociado a un repuesto según proximidad en la frase."""
    if not texto or not termino:
        return None
    t = normalizar_texto_basico(texto).lower()
    term = normalizar_para_busqueda(corregir_termino_repuesto(termino))
    if not term:
        return None
    idx = t.find(term.split()[0]) if term.split() else -1
    if idx < 0:
        return extraer_vehiculo_global_orden(texto)

    ventana = t[max(0, idx - 40): idx + len(term) + 40]
    vehs = extraer_vehiculos_de_texto(ventana)
    if vehs:
        return vehs[-1]
    return extraer_vehiculo_global_orden(texto)


def _termino_es_solo_vehiculo(termino: str, vehiculos: List[str]) -> bool:
    t = normalizar_para_busqueda(str(termino or ""))
    if not t:
        return True
    veh_norm = {normalizar_para_busqueda(v) for v in (vehiculos or [])}
    return t in veh_norm


def _limpiar_termino_item_voz(termino: str, vehiculos: List[str], nombre_cliente: str = "") -> str:
    """Quita prefijo/sufijo de vehículo, cliente y corrige dictado."""
    from modulos.util_busqueda import parece_codigo_producto

    t = corregir_termino_repuesto(str(termino or "")).strip()
    if not t:
        return t
    if parece_codigo_producto(t):
        return t.upper()
    if nombre_cliente:
        nc = normalizar_texto_basico(nombre_cliente).upper()
        tu = normalizar_texto_basico(t).upper()
        if tu.startswith(nc + " "):
            t = t[len(nombre_cliente):].strip()
        tu = normalizar_texto_basico(t).upper()
        if tu == nc:
            return ""
    t_low = normalizar_texto_basico(t).lower()
    for v in vehiculos or []:
        vn = normalizar_texto_basico(v).lower()
        if t_low.startswith(vn + " "):
            t = t[len(vn):].strip()
            t_low = normalizar_texto_basico(t).lower()
        elif t_low.endswith(" " + vn):
            t = t[: -(len(vn) + 1)].strip()
            t_low = normalizar_texto_basico(t).lower()
        elif t_low == vn:
            return ""
    palabras = [p.upper() for p in t.split() if p]
    return " ".join(palabras)


def enriquecer_items_con_vehiculo(items: list, texto_completo: str) -> list:
    """Agrega vehiculo y limpia términos (orden de palabras no importa)."""
    if not items:
        return items
    vehs = extraer_vehiculos_de_texto(texto_completo)
    global_v = extraer_vehiculo_global_orden(texto_completo)
    try:
        from modulos.mostrador_voz_flujo import extraer_cliente_orden_voz

        nom_cli = (extraer_cliente_orden_voz(texto_completo) or {}).get("nombre_cliente", "")
    except Exception:
        nom_cli = ""
    from modulos.util_busqueda import parece_codigo_producto

    out = []
    vistos = set()
    for item in items:
        if not isinstance(item, dict):
            continue
        term = item.get("termino") or item.get("descripcion") or ""
        es_codigo = parece_codigo_producto(str(term))
        v = item.get("vehiculo")
        if not v and not es_codigo:
            v = extraer_vehiculo_cerca_termino(texto_completo, str(term)) or global_v
        term_limpio = _limpiar_termino_item_voz(str(term), vehs, nombre_cliente=str(nom_cli or ""))
        if nom_cli and term_limpio:
            palabras = [
                p for p in term_limpio.split()
                if p.upper() != str(nom_cli).upper()
            ]
            term_limpio = " ".join(palabras).strip()
        if not term_limpio or _termino_es_solo_vehiculo(term_limpio, vehs):
            continue
        cant = int(item.get("cantidad", 1))
        if cant > 99 and not str(term_limpio).isdigit():
            continue
        if v and str(cant) == re.sub(r"\D", "", str(v)):
            continue
        clave = (term_limpio, int(item.get("cantidad", 1)), v or "")
        if clave in vistos:
            continue
        vistos.add(clave)
        nuevo = dict(item)
        nuevo["termino"] = term_limpio
        if v:
            nuevo["vehiculo"] = v
        out.append(nuevo)
    return out
