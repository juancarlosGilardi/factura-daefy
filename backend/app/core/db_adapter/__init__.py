"""Adapter de BD para Factura-mdb.

Soporta tres modos de BD configurables en `mdb.mode` (config.json):
- mdb: archivo .mdb (Access) compartido con SIAP legacy
- dbf: archivos DBF (e.g., Daefy VFP9)
- sqlite: base de datos SQLite (legacy, para compatibilidad)

El path se lee desde config.json o env vars (que sobrescriben).
"""
from __future__ import annotations

from pathlib import Path

from ..config import settings


def is_mdb_mode() -> bool:
    """True si modo activo es 'mdb'."""
    return settings.MDB_MODE == "mdb"


def is_dbf_mode() -> bool:
    """True si modo activo es 'dbf'."""
    return settings.MDB_MODE == "dbf"


def is_sqlite_mode() -> bool:
    """True si modo activo es 'sqlite'."""
    return settings.MDB_MODE == "sqlite"


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


def get_dbf_path() -> Path:
    """Devuelve el path a la carpeta de DBFs, o lanza RuntimeError si falta.

    Raises:
        RuntimeError: si `config.json` no define `dbf.path` o la carpeta
        no existe en disco.
    """
    p = settings.DBF_PATH
    if not p or str(p) == ".":
        raise RuntimeError(
            "DBF no configurado. Edita config.json (dbf.path) o setea "
            "FACTURA_DBF_PATH apuntando a la carpeta con los .dbf."
        )
    if not p.exists():
        raise RuntimeError(f"Carpeta DBF no encontrada en: {p}")
    if not p.is_dir():
        raise RuntimeError(f"La ruta no es una carpeta: {p}")
    return p


def describe_mode() -> str:
    """Descripción legible del modo activo para health endpoints / logs."""
    try:
        if is_mdb_mode():
            return f"mdb ({get_mdb_path().name})"
        elif is_dbf_mode():
            return f"dbf ({get_dbf_path().name})"
        else:
            return "sqlite"
    except RuntimeError as exc:
        return f"{settings.MDB_MODE} (ERROR: {exc})"
