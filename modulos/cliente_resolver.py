"""Resolución inteligente de clientes: Firebase + fonética + fuzzy para dictado por voz."""
from __future__ import annotations

import re
import time
import unicodedata
from difflib import SequenceMatcher
from typing import Optional

import streamlit as st

from modulos.db_firebase import obtener_clientes
from modulos.util_busqueda import normalizar_para_busqueda

_UMBRAL_ALTO = 0.82
_UMBRAL_MEDIO = 0.68
_TTL_CLIENTES_SEG = 300

_FONETICA_REEMPLAZOS = (
    (r"ph", "f"),
    (r"ll", "y"),
    (r"sh", "s"),
    (r"ch", "s"),
    (r"qu", "k"),
    (r"gu([ei])", r"g\1"),
    (r"h", ""),
    (r"v", "b"),
    (r"z", "s"),
    (r"c([ei])", r"s\1"),
    (r"ñ", "n"),
    (r"y", "i"),
    (r"ks", "x"),
    (r"[^a-z0-9\s]", ""),
)


def _sin_acentos(texto: str) -> str:
    if not texto:
        return ""
    t = unicodedata.normalize("NFD", str(texto))
    return "".join(c for c in t if unicodedata.category(c) != "Mn")


def fold_fonetico_es(texto: str) -> str:
    """Colapsa variaciones fonéticas del español rioplatense para comparar nombres."""
    t = normalizar_para_busqueda(_sin_acentos(texto))
    for patron, repl in _FONETICA_REEMPLAZOS:
        t = re.sub(patron, repl, t)
    t = re.sub(r"(.)\1+", r"\1", t)
    return re.sub(r"\s+", " ", t).strip()


def _ratio_similitud(a: str, b: str) -> float:
    if not a or not b:
        return 0.0
    return SequenceMatcher(None, a, b).ratio()


def _tokens_nombre(nombre: str) -> list[str]:
    return [t for t in fold_fonetico_es(nombre).split() if len(t) >= 2]


def _score_cliente(termino: str, nombre_db: str) -> float:
    term = str(termino or "").strip().upper()
    nombre = str(nombre_db or "").strip().upper()
    if not term or not nombre:
        return 0.0

    if term == nombre:
        return 1.0
    if nombre.startswith(term) or term.startswith(nombre):
        return 0.95

    term_norm = normalizar_para_busqueda(term)
    nom_norm = normalizar_para_busqueda(nombre)
    if term_norm and term_norm in nom_norm:
        return 0.92

    term_f = fold_fonetico_es(term)
    nom_f = fold_fonetico_es(nombre)
    if term_f == nom_f:
        return 0.9
    if nom_f.startswith(term_f) or term_f.startswith(nom_f):
        return 0.88

    term_tok = set(_tokens_nombre(term))
    nom_tok = set(_tokens_nombre(nombre))
    if term_tok and term_tok <= nom_tok:
        return 0.86 + 0.04 * min(len(term_tok), 3)
    if term_tok and nom_tok and term_tok & nom_tok:
        inter = len(term_tok & nom_tok)
        union = len(term_tok | nom_tok)
        score = 0.72 + 0.18 * (inter / union)
        ratio_f = _ratio_similitud(term_f, nom_f)
        if len(term_tok) >= 2 and inter < len(term_tok) and ratio_f < 0.78:
            if inter <= 1:
                return min(score, 0.5)
            return min(score, 0.62)
        if ratio_f >= 0.78:
            return max(score, ratio_f)
        return score

    ratio = _ratio_similitud(term_f, nom_f)
    if ratio >= 0.78:
        return ratio
    return _ratio_similitud(term_norm, nom_norm) * 0.85


def clientes_cache_mostrador(ttl_seg: int = _TTL_CLIENTES_SEG) -> dict:
    """Cache de clientes Firebase en sesión (evita lecturas repetidas al dictar)."""
    ahora = time.time()
    ts = float(st.session_state.get("_clientes_cache_ts", 0))
    if "_clientes_cache" in st.session_state and ahora - ts < ttl_seg:
        return st.session_state["_clientes_cache"]
    try:
        data = obtener_clientes() or {}
    except Exception:
        data = st.session_state.get("_clientes_cache") or {}
    st.session_state["_clientes_cache"] = data
    st.session_state["_clientes_cache_ts"] = ahora
    return data


