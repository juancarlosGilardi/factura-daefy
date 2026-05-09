"""Validadores y normalizadores para datos legacy SIAP.

Las bases SIAP suelen venir con basura: RUC con espacios, fechas como string,
montos con coma decimal, descripciones gigantes con saltos de línea, etc.
Estas funciones devuelven valores "limpios" listos para los modelos
SQLAlchemy de Factura-mdb, sin lanzar excepciones.
"""
from __future__ import annotations

import re
from datetime import date, datetime
from typing import Any, Optional


# ---------------------------------------------------------------------------
# Documentos
# ---------------------------------------------------------------------------
_PESOS_RUC = (5, 4, 3, 2, 7, 6, 5, 4, 3, 2)
_PREFIJOS_RUC_VALIDOS = ("10", "15", "16", "17", "20")


def validar_ruc(ruc: Any) -> tuple[bool, str]:
    """Valida RUC mod-11.

    Returns:
        Tupla `(válido, ruc_normalizado_o_mensaje_error)`. Si es válido,
        el segundo elemento es el RUC con sólo dígitos. Si no lo es,
        es un mensaje describiendo el problema.
    """
    if ruc is None:
        return False, "RUC vacío"
    s = "".join(c for c in str(ruc) if c.isdigit())
    if len(s) != 11:
        return False, f"RUC debe tener 11 dígitos: {s}"
    if s[:2] not in _PREFIJOS_RUC_VALIDOS:
        return False, f"RUC con prefijo inválido: {s[:2]}"
    suma = sum(int(d) * w for d, w in zip(s[:10], _PESOS_RUC))
    resto = suma % 11
    dv = 11 - resto
    if dv == 11:
        dv = 0
    elif dv == 10:
        dv = 1
    if str(dv) != s[10]:
        return False, f"DV mod-11 incorrecto: {s}"
    return True, s


def validar_dni(dni: Any) -> tuple[bool, str]:
    """Valida DNI peruano (8 dígitos exactos)."""
    if dni is None:
        return False, "DNI vacío"
    s = "".join(c for c in str(dni) if c.isdigit())
    if len(s) != 8:
        return False, f"DNI debe tener 8 dígitos: {s}"
    return True, s


def inferir_tipo_documento(numero: str) -> str:
    """Devuelve código SUNAT (cat 06) inferido por el número.

    * 11 dígitos numéricos con prefijo válido → "6" (RUC)
    * 8 dígitos numéricos                   → "1" (DNI)
    * 9-12 caracteres alfanuméricos         → "4" (Carnet de extranjería)
    * Resto                                 → "0" (otros / no domiciliado)
    """
    if not numero:
        return "0"
    s = str(numero).strip()
    if s.isdigit() and len(s) == 11 and s[:2] in _PREFIJOS_RUC_VALIDOS:
        return "6"
    if s.isdigit() and len(s) == 8:
        return "1"
    if 9 <= len(s) <= 12 and any(c.isalpha() for c in s):
        return "4"
    return "0"


def normalizar_tipo_documento_mdb(tipo_mdb: Any, numero: str) -> str:
    """Normaliza el campo F2TIPDOC del MDB al catálogo 06 SUNAT.

    El MDB puede traer "01", "1", "06", "6" (varía según versión SIAP)
    o nada. Si el valor no es claro, infiere por el número.
    """
    if tipo_mdb is None:
        return inferir_tipo_documento(numero)
    s = str(tipo_mdb).strip()
    mapeo = {
        "01": "1", "1": "1",
        "04": "4", "4": "4",
        "06": "6", "6": "6",
        "07": "7", "7": "7",
        "00": "0", "0": "0",
    }
    if s in mapeo:
        return mapeo[s]
    return inferir_tipo_documento(numero)


# ---------------------------------------------------------------------------
# Fechas
# ---------------------------------------------------------------------------
_FECHA_FORMATOS = (
    "%Y-%m-%d",
    "%d/%m/%Y",
    "%d-%m-%Y",
    "%Y/%m/%d",
    "%d/%m/%y",
)


def validar_fecha(v: Any) -> Optional[date]:
    """Convierte distintos formatos de fecha legacy a `date`.

    Acepta `date`, `datetime`, string en varios formatos comunes, o None.
    Devuelve `None` si no puede parsear (en lugar de lanzar).
    """
    if v is None or v == "":
        return None
    if isinstance(v, date) and not isinstance(v, datetime):
        return v
    if isinstance(v, datetime):
        return v.date()
    s = str(v).strip()
    if not s:
        return None
    # Algunos MDB exportan "2025-01-05 00:00:00"; tomamos el prefijo fecha.
    s_short = s.split(" ", 1)[0].split("T", 1)[0]
    for fmt in _FECHA_FORMATOS:
        try:
            return datetime.strptime(s_short, fmt).date()
        except ValueError:
            continue
    return None


