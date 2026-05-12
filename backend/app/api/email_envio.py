"""Endpoints para enviar comprobantes/cotizaciones/pedidos por correo.

POST /api/comprobantes/{id}/enviar-email
POST /api/comprobantes/{id}/enviar-cotizacion-email?email=...
POST /api/comprobantes/{id}/enviar-pedido-email?email=...

Reusa la lógica de generación de PDF del endpoint /api/comprobantes/{id}/pdf
y los generadores `generar_pdf_cotizacion` / `generar_pdf_pedido`. El SMTP
viene de config.json -> smtp (con defaults para Gmail con app password).
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from ..core.config import storage_dir
from ..core.db_adapter import is_dbf_mode, is_mdb_mode
from ..services.email_service import (
    EmailError,
    EmailValidacionError,
    cuerpo_html_default,
    email_valido,
    enviar_email_con_adjunto,
)


logger = logging.getLogger("factura_mdb.api.email_envio")


router = APIRouter(prefix="/api/comprobantes", tags=["email"])


# ─────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────


class _Ns:
    """Wrapper estilo namespace sobre un dict — los generators de PDF
    acceden a los campos via getattr (`comprobante.tipo_documento`)."""

    __slots__ = ("_data",)

    def __init__(self, data: dict):
        object.__setattr__(self, "_data", data or {})

    def __getattr__(self, item: str):
        return self._data.get(item)

    def __setattr__(self, item: str, value) -> None:
        self._data[item] = value

    def get(self, item: str, default=None):
        return self._data.get(item, default)


_TIPO_NOMBRES_HUMANOS = {
    "01": "factura",
    "03": "boleta",
    "07": "nota de crédito",
    "08": "nota de débito",
}

_TIPO_FILENAME = {
    "01": "factura",
    "03": "boleta",
    "07": "nota_credito",
    "08": "nota_debito",
}


def _cargar_repos():
    """Devuelve (ComprobanteRepo, EmpresaRepo) según el modo activo."""
    if is_dbf_mode():
        from ..core.db_adapter.dbf_repo import (
            ComprobanteRepoDBF, EmpresaRepoDBF,
        )
        return ComprobanteRepoDBF, EmpresaRepoDBF
    if is_mdb_mode():
        from ..core.db_adapter.mdb_repo import (
            ComprobanteRepoMDB, EmpresaRepoMDB,
        )
        return ComprobanteRepoMDB, EmpresaRepoMDB
    raise HTTPException(
        status_code=501,
        detail="Modo de BD no soportado para envío por email",
    )


def _generar_pdf_comprobante_bytes(comp_dict: dict, empresa_dict: dict) -> bytes:
    """Genera (o reusa) el PDF del comprobante.

    Reusa el cache de storage/pdf/{RUC}-{TIPO}-{SERIE}-{CORR}.pdf que ya
    deja el endpoint /api/comprobantes/{id}/pdf. Si no existe, lo genera.
    """
    from ..services.pdf_generator import generar_pdf_comprobante

    num_parts = (comp_dict.get("numero_completo") or "").split("-")
    correl = (
        num_parts[1] if len(num_parts) == 2
        else f"{int(comp_dict.get('correlativo') or 0):08d}"
    )
    filename = (
        f"{empresa_dict.get('ruc')}-{comp_dict.get('tipo_documento')}-"
        f"{comp_dict.get('serie')}-{correl}"
    )
    pdf_path = storage_dir() / "pdf" / f"{filename}.pdf"

    if pdf_path.exists():
        return pdf_path.read_bytes()

    comp_ns = _Ns(comp_dict)
    emp_ns = _Ns(empresa_dict)
    det_list = [_Ns(d) for d in (comp_dict.get("detalles") or [])]
    pdf_bytes = generar_pdf_comprobante(comp_ns, det_list, emp_ns, None)
    try:
        pdf_path.parent.mkdir(parents=True, exist_ok=True)
        pdf_path.write_bytes(pdf_bytes)
    except OSError:
        logger.warning("No se pudo cachear PDF en %s", pdf_path)
    return pdf_bytes


def _filename_comprobante(comp: dict) -> str:
    tipo_label = _TIPO_FILENAME.get(comp.get("tipo_documento"), "comprobante")
    nro = comp.get("numero_completo") or f"{comp.get('serie')}-{comp.get('correlativo')}"
    return f"{tipo_label}_{nro}.pdf"


def _formato_monto(comp: dict) -> Optional[str]:
    moneda = comp.get("moneda") or "PEN"
    sym = "S/" if moneda == "PEN" else ("$" if moneda == "USD" else moneda + " ")
    total = comp.get("total_venta")
    if total is None:
        return None
    try:
        return f"{sym} {float(total):,.2f}"
    except (TypeError, ValueError):
        return None


# ─────────────────────────────────────────────────────────────────────────
# Schemas
# ─────────────────────────────────────────────────────────────────────────


class EnviarEmailIn(BaseModel):
    destinatario: str = Field(..., description="Email del receptor")
    asunto: Optional[str] = Field(None, description="Subject del email (opcional)")
    mensaje: Optional[str] = Field(None, description="Mensaje extra opcional, se inserta en el body HTML")


class EnviarEmailOut(BaseModel):
    ok: bool
    mensaje: str
    destinatario: str
    asunto: str
    adjunto: str


# ─────────────────────────────────────────────────────────────────────────
# Endpoints
# ─────────────────────────────────────────────────────────────────────────


@router.post("/{comp_id}/enviar-email", response_model=EnviarEmailOut)
def enviar_comprobante_email(comp_id: int, payload: EnviarEmailIn):
    """Envía el PDF del comprobante por correo electrónico.

    - El PDF se genera reusando la lógica del endpoint /pdf.
    - El nombre del adjunto sigue la convención `factura_F001-00000356.pdf`,
      `boleta_B001-...pdf`, etc.
    - Subject default: "Comprobante {tipo} {numero} de {empresa}".
    - SMTP desde config.json (con defaults Gmail HANDOFF).
    """
    if not email_valido(payload.destinatario):
        raise HTTPException(
            status_code=422,
            detail=f"Email inválido: {payload.destinatario!r}",
        )

    ComprobanteRepo, EmpresaRepo = _cargar_repos()

    comp = ComprobanteRepo.obtener(comp_id)
    if not comp:
        raise HTTPException(status_code=404, detail="Comprobante no encontrado")
    empresa = EmpresaRepo.obtener() or {}

    try:
        pdf_bytes = _generar_pdf_comprobante_bytes(comp, empresa)
    except Exception as exc:  # noqa: BLE001
        logger.exception("Error generando PDF para email")
        raise HTTPException(
            status_code=500,
            detail=f"No se pudo generar el PDF: {exc}",
        )

    tipo_humano = _TIPO_NOMBRES_HUMANOS.get(
        comp.get("tipo_documento"), "comprobante"
    ).capitalize()
    numero = comp.get("numero_completo") or ""
    razon = empresa.get("razon_social") or empresa.get("nombre_comercial") or ""

    asunto = payload.asunto or f"Comprobante {tipo_humano} {numero} de {razon}"
    body_html = cuerpo_html_default(
        tipo_nombre=tipo_humano,
        numero_completo=numero,
        empresa_razon_social=razon,
        empresa_ruc=str(empresa.get("ruc") or ""),
        monto=_formato_monto(comp),
        mensaje_extra=payload.mensaje or "",
    )
    adjunto_nombre = _filename_comprobante(comp)

    try:
        result = enviar_email_con_adjunto(
            destinatario=payload.destinatario,
            asunto=asunto,
            body_html=body_html,
            adjunto_bytes=pdf_bytes,
            adjunto_nombre=adjunto_nombre,
            remitente_nombre=razon or None,
        )
    except EmailValidacionError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except EmailError as exc:
        logger.error("Falla SMTP: %s", exc)
        raise HTTPException(status_code=502, detail=f"Error SMTP: {exc}")

    return EnviarEmailOut(**result)


@router.post("/{comp_id}/enviar-cotizacion-email", response_model=EnviarEmailOut)
def enviar_cotizacion_email(
    comp_id: int,
    email: str = Query(..., description="Email destino"),
    asunto: Optional[str] = Query(None),
    mensaje: Optional[str] = Query(None),
    validez_dias: int = Query(15),
    condicion_pago: str = Query(""),
    vendedor: str = Query(""),
    observaciones: str = Query(""),
):
    """Envía PDF de COTIZACIÓN (no SUNAT) generado a partir del comprobante."""
    if not email_valido(email):
        raise HTTPException(status_code=422, detail=f"Email inválido: {email!r}")

    ComprobanteRepo, EmpresaRepo = _cargar_repos()
    comp = ComprobanteRepo.obtener(comp_id)
    if not comp:
        raise HTTPException(status_code=404, detail="Comprobante no encontrado")
    empresa = EmpresaRepo.obtener() or {}

    try:
        from ..services.pdf_generator import generar_pdf_cotizacion
        comp_ns = _Ns(comp)
        emp_ns = _Ns(empresa)
        det_list = [_Ns(d) for d in (comp.get("detalles") or [])]
        pdf_bytes = generar_pdf_cotizacion(
            comp_ns, det_list, emp_ns,
            validez_dias=validez_dias,
            condicion_pago=condicion_pago,
            vendedor=vendedor,
            observaciones=observaciones,
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("Error generando PDF cotización para email")
        raise HTTPException(status_code=500, detail=f"No se pudo generar PDF: {exc}")

    razon = empresa.get("razon_social") or empresa.get("nombre_comercial") or ""
    numero = comp.get("numero_completo") or ""
    asunto_final = asunto or f"Cotización {numero} de {razon}"
    body_html = cuerpo_html_default(
        tipo_nombre="Cotización",
        numero_completo=numero,
        empresa_razon_social=razon,
        empresa_ruc=str(empresa.get("ruc") or ""),
        monto=_formato_monto(comp),
        mensaje_extra=mensaje or f"Validez de la cotización: {validez_dias} días.",
    )
    adjunto_nombre = f"cotizacion_{numero}.pdf"

    try:
        result = enviar_email_con_adjunto(
            destinatario=email,
            asunto=asunto_final,
            body_html=body_html,
            adjunto_bytes=pdf_bytes,
            adjunto_nombre=adjunto_nombre,
            remitente_nombre=razon or None,
        )
    except EmailValidacionError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except EmailError as exc:
        logger.error("Falla SMTP: %s", exc)
        raise HTTPException(status_code=502, detail=f"Error SMTP: {exc}")

    return EnviarEmailOut(**result)


@router.post("/{comp_id}/enviar-pedido-email", response_model=EnviarEmailOut)
def enviar_pedido_email(
    comp_id: int,
    email: str = Query(..., description="Email destino"),
    asunto: Optional[str] = Query(None),
    mensaje: Optional[str] = Query(None),
    condicion_pago: str = Query(""),
    vendedor: str = Query(""),
    observaciones: str = Query(""),
):
    """Envía PDF de NOTA DE PEDIDO (no SUNAT) generado a partir del comprobante."""
    if not email_valido(email):
        raise HTTPException(status_code=422, detail=f"Email inválido: {email!r}")

    ComprobanteRepo, EmpresaRepo = _cargar_repos()
    comp = ComprobanteRepo.obtener(comp_id)
    if not comp:
        raise HTTPException(status_code=404, detail="Comprobante no encontrado")
    empresa = EmpresaRepo.obtener() or {}

    try:
        from ..services.pdf_generator import generar_pdf_pedido
        comp_ns = _Ns(comp)
        emp_ns = _Ns(empresa)
        det_list = [_Ns(d) for d in (comp.get("detalles") or [])]
        pdf_bytes = generar_pdf_pedido(
            comp_ns, det_list, emp_ns,
            condicion_pago=condicion_pago,
            vendedor=vendedor,
            observaciones=observaciones,
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("Error generando PDF pedido para email")
        raise HTTPException(status_code=500, detail=f"No se pudo generar PDF: {exc}")

    razon = empresa.get("razon_social") or empresa.get("nombre_comercial") or ""
    numero = comp.get("numero_completo") or ""
    asunto_final = asunto or f"Nota de pedido {numero} de {razon}"
    body_html = cuerpo_html_default(
        tipo_nombre="Nota de pedido",
        numero_completo=numero,
        empresa_razon_social=razon,
        empresa_ruc=str(empresa.get("ruc") or ""),
        monto=_formato_monto(comp),
        mensaje_extra=mensaje or "",
    )
    adjunto_nombre = f"pedido_{numero}.pdf"

    try:
        result = enviar_email_con_adjunto(
            destinatario=email,
            asunto=asunto_final,
            body_html=body_html,
            adjunto_bytes=pdf_bytes,
            adjunto_nombre=adjunto_nombre,
            remitente_nombre=razon or None,
        )
    except EmailValidacionError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except EmailError as exc:
        logger.error("Falla SMTP: %s", exc)
        raise HTTPException(status_code=502, detail=f"Error SMTP: {exc}")

    return EnviarEmailOut(**result)
