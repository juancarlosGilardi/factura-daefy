"""Servicio de Resumen Diario para modo DBF.

Genera y envia resumen diario de boletas directamente desde ventas.dbf
sin depender de SQLAlchemy/MDB. Flujo:
    1. Lee boletas (tipo 03) de fecha especificada desde ventas.dbf
    2. Construye ResumenDiarioRequest
    3. Genera XML, firma, envia (sendSummary -> ticket)
    4. Persiste CDR en storage/cdr/
    5. Retorna metadata de respuesta SUNAT
"""
from __future__ import annotations

import asyncio
import base64
import logging
from datetime import datetime, date
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from ..core.config import storage_dir, settings
from ..core.db_adapter import get_dbf_path
from ..services.comprobante_mapper import comprobantes_to_resumen_request
from ..services.sunat_client import SUNATClient
from ..services.xml_service import generar_y_firmar_resumen
from ..services.dbf_importer import mappers as dbf_mappers

logger = logging.getLogger(__name__)


def _save_bytes(path: Path, data: bytes) -> None:
    """Escritura atomica via tempfile + os.replace."""
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


def _dbf(name: str):
    """Abre un DBF de GECOPE — devuelve iterable de dicts."""
    from ..core.db_adapter.dbf_repo import _DbfWrapper
    path: Path = get_dbf_path() / name
    if not path.exists():
        for child in path.parent.iterdir():
            if child.name.lower() == name.lower():
                path = child
                break
    return _DbfWrapper(path)


async def _enviar_async(empresa_dict: dict, xml_str: str, nombre: str) -> str:
    """Envia resumen a SUNAT y devuelve ticket."""
    client = SUNATClient(
        ruc=empresa_dict.get("ruc", ""),
        sol_user=empresa_dict.get("sol_user", ""),
        sol_pass=empresa_dict.get("sol_pass", ""),
        ambiente=empresa_dict.get("sunat_env", "beta"),
    )
    return await client.enviar_resumen(xml_str, nombre)


async def _consultar_async(empresa_dict: dict, ticket: str):
    """Consulta estado de ticket a SUNAT."""
    client = SUNATClient(
        ruc=empresa_dict.get("ruc", ""),
        sol_user=empresa_dict.get("sol_user", ""),
        sol_pass=empresa_dict.get("sol_pass", ""),
        ambiente=empresa_dict.get("sunat_env", "beta"),
    )
    return await client.consultar_ticket(ticket)


def _cargar_comprobantes_fecha(fecha_emision: date) -> list[dict]:
    """Carga todos los comprobantes (tipo 03 = boleta) de una fecha específica.

    Lee directamente desde ventas.dbf y ventas_detalle.dbf.
    Retorna lista de dicts con estructura similar a modelos Comprobante.
    """
    comprobantes = []
    fecha_str = _str_date(fecha_emision)

    # Mapeo de CODIGO ventas -> comprobante completo
    ventas_por_codigo: dict[Any, dict] = {}

    for row in _dbf("ventas.dbf"):
        d = dbf_mappers.dbf_comprobante_to_dict(row)
        if not d:
            continue
        # Filtrar por tipo 03 (boleta) y fecha
        if d.get("tipo_documento") != "03":
            continue
        if _str_date(d.get("fecha_emision")) != fecha_str:
            continue

        gecope_codigo = d.get("_gecope_codigo")
        ventas_por_codigo[gecope_codigo] = d

    if not ventas_por_codigo:
        logger.warning("No hay boletas del %s para resumen", fecha_str)
        return []

    # Cargar detalles
    for row in _dbf("ventas_detalle.dbf"):
        row_cod = row.get("CODIGO")
        if row_cod not in ventas_por_codigo:
            continue

        # Saltar líneas anuladas
        if bool(row.get("ANULADO")) or bool(row.get("REGISTRO_A")):
            continue

        comp = ventas_por_codigo[row_cod]
        if "detalles" not in comp:
            comp["detalles"] = []

        # Mapear detalle
        det_dict = dbf_mappers.dbf_detalle_to_dict(row, orden=len(comp["detalles"]) + 1)
        if det_dict:
            comp["detalles"].append(det_dict)

    return list(ventas_por_codigo.values())


