"""Endpoints del flujo de emision en 2 pasos (DAEFY / modo DBF).

Complementa a `legacy_emision.py` (que solo gestiona el paso 1: emitir
localmente con estado E). Aqui van los pasos 2 en adelante:

    - POST /api/comprobantes/{id}/enviar-sunat   firma + envia + clasifica
    - POST /api/comprobantes/detectar-tipo       deduce 01/03 segun cliente
    - POST /api/comprobantes/{id}/anular-local   estado E → B
    - POST /api/comprobantes/{id}/dar-baja-sunat estado A → resumen/RA
    - GET  /api/robot/status                     estado del robot SUNAT
    - POST /api/robot/toggle                     activa/desactiva el robot

Todos son DBF-only (501 en modos MDB/SQLite — el flujo en 2 pasos se
diseno para DAEFY).
"""
from __future__ import annotations

import logging
from typing import Any, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from ..core.db_adapter import is_dbf_mode

logger = logging.getLogger("factura_mdb.api.flujo_emision")

router = APIRouter(tags=["flujo-emision-2pasos"])


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------

class DetectarTipoIn(BaseModel):
    """Payload para detectar 01 (Factura) o 03 (Boleta) segun cliente."""
    tipo_doc_cliente: Optional[str] = Field(
        None, description="Codigo SUNAT cat 06: 0,1,4,6,7..."
    )
    num_doc_cliente: Optional[str] = Field(
        None, description="DNI/RUC/CE/Pasaporte del receptor",
    )


class DetectarTipoOut(BaseModel):
    tipo_documento: str
    razon: str


class AnularLocalIn(BaseModel):
    motivo: str = Field(..., min_length=1, max_length=100)


class BajaSunatIn(BaseModel):
    motivo: str = Field("Anulacion de la operacion", min_length=1, max_length=100)
    # codigo motivo cat 09 (NC) o cat 10 (RA). Para baja por usuario suele ser '01'.
    codigo_motivo: str = Field("01", min_length=1, max_length=2)


# ---------------------------------------------------------------------------
# POST /api/comprobantes/detectar-tipo
# ---------------------------------------------------------------------------

@router.post("/api/comprobantes/detectar-tipo", response_model=DetectarTipoOut)
def detectar_tipo(payload: DetectarTipoIn) -> DetectarTipoOut:
    """Devuelve el tipo de comprobante (01 Factura / 03 Boleta) recomendado.

    Reglas:
        - tipo_doc = '6' (RUC, 11 digitos):
            - empieza con 10/15/17 (persona natural con negocio, sucesion) → 01
            - empieza con 20 (persona juridica)                            → 01
            - otros prefijos                                                → 03
        - tipo_doc = '1' (DNI 8 digitos)   → 03
        - tipo_doc = '4' (Carnet extranjeria) → 03
        - tipo_doc = '7' (Pasaporte)          → 03
        - tipo_doc = '0' (Sin documento)      → 03
        - Cualquier otro                       → 03 (fallback seguro)
    """
    tipo_doc = (payload.tipo_doc_cliente or "").strip()
    num_doc = (payload.num_doc_cliente or "").strip()

    # Si solo nos llega el numero, intentamos inferir el tipo
    if not tipo_doc and num_doc:
        if num_doc.isdigit() and len(num_doc) == 11:
            tipo_doc = "6"
        elif num_doc.isdigit() and len(num_doc) == 8:
            tipo_doc = "1"

    if tipo_doc == "6" and num_doc.isdigit() and len(num_doc) == 11:
        prefijo = num_doc[:2]
        if prefijo in ("10", "15", "17"):
            return DetectarTipoOut(
                tipo_documento="01",
                razon=f"RUC persona natural ({prefijo}) → Factura",
            )
        if prefijo == "20":
            return DetectarTipoOut(
                tipo_documento="01",
                razon="RUC persona juridica (20) → Factura",
            )
        return DetectarTipoOut(
            tipo_documento="03",
            razon=f"RUC con prefijo no estandar ({prefijo}) → Boleta",
        )

    if tipo_doc == "1":
        return DetectarTipoOut(
            tipo_documento="03",
            razon="DNI → Boleta",
        )
    if tipo_doc == "4":
        return DetectarTipoOut(
            tipo_documento="03",
            razon="Carnet de extranjeria → Boleta",
        )
    if tipo_doc == "7":
        return DetectarTipoOut(
            tipo_documento="03",
            razon="Pasaporte → Boleta",
        )
    if tipo_doc == "0":
        return DetectarTipoOut(
            tipo_documento="03",
            razon="Sin documento (otros) → Boleta",
        )
    return DetectarTipoOut(
        tipo_documento="03",
        razon=f"Tipo doc {tipo_doc!r} desconocido → Boleta (fallback)",
    )