def invalidar_cache_clientes_mostrador():
    st.session_state.pop("_clientes_cache", None)
    st.session_state.pop("_clientes_cache_ts", None)


def resolver_cliente_por_nombre(
    termino: str,
    clientes_db: Optional[dict] = None,
    umbral: float = _UMBRAL_MEDIO,
) -> tuple[Optional[dict], float, str]:
    """
    Busca el mejor cliente en Firebase para un nombre dictado.
    Retorna (datos_cliente, score, método).
    """
    term = str(termino or "").strip()
    if not term or re.search(r"consumidor\s+final|particular", term, re.I):
        return None, 0.0, "cf"

    db = clientes_db if clientes_db is not None else clientes_cache_mostrador()
    if not db:
        return None, 0.0, "sin_db"

    mejor: Optional[dict] = None
    mejor_score = 0.0
    metodo = ""

    for datos in db.values():
        if not isinstance(datos, dict):
            continue
        nombre = str(datos.get("nombre", "")).strip()
        if not nombre:
            continue
        score = _score_cliente(term, nombre)
        tipo = str(datos.get("tipo_cliente", "")).lower()
        if tipo in ("mecanico", "cuenta_corriente"):
            score += 0.02
        if score > mejor_score:
            mejor_score = score
            mejor = datos
            if score >= 0.99:
                metodo = "exacto"
            elif score >= 0.9:
                metodo = "prefijo"
            elif score >= 0.85:
                metodo = "tokens"
            else:
                metodo = "fonetico"

    if mejor and mejor_score >= umbral:
        return mejor, mejor_score, metodo
    return None, mejor_score, "sin_match"


def corregir_nombre_con_clientes(termino: str, clientes_db: Optional[dict] = None) -> str:
    """Si el nombre dictado se parece a un cliente de Firebase, devuelve el nombre oficial."""
    term = str(termino or "").strip()
    tok_count = len(_tokens_nombre(term))
    umbral = _UMBRAL_ALTO if tok_count >= 2 else _UMBRAL_MEDIO
    encontrado, score, _ = resolver_cliente_por_nombre(termino, clientes_db, umbral=umbral)
    if encontrado and score >= umbral:
        return str(encontrado.get("nombre", termino)).strip().upper()
    return term.upper()


def sugerencias_clientes(termino: str, max_resultados: int = 5) -> list[tuple[str, dict, float]]:
    """Lista [(nombre, datos, score), ...] para autocompletar o desambiguar."""
    term = str(termino or "").strip()
    db = clientes_cache_mostrador()
    if not db:
        return []

    scored: list[tuple[str, dict, float]] = []
    for datos in db.values():
        if not isinstance(datos, dict):
            continue
        nombre = str(datos.get("nombre", "")).strip()
        if not nombre:
            continue
        if not term:
            score = 0.5
            if str(datos.get("tipo_cliente", "")).lower() in ("mecanico", "cuenta_corriente"):
                score = 0.7
        else:
            score = _score_cliente(term, nombre)
        if term or score >= 0.5:
            scored.append((nombre, datos, score))

    scored.sort(key=lambda x: (-x[2], x[0]))
    if term:
        scored = [s for s in scored if s[2] >= 0.45]
    return scored[:max_resultados]


def listar_clientes_frecuentes(max_resultados: int = 8) -> list[tuple[str, dict]]:
    """Clientes prioritarios para atajos en pantalla (mecánicos y cuenta corriente primero)."""
    db = clientes_cache_mostrador()
    if not db:
        return []

    def _prio(datos: dict) -> tuple:
        tipo = str(datos.get("tipo_cliente", "")).lower()
        rank = {"mecanico": 0, "cuenta_corriente": 1, "ocasional": 2}.get(tipo, 3)
        return (rank, str(datos.get("nombre", "")).upper())

    items = [
        (str(d.get("nombre", "")).strip(), d)
        for d in db.values()
        if isinstance(d, dict) and str(d.get("nombre", "")).strip()
    ]
    items.sort(key=lambda x: _prio(x[1]))
    return items[:max_resultados]
