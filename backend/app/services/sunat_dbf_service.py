"""Servicio de envio a SUNAT para modo DBF (flujo en 2 pasos DAEFY).

Lee un comprobante desde `ventas.dbf` (estado E o T), genera XML UBL 2.1,
lo firma, lo envia a SUNAT y persiste el resultado clasificando el error:

    - HTTP 200 + CDR codigo "0"           → estado A (Aceptado)
    - HTTP 200 + CDR codigo != "0"        → estado R (Rechazado por contenido)
    - HTTPError 4xx                       → estado R (rechazo definitivo)
    - HTTPError 5xx / timeout / SSL / cnx → estado T (timeout, robot reintenta)
    - Error de firma / config             → estado R (no es transitorio)

Nota: el rechazo R se INMEDIATAMENTE escala a B (anulado de oficio por
SUNAT) — politica DAEFY. El endpoint /enviar-sunat hace ese 2do paso.

No usa SQLAlchemy. Usa DictNS para envolver dicts del DBF como objetos
con attribute access (los mappers XML hacen `comp.tipo_documento`).
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import logging
import os
import ssl
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

import httpx

from ..core.config import settings, storage_dir
from ..core.db_adapter import is_dbf_mode
from ..core.db_adapter.dbf_repo import ComprobanteRepoDBF, EmpresaRepoDBF
from ..core.db_adapter.dbf_writer import ComprobanteWriterDBF
from ._dict_adapter import DictNS
from .comprobante_mapper import (
    comprobante_to_factura_request,
    comprobante_to_nota_credito_request,
    comprobante_to_nota_debito_request,
)
from .sunat_client import SUNATClient
from .xml_service import (
    generar_y_firmar_factura,
    generar_y_firmar_nota_credito,
    generar_y_firmar_nota_debito,
)

logger = logging.getLogger("factura_mdb.sunat_dbf")


# Estados del flujo en 2 pasos.
ESTADO_EMITIDO = "E"
ESTADO_ACEPTADO = "A"
ESTADO_TIMEOUT = "T"
ESTADO_RECHAZADO = "R"
ESTADO_BAJA = "B"


# Codigos de error httpx que clasificamos como TRANSITORIOS (estado T).
# El robot APScheduler los reintentara cada 60s. Si la lista crece se ajusta.
_TRANSITORIOS = (
    httpx.ConnectError,
    httpx.ConnectTimeout,
    httpx.ReadTimeout,
    httpx.WriteTimeout,
    httpx.PoolTimeout,
    httpx.NetworkError,
    httpx.RemoteProtocolError,
    ssl.SSLError,
    asyncio.TimeoutError,
    TimeoutError,
)


def _save_bytes(path: Path, data: bytes) -> None:
    """Escritura atomica via tempfile + os.replace."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = tempfile.NamedTemporaryFile(
        dir=path.parent, delete=False, suffix=".tmp",
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
    """SHA1 hex del ZIP CDR (campo 11 del QR SUNAT)."""
    if not cdr_b64:
        return ""
    try:
        raw = base64.b64decode(cdr_b64)
        return hashlib.sha1(raw).hexdigest()
    except Exception:
        return ""


def _xml_filename(ruc: str, tipo: str, serie: str, correlativo: int) -> str:
    """Convencion SUNAT: RUC-TIPO-SERIE-CORRELATIVO (sin padding fijo)."""
    return f"{ruc}-{tipo}-{serie}-{correlativo}"


def _empresa_to_ns() -> Any:
    """Lee Empresa del DBF + settings y devuelve DictNS (attribute access).

    Los mappers XML hacen `empresa.ruc`, `empresa.certificado_path`, etc.
    """
    base = EmpresaRepoDBF.obtener()
    # Garantizar campos minimos
    base.setdefault("sunat_env", (settings.SUNAT_ENV or "beta").lower())
    base.setdefault("sol_user", settings.SOL_USER or "")
    base.setdefault("sol_pass", settings.SOL_PASS or "")
    if not base.get("certificado_path") and settings.CERT_PATH:
        base["certificado_path"] = str(settings.CERT_PATH)
    if not base.get("certificado_pass"):
        base["certificado_pass"] = settings.CERT_PASS or ""
    return DictNS(base)


def _generar_xml_firmado(comp_ns: Any, emp_ns: Any,
                          detalles_ns: list[Any]) -> Tuple[str, Dict[str, Any]]:
    """Genera y firma el XML segun tipo. Retorna (xml_str, totales)."""
    cert_path = getattr(emp_ns, "certificado_path", None)
    cert_pass = getattr(emp_ns, "certificado_pass", "") or ""
    if not cert_path or not Path(cert_path).exists():
        raise RuntimeError(
            f"Certificado SUNAT no encontrado en {cert_path!r}. "
            "Configura empresa.certificado_path en config.json."
        )

    tipo = getattr(comp_ns, "tipo_documento", None)
    if tipo in ("01", "03"):
        req = comprobante_to_factura_request(comp_ns, emp_ns, detalles_ns)
        return generar_y_firmar_factura(req, cert_path, cert_pass)
    if tipo == "07":
        req = comprobante_to_nota_credito_request(comp_ns, emp_ns, detalles_ns)
        return generar_y_firmar_nota_credito(req, cert_path, cert_pass)
    if tipo == "08":
        req = comprobante_to_nota_debito_request(comp_ns, emp_ns, detalles_ns)
        return generar_y_firmar_nota_debito(req, cert_path, cert_pass)
    raise ValueError(f"tipo_documento no soportado: {tipo}")


