"""Helpers compartidos para generacion de PDFs (filtros Jinja2, logo, etc.).

Todo lo visible en el PDF debe salir de BD o archivos configurables.
Nada hardcoded.
"""
import os
import base64


# ────────────────────────────────────────────────────
# FORMATO DE NUMEROS — Peru (miles=',', decimales='.')
# ────────────────────────────────────────────────────
def fmt_money(value, decimals: int = 2) -> str:
    """Formatea un numero como moneda Peru: 1,475.00"""
    if value is None or value == "":
        value = 0
    try:
        n = float(value)
    except (TypeError, ValueError):
        return "0.00"
    return f"{n:,.{decimals}f}"


def fmt_qty(value) -> str:
    """Formatea cantidad: 2 decimales por defecto."""
    if value is None:
        return "0.00"
    try:
        n = float(value)
    except (TypeError, ValueError):
        return "0.00"
    return f"{n:,.2f}"


# ────────────────────────────────────────────────────
# LOGO — leido desde Empresa.logo_path (path absoluto local)
# ────────────────────────────────────────────────────
def resolver_logo(empresa) -> tuple[str, str]:
    """Devuelve (logo_path_file_uri, logo_b64).

    Factura-mdb guarda el logo de la empresa en `Empresa.logo_path` como path
    absoluto local. Si existe el archivo, retorna logo_b64 (base64) inline
    para evitar problemas con file:// en motores de PDF.
    """
    logo_path = getattr(empresa, "logo_path", None) if empresa else None
    if not logo_path or not os.path.isfile(logo_path):
        return "", ""
    try:
        with open(logo_path, "rb") as f:
            b64 = base64.b64encode(f.read()).decode()
        return "file://" + logo_path, b64
    except Exception:
        return "", ""


# ────────────────────────────────────────────────────
# REGISTRO DE FILTROS EN JINJA2 ENV
# ────────────────────────────────────────────────────
def registrar_filtros(env):
    """Registra los filtros custom en el environment de Jinja2."""
    env.filters["money"] = fmt_money
    env.filters["qty"] = fmt_qty
    return env
