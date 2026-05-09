"""Orquestador de emision de comprobantes individuales (factura/boleta/NC/ND).

Flujo:
    1. Carga Comprobante + ComprobanteDetalle + Empresa + Configuracion
    2. Convierte modelo Factura-mdb -> Pydantic (FacturaRequest/NotaCreditoRequest/NotaDebitoRequest)
    3. Genera XML UBL 2.1 + lo firma con el certificado de la empresa
    4. Envia a SUNAT (sendBill, sincrono)
    5. Guarda XML, CDR; actualiza Comprobante.estado/cdr_codigo/cdr_descripcion/cdr_hash
    6. Si aceptado y `generar_pdf=True`, genera PDF y guarda pdf_path
    7. Devuelve dict con resultado

Esta es la funcion que llama el endpoint POST /api/comprobantes/{id}/enviar-sunat.
"""
from __future__ import annotations
import asyncio
import base64
import hashlib
import logging
import os
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional
from zoneinfo import ZoneInfo

# Bug C1/F3: usar hora Lima en vez de UTC (consistencia con la zona del usuario)
_LIMA = ZoneInfo("America/Lima")


def _now_lima_naive() -> datetime:
    return datetime.now(_LIMA).replace(tzinfo=None)

from sqlalchemy.orm import Session

from ..core.config import storage_dir
from ..models.comprobante import Comprobante, ComprobanteDetalle, EnvioSunat
from ..models.empresa import Empresa, Configuracion
from .comprobante_mapper import (
    comprobante_to_factura_request,
    comprobante_to_nota_credito_request,
    comprobante_to_nota_debito_request,
)
from .firma_digital import calcular_hash
from .sunat_client import SUNATClient
from .xml_service import (
    generar_y_firmar_factura,
    generar_y_firmar_nota_credito,
    generar_y_firmar_nota_debito,
)


logger = logging.getLogger(__name__)


def _xml_filename(empresa: Empresa, comp: Comprobante) -> str:
    """Convencion SUNAT: RUC-TIPO-SERIE-CORRELATIVO."""
    serie = comp.serie
    correlativo_str = str(comp.correlativo)
    if comp.numero_completo and "-" in comp.numero_completo:
        correlativo_str = comp.numero_completo.split("-")[1].lstrip("0") or "0"
    return f"{empresa.ruc}-{comp.tipo_documento}-{serie}-{correlativo_str}"