async def _enviar_async(emp_ns: Any, xml_str: str, filename: str) -> Tuple[str, str, str]:
    """Envia el XML a SUNAT. Retorna (codigo, descripcion, cdr_b64)."""
    client = SUNATClient(
        ruc=getattr(emp_ns, "ruc", ""),
        sol_user=getattr(emp_ns, "sol_user", "") or "",
        sol_pass=getattr(emp_ns, "sol_pass", "") or "",
        ambiente=getattr(emp_ns, "sunat_env", "beta"),
    )
    return await client.enviar_comprobante(xml_str, filename)


def _es_transitorio(exc: BaseException) -> bool:
    """Clasifica si un error httpx/red es transitorio (estado T)."""
    if isinstance(exc, _TRANSITORIOS):
        return True
    # HTTPStatusError 5xx tambien es transitorio.
    if isinstance(exc, httpx.HTTPStatusError):
        try:
            return 500 <= exc.response.status_code < 600
        except Exception:
            return False
    # Mensaje contiene texto tipo timeout/ssl/connection
    msg = str(exc).lower()
    if any(t in msg for t in ("timeout", "timed out", "ssl", "connection",
                                "connect", "network", "temporarily")):
        return True
    return False


def enviar_a_sunat_dbf(comp_id: int) -> Dict[str, Any]:
    """Envia un comprobante DBF a SUNAT y persiste el resultado.

    Args:
        comp_id: ID sintetico (`_synth_id("comp_dbf", tipo, serie, corr)`).

    Returns:
        dict con: {success, estado_nuevo, codigo_cdr, mensaje, debe_reintentar,
                   xml_path, cdr_path}.
    """
    if not is_dbf_mode():
        return {
            "success": False,
            "estado_nuevo": None,
            "mensaje": "Servicio solo disponible en modo DBF",
            "debe_reintentar": False,
        }

    comp_dict = ComprobanteRepoDBF.obtener(comp_id)
    if comp_dict is None:
        return {
            "success": False,
            "estado_nuevo": None,
            "mensaje": f"Comprobante {comp_id} no encontrado",
            "debe_reintentar": False,
        }

    estado_actual = comp_dict.get("estado")
    if estado_actual == ESTADO_ACEPTADO:
        return {
            "success": True,
            "estado_nuevo": ESTADO_ACEPTADO,
            "mensaje": "El comprobante ya fue aceptado por SUNAT",
            "debe_reintentar": False,
            "cdr_codigo": "0",
        }
    if estado_actual == ESTADO_BAJA:
        return {
            "success": False,
            "estado_nuevo": ESTADO_BAJA,
            "mensaje": "El comprobante esta anulado/baja; no se puede reenviar",
            "debe_reintentar": False,
        }

    tipo = comp_dict.get("tipo_documento")
    serie = comp_dict.get("serie")
    correlativo = int(comp_dict.get("correlativo") or 0)
    detalles = comp_dict.get("detalles") or []
    if not detalles:
        return {
            "success": False,
            "estado_nuevo": estado_actual,
            "mensaje": "Comprobante sin detalles, no se puede firmar",
            "debe_reintentar": False,
        }

    emp_ns = _empresa_to_ns()
    comp_ns = DictNS(comp_dict)
    detalles_ns = [DictNS(d) for d in detalles]

    # 1) Firmar XML — si falla, es definitivo (R), no transitorio.
    try:
        xml_str, _totales = _generar_xml_firmado(comp_ns, emp_ns, detalles_ns)
    except Exception as exc:  # noqa: BLE001
        logger.exception("Error firmando XML comp=%s", comp_id)
        try:
            ComprobanteWriterDBF.marcar_enviado_rechazado(
                serie, correlativo, tipo, "FIRMA",
                f"Error firma: {exc}",
            )
        except Exception:
            logger.exception("No se pudo persistir estado R tras error firma")
        return {
            "success": False,
            "estado_nuevo": ESTADO_RECHAZADO,
            "mensaje": f"Error firmando XML: {exc}",
            "debe_reintentar": False,
            "stage": "firma",
        }

    storage = storage_dir()
    ruc = getattr(emp_ns, "ruc", "") or ""
    filename = _xml_filename(ruc, tipo, serie, correlativo)
    xml_path = storage / "xml" / f"{filename}.xml"
    try:
        _save_text(xml_path, xml_str)
    except Exception as exc:  # noqa: BLE001
        logger.warning("No se pudo persistir XML (no critico): %s", exc)
        xml_path = None  # type: ignore[assignment]

    # 2) Enviar a SUNAT — clasificar excepcion.
    try:
        codigo, descripcion, cdr_b64 = asyncio.run(
            _enviar_async(emp_ns, xml_str, filename)
        )
    except Exception as exc:  # noqa: BLE001
        if _es_transitorio(exc):
            logger.warning(
                "SUNAT transitorio comp=%s: %s — marcando estado T",
                comp_id, exc,
            )
            try:
                ComprobanteWriterDBF.marcar_timeout_sunat(
                    serie, correlativo, tipo, str(exc)[:90],
                )
            except Exception:
                logger.exception("No se pudo persistir estado T")
            return {
                "success": False,
                "estado_nuevo": ESTADO_TIMEOUT,
                "mensaje": f"Error transitorio SUNAT: {exc}",
                "debe_reintentar": True,
                "stage": "envio",
                "xml_path": str(xml_path) if xml_path else None,
            }
        logger.exception("SUNAT rechazo definitivo comp=%s", comp_id)
        try:
            ComprobanteWriterDBF.marcar_enviado_rechazado(
                serie, correlativo, tipo, "ENVIO", str(exc)[:80],
            )
        except Exception:
            logger.exception("No se pudo persistir estado R")
        return {
            "success": False,
            "estado_nuevo": ESTADO_RECHAZADO,
            "mensaje": f"Rechazo definitivo SUNAT: {exc}",
            "debe_reintentar": False,
            "stage": "envio",
            "xml_path": str(xml_path) if xml_path else None,
        }

    # 3) Procesar respuesta — clasificar codigo CDR.
    aceptado = (str(codigo) == "0") and bool(cdr_b64)
    cdr_path: Optional[Path] = None
    if cdr_b64:
        cdr_path = storage / "cdr" / f"R-{filename}.zip"
        try:
            _save_bytes(cdr_path, base64.b64decode(cdr_b64))
        except Exception as exc:
            logger.warning("No se pudo guardar CDR: %s", exc)
            cdr_path = None

    if aceptado:
        cdr_h = _cdr_hash(cdr_b64)
        # QR oficial SUNAT (cadena final | hash CDR)
        qr_dato = _construir_qr(comp_dict, emp_ns, cdr_h)
        try:
            ComprobanteWriterDBF.marcar_enviado_aceptado(
                serie, correlativo, tipo, cdr_h,
                descripcion or "Aceptado", qr_dato,
            )
        except Exception:
            logger.exception("No se pudo persistir estado A (continuo)")
        return {
            "success": True,
            "estado_nuevo": ESTADO_ACEPTADO,
            "mensaje": descripcion or "Aceptado",
            "cdr_codigo": codigo,
            "cdr_hash": cdr_h,
            "debe_reintentar": False,
            "xml_path": str(xml_path) if xml_path else None,
            "cdr_path": str(cdr_path) if cdr_path else None,
        }

    # CDR codigo != "0" — rechazo por contenido. INMEDIATAMENTE pasa a B
    # (politica DAEFY: SUNAT lo dio de baja de oficio).
    try:
        ComprobanteWriterDBF.marcar_enviado_rechazado(
            serie, correlativo, tipo,
            str(codigo or "?"), (descripcion or "Rechazado")[:80],
        )
    except Exception:
        logger.exception("No se pudo persistir estado R")
    # Y acto seguido lo marcamos como B (anulado de oficio).
    try:
        ComprobanteWriterDBF.marcar_baja_sunat(
            serie, correlativo, tipo, "",
            f"Baja oficio SUNAT: {descripcion or codigo}",
        )
    except Exception:
        logger.exception("No se pudo persistir estado B tras R")

    return {
        "success": False,
        "estado_nuevo": ESTADO_BAJA,
        "mensaje": descripcion or "Rechazado por contenido",
        "cdr_codigo": codigo,
        "debe_reintentar": False,
        "xml_path": str(xml_path) if xml_path else None,
        "cdr_path": str(cdr_path) if cdr_path else None,
    }