def _cargar_empresa() -> dict[str, Any]:
    """Carga configuración de empresa desde config.json y datos_compañia.dbf."""
    empresa: dict[str, Any] = {}

    # De settings
    empresa["ruc"] = settings.RUC or ""
    empresa["razon_social"] = settings.EMPRESA.get("razon_social", "") if settings.EMPRESA else ""
    empresa["nombre_comercial"] = (
        settings.EMPRESA.get("nombre_comercial")
        if settings.EMPRESA else
        empresa.get("razon_social", "")
    )
    empresa["direccion"] = settings.EMPRESA.get("direccion", "") if settings.EMPRESA else ""
    empresa["ubigeo"] = settings.EMPRESA.get("ubigeo", "150131") if settings.EMPRESA else "150131"
    empresa["departamento"] = settings.EMPRESA.get("departamento") if settings.EMPRESA else None
    empresa["provincia"] = settings.EMPRESA.get("provincia") if settings.EMPRESA else None
    empresa["distrito"] = settings.EMPRESA.get("distrito") if settings.EMPRESA else None
    empresa["telefono"] = settings.EMPRESA.get("telefono") if settings.EMPRESA else None
    empresa["email"] = settings.EMPRESA.get("email") if settings.EMPRESA else None

    # De SUNAT
    empresa["sol_user"] = settings.SOL_USER or ""
    empresa["sol_pass"] = settings.SOL_PASS or ""
    empresa["sunat_env"] = (settings.SUNAT_ENV or "beta").lower()
    empresa["certificado_path"] = str(settings.CERT_PATH) if settings.CERT_PATH else None
    empresa["certificado_pass"] = settings.CERT_PASS or ""

    return empresa


def _construir_comprobante_simulado(d: dict) -> dict:
    """Convierte dict de DBF a estructura compatible con comprobante_mapper."""
    # El mapeo ya se hizo en dbf_comprobante_to_dict, solo normalizar algunos campos
    return {
        "id": d.get("id"),
        "tipo_documento": d.get("tipo_documento", "03"),
        "serie": d.get("serie", ""),
        "correlativo": d.get("correlativo", 0),
        "numero_completo": d.get("numero_completo", ""),
        "fecha_emision": d.get("fecha_emision"),
        "cliente_tipo_doc": d.get("cliente_tipo_doc", "1"),
        "cliente_numero_doc": d.get("cliente_numero_doc", ""),
        "cliente_razon_social": d.get("cliente_razon_social", ""),
        "moneda": d.get("moneda", "PEN"),
        "total_venta": d.get("total_venta", 0),
        "total_gravado": d.get("total_gravado", 0),
        "total_exonerado": d.get("total_exonerado", 0),
        "total_inafecto": d.get("total_inafecto", 0),
        "total_exportacion": d.get("total_exportacion", 0),
        "total_gratuito": d.get("total_gratuito", 0),
        "total_igv": d.get("total_igv", 0),
        "total_isc": d.get("total_isc", 0),
        "estado": d.get("estado", "E"),
        # NC/ND referencias
        "doc_referencia_serie": d.get("doc_referencia_serie"),
        "doc_referencia_tipo": d.get("doc_referencia_tipo"),
    }


