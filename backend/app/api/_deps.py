"""Dependencias y utilidades compartidas entre routers."""
import logging
from typing import Callable, Any, Optional
from fastapi import HTTPException
from sqlalchemy.orm import Session

logger = logging.getLogger("factura_mdb.api")


def http_502_sunat(detalle: str) -> HTTPException:
    """Devuelve un 502 con mensaje legible ante errores SUNAT."""
    return HTTPException(
        status_code=502,
        detail=f"SUNAT respondió: {detalle}",
    )


def call_servicio_sunat(fn: Callable[..., Any], *args, **kwargs) -> Any:
    """Wrap de llamadas a servicios del Agente A.

    Si lanzan, mapeamos a 502 con mensaje. El servicio puede no existir aún
    durante el desarrollo paralelo — devolvemos 503 en ese caso.
    """
    try:
        return fn(*args, **kwargs)
    except ImportError as e:
        logger.warning(f"Servicio SUNAT no disponible aún: {e}")
        raise HTTPException(
            status_code=503,
            detail="Servicio SUNAT aún no disponible. Reintenta en unos segundos.",
        )
    except HTTPException:
        raise
    except Exception as e:  # noqa: BLE001
        logger.exception("Error invocando servicio SUNAT")
        raise http_502_sunat(str(e))


def cargar_servicio_comprobante() -> Optional[Any]:
    """Import perezoso del servicio del Agente A."""
    try:
        from ..services import comprobante_service  # type: ignore
        return comprobante_service
    except ImportError:
        return None


def cargar_servicio_baja() -> Optional[Any]:
    try:
        from ..services import baja_service  # type: ignore
        return baja_service
    except ImportError:
        return None


def cargar_servicio_resumen() -> Optional[Any]:
    try:
        from ..services import resumen_service  # type: ignore
        return resumen_service
    except ImportError:
        return None


def cargar_servicio_pdf() -> Optional[Any]:
    try:
        from ..services import pdf_service  # type: ignore
        return pdf_service
    except ImportError:
        return None


def cargar_servicio_sunat_test() -> Optional[Any]:
    try:
        from ..services import sunat_test_service  # type: ignore
        return sunat_test_service
    except ImportError:
        return None


def escape_like(s: str) -> str:
    """Escapa wildcards LIKE para que `%` y `_` se busquen literales.

    Bug E1: sin escape, un cliente que busca "5%" recibe TODA la base.
    Usar siempre con `.like(pattern, escape="\\\\")`.
    """
    if not s:
        return s
    return s.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
