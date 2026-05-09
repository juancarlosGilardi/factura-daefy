"""Detección de lock de Access (.ldb / .laccdb) y context manager de escritura.

Sprint 2 (Factura-mdb): cuando el .mdb se comparte con SIAP corriendo en
paralelo, Access mantiene un archivo .ldb (mdb) o .laccdb (accdb) como
"lock file". Este archivo señala apertura activa pero NO siempre implica
acceso exclusivo — Access permite multi-usuario en modo compartido.

Estrategia Sprint 2:
- `is_locked()` y `wait_for_unlock()` quedan disponibles para diagnóstico
  y casos en que el caller quiera esperar antes de escribir.
- `write_cursor()` (context manager) abre conexión read/write y maneja
  commit/rollback. NO espera por defecto: si Access está en modo
  compartido, la conexión ABRE bien y la escritura prospera. Si está
  en exclusivo, pyodbc lanza error y lo envolvemos como MDBLockTimeout
  con mensaje claro.
"""
from __future__ import annotations

import logging
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, Any

from . import get_mdb_path

logger = logging.getLogger("factura_mdb.mdb.lock")


class MDBLockTimeout(Exception):
    """Se lanzó al no poder adquirir acceso de escritura al .mdb."""


def _lock_path(mdb_path: Path) -> Path:
    """Devuelve el path al archivo de lock que usa Access.

    - .mdb → .ldb (Access 97-2003 / JET 4)
    - .accdb → .laccdb (Access 2007+)
    """
    if mdb_path.suffix.lower() == ".accdb":
        return mdb_path.with_suffix(".laccdb")
    return mdb_path.with_suffix(".ldb")


def is_locked() -> bool:
    """True si existe el .ldb/.laccdb (Access tiene el archivo abierto).

    Nota: la presencia del lock file NO implica acceso exclusivo. Access
    puede tener el .ldb activo en modo compartido (varios usuarios). Este
    helper sirve para diagnóstico/UI; las decisiones de escritura se
    delegan a `write_cursor()` que confía en pyodbc para abrir o fallar.
    """
    try:
        mdb = get_mdb_path()
    except Exception:  # noqa: BLE001
        return False
    return _lock_path(mdb).exists()


def wait_for_unlock(timeout_s: float = 30.0, poll_ms: int = 500) -> None:
    """Espera hasta que el archivo .ldb desaparezca.

    Args:
        timeout_s: segundos máximos a esperar.
        poll_ms: intervalo de chequeo en milisegundos.

    Raises:
        MDBLockTimeout: si no se desbloqueó dentro del timeout.

    Uso opcional: la mayoría de operaciones llaman directamente a
    `write_cursor()` sin esperar, ya que Access permite escritura
    compartida. Este helper sirve cuando el caller necesita exclusividad
    (p. ej. CREATE TABLE, ALTER TABLE).
    """
    mdb = get_mdb_path()
    lock = _lock_path(mdb)
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        if not lock.exists():
            return
        time.sleep(poll_ms / 1000.0)
    raise MDBLockTimeout(
        f"El archivo .mdb está bloqueado por otro proceso (probablemente "
        f"SIAP) hace más de {timeout_s} segundos. Cierra SIAP e intenta "
        f"de nuevo. Lock: {lock.name}"
    )


@contextmanager
def write_cursor() -> Iterator[Any]:
    """Context manager que abre cursor pyodbc en modo escritura.

    Maneja:
    - apertura read/write
    - commit al salir sin error
    - rollback en excepción
    - cierre garantizado

    Si pyodbc no puede abrir (lock exclusivo, permisos), envuelve el
    error en `MDBLockTimeout` con mensaje accionable.

    Yields:
        pyodbc.Cursor — el caller usa `cur.execute(...)`.
    """
    from ...services.mdb_importer.connection import conectar, MDBConnectionError

    try:
        cn = conectar(get_mdb_path(), readonly=False)
    except MDBConnectionError as e:
        msg = str(e).lower()
        if (
            "could not lock" in msg
            or "no se puede" in msg
            or "exclusivo" in msg
            or "exclusive" in msg
            or "denied" in msg
            or "denegado" in msg
        ):
            raise MDBLockTimeout(
                f"No se pudo abrir el .mdb para escritura. Probablemente "
                f"SIAP lo tiene en modo exclusivo. Cierra SIAP e intenta "
                f"de nuevo. Detalle: {e}"
            ) from e
        raise

    cur = cn.cursor()
    try:
        yield cur
        cn.commit()
    except Exception:
        try:
            cn.rollback()
        except Exception:  # noqa: BLE001
            logger.debug("rollback falló (ignorado)", exc_info=True)
        raise
    finally:
        try:
            cur.close()
        except Exception:  # noqa: BLE001
            pass
        try:
            cn.close()
        except Exception:  # noqa: BLE001
            pass