def _construir_qr(comp_dict: dict, emp_ns: Any, cdr_hash: str) -> str:
    """Construye la cadena QR oficial SUNAT (Res. 113-2024)."""
    serie = comp_dict.get("serie") or ""
    correlativo = comp_dict.get("correlativo") or 0
    fecha = comp_dict.get("fecha_emision")
    fecha_str = fecha.isoformat() if hasattr(fecha, "isoformat") else str(fecha or "")
    campos = [
        getattr(emp_ns, "ruc", "") or "",
        comp_dict.get("tipo_documento") or "",
        serie,
        f"{int(correlativo):08d}",
        f"{float(comp_dict.get('total_igv') or 0):.2f}",
        f"{float(comp_dict.get('total_venta') or 0):.2f}",
        fecha_str,
        comp_dict.get("cliente_tipo_doc") or "",
        comp_dict.get("cliente_numero_doc") or "",
    ]
    if cdr_hash:
        campos.append(cdr_hash)
    return "|".join(campos)


def listar_pendientes_timeout() -> list[dict]:
    """Devuelve la lista de comprobantes en estado T (a reintentar)."""
    items, _ = ComprobanteRepoDBF.listar(
        filtros={"estado": ESTADO_TIMEOUT}, limit=500, offset=0,
    )
    return items
