"""Orquestador de Comunicacion de Baja (RA).

Flujo:
    1. Carga ComunicacionBaja + items
    2. Convierte a ComunicacionBajaRequest
    3. Genera XML, firma, envia (sendSummary -> ticket)
    4. Guarda ticket
    5. (Opcional) consulta getStatus y procesa CDR
"""
from __future__ import annotations
import asyncio
import base64
import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

from sqlalchemy.orm import Session

from ..core.config import storage_dir
from ..models.comprobante import Comprobante
from ..models.empresa import Empresa
from ..models.resumen import ComunicacionBaja, ComunicacionBajaItem
from .comprobante_mapper import comunicacion_to_baja_request
from .sunat_client import SUNATClient
from .xml_service import generar_y_firmar_baja


logger = logging.getLogger(__name__)


def _save_bytes(path: Path, data: bytes) -> None:
    """Bug F6: escritura atomica via tempfile + os.replace."""
    import os, tempfile
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


def _str_date(d) -> str:
    if d is None:
        return ""
    if isinstance(d, str):
        return d
    return d.strftime("%Y-%m-%d")


async def _enviar_async(empresa: Empresa, xml_str: str, nombre: str) -> str:
    client = SUNATClient(
        ruc=empresa.ruc,
        sol_user=empresa.sol_user or "",
        sol_pass=empresa.sol_pass or "",
        ambiente=empresa.sunat_env,
    )
    return await client.enviar_resumen(xml_str, nombre)


async def _consultar_async(empresa: Empresa, ticket: str):
    client = SUNATClient(
        ruc=empresa.ruc,
        sol_user=empresa.sol_user or "",
        sol_pass=empresa.sol_pass or "",
        ambiente=empresa.sunat_env,
    )
    return await client.consultar_ticket(ticket)


def enviar_baja(db: Session, baja_id: int, *,
                consultar_ahora: bool = False) -> Dict[str, Any]:
    """Envia una Comunicacion de Baja a SUNAT."""
    baja = db.query(ComunicacionBaja).filter(ComunicacionBaja.id == baja_id).first()
    if not baja:
        return {"success": False, "error": f"ComunicacionBaja {baja_id} no encontrada"}

    empresa = db.query(Empresa).first()
    if not empresa:
        return {"success": False, "error": "No hay Empresa configurada"}

    items = (
        db.query(ComunicacionBajaItem)
          .filter(ComunicacionBajaItem.baja_id == baja.id)
          .all()
    )
    if not items:
        return {"success": False, "error": "Baja sin items"}

    comp_ids = [it.comprobante_id for it in items]
    comprobantes = (
        db.query(Comprobante).filter(Comprobante.id.in_(comp_ids)).all()
    )
    motivo_por_id = {it.comprobante_id: (it.motivo or baja.motivo) for it in items}

    docs = []
    for c in comprobantes:
        docs.append({
            "tipo_doc": c.tipo_documento,
            "serie": c.serie,
            "correlativo": c.correlativo,
            "motivo": motivo_por_id.get(c.id) or baja.motivo,
        })

    correlativo = str(baja.correlativo).zfill(5)
    req = comunicacion_to_baja_request(
        empresa=empresa,
        correlativo=correlativo,
        fecha_documentos=_str_date(baja.fecha_documentos),
        fecha_comunicacion=_str_date(baja.fecha_comunicacion),
        documentos_baja=docs,
    )

    cert_path = empresa.certificado_path
    cert_pass = empresa.certificado_pass or ""
    if not cert_path:
        return {"success": False, "error": "Empresa sin certificado_path configurado"}

    try:
        xml_str, nombre = generar_y_firmar_baja(req, cert_path, cert_pass)
    except Exception as e:
        logger.exception("Error firmando baja")
        baja.estado = "R"
        baja.cdr_descripcion = f"Error firma: {e}"[:500]
        db.commit()
        return {"success": False, "error": str(e), "stage": "firma"}

    storage = storage_dir()
    xml_path = storage / "xml" / f"{nombre}.xml"
    _save_text(xml_path, xml_str)
    baja.xml_path = str(xml_path)
    baja.nombre_archivo = nombre

    try:
        logger.info("Enviando baja %s a SUNAT", nombre)
        ticket = asyncio.run(_enviar_async(empresa, xml_str, nombre))
    except Exception as e:
        logger.exception("Error enviando baja")
        baja.estado = "R"
        baja.cdr_descripcion = f"Error envio: {e}"[:500]
        db.commit()
        return {"success": False, "error": str(e), "stage": "envio"}

    if not ticket:
        baja.estado = "R"
        baja.cdr_descripcion = "SUNAT no devolvio ticket"
        db.commit()
        return {"success": False, "error": "Sin ticket", "stage": "envio"}

    baja.ticket = ticket
    baja.estado = "P"
    db.commit()

    if not consultar_ahora:
        return {
            "success": True,
            "ticket": ticket,
            "estado": "P",
            "xml_path": str(xml_path),
            "baja_id": baja.id,
        }

    return procesar_ticket_baja(db, baja.id)