# ---------------------------------------------------------------------------
# POST /api/comprobantes/{id}/enviar-sunat — PASO 2 del flujo
# ---------------------------------------------------------------------------

@router.post("/api/comprobantes/{comp_id}/enviar-sunat")
def enviar_a_sunat(comp_id: int) -> dict:
    """PASO 2: firma + envia el comprobante a SUNAT.

    Lee el comp del DBF (debe estar en E o T), genera XML, firma con el
    cert de la empresa, envia y clasifica el resultado:

        - codigo CDR '0'           → estado A, persiste CDR + QR
        - codigo CDR != '0'        → estado R → INMEDIATAMENTE pasa a B
        - HTTP 4xx por contenido   → estado R → B
        - HTTP 5xx / timeout / SSL → estado T (robot reintenta)

    Respuesta:
        {
          "estado_nuevo": "A"|"T"|"R"|"B",
          "mensaje": "...",
          "cdr_codigo": "0"|"2xxx"|...,
          "debe_reintentar": true|false,
          "xml_path": "...",
          "cdr_path": "..."
        }
    """
    if not is_dbf_mode():
        raise HTTPException(
            status_code=501,
            detail="Endpoint solo disponible en modo DBF (DAEFY).",
        )
    from ..services.sunat_dbf_service import enviar_a_sunat_dbf
    return enviar_a_sunat_dbf(comp_id)


# ---------------------------------------------------------------------------
# POST /api/comprobantes/{id}/anular-local — E → B
# ---------------------------------------------------------------------------

