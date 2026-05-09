"""Adapter de BD para Factura-mdb.

Este proyecto SOLO opera en modo MDB: lee y escribe directamente sobre el
archivo .mdb (Microsoft Access) del cliente, compartiéndolo con SIAP
legacy. NO existe modo SQLite aquí (a diferencia del proyecto del que se
heredó este código adaptable, donde MDB era un modo lab opcional).

El path al .mdb se lee desde `config.json` (clave `mdb.path`) o de la env
var `FACTURA_MDB_PATH` (sobrescribe al config).

Mantenemos las funciones `is_mdb_mode()` e `is_sqlite_mode()` por
compatibilidad con los routers heredados que las verifican: en este
proyecto la primera siempre es True y la segunda siempre False.
"""
from __future__ import annotations

from pathlib import Path

from ..config import settings


def is_mdb_mode() -> bool:
    """Modo MDB: SIEMPRE True en Factura-mdb."""
    return True


def is_sqlite_mode() -> bool:
    """Modo SQLite: SIEMPRE False en Factura-mdb."""
    return False


def get_mdb_path() -> Path:
    """Devuelve el path al .mdb activo, o lanza RuntimeError si falta.

    Raises:
        RuntimeError: si `config.json` no define `mdb.path` o el archivo
        no existe en disco.
    """
    p = settings.MDB_PATH
    if not p or str(p) == ".":
        raise RuntimeError(
            "MDB no configurado. Edita config.json (mdb.path) o setea "
            "FACTURA_MDB_PATH apuntando al archivo .mdb del cliente."
        )
    if not p.exists():
        raise RuntimeError(f"MDB no encontrado en: {p}")
    if not p.is_file():
        raise RuntimeError(f"La ruta no es un archivo: {p}")
    return p


def describe_mode() -> str:
    """Descripción legible para health endpoints / logs."""
    try:
        return f"mdb ({get_mdb_path().name})"
    except RuntimeError as exc:
        return f"mdb (ERROR: {exc})"
