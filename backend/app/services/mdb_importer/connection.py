"""Wrapper pyodbc para acceder a archivos .mdb de SIAP.

Requiere:
  * pyodbc instalado (`pip install pyodbc`).
  * Microsoft Access Database Engine (Windows). Descarga oficial:
    https://www.microsoft.com/en-us/download/details.aspx?id=54920
    Es **gratuito** y portable; no necesitas Office instalado.

En Linux/macOS pyodbc puede instalarse pero no existe driver Access
oficial — el módulo arroja `MDBConnectionError` con mensaje claro.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger("factura_mdb.mdb")

# Drivers en orden de preferencia. El primero soporta también .accdb.
ACCESS_DRIVERS: tuple[str, ...] = (
    "{Microsoft Access Driver (*.mdb, *.accdb)}",
    "{Microsoft Access Driver (*.mdb)}",
)


class MDBConnectionError(Exception):
    """Cualquier fallo abriendo o consultando un archivo .mdb."""


def detectar_driver() -> str:
    """Devuelve el primer driver Access disponible.

    Raises:
        MDBConnectionError: si pyodbc no está instalado o no hay driver.
    """
    try:
        import pyodbc  # noqa: WPS433 — import aquí para diagnosticar
    except ImportError as e:
        raise MDBConnectionError(
            "pyodbc no instalado. Instala con: pip install pyodbc"
        ) from e

    drivers_disponibles = pyodbc.drivers()
    logger.debug("Drivers ODBC disponibles: %s", drivers_disponibles)

    for d in ACCESS_DRIVERS:
        nombre = d.strip("{}")
        if nombre in drivers_disponibles:
            return d

    raise MDBConnectionError(
        "No se detectó Microsoft Access Database Engine. "
        "Descárgalo gratis de "
        "https://www.microsoft.com/en-us/download/details.aspx?id=54920 "
        "(elige la versión 64-bit si tu Python es 64-bit)."
    )


def conectar(mdb_path: Path | str, readonly: bool = True) -> Any:
    """Abre conexión pyodbc al archivo .mdb.

    Args:
        mdb_path: ruta absoluta al .mdb (o .accdb).
        readonly: si True (default) abre en modo solo lectura. Si False,
            abre en modo lectura/escritura — usado por mdb_writer (Sprint 2).
            La conexión read-write se abre con `autocommit=False` para que
            el caller controle commit/rollback.

    Returns:
        pyodbc.Connection — el caller debe cerrarla con `.close()`.

    Raises:
        MDBConnectionError: archivo inexistente, driver faltante o
        problema durante la apertura.
    """
    import pyodbc  # noqa: WPS433

    mdb_path = Path(mdb_path)
    if not mdb_path.exists():
        raise MDBConnectionError(f"Archivo no existe: {mdb_path}")
    if not mdb_path.is_file():
        raise MDBConnectionError(f"La ruta no es un archivo: {mdb_path}")

    driver = detectar_driver()
    # `DBQ=` espera la ruta sin comillas. Aceptamos espacios porque las
    # rutas reales de usuario suelen tenerlos.
    conn_str = f"DRIVER={driver};DBQ={mdb_path};"
    try:
        if readonly:
            conn = pyodbc.connect(conn_str, readonly=True, autocommit=True)
        else:
            # Lectura/escritura: autocommit=False para que el writer
            # controle commit/rollback dentro de un context manager.
            conn = pyodbc.connect(conn_str, readonly=False, autocommit=False)
        logger.info("MDB abierto: %s (readonly=%s)", mdb_path.name, readonly)
        return conn
    except Exception as e:  # noqa: BLE001
        raise MDBConnectionError(f"Error conectando al .mdb: {e}") from e


def cerrar_silencioso(conn: Any | None) -> None:
    """Cierra la conexión sin propagar excepciones (útil en `finally`)."""
    if conn is None:
        return
    try:
        conn.close()
    except Exception:  # noqa: BLE001
        logger.debug("Cerrando conexión MDB con error ignorado", exc_info=True)