@router.post("/api/comprobantes/{comp_id}/anular-local")
def anular_local(comp_id: int, payload: AnularLocalIn) -> dict:
    """Anula localmente un comprobante en estado E (no enviado).

    Solo aplica si estado == 'E'. NO toca SUNAT. Marca REGISTRO_A=True
    en ventas.dbf → el reader lo traduce a estado B.
    """
    if not is_dbf_mode():
        raise HTTPException(
            status_code=501,
            detail="Endpoint solo disponible en modo DBF.",
        )

    from ..core.db_adapter.dbf_repo import ComprobanteRepoDBF
    from ..core.db_adapter.dbf_writer import ComprobanteWriterDBF

    comp = ComprobanteRepoDBF.obtener(comp_id)
    if comp is None:
        raise HTTPException(status_code=404, detail="Comprobante no encontrado")

    estado = comp.get("estado")
    if estado != "E":
        raise HTTPException(
            status_code=409,
            detail=(
                f"Anular-local solo aplica a estado E (actual={estado}). "
                "Para comprobantes aceptados usa /dar-baja-sunat."
            ),
        )

    try:
        ok = ComprobanteWriterDBF.anular_local(
            comp.get("serie"), int(comp.get("correlativo") or 0),
            comp.get("tipo_documento"), payload.motivo,
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("Error anulando localmente comp=%s", comp_id)
        raise HTTPException(status_code=500, detail=f"Error escribiendo DBF: {exc}")
    if not ok:
        raise HTTPException(status_code=404, detail="No se encontro el registro en DBF")

    return {
        "ok": True,
        "estado_nuevo": "B",
        "mensaje": f"Comprobante anulado localmente: {payload.motivo}",
    }


# ---------------------------------------------------------------------------
# POST /api/comprobantes/{id}/dar-baja-sunat — A → comunicacion baja / resumen
# ---------------------------------------------------------------------------

@router.post("/api/comprobantes/{comp_id}/dar-baja-sunat")
def dar_baja_sunat(comp_id: int, payload: BajaSunatIn) -> dict:
    """Da de baja en SUNAT un comprobante aceptado (estado A).

    Reglas:
        - tipo 01 (Factura) o 08 (ND) → genera Comunicacion de Baja (RA)
        - tipo 03 (Boleta) o 07 (NC)  → resumen diario con condicion=3

    Devuelve el ticket; el frontend debera consultar luego /consultar-ticket
    para confirmar la aceptacion y mover el comp de A → B.
    """
    if not is_dbf_mode():
        raise HTTPException(
            status_code=501,
            detail="Endpoint solo disponible en modo DBF.",
        )

    from ..core.db_adapter.dbf_repo import ComprobanteRepoDBF
    comp = ComprobanteRepoDBF.obtener(comp_id)
    if comp is None:
        raise HTTPException(status_code=404, detail="Comprobante no encontrado")
    if comp.get("estado") != "A":
        raise HTTPException(
            status_code=409,
            detail=(
                f"Dar-baja-sunat solo aplica a estado A "
                f"(actual={comp.get('estado')}). Usa anular-local si esta en E."
            ),
        )

    tipo = comp.get("tipo_documento")
    # Por ahora devolvemos un stub explicito: la integracion completa con
    # baja_service (que es SQLAlchemy) requiere refactor. Para DAEFY
    # marcamos B de inmediato y dejamos el envio real como TODO.
    # TODO(daefy): implementar RA / resumen condicion=3 reales sobre DBF.
    if tipo in ("01", "08"):
        canal = "comunicacion_baja_ra"
        mensaje = (
            "STUB: Comunicacion de Baja (RA) pendiente de implementar para DBF. "
            f"Motivo: {payload.motivo}"
        )
    elif tipo in ("03", "07"):
        canal = "resumen_diario_cond3"
        mensaje = (
            "STUB: Resumen diario condicion 3 (baja boletas) pendiente para DBF. "
            f"Motivo: {payload.motivo}"
        )
    else:
        raise HTTPException(
            status_code=422,
            detail=f"Tipo {tipo} no soporta baja SUNAT",
        )

    logger.warning(
        "dar-baja-sunat comp=%s canal=%s motivo=%s (STUB)",
        comp_id, canal, payload.motivo,
    )

    # Stub: ticket sintetico para que el frontend lo persista, pero no
    # marcamos B hasta tener implementacion real (evitamos divergencia
    # con SUNAT). El frontend debe tratar `requiere_implementacion=True`
    # como caso especial.
    return {
        "ok": False,
        "canal": canal,
        "ticket": "",
        "mensaje": mensaje,
        "requiere_consultar_ticket": False,
        "requiere_implementacion": True,
    }


# ---------------------------------------------------------------------------
# Robot SUNAT — endpoints de control
# ---------------------------------------------------------------------------

@router.get("/api/robot/status")
def robot_status() -> dict:
    """Estado del robot que reintenta comprobantes en T cada 60s."""
    from ..services.robot_sunat import obtener_estado
    return obtener_estado()


@router.post("/api/robot/toggle")
def robot_toggle(payload: Optional[dict] = None) -> dict:
    """Activa o desactiva el robot SUNAT. Body opcional: {activo: bool}."""
    from ..services.robot_sunat import toggle_activo
    deseado: Optional[bool] = None
    if isinstance(payload, dict) and "activo" in payload:
        deseado = bool(payload["activo"])
    return toggle_activo(deseado)
