"""Utilidades de fecha/hora — visualización en Argentina."""
from datetime import datetime, timezone

try:
    from zoneinfo import ZoneInfo
    TZ_ARGENTINA = ZoneInfo("America/Argentina/Buenos_Aires")
except Exception:
    TZ_ARGENTINA = timezone.utc


def _a_utc(dt):
    if dt is None:
        return None
    if isinstance(dt, datetime):
        if dt.tzinfo is None:
            return dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    if hasattr(dt, "timestamp"):
        return datetime.fromtimestamp(dt.timestamp(), tz=timezone.utc)
    return None


def formatear_fecha_ar(valor, con_hora=True):
    """Convierte timestamp UTC (Firestore) a hora Argentina para mostrar."""
    dt = _a_utc(valor)
    if not dt:
        return "—"
    try:
        local = dt.astimezone(TZ_ARGENTINA)
    except Exception:
        local = dt
    if con_hora:
        return local.strftime("%d/%m/%Y %H:%M")
    return local.strftime("%d/%m/%Y")


def ahora_utc():
    return datetime.now(timezone.utc)
