"""Robot que reintenta comprobantes en estado T (timeout SUNAT).

Funciona como una task asyncio recurrente lanzada desde el lifespan de
FastAPI. Cada `INTERVAL_S` segundos:

    1. Lista comprobantes en estado T (timeout transitorio).
    2. Para cada uno, llama a `enviar_a_sunat_dbf(comp_id)`.
    3. Loguea en `logs/robot.log` el resultado (A / T / R / B).

No usa APScheduler para evitar añadir dependencia. Es suficiente con
asyncio.create_task + bucle controlado.

Exposicion:
    - iniciar_robot(app)    monta el robot en el lifespan de FastAPI.
    - obtener_estado()      devuelve dict con activo, ultima_corrida...
    - toggle_activo(bool)   activa/desactiva sin reiniciar el proceso.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from typing import Optional

from ..core.config import log_dir
from ..core.db_adapter import is_dbf_mode

# Intervalo entre corridas (segundos). 60s segun spec DAEFY.
INTERVAL_S = 60

# Logger dedicado al robot — escribe en logs/robot.log con rotacion.
_logger = logging.getLogger("factura_mdb.robot_sunat")
_logger.setLevel(logging.INFO)
_logger.propagate = True  # tambien al stdout de uvicorn


def _setup_file_handler() -> None:
    """Idempotente: anade un FileHandler a logs/robot.log si no esta."""
    try:
        existing = [
            h for h in _logger.handlers
            if isinstance(h, RotatingFileHandler)
            and "robot.log" in getattr(h, "baseFilename", "")
        ]
        if existing:
            return
        path = log_dir() / "robot.log"
        h = RotatingFileHandler(
            str(path), maxBytes=2 * 1024 * 1024, backupCount=3, encoding="utf-8",
        )
        h.setFormatter(
            logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")
        )
        _logger.addHandler(h)
    except Exception as exc:  # noqa: BLE001
        _logger.warning("No se pudo montar handler de archivo: %s", exc)


# ---------------------------------------------------------------------------
# Estado en memoria del robot
# ---------------------------------------------------------------------------

_state: dict = {
    "activo": True,
    "task": None,           # asyncio.Task del bucle
    "ultima_corrida": None,
    "ultima_duracion_ms": None,
    "ultimo_resultado": None,
    "pendientes": 0,
    "total_corridas": 0,
    "total_reintentos": 0,
    "total_aceptados": 0,
    "total_rechazados": 0,
    "total_timeout": 0,
}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


async def _una_corrida() -> dict:
    """Una iteracion: busca T y reintenta cada uno."""
    inicio = datetime.now(timezone.utc)

    if not is_dbf_mode():
        return {
            "skipped": True,
            "razon": "Modo no-DBF, robot deshabilitado",
            "pendientes": 0,
        }

    # Import diferido: evita circular y posibles errores al arrancar.
    try:
        from .sunat_dbf_service import listar_pendientes_timeout, enviar_a_sunat_dbf
    except Exception as exc:  # noqa: BLE001
        _logger.exception("Error importando sunat_dbf_service: %s", exc)
        return {"skipped": True, "razon": str(exc), "pendientes": 0}

    pendientes: list[dict] = []
    try:
        pendientes = listar_pendientes_timeout()
    except Exception as exc:  # noqa: BLE001
        _logger.exception("Error listando pendientes T: %s", exc)
        return {"skipped": True, "razon": str(exc), "pendientes": 0}

    aceptados = 0
    rechazados = 0
    timeout = 0
    resultados: list[dict] = []

    for comp in pendientes:
        comp_id = comp.get("id")
        numero = comp.get("numero_completo")
        # Ejecutar el envio en thread (es sincrono y hace asyncio.run dentro;
        # to_thread evita anidar event loops).
        try:
            res = await asyncio.to_thread(enviar_a_sunat_dbf, comp_id)
        except Exception as exc:  # noqa: BLE001
            _logger.exception("Robot: error reintentando %s: %s", numero, exc)
            res = {"estado_nuevo": "T", "mensaje": str(exc)}

        estado = res.get("estado_nuevo")
        if estado == "A":
            aceptados += 1
            _logger.info("Robot OK: %s → A", numero)
        elif estado in ("R", "B"):
            rechazados += 1
            _logger.warning(
                "Robot RECHAZO: %s → %s | %s",
                numero, estado, res.get("mensaje"),
            )
        else:
            timeout += 1
            _logger.info(
                "Robot TIMEOUT: %s sigue en T (%s)",
                numero, res.get("mensaje"),
            )
        resultados.append({"comp_id": comp_id, "numero": numero, "estado": estado})

    fin = datetime.now(timezone.utc)
    duracion_ms = int((fin - inicio).total_seconds() * 1000)

    _state["ultima_corrida"] = inicio.isoformat()
    _state["ultima_duracion_ms"] = duracion_ms
    _state["pendientes"] = len(pendientes)
    _state["total_corridas"] += 1
    _state["total_reintentos"] += len(pendientes)
    _state["total_aceptados"] += aceptados
    _state["total_rechazados"] += rechazados
    _state["total_timeout"] += timeout
    _state["ultimo_resultado"] = {
        "pendientes": len(pendientes),
        "aceptados": aceptados,
        "rechazados": rechazados,
        "timeout": timeout,
        "duracion_ms": duracion_ms,
    }

    return _state["ultimo_resultado"]


async def _bucle() -> None:
    """Bucle infinito que corre _una_corrida cada INTERVAL_S si activo."""
    _setup_file_handler()
    _logger.info("Robot SUNAT iniciado (interval=%ss)", INTERVAL_S)
    while True:
        try:
            if _state.get("activo"):
                try:
                    await _una_corrida()
                except Exception as exc:  # noqa: BLE001
                    _logger.exception("Error en corrida del robot: %s", exc)
            await asyncio.sleep(INTERVAL_S)
        except asyncio.CancelledError:
            _logger.info("Robot SUNAT detenido (CancelledError)")
            raise


def iniciar_robot() -> Optional[asyncio.Task]:
    """Crea la task del robot. Idempotente: si ya existe, devuelve la actual."""
    task = _state.get("task")
    if task and not task.done():
        return task
    try:
        loop = asyncio.get_event_loop()
        task = loop.create_task(_bucle(), name="robot_sunat")
        _state["task"] = task
        return task
    except RuntimeError:
        # No event loop disponible (fuera de FastAPI lifespan)
        _logger.warning("No hay event loop activo; robot no iniciado")
        return None


async def detener_robot() -> None:
    """Cancela la task del robot (idempotente)."""
    task = _state.get("task")
    if task and not task.done():
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):
            pass
    _state["task"] = None


def obtener_estado() -> dict:
    """Snapshot del estado actual del robot para `GET /api/robot/status`."""
    task = _state.get("task")
    running = bool(task and not task.done())
    return {
        "activo": _state.get("activo"),
        "ejecutando": running,
        "interval_s": INTERVAL_S,
        "ultima_corrida": _state.get("ultima_corrida"),
        "ultima_duracion_ms": _state.get("ultima_duracion_ms"),
        "pendientes": _state.get("pendientes", 0),
        "total_corridas": _state.get("total_corridas", 0),
        "total_reintentos": _state.get("total_reintentos", 0),
        "total_aceptados": _state.get("total_aceptados", 0),
        "total_rechazados": _state.get("total_rechazados", 0),
        "total_timeout": _state.get("total_timeout", 0),
        "ultimo_resultado": _state.get("ultimo_resultado"),
    }


def toggle_activo(activo: Optional[bool] = None) -> dict:
    """Activa o desactiva el robot. Si activo=None, hace toggle."""
    actual = bool(_state.get("activo"))
    nuevo = (not actual) if activo is None else bool(activo)
    _state["activo"] = nuevo
    _logger.info("Robot %s por toggle (era %s)", "ACTIVADO" if nuevo else "DESACTIVADO", actual)
    return {"activo": nuevo, "previo": actual}