def _save_bytes(path: Path, data: bytes) -> None:
    """Bug F6: escritura atomica via tempfile + os.replace."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = tempfile.NamedTemporaryFile(
        dir=path.parent, delete=False, suffix=".tmp"
    )
    try:
        tmp.write(data)
        tmp.close()
        os.replace(tmp.name, path)
    except Exception:
        try:
            os.unlink(tmp.name)
        except OSError:
            pass
        raise


def _save_text(path: Path, data: str) -> None:
    _save_bytes(path, data.encode("utf-8"))


def _cdr_hash(cdr_b64: str) -> str:
    """SHA1 hex del contenido binario del ZIP CDR (campo 11 del QR SUNAT)."""
    if not cdr_b64:
        return ""
    try:
        raw = base64.b64decode(cdr_b64)
        return hashlib.sha1(raw).hexdigest()
    except Exception:
        return ""


def _generar_xml_firmado(comp: Comprobante, empresa: Empresa,
                          detalles: list[ComprobanteDetalle]) -> tuple[str, Dict[str, Any]]:
    """Despacha al generador adecuado segun tipo_documento."""
    cert_path = empresa.certificado_path
    cert_pass = empresa.certificado_pass or ""
    if not cert_path:
        raise RuntimeError("Empresa sin certificado_path configurado")

    tipo = comp.tipo_documento
    if tipo in ("01", "03"):
        req = comprobante_to_factura_request(comp, empresa, detalles)
        return generar_y_firmar_factura(req, cert_path, cert_pass)
    if tipo == "07":
        req = comprobante_to_nota_credito_request(comp, empresa, detalles)
        return generar_y_firmar_nota_credito(req, cert_path, cert_pass)
    if tipo == "08":
        req = comprobante_to_nota_debito_request(comp, empresa, detalles)
        return generar_y_firmar_nota_debito(req, cert_path, cert_pass)
    raise ValueError(f"tipo_documento no soportado: {tipo}")


async def _enviar_sunat_async(empresa: Empresa, xml_str: str,
                               filename: str) -> tuple[str, str, str]:
    """Envia el XML a SUNAT. Retorna (codigo, descripcion, cdr_b64)."""
    client = SUNATClient(
        ruc=empresa.ruc,
        sol_user=empresa.sol_user or "",
        sol_pass=empresa.sol_pass or "",
        ambiente=empresa.sunat_env,
    )
    return await client.enviar_comprobante(xml_str, filename)


def _enviar_sunat_sync(empresa: Empresa, xml_str: str,
                        filename: str) -> tuple[str, str, str]:
    """Wrapper sincrono — se ejecuta el coroutine en un loop nuevo."""
    return asyncio.run(_enviar_sunat_async(empresa, xml_str, filename))


def emitir_y_enviar(db: Session, comprobante_id: int, *,
                    generar_pdf: bool = True) -> Dict[str, Any]:
    """Emite y envia un comprobante a SUNAT.

    Args:
        db: Session SQLAlchemy abierta.
        comprobante_id: ID del Comprobante a emitir.
        generar_pdf: Si True (default), tras aceptacion genera el PDF.

    Returns:
        dict con: success, codigo, descripcion, xml_path, cdr_path,
                  pdf_path (opcional), envio_id.
    """
    comp = db.query(Comprobante).filter(Comprobante.id == comprobante_id).first()
    if not comp:
        return {"success": False, "error": f"Comprobante {comprobante_id} no encontrado"}

    # Defensa: no re-firmar comprobantes ya aceptados o anulados
    if comp.estado == "A":
        return {
            "success": False,
            "error": "Ya fue aceptado por SUNAT",
            "skipped": True,
            "comprobante_id": comp.id,
            "estado": comp.estado,
        }
    if comp.estado == "B":
        return {
            "success": False,
            "error": "Comprobante anulado/baja",
            "skipped": True,
            "comprobante_id": comp.id,
            "estado": comp.estado,
        }

    empresa = db.query(Empresa).first()
    if not empresa:
        return {"success": False, "error": "No hay Empresa configurada"}

    config = db.query(Configuracion).first()

    detalles = (
        db.query(ComprobanteDetalle)
          .filter(ComprobanteDetalle.comprobante_id == comp.id)
          .order_by(ComprobanteDetalle.orden)
          .all()
    )
    if not detalles:
        return {"success": False, "error": "Comprobante sin detalles"}

    storage = storage_dir()
    filename = _xml_filename(empresa, comp)
    intento = (
        (db.query(EnvioSunat).filter(EnvioSunat.comprobante_id == comp.id).count() or 0) + 1
    )

    envio = EnvioSunat(
        comprobante_id=comp.id,
        intento=intento,
        estado="pendiente",
        xml_nombre=filename,
        enviado_at=_now_lima_naive(),
    )
    db.add(envio)
    db.flush()

    # 1) Generar + firmar
    try:
        logger.info("Generando y firmando XML para comprobante %s (%s)",
                     comp.id, comp.numero_completo)
        xml_str, _totales = _generar_xml_firmado(comp, empresa, detalles)
    except Exception as e:
        logger.exception("Error generando/firmando XML")
        envio.estado = "error"
        envio.error_detalle = f"firma: {e}"
        comp.estado = "R"
        comp.cdr_codigo = "-1"
        comp.cdr_descripcion = f"Error firma: {e}"[:500]
        db.commit()
        return {"success": False, "error": str(e), "stage": "firma", "envio_id": envio.id}

    xml_path = storage / "xml" / f"{filename}.xml"
    _save_text(xml_path, xml_str)
    comp.xml_path = str(xml_path)
    comp.xml_hash = calcular_hash(xml_str)
    envio.xml_path = str(xml_path)

    # 2) Enviar a SUNAT
    try:
        logger.info("Enviando %s a SUNAT (%s)", filename, empresa.sunat_env)
        codigo, descripcion, cdr_b64 = _enviar_sunat_sync(empresa, xml_str, filename)
    except Exception as e:
        logger.exception("Error enviando a SUNAT")
        envio.estado = "error"
        envio.error_detalle = f"sunat: {e}"
        comp.estado = "R"
        comp.cdr_codigo = "-1"
        comp.cdr_descripcion = f"Error envio: {e}"[:500]
        db.commit()
        return {"success": False, "error": str(e), "stage": "envio",
                "envio_id": envio.id, "xml_path": str(xml_path)}

    envio.codigo_respuesta = codigo
    envio.descripcion_respuesta = (descripcion or "")[:500]
    envio.recibido_at = _now_lima_naive()

    cdr_path: Optional[Path] = None
    if cdr_b64:
        cdr_path = storage / "cdr" / f"R-{filename}.zip"
        try:
            _save_bytes(cdr_path, base64.b64decode(cdr_b64))
            comp.cdr_path = str(cdr_path)
            envio.cdr_path = str(cdr_path)
        except Exception as e:
            logger.warning("No se pudo guardar CDR: %s", e)
            cdr_path = None

    comp.cdr_codigo = (codigo or "")[:10]
    comp.cdr_descripcion = (descripcion or "")[:500]
    comp.cdr_hash = _cdr_hash(cdr_b64)

    # Bug A8/F10: solo "Aceptado" si CDR vino con codigo "0"
    aceptado = (codigo == "0") and bool(cdr_b64)
    if aceptado:
        comp.estado = "A"
        envio.estado = "aceptado"
    else:
        # Codigos 2xxx-3xxx son rechazos; 4xxx son observaciones (aceptado con obs).
        # Para Factura-mdb simplificamos: solo "0" es aceptado, todo lo demas R.
        comp.estado = "R"
        envio.estado = "rechazado"

    pdf_path: Optional[Path] = None
    if aceptado and generar_pdf:
        try:
            from .pdf_generator import generar_pdf_comprobante
            logger.info("Generando PDF para comprobante %s", comp.id)
            pdf_bytes = generar_pdf_comprobante(comp, detalles, empresa, config)
            pdf_path = storage / "pdf" / f"{filename}.pdf"
            _save_bytes(pdf_path, pdf_bytes)
            comp.pdf_path = str(pdf_path)
        except Exception as e:
            logger.warning("PDF fallo (no critico): %s", e)
            pdf_path = None

    db.commit()
    db.refresh(comp)

    return {
        "success": aceptado,
        "codigo": codigo,
        "descripcion": descripcion,
        "estado": comp.estado,
        "xml_path": str(xml_path),
        "cdr_path": str(cdr_path) if cdr_path else None,
        "pdf_path": str(pdf_path) if pdf_path else None,
        "envio_id": envio.id,
        "comprobante_id": comp.id,
    }