# ---------------------------------------------------------------------------
# Montos
# ---------------------------------------------------------------------------
def validar_monto(v: Any, default: float = 0.0) -> float:
    """Convierte a `float`. Acepta coma decimal y devuelve `default` si falla."""
    if v is None or v == "":
        return default
    if isinstance(v, (int, float)):
        try:
            f = float(v)
        except (ValueError, TypeError):
            return default
        # NaN/Inf vienen del MDB con frecuencia: rechazar.
        if f != f or f in (float("inf"), float("-inf")):
            return default
        return f
    s = str(v).strip()
    if not s:
        return default
    # Normaliza coma decimal y separadores de miles (español/europa)
    # Ej.: "1.234,56" → "1234.56", "1234,56" → "1234.56"
    if "," in s and "." in s:
        # asume "." mil y "," decimal (formato es-PE legacy)
        s = s.replace(".", "").replace(",", ".")
    elif "," in s:
        s = s.replace(",", ".")
    try:
        f = float(s)
        if f != f or f in (float("inf"), float("-inf")):
            return default
        return f
    except (ValueError, TypeError):
        return default


# ---------------------------------------------------------------------------
# Strings
# ---------------------------------------------------------------------------
_WS_RE = re.compile(r"\s+")


def normalizar_descripcion(s: Any, maxlen: int = 500) -> str:
    """Limpia espacios, saltos de línea y trunca a `maxlen`."""
    if s is None:
        return ""
    txt = str(s).replace("\r", " ").replace("\n", " ").replace("\t", " ")
    txt = _WS_RE.sub(" ", txt).strip()
    return txt[:maxlen]


def normalizar_codigo(s: Any, maxlen: int = 40) -> str:
    """Códigos: trim, sin espacios internos, mayúsculas, len limitado."""
    if s is None:
        return ""
    txt = str(s).strip().upper()
    txt = _WS_RE.sub("_", txt)
    return txt[:maxlen]


_TELEFONO_RE = re.compile(r"[^\d+\-() ]")


def normalizar_telefono(s: Any, maxlen: int = 40) -> str:
    """Mantiene dígitos, +, -, paréntesis y espacios. Trunca."""
    if s is None:
        return ""
    txt = _TELEFONO_RE.sub("", str(s))
    txt = txt.strip()
    return txt[:maxlen]


_EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$")


def normalizar_email(s: Any) -> Optional[str]:
    """Devuelve el email si es válido; None si no lo es o viene vacío.

    Algunos SIAP guardan basura en F2EMAIL ("-", " ", "n/a"); lo descartamos
    silenciosamente para no romper comprobantes futuros con email inválido.
    """
    if s is None:
        return None
    txt = str(s).strip()
    if not txt or txt in ("-", "n/a", "N/A", "."):
        return None
    if _EMAIL_RE.match(txt):
        return txt[:120]
    return None


# ---------------------------------------------------------------------------
# Tipos de documento de comprobante (catálogo 01)
# ---------------------------------------------------------------------------
TIPOS_COMPROBANTE_VALIDOS = ("01", "03", "07", "08")


def normalizar_tipo_comprobante(v: Any) -> Optional[str]:
    """Devuelve el código del catálogo 01 SUNAT.

    Si viene "1", "3", "7", "8" agrega el cero a la izquierda.
    Si no es uno de los soportados, devuelve None.
    """
    if v is None:
        return None
    s = str(v).strip()
    if not s:
        return None
    if len(s) == 1 and s.isdigit():
        s = f"0{s}"
    if s in TIPOS_COMPROBANTE_VALIDOS:
        return s
    return None


# ---------------------------------------------------------------------------
# Moneda
# ---------------------------------------------------------------------------
def normalizar_moneda(v: Any) -> str:
    """SIAP guarda 'S' para soles y 'D' para dólares. Otras versiones
    ya guardan 'PEN'/'USD'. Default PEN.
    """
    if v is None:
        return "PEN"
    s = str(v).strip().upper()
    if s in ("D", "USD", "$"):
        return "USD"
    if s in ("S", "PEN", "S/", "S/."):
        return "PEN"
    if s in ("EUR", "€"):
        return "EUR"
    return "PEN"
