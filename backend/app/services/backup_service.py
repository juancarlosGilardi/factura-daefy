"""Servicio de backup local de la BD SQLite.

Crea archivos .zip con la copia de `factura_mdb.db` en `%APPDATA%/Factura-mdb/backups/`.

Convenciones:
    factura_mdb_YYYYMMDD_HHMMSS_<motivo>.db.zip

Funciones:
    crear_backup(motivo)            -> Path del .zip generado
    listar_backups()                -> list[dict] con metadata
    limpiar_backups_antiguos(dias)  -> cantidad de archivos eliminados
    restaurar_backup(zip_path)      -> Path de la BD restaurada (con safety copy)
"""
from __future__ import annotations

import logging
import re
import shutil
import sqlite3
import tempfile
import zipfile
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Iterable

from ..core.config import backup_dir, db_path

# Tablas que debe contener cualquier BD Factura-mdb válida (subset esperado).
_TABLAS_REQUERIDAS = {"empresas", "comprobantes"}

logger = logging.getLogger("factura_mdb.backup")

# Mínimo a conservar siempre, sin importar antigüedad
MIN_RETAIN_COUNT = 7

_NAME_RE = re.compile(
    r"^factura_mdb_(\d{8})_(\d{6})(?:_\d{6})?_([A-Za-z0-9_-]+?)(?:_\d+)?\.db\.zip$"
)


@dataclass
class BackupInfo:
    nombre: str
    ruta: Path
    tamano_bytes: int
    fecha: datetime
    motivo: str

    def to_dict(self) -> dict:
        return {
            "nombre": self.nombre,
            "tamano_bytes": self.tamano_bytes,
            "tamano_legible": _bytes_legible(self.tamano_bytes),
            "fecha": self.fecha.isoformat(timespec="seconds"),
            "motivo": self.motivo,
        }


