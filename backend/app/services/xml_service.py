"""
Orquestador local de generacion XML + firma digital.
"""
import logging
from typing import Dict, Any, Tuple

from .xml_generators.invoice_generator import (
    calcular_totales as calcular_totales_invoice,
    generar_xml_invoice,
)
from .xml_generators.credit_note_generator import (
    calcular_totales as calcular_totales_cn,
    generar_xml_credit_note,
)
from .xml_generators.debit_note_generator import (
    calcular_totales as calcular_totales_dn,
    generar_xml_debit_note,
)
from .xml_generators.summary_generator import generar_xml_resumen
from .xml_generators.voided_generator import generar_xml_baja
from .firma_digital import firmar_desde_archivo, xml_to_string
from .xml_models import (
    FacturaRequest, NotaCreditoRequest, NotaDebitoRequest,
    ResumenDiarioRequest, ComunicacionBajaRequest,
)


logger = logging.getLogger(__name__)


def generar_y_firmar_factura(
    req: FacturaRequest,
    cert_path: str,
    cert_password: str,
) -> Tuple[str, Dict[str, Any]]:
    """Genera XML de Factura/Boleta, lo firma y devuelve (xml_firmado_str, totales)."""
    totales = calcular_totales_invoice(req)
    xml_root = generar_xml_invoice(req, totales)
    firmar_desde_archivo(xml_root, cert_path, cert_password)
    xml_str = xml_to_string(xml_root)
    logger.info("XML factura generado y firmado: %s-%s", req.serie, req.numero)
    return xml_str, totales


def generar_y_firmar_nota_credito(
    req: NotaCreditoRequest,
    cert_path: str,
    cert_password: str,
) -> Tuple[str, Dict[str, Any]]:
    """Genera XML de Nota de Credito, lo firma y devuelve (xml_firmado_str, totales)."""
    totales = calcular_totales_cn(req)
    xml_root = generar_xml_credit_note(req, totales)
    firmar_desde_archivo(xml_root, cert_path, cert_password)
    xml_str = xml_to_string(xml_root)
    logger.info("XML nota credito generado y firmado: %s-%s", req.serie, req.numero)
    return xml_str, totales


def generar_y_firmar_nota_debito(
    req: NotaDebitoRequest,
    cert_path: str,
    cert_password: str,
) -> Tuple[str, Dict[str, Any]]:
    """Genera XML de Nota de Debito, lo firma y devuelve (xml_firmado_str, totales)."""
    totales = calcular_totales_dn(req)
    xml_root = generar_xml_debit_note(req, totales)
    firmar_desde_archivo(xml_root, cert_path, cert_password)
    xml_str = xml_to_string(xml_root)
    logger.info("XML nota debito generado y firmado: %s-%s", req.serie, req.numero)
    return xml_str, totales


def generar_y_firmar_resumen(
    req: ResumenDiarioRequest,
    cert_path: str,
    cert_password: str,
) -> Tuple[str, str]:
    """Genera XML Resumen Diario firmado. Retorna (xml_str, nombre_archivo)."""
    xml_root = generar_xml_resumen(req)
    firmar_desde_archivo(xml_root, cert_path, cert_password)
    xml_str = xml_to_string(xml_root)

    # SUNAT exige que la fecha del nombre del archivo == IssueDate (fecha_comunicacion)
    fecha_str = req.fecha_comunicacion.replace("-", "")
    nombre = f"{req.ruc_emisor}-RC-{fecha_str}-{req.correlativo}"
    logger.info("XML resumen diario generado y firmado: %s", nombre)
    return xml_str, nombre


def generar_y_firmar_baja(
    req: ComunicacionBajaRequest,
    cert_path: str,
    cert_password: str,
) -> Tuple[str, str]:
    """Genera XML Comunicacion de Baja firmado. Retorna (xml_str, nombre_archivo)."""
    xml_root = generar_xml_baja(req)
    firmar_desde_archivo(xml_root, cert_path, cert_password)
    xml_str = xml_to_string(xml_root)

    fecha_str = req.fecha_comunicacion.replace("-", "")
    nombre = f"{req.ruc_emisor}-RA-{fecha_str}-{req.correlativo.zfill(5)}"
    logger.info("XML comunicacion de baja generado y firmado: %s", nombre)
    return xml_str, nombre