# Aliases para alinear con contrato del Agente B (api/comunicacion_baja.py)
def emitir_y_enviar(db: Session, baja_id: int) -> Dict[str, Any]:
    return enviar_baja(db, baja_id, consultar_ahora=False)


def consultar_ticket(db: Session, baja_id: int) -> Dict[str, Any]:
    return procesar_ticket_baja(db, baja_id)


def procesar_ticket_baja(db: Session, baja_id: int) -> Dict[str, Any]:
    """Consulta getStatus para una Baja y procesa el CDR.

    Marca los Comprobantes incluidos como estado 'B' (baja) si SUNAT acepta.
    """
    baja = db.query(ComunicacionBaja).filter(ComunicacionBaja.id == baja_id).first()
    if not baja:
        return {"success": False, "error": "Baja no encontrada"}
    if not baja.ticket:
        return {"success": False, "error": "Baja sin ticket"}

    empresa = db.query(Empresa).first()
    if not empresa:
        return {"success": False, "error": "Empresa no configurada"}

    try:
        codigo, descripcion, cdr_b64 = asyncio.run(_consultar_async(empresa, baja.ticket))
    except Exception as e:
        logger.exception("Error consultando ticket baja %s", baja.ticket)
        return {"success": False, "error": str(e), "stage": "consulta"}

    storage = storage_dir()
    cdr_path: Optional[Path] = None
    if cdr_b64:
        cdr_path = storage / "cdr" / f"R-{baja.nombre_archivo}.zip"
        try:
            _save_bytes(cdr_path, base64.b64decode(cdr_b64))
            baja.cdr_path = str(cdr_path)
        except Exception as e:
            logger.warning("No se pudo guardar CDR baja: %s", e)
            cdr_path = None

    baja.cdr_codigo = (codigo or "")[:10]
    baja.cdr_descripcion = (descripcion or "")[:500]
    if codigo == "0" and cdr_b64:
        baja.estado = "A"
        # Marcar comprobantes como dados de baja
        items = (
            db.query(ComunicacionBajaItem)
              .filter(ComunicacionBajaItem.baja_id == baja.id)
              .all()
        )
        for it in items:
            comp = db.query(Comprobante).filter(Comprobante.id == it.comprobante_id).first()
            if comp:
                comp.estado = "B"
    elif codigo and codigo not in ("98", "99"):
        baja.estado = "R"
        if codigo == "0" and not cdr_b64:
            baja.cdr_descripcion = (
                f"Sin CDR valido. Codigo: {codigo}, descripcion: {descripcion}"
            )[:500]

    db.commit()

    return {
        "success": codigo == "0" and bool(cdr_b64),
        "codigo": codigo,
        "descripcion": descripcion,
        "estado": baja.estado,
        "cdr_path": str(cdr_path) if cdr_path else None,
        "baja_id": baja.id,
    }
