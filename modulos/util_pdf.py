"""Texto seguro para PDF con fuente Helvetica (Latin-1)."""


def texto_para_pdf(texto) -> str:
    """Evita FPDFUnicodeEncodingException: reemplaza Unicode raro y limita a Latin-1."""
    if texto is None:
        return ""
    s = str(texto)
    for viejo, nuevo in (
        ("\u2014", "-"),
        ("\u2013", "-"),
        ("\u2026", "..."),
        ("\u201c", '"'),
        ("\u201d", '"'),
        ("\u2018", "'"),
        ("\u2019", "'"),
    ):
        s = s.replace(viejo, nuevo)
    return s.encode("latin-1", errors="replace").decode("latin-1")