# ---------------------------------------------------------------------------
def crear_backup(motivo: str = "manual") -> Path:
    """Crea un .zip con la BD actual y devuelve su ruta."""
    motivo = re.sub(r"[^A-Za-z0-9_-]", "_", motivo)[:30] or "manual"
    src = db_path()
    if not src.exists():
        raise FileNotFoundError(f"No se encontró la BD en {src}")

    ts = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    out = backup_dir() / f"factura_mdb_{ts}_{motivo}.db.zip"
    n = 1
    while out.exists():
        out = backup_dir() / f"factura_mdb_{ts}_{motivo}_{n}.db.zip"
        n += 1

    with zipfile.ZipFile(out, "x", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.write(src, arcname="factura_mdb.db")
        # incluir también journals si existen (WAL)
        for ext in ("-wal", "-shm"):
            extra = src.with_name(src.name + ext)
            if extra.exists():
                zf.write(extra, arcname=f"factura_mdb.db{ext}")

    logger.info(f"Backup creado: {out.name} ({out.stat().st_size} bytes)")
    return out


# ---------------------------------------------------------------------------
def listar_backups() -> list[BackupInfo]:
    backups: list[BackupInfo] = []
    for p in sorted(backup_dir().glob("factura_mdb_*.db.zip"), reverse=True):
        info = _parse_nombre(p)
        if info:
            backups.append(info)
    return backups


def _parse_nombre(p: Path) -> BackupInfo | None:
    m = _NAME_RE.match(p.name)
    if not m:
        # archivo con nombre raro: lo conservamos pero con fecha de mtime
        try:
            return BackupInfo(
                nombre=p.name,
                ruta=p,
                tamano_bytes=p.stat().st_size,
                fecha=datetime.fromtimestamp(p.stat().st_mtime),
                motivo="?",
            )
        except OSError:
            return None
    fecha_str, hora_str, motivo = m.groups()
    try:
        fecha = datetime.strptime(fecha_str + hora_str, "%Y%m%d%H%M%S")
    except ValueError:
        fecha = datetime.fromtimestamp(p.stat().st_mtime)
    return BackupInfo(
        nombre=p.name,
        ruta=p,
        tamano_bytes=p.stat().st_size,
        fecha=fecha,
        motivo=motivo,
    )


# ---------------------------------------------------------------------------
def limpiar_backups_antiguos(retain_dias: int = 30, max_count: int = 100) -> int:
    """Borra los .zip > retain_dias O cuando excedan `max_count` archivos.

    Conserva al menos los últimos MIN_RETAIN_COUNT más recientes sin importar
    la antigüedad ni el max_count.
    """
    backups = listar_backups()  # ya vienen ordenados desc por nombre
    if len(backups) <= MIN_RETAIN_COUNT:
        return 0

    corte = datetime.now() - timedelta(days=retain_dias)
    eliminados = 0
    # Mantener los primeros MIN_RETAIN_COUNT pase lo que pase
    for idx, b in enumerate(backups):
        if idx < MIN_RETAIN_COUNT:
            continue
        # Bug C14/G4: borrar también si excedió max_count
        excede_count = idx >= max_count
        muy_antiguo = b.fecha < corte
        if excede_count or muy_antiguo:
            try:
                b.ruta.unlink()
                eliminados += 1
                motivo = "exceso" if excede_count else "antigüedad"
                logger.info(f"Backup eliminado ({motivo}): {b.nombre}")
            except OSError as e:
                logger.warning(f"No se pudo eliminar {b.nombre}: {e}")
    return eliminados


# ---------------------------------------------------------------------------
def _es_sqlite_valido(path: Path) -> bool:
    """True si el archivo es un SQLite legible y pasa integrity_check."""
    try:
        # Modo solo-lectura para no crear el archivo si no existe.
        uri = f"file:{path.as_posix()}?mode=ro"
        with sqlite3.connect(uri, uri=True) as conn:
            row = conn.execute("PRAGMA integrity_check").fetchone()
            return bool(row) and row[0] == "ok"
    except sqlite3.DatabaseError:
        return False
    except Exception:  # noqa: BLE001
        return False


def _tiene_tablas_factura_mdb(path: Path) -> bool:
    """Verifica que la BD contenga las tablas esperadas de Factura-mdb."""
    try:
        uri = f"file:{path.as_posix()}?mode=ro"
        with sqlite3.connect(uri, uri=True) as conn:
            rows = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
            tablas = {r[0] for r in rows}
            return _TABLAS_REQUERIDAS.issubset(tablas)
    except Exception:  # noqa: BLE001
        return False


def restaurar_backup(zip_path: Path) -> Path:
    """Restaura un backup sobre `factura_mdb.db`.

    Antes de sobrescribir, copia la BD actual a `factura_mdb.db.before_restore`
    para que pueda revertirse manualmente si algo sale mal.

    Valida que el contenido del .zip sea una BD Factura-mdb legítima:
    archivo SQLite válido + tablas esperadas. Si no, aborta sin tocar
    la BD activa.
    """
    zip_path = Path(zip_path)
    if not zip_path.exists():
        raise FileNotFoundError(f"No existe el backup {zip_path}")

    db = db_path()

    # Bug A3/G2: cerrar engine antes del replace para liberar locks WAL
    try:
        from ..core.database import engine
        engine.dispose()
    except Exception as e:  # noqa: BLE001
        logger.warning(f"No se pudo dispose() del engine antes del restore: {e}")

    # 1) Extraer a temporal y validar ANTES de tocar la BD activa
    with zipfile.ZipFile(zip_path, "r") as zf:
        entrada = None
        for n in zf.namelist():
            if n.endswith("factura_mdb.db"):
                entrada = n
                break
        if not entrada:
            raise ValueError("El archivo .zip no contiene factura_mdb.db")

        tmp_fd, tmp_name = tempfile.mkstemp(prefix="factura_mdb_restore_", suffix=".db")
        tmp_path = Path(tmp_name)
        try:
            import os
            os.close(tmp_fd)
            with zf.open(entrada) as src, open(tmp_path, "wb") as dst:
                shutil.copyfileobj(src, dst)

            if not _es_sqlite_valido(tmp_path):
                raise ValueError(
                    "El archivo de backup no es una BD Factura-mdb válida (no es SQLite o está corrupta)"
                )
            if not _tiene_tablas_factura_mdb(tmp_path):
                raise ValueError(
                    "El archivo de backup no es una BD Factura-mdb válida (faltan tablas esperadas)"
                )

            # 2) Recién ahora hacer safety copy y reemplazar
            if db.exists():
                safety = db.with_name(db.name + ".before_restore")
                try:
                    shutil.copy2(db, safety)
                    logger.info(f"Copia de seguridad pre-restore en {safety}")
                except OSError as e:
                    logger.warning(f"No se pudo crear safety copy: {e}")

            # Limpiar archivos auxiliares WAL para evitar inconsistencia tras restore
            for ext in ("-wal", "-shm"):
                extra = db.with_name(db.name + ext)
                if extra.exists():
                    try:
                        extra.unlink()
                    except OSError:
                        pass

            shutil.copy2(tmp_path, db)
        finally:
            try:
                tmp_path.unlink(missing_ok=True)
            except OSError:
                pass

    logger.info(f"Backup restaurado desde {zip_path.name}")
    return db


# ---------------------------------------------------------------------------
def _bytes_legible(n: int) -> str:
    for unidad in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.1f} {unidad}"
        n /= 1024  # type: ignore[assignment]
    return f"{n:.1f} TB"


