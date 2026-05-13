"""Endpoints REST para importar productos NUEVOS desde el DBF de la
empresa matriz (IDIVSA) hacia el DBF local de Daefy.

Ver `services/importador_productos.py` para la lógica de comparación
e inserción. Estos endpoints son finos y solo:

    - Validan el payload.
    - Delegan en el servicio.
    - Invalidan el caché de productos tras una importación exitosa
      (el repo DBF no tiene caché global activa hoy, pero llamamos al
      hook si existe a futuro).

Solo aplican cuando el modo activo es DBF (Daefy / GECOPE). Si la app
arranca en modo MDB se devuelve 400.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from ..core.db_adapter import is_dbf_mode
from ..services.importador_productos import (
    comparar_con_matriz,
    importar_desde_matriz,
)

logger = logging.getLogger("factura_mdb.api.importar")

router = APIRouter(prefix="/api/productos", tags=["productos"])


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------

class CompararMatrizIn(BaseModel):
    ruta_dbf_matriz: str = Field(
        ...,
        description="Ruta absoluta al articulo.dbf de la empresa matriz",
        min_length=1,
    )


class CompararMatrizOut(BaseModel):
    nuevos: list[dict]
    existentes: int
    matriz_total: int


class ImportarMatrizIn(BaseModel):
    ruta_dbf_matriz: str = Field(..., min_length=1)
    codigos_seleccionados: list[str] = Field(
        default_factory=list,
        description="Lista de CODIGOs a importar desde la matriz al DBF local",
    )


class ImportarMatrizOut(BaseModel):
    importados: int
    errores: list[dict]


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

def _check_dbf_mode() -> None:
    if not is_dbf_mode():
        raise HTTPException(
            status_code=400,
            detail=(
                "Esta función solo está disponible en modo DBF (Daefy/GECOPE). "
                "El modo activo es MDB."
            ),
        )


@router.post(
    "/comparar-con-matriz",
    response_model=CompararMatrizOut,
    summary="Compara articulo.dbf matriz vs local y devuelve productos nuevos",
)
def endpoint_comparar(payload: CompararMatrizIn) -> CompararMatrizOut:
    """Lee el DBF matriz, compara contra el local y devuelve los nuevos.

    No modifica nada. Es seguro de llamar varias veces.
    """
    _check_dbf_mode()
    try:
        resultado = comparar_con_matriz(payload.ruta_dbf_matriz)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        logger.exception("Error comparando con matriz")
        raise HTTPException(
            status_code=500,
            detail=f"Error al leer el DBF matriz: {exc}",
        ) from exc
    return CompararMatrizOut(**resultado)


@router.post(
    "/importar-desde-matriz",
    response_model=ImportarMatrizOut,
    summary="Inserta en articulo.dbf local los productos seleccionados",
)
def endpoint_importar(payload: ImportarMatrizIn) -> ImportarMatrizOut:
    """Importa los códigos seleccionados desde la matriz hacia el DBF local."""
    _check_dbf_mode()
    try:
        resultado = importar_desde_matriz(
            payload.ruta_dbf_matriz,
            payload.codigos_seleccionados,
        )
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        logger.exception("Error importando desde matriz")
        raise HTTPException(
            status_code=500,
            detail=f"Error al importar productos: {exc}",
        ) from exc

    # Invalidar caché de productos (best-effort). Hoy `dbf_repo` relee
    # cada vez, pero llamamos al hook si existe a futuro.
    if resultado.get("importados", 0) > 0:
        try:
            from ..core.db_adapter import dbf_repo  # noqa: F401
            invalidate = getattr(dbf_repo, "invalidate_cache", None)
            if callable(invalidate):
                invalidate()
        except Exception as exc:  # noqa: BLE001
            logger.debug("invalidate_cache skipped: %s", exc)

    return ImportarMatrizOut(**resultado)
