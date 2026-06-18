"""Utilidades de fecha/hora — almacenamiento UTC, visualización Argentina."""
from datetime import date, datetime, time, timedelta, timezone

try:
    from zoneinfo import ZoneInfo
    TZ_ARGENTINA = ZoneInfo("America/Argentina/Buenos_Aires")
except Exception:
    # Argentina sin DST desde 2009 (UTC-3)
    TZ_ARGENTINA = timezone(timedelta(hours=-3))


def _a_utc(valor):
    """Normaliza Firestore / datetime / date a datetime UTC."""
    if valor is None:
        return None
    if isinstance(valor, datetime):
        if valor.tzinfo is None:
            return valor.replace(tzinfo=timezone.utc)
        return valor.astimezone(timezone.utc)
    if isinstance(valor, date):
        return datetime.combine(valor, time.min, tzinfo=timezone.utc)
    if hasattr(valor, "timestamp"):
        return datetime.fromtimestamp(valor.timestamp(), tz=timezone.utc)
    return None


def ahora_utc():
    return datetime.now(timezone.utc)


def ahora_ar():
    """Hora actual en Argentina."""
    return ahora_utc().astimezone(TZ_ARGENTINA)


def fecha_hoy_ar() -> date:
    """Fecha calendario de hoy en Argentina (no la del servidor UTC)."""
    return ahora_ar().date()


def rango_fechas_ar_a_utc(fecha_desde: date, fecha_hasta: date):
    """
    Convierte un rango de fechas calendario (Argentina) a UTC para filtrar Firestore.
    Inclusive: desde 00:00:00 AR del primer día hasta 23:59:59.999 AR del último.
    """
    inicio = datetime.combine(fecha_desde, time.min, tzinfo=TZ_ARGENTINA).astimezone(timezone.utc)
    fin = datetime.combine(fecha_hasta, time.max, tzinfo=TZ_ARGENTINA).astimezone(timezone.utc)
    return inicio, fin


def formatear_fecha_ar(valor, con_hora=True):
    """Convierte timestamp UTC (Firestore) a hora Argentina para mostrar."""
    dt = _a_utc(valor)
    if not dt:
        return "—"
    local = dt.astimezone(TZ_ARGENTINA)
    if con_hora:
        return local.strftime("%d/%m/%Y %H:%M")
    return local.strftime("%d/%m/%Y")