# ---------------------------------------------------------------------------
# Scheduler (apscheduler con fallback threading.Timer)
# ---------------------------------------------------------------------------
_scheduler_started = False


def iniciar_scheduler_diario(hora: int = 23, minuto: int = 0) -> None:
    """Programa un job diario que crea backup + limpia antiguos.

    Llamar desde el lifespan startup. Es idempotente — si ya estaba arrancado
    no hace nada.
    """
    global _scheduler_started
    if _scheduler_started:
        return

    try:
        from apscheduler.schedulers.background import BackgroundScheduler
        from apscheduler.triggers.cron import CronTrigger

        sched = BackgroundScheduler(timezone="America/Lima", daemon=True)
        sched.add_job(
            _job_backup_diario,
            CronTrigger(hour=hora, minute=minuto),
            id="factura_mdb_backup_diario",
            replace_existing=True,
            coalesce=True,
            misfire_grace_time=60 * 60 * 6,  # 6h tolerancia si la PC estaba apagada
        )
        sched.start()
        logger.info(
            f"Scheduler de backup arrancado (apscheduler) — diario a las {hora:02d}:{minuto:02d}"
        )
        _scheduler_started = True
    except Exception as e:  # noqa: BLE001
        logger.warning(
            f"apscheduler no disponible ({e}); usando fallback threading.Timer"
        )
        _arrancar_timer_fallback(hora, minuto)
        _scheduler_started = True


def _job_backup_diario() -> None:
    try:
        crear_backup("auto_diario")
        limpiar_backups_antiguos(30)
    except Exception:
        logger.exception("Fallo en backup automático diario")


def _arrancar_timer_fallback(hora: int, minuto: int) -> None:
    import threading

    def _run():
        try:
            _job_backup_diario()
        finally:
            # Reprogramar a las (hora, minuto) del día siguiente
            t = _segundos_hasta(hora, minuto)
            timer = threading.Timer(t, _run)
            timer.daemon = True
            timer.start()

    t = _segundos_hasta(hora, minuto)
    timer = threading.Timer(t, _run)
    timer.daemon = True
    timer.start()
    logger.info(
        f"Fallback threading.Timer programado, próximo backup en {t/3600:.1f}h"
    )


def _segundos_hasta(hora: int, minuto: int) -> float:
    now = datetime.now()
    target = now.replace(hour=hora, minute=minuto, second=0, microsecond=0)
    if target <= now:
        target += timedelta(days=1)
    return (target - now).total_seconds()
