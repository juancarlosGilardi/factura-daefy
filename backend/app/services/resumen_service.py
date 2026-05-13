"""Orquestador de Resumen Diario (RC) — boletas, NC/ND de boletas.

Flujo:
    1. Carga ResumenDiario + ResumenDiarioItem -> Comprobantes
    2. Convierte a ResumenDiarioRequest
    3. Genera XML, firma, envia (sendSummary -> ticket)
    4. Guarda ticket
    5. (Opcional, si `consultar_ahora=True`) consulta getStatus y procesa CDR
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
from ..models.resumen import ResumenDiario, ResumenDiarioItem
from .comprobante_mapper import comprobantes_to_resumen_request
from .sunat_client import SUNATClient
from .xml_service import generar_y_firmar_resumen


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


def enviar_resumen(db: Session, resumen_id: int, *,
                    consultar_ahora: bool = False) -> Dict[str, Any]:
    """Envia un Resumen Diario a SUNAT.

    Si `consultar_ahora=True`, hace getStatus inmediatamente despues de sendSummary.
    En produccion conviene dejarlo en False y tener un job que consulte ticket
    pendiente cada N minutos.
    """
    resumen = db.query(ResumenDiario).filter(ResumenDiario.id == resumen_id).first()
    if not resumen:
        return {"success": False, "error": f"ResumenDiario {resumen_id} no encontrado"}

    empresa = db.query(Empresa).first()
    if not empresa:
        return {"success": False, "error": "No hay Empresa configurada"}

    items = (
        db.query(ResumenDiarioItem)
          .filter(ResumenDiarioItem.resumen_id == resumen.id)
          .all()
    )
    if not items:
        return {"success": False, "error": "Resumen sin items"}

    comp_ids = [it.comprobante_id for it in items]
    comprobantes = (
        db.query(Comprobante).filter(Comprobante.id.in_(comp_ids)).all()
    )

    correlativo = str(resumen.correlativo).zfill(5)
    req = comprobantes_to_resumen_request(
        empresa=empresa,
        correlativo=correlativo,
        fecha_documentos=_str_date(resumen.fecha_referencia),
        fecha_comunicacion=_str_date(resumen.fecha_comunicacion),
        comprobantes=comprobantes,
    )

    cert_path = empresa.certificado_path
    cert_pass = empresa.certificado_pass or ""
    if not cert_path:
        return {"success": False, "error": "Empresa sin certificado_path configurado"}

    try:
        xml_str, nombre = generar_y_firmar_resumen(req, cert_path, cert_pass)
    except Exception as e:
        logger.exception("Error firmando resumen")
        resumen.estado = "R"
        resumen.cdr_descripcion = f"Error firma: {e}"[:500]
        db.commit()
        return {"success": False, "error": str(e), "stage": "firma"}

    storage = storage_dir()
    xml_path = storage / "xml" / f"{nombre}.xml"
    _save_text(xml_path, xml_str)
    resumen.xml_path = str(xml_path)
    resumen.nombre_archivo = nombre

    try:
        logger.info("Enviando resumen %s a SUNAT", nombre)
        ticket = asyncio.run(_enviar_async(empresa, xml_str, nombre))
    except Exception as e:
        logger.exception("Error enviando resumen")
        resumen.estado = "R"
        resumen.cdr_descripcion = f"Error envio: {e}"[:500]
        db.commit()
        return {"success": False, "error": str(e), "stage": "envio"}

    if not ticket:
        resumen.estado = "R"
        resumen.cdr_descripcion = "SUNAT no devolvio ticket"
        db.commit()
        return {"success": False, "error": "Sin ticket", "stage": "envio"}

    resumen.ticket = ticket
    resumen.estado = "P"  # pendiente de getStatus
    db.commit()

    if not consultar_ahora:
        return {
            "success": True,
            "ticket": ticket,
            "estado": "P",
            "xml_path": str(xml_path),
            "resumen_id": resumen.id,
        }

    return procesar_ticket_resumen(db, resumen.id)


# Aliases para alinear con contrato del Agente B (api/resumen_diario.py)
def emitir_y_enviar(db: Session, resumen_id: int) -> Dict[str, Any]:
    return enviar_resumen(db, resumen_id, consultar_ahora=False)


def consultar_ticket(db: Session, resumen_id: int) -> Dict[str, Any]:
    return procesar_ticket_resumen(db, resumen_id)


def procesar_ticket_resumen(db: Session, resumen_id: int) -> Dict[str, Any]:
    """Consulta getStatus para un Resumen y procesa el CDR."""
    resumen = db.query(ResumenDiario).filter(ResumenDiario.id == resumen_id).first()
    if not resumen:
        return {"success": False, "error": "Resumen no encontrado"}
    if not resumen.ticket:
        return {"success": False, "error": "Resumen sin ticket"}

    empresa = db.query(Empresa).first()
    if not empresa:
        return {"success": False, "error": "Empresa no configurada"}

    try:
        codigo, descripcion, cdr_b64 = asyncio.run(_consultar_async(empresa, resumen.ticket))
    except Exception as e:
        logger.exception("Error consultando ticket %s", resumen.ticket)
        return {"success": False, "error": str(e), "stage": "consulta"}

    storage = storage_dir()
    cdr_path: Optional[Path] = None
    if cdr_b64:
        cdr_path = storage / "cdr" / f"R-{resumen.nombre_archivo}.zip"
        try:
            _save_bytes(cdr_path, base64.b64decode(cdr_b64))
            resumen.cdr_path = str(cdr_path)
        except Exception as e:
            logger.warning("No se pudo guardar CDR resumen: %s", e)
            cdr_path = None

    resumen.cdr_codigo = (codigo or "")[:10]
    resumen.cdr_descripcion = (descripcion or "")[:500]
    if codigo == "0" and cdr_b64:
        resumen.estado = "A"
    elif codigo and codigo not in ("98", "99"):
        # 98/99 = pendiente de procesamiento
        resumen.estado = "R"
        if codigo == "0" and not cdr_b64:
            resumen.cdr_descripcion = (
                f"Sin CDR valido. Codigo: {codigo}, descripcion: {descripcion}"
            )[:500]
    db.commit()

    return {
        "success": codigo == "0" and bool(cdr_b64),
        "codigo": codigo,
        "descripcion": descripcion,
        "estado": resumen.estado,
        "cdr_path": str(cdr_path) if cdr_path else None,
        "resumen_id": resumen.id,
    }


# ===================================================================
# Modo MDB / SQLite (sin SQLAlchemy) — Sprint 3
# Añadido por agente para soportar /api/resumen-diario sobre TBVENTA_CAB
# ===================================================================
def _build_resumen_request_from_dicts(empresa_ns, correlativo, fecha_documentos,
                                       fecha_comunicacion, comprobantes_dicts):
    """Versión de comprobantes_to_resumen_request que acepta dicts y respeta
    la condicion por comprobante (key '__condicion'). 1=adicionar, 2=modificar,
    3=anular."""
    from .xml_models import ResumenDiarioRequest
    from .xml_models.summary import DocumentoResumen

    documentos = []
    for c in comprobantes_dicts:
        tipo = c.get("tipo_documento")
        condicion = str(c.get("__condicion") or "1")
        doc_ref = ""
        tipo_doc_ref = "03"
        if tipo in ("07", "08"):
            doc_ref = c.get("doc_referencia_serie") or ""
            tipo_doc_ref = c.get("doc_referencia_tipo") or "03"

        documentos.append(DocumentoResumen(
            tipo_doc=tipo,
            serie_numero=c.get("numero_completo") or "",
            tipo_doc_cliente=c.get("cliente_tipo_doc") or "0",
            num_doc_cliente=c.get("cliente_numero_doc") or "00000000",
            condicion=condicion,
            moneda=c.get("moneda") or "PEN",
            total=float(c.get("total_venta") or 0),
            doc_referencia=doc_ref,
            tipo_doc_referencia=tipo_doc_ref,
            gravada=float(c.get("total_gravado") or 0),
            exonerada=float(c.get("total_exonerado") or 0),
            inafecta=float(c.get("total_inafecto") or 0),
            exportacion=float(c.get("total_exportacion") or 0),
            gratuita=float(c.get("total_gratuito") or 0),
            igv=float(c.get("total_igv") or 0),
            isc=float(c.get("total_isc") or 0),
        ))

    return ResumenDiarioRequest(
        ruc_emisor=empresa_ns.ruc,
        razon_social_emisor=empresa_ns.razon_social,
        correlativo=str(correlativo),
        fecha_documentos=fecha_documentos,
        fecha_comunicacion=fecha_comunicacion,
        documentos=documentos,
    )


def enviar_resumen_directo(
    resumen_dict: dict,
    empresa_dict: dict,
    comprobantes_dicts: list[dict],
) -> Dict[str, Any]:
    """Genera + firma + envía un Resumen Diario a SUNAT (sin Session).

    Args:
        resumen_dict: ``{correlativo, fecha_referencia, fecha_comunicacion,
                          nombre_archivo}``.
        empresa_dict: dict de empresa.
        comprobantes_dicts: lista de dicts de comprobante (incluyendo key
            ``__condicion`` con '1'|'2'|'3').

    Returns:
        dict con: success, ticket, error, stage, xml_path, nombre_archivo.
    """
    from ._dict_adapter import DictNS

    empresa = DictNS(empresa_dict)
    if not getattr(empresa, "certificado_path", None):
        return {"success": False,
                "error": "Empresa sin certificado_path configurado",
                "stage": "config"}

    correlativo = str(resumen_dict.get("correlativo") or 1).zfill(3)
    try:
        req = _build_resumen_request_from_dicts(
            empresa_ns=empresa,
            correlativo=correlativo,
            fecha_documentos=_str_date(resumen_dict.get("fecha_referencia")),
            fecha_comunicacion=_str_date(resumen_dict.get("fecha_comunicacion")),
            comprobantes_dicts=comprobantes_dicts,
        )
    except Exception as e:  # noqa: BLE001
        logger.exception("Error armando request de resumen")
        return {"success": False, "error": str(e), "stage": "mapper"}

    try:
        xml_str, nombre = generar_y_firmar_resumen(
            req,
            empresa.certificado_path,
            empresa.certificado_pass or "",
        )
    except Exception as e:  # noqa: BLE001
        logger.exception("Error firmando resumen (directo)")
        return {"success": False, "error": str(e), "stage": "firma"}

    from ..core.config import storage_dir
    storage = storage_dir()
    xml_path = storage / "xml" / f"{nombre}.xml"
    _save_text(xml_path, xml_str)

    try:
        logger.info("Enviando resumen %s a SUNAT (directo)", nombre)
        ticket = asyncio.run(_enviar_async(empresa, xml_str, nombre))
    except Exception as e:  # noqa: BLE001
        logger.exception("Error enviando resumen (directo)")
        return {
            "success": False,
            "error": str(e),
            "stage": "envio",
            "xml_path": str(xml_path),
            "nombre_archivo": nombre,
        }

    if not ticket:
        return {
            "success": False,
            "error": "SUNAT no devolvió ticket",
            "stage": "envio",
            "xml_path": str(xml_path),
            "nombre_archivo": nombre,
        }

    return {
        "success": True,
        "ticket": ticket,
        "estado": "P",
        "xml_path": str(xml_path),
        "nombre_archivo": nombre,
    }


def consultar_ticket_directo(
    empresa_dict: dict,
    ticket: str,
    nombre_archivo: str,
) -> Dict[str, Any]:
    """Consulta getStatus para un ticket de resumen, sin Session."""
    from ._dict_adapter import DictNS
    empresa = DictNS(empresa_dict)

    if not ticket:
        return {"success": False, "error": "ticket vacío"}

    try:
        codigo, descripcion, cdr_b64 = asyncio.run(_consultar_async(empresa, ticket))
    except Exception as e:  # noqa: BLE001
        logger.exception("Error consultando ticket resumen %s", ticket)
        return {"success": False, "error": str(e), "stage": "consulta"}

    from ..core.config import storage_dir
    storage = storage_dir()
    cdr_path: Optional[Path] = None
    if cdr_b64:
        cdr_path = storage / "cdr" / f"R-{nombre_archivo}.zip"
        try:
            _save_bytes(cdr_path, base64.b64decode(cdr_b64))
        except Exception as e:  # noqa: BLE001
            logger.warning("No se pudo guardar CDR resumen: %s", e)
            cdr_path = None

    aceptado = (codigo == "0") and bool(cdr_b64)
    estado = "A" if aceptado else ("R" if codigo and codigo not in ("98", "99") else "P")

    return {
        "success": aceptado,
        "codigo": codigo,
        "descripcion": descripcion,
        "estado": estado,
        "cdr_path": str(cdr_path) if cdr_path else None,
    }