def emitir_y_enviar_resumen(
    fecha_referencia: date,
    fecha_comunicacion: Optional[date] = None,
    correlativo: Optional[int] = None,
) -> Dict[str, Any]:
    """Genera, firma y envía resumen diario a SUNAT.

    Args:
        fecha_referencia: Fecha de emisión de boletas a incluir (YYYY-MM-DD)
        fecha_comunicacion: Fecha de comunicación (default: hoy)
        correlativo: Correlativo del resumen (default: auto-increment)

    Returns:
        Dict con {success, ticket, estado, xml_path, o error}
    """
    fecha_com = fecha_comunicacion or datetime.now().date()

    # Cargar comprobantes de la fecha especificada
    comprobantes_dbf = _cargar_comprobantes_fecha(fecha_referencia)
    if not comprobantes_dbf:
        return {
            "success": False,
            "error": f"No hay boletas emitidas el {fecha_referencia}",
        }

    logger.info("Cargados %d comprobantes del %s", len(comprobantes_dbf), fecha_referencia)

    # Cargar empresa
    empresa = _cargar_empresa()
    if not empresa.get("ruc"):
        return {"success": False, "error": "RUC de empresa no configurado"}

    # Validar certificado
    cert_path = empresa.get("certificado_path")
    cert_pass = empresa.get("certificado_pass", "")
    if not cert_path or not Path(cert_path).exists():
        return {
            "success": False,
            "error": f"Certificado no encontrado: {cert_path}",
        }

    # Determinar correlativo
    if correlativo is None:
        correlativo = 1  # TODO: guardar último correlativo en archivo de control

    # Construir ResumenDiarioRequest
    try:
        # Convertir comprobantes a estructura esperada por mapper
        comps_convertidos = [
            _construir_comprobante_simulado(d) for d in comprobantes_dbf
        ]

        req = comprobantes_to_resumen_request(
            empresa=type('Empresa', (), empresa),  # Mock de objeto Empresa
            correlativo=str(correlativo).zfill(5),
            fecha_documentos=_str_date(fecha_referencia),
            fecha_comunicacion=_str_date(fecha_com),
            comprobantes=comps_convertidos,
        )
    except Exception as e:
        logger.exception("Error construyendo ResumenDiarioRequest")
        return {"success": False, "error": str(e), "stage": "mapper"}

    # Generar y firmar XML
    try:
        xml_str, nombre = generar_y_firmar_resumen(req, cert_path, cert_pass)
    except Exception as e:
        logger.exception("Error firmando resumen")
        return {"success": False, "error": str(e), "stage": "firma"}

    # Guardar XML
    storage = storage_dir()
    xml_path = storage / "xml" / f"{nombre}.xml"
    try:
        _save_text(xml_path, xml_str)
        logger.info("XML guardado en %s", xml_path)
    except Exception as e:
        logger.exception("Error guardando XML")
        return {"success": False, "error": str(e), "stage": "almacenamiento"}

    # Enviar a SUNAT
    try:
        logger.info("Enviando resumen %s a SUNAT", nombre)
        ticket = asyncio.run(_enviar_async(empresa, xml_str, nombre))
    except Exception as e:
        logger.exception("Error enviando resumen a SUNAT")
        return {"success": False, "error": str(e), "stage": "envio"}

    if not ticket:
        return {
            "success": False,
            "error": "SUNAT no devolvió ticket",
            "stage": "envio",
        }

    logger.info("Resumen enviado con ticket %s", ticket)

    return {
        "success": True,
        "ticket": ticket,
        "estado": "P",  # Pendiente de getStatus
        "xml_path": str(xml_path),
        "nombre_archivo": nombre,
        "fecha_referencia": _str_date(fecha_referencia),
        "fecha_comunicacion": _str_date(fecha_com),
        "correlativo": int(correlativo),
        "total_documentos": len(comps_convertidos),
    }


def consultar_ticket_resumen(ticket: str) -> Dict[str, Any]:
    """Consulta estado de un ticket de resumen diario a SUNAT.

    Args:
        ticket: Número de ticket devuelto por sendSummary

    Returns:
        Dict con {success, codigo, descripcion, cdr_path, estado}
    """
    empresa = _cargar_empresa()

    try:
        codigo, descripcion, cdr_b64 = asyncio.run(
            _consultar_async(empresa, ticket)
        )
    except Exception as e:
        logger.exception("Error consultando ticket %s", ticket)
        return {"success": False, "error": str(e), "stage": "consulta"}

    # Guardar CDR si está disponible
    cdr_path: Optional[str] = None
    if cdr_b64:
        storage = storage_dir()
        cdr_file = storage / "cdr" / f"R-{ticket}.zip"
        try:
            _save_bytes(cdr_file, base64.b64decode(cdr_b64))
            cdr_path = str(cdr_file)
            logger.info("CDR guardado en %s", cdr_path)
        except Exception as e:
            logger.warning("Error guardando CDR: %s", e)

    # Determinar estado final
    estado = "R"  # Rechazado por defecto
    if codigo == "0" and cdr_b64:
        estado = "A"  # Aceptado
    elif codigo in ("98", "99"):
        estado = "P"  # Pendiente

    return {
        "success": codigo == "0" and bool(cdr_b64),
        "codigo": codigo,
        "descripcion": descripcion,
        "estado": estado,
        "cdr_path": cdr_path,
        "ticket": ticket,
    }
