"""Cliente SOAP para envio de comprobantes a SUNAT.

Implementacion propia (sin zeep ni librerias comerciales).
Soporta WS-Security UsernameToken + sendBill, sendSummary, getStatus.
"""
import httpx
import base64
import zipfile
import io
import logging
from xml.sax.saxutils import escape as _xml_escape
from typing import Tuple, Optional
from ..core.config import settings


logger = logging.getLogger(__name__)


# Timeout SUNAT: connect rapido, read amplio (SUNAT a veces tarda)
_SUNAT_TIMEOUT = httpx.Timeout(connect=10.0, read=60.0, write=10.0, pool=5.0)


class SUNATClient:
    """Cliente SOAP para el servicio de facturacion electronica SUNAT."""

    def __init__(self, ruc: str, sol_user: str, sol_pass: str,
                 ambiente: Optional[str] = None):
        self.ruc = ruc
        self.sol_user = sol_user
        self.sol_pass = sol_pass
        env = (ambiente or "beta").lower()
        self.ambiente = env
        # Acepta tanto "production" (ingles) como "produccion" (espanol)
        is_prod = env in ("production", "produccion", "prod")
        self.url = settings.SUNAT_PROD_URL if is_prod else settings.SUNAT_BETA_URL

    async def enviar_comprobante(self, xml_firmado: str,
                                  nombre_archivo: str) -> Tuple[str, str, str]:
        """
        Envia comprobante individual (factura, NC, ND).
        Retorna: (codigo_respuesta, descripcion, cdr_bytes_b64)
        """
        zip_buffer = io.BytesIO()
        with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr(f"{nombre_archivo}.xml", xml_firmado)
        zip_bytes = base64.b64encode(zip_buffer.getvalue()).decode()

        soap = self._build_soap_envelope("sendBill", nombre_archivo + ".zip", zip_bytes)
        logger.info("SUNAT sendBill -> %s (%s, %s)", nombre_archivo, self.ambiente, self.url)

        async with httpx.AsyncClient(timeout=_SUNAT_TIMEOUT) as client:
            response = await client.post(
                self.url,
                content=soap,
                headers={"Content-Type": "text/xml; charset=utf-8", "SOAPAction": ""},
            )

        return self._parse_response(response.content)

    async def enviar_resumen(self, xml_firmado: str, nombre_archivo: str) -> str:
        """
        Envia resumen diario o comunicacion de baja.
        Retorna: ticket_number
        """
        zip_buffer = io.BytesIO()
        with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr(f"{nombre_archivo}.xml", xml_firmado)
        zip_bytes = base64.b64encode(zip_buffer.getvalue()).decode()

        soap = self._build_soap_envelope("sendSummary", nombre_archivo + ".zip", zip_bytes)
        logger.info("SUNAT sendSummary -> %s", nombre_archivo)

        async with httpx.AsyncClient(timeout=_SUNAT_TIMEOUT) as client:
            response = await client.post(
                self.url, content=soap,
                headers={"Content-Type": "text/xml; charset=utf-8", "SOAPAction": ""},
            )

        return self._extract_ticket(response.content)

    async def consultar_ticket(self, ticket: str) -> Tuple[str, str, str]:
        """Consulta estado de un ticket (resumen/baja). Retorna (cod, desc, cdr_b64)."""
        soap = self._build_status_soap(ticket)
        logger.info("SUNAT getStatus -> ticket=%s", ticket)
        async with httpx.AsyncClient(timeout=_SUNAT_TIMEOUT) as client:
            response = await client.post(
                self.url, content=soap,
                headers={"Content-Type": "text/xml; charset=utf-8", "SOAPAction": ""},
            )
        return self._parse_response(response.content)

    def _build_soap_envelope(self, action: str, filename: str, content_b64: str) -> str:
        # Escapar para evitar SOAP injection si sol_pass/usuario/etc contienen & < > " '
        ruc = _xml_escape(self.ruc)
        sol_user = _xml_escape(self.sol_user)
        sol_pass = _xml_escape(self.sol_pass)
        fname = _xml_escape(filename)
        # action es controlado por el codigo (literal "sendBill"/"sendSummary"), pero por defensa:
        act = _xml_escape(action)
        # content_b64 es base64 (alfabeto ASCII seguro), pero escapar por defensa no rompe nada
        content_safe = _xml_escape(content_b64)
        return f"""<?xml version="1.0" encoding="UTF-8"?>
<soapenv:Envelope xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/"
                  xmlns:ser="http://service.sunat.gob.pe">
  <soapenv:Header>
    <wsse:Security xmlns:wsse="http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-secext-1.0.xsd">
      <wsse:UsernameToken>
        <wsse:Username>{ruc}{sol_user}</wsse:Username>
        <wsse:Password>{sol_pass}</wsse:Password>
      </wsse:UsernameToken>
    </wsse:Security>
  </soapenv:Header>
  <soapenv:Body>
    <ser:{act}>
      <fileName>{fname}</fileName>
      <contentFile>{content_safe}</contentFile>
    </ser:{act}>
  </soapenv:Body>
</soapenv:Envelope>"""

    def _build_status_soap(self, ticket: str) -> str:
        ruc = _xml_escape(self.ruc)
        sol_user = _xml_escape(self.sol_user)
        sol_pass = _xml_escape(self.sol_pass)
        tkt = _xml_escape(ticket)
        return f"""<?xml version="1.0" encoding="UTF-8"?>
<soapenv:Envelope xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/"
                  xmlns:ser="http://service.sunat.gob.pe">
  <soapenv:Header>
    <wsse:Security xmlns:wsse="http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-secext-1.0.xsd">
      <wsse:UsernameToken>
        <wsse:Username>{ruc}{sol_user}</wsse:Username>
        <wsse:Password>{sol_pass}</wsse:Password>
      </wsse:UsernameToken>
    </wsse:Security>
  </soapenv:Header>
  <soapenv:Body>
    <ser:getStatus><ticket>{tkt}</ticket></ser:getStatus>
  </soapenv:Body>
</soapenv:Envelope>"""

    def _parse_response(self, xml_response) -> Tuple[str, str, str]:
        """Parsea la respuesta SOAP de SUNAT. Retorna (codigo, descripcion, cdr_b64)."""
        try:
            from lxml import etree
            if isinstance(xml_response, str):
                xml_bytes = xml_response.encode('utf-8')
            else:
                xml_bytes = xml_response
            xml_bytes = xml_bytes.lstrip(b'\xef\xbb\xbf').strip()
            if not xml_bytes:
                return ("-1", "Respuesta SUNAT vacia", "")
            root = etree.fromstring(xml_bytes)

            # Buscar CDR — usar iter() para evitar problemas de namespace
            cdr_b64 = ""
            for el in root.iter():
                tag = el.tag if isinstance(el.tag, str) else ""
                if tag.endswith("applicationResponse") or tag == "applicationResponse":
                    if el.text and len(el.text.strip()) > 10:
                        cdr_b64 = el.text.strip()
                        break

            if cdr_b64:
                cdr_zip = base64.b64decode(cdr_b64)
                with zipfile.ZipFile(io.BytesIO(cdr_zip)) as zf:
                    xml_files = [f for f in zf.namelist() if f.endswith('.xml')]
                    cdr_xml = zf.read(xml_files[0]) if xml_files else b""
                if not cdr_xml.strip():
                    return ("0", "Aceptado (CDR vacio)", cdr_b64)
                cdr_root = etree.fromstring(cdr_xml)
                code = cdr_root.find(".//{urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2}ResponseCode")
                desc = cdr_root.find(".//{urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2}Description")
                return (
                    code.text if code is not None else "0",
                    desc.text if desc is not None else "Aceptado",
                    cdr_b64,
                )

            # Sin CDR — verificar si hay faultstring (con y sin namespace)
            for fault_xpath in [
                ".//{http://schemas.xmlsoap.org/soap/envelope/}faultstring",
                ".//faultstring",
            ]:
                fault = root.find(fault_xpath)
                if fault is not None and fault.text:
                    return ("-1", fault.text, "")

            # Sin CDR: NO devolver "0" porque downstream lo trata como aceptado
            return ("-1", "Respuesta SUNAT sin CDR valido", "")
        except Exception as e:
            xml_preview = None
            try:
                if isinstance(xml_response, (str, bytes)):
                    xml_preview = xml_response[:500] if isinstance(xml_response, str) else xml_response[:500].decode("utf-8", errors="replace")
            except Exception:
                xml_preview = None
            logger.exception(
                "Error parseando respuesta SUNAT (xml_response[:500]=%r)",
                xml_preview,
            )
            return ("-1", f"Error parseando respuesta: {str(e)}", "")

    def _extract_ticket(self, xml_response) -> str:
        try:
            from lxml import etree
            if isinstance(xml_response, str):
                xml_bytes = xml_response.encode('utf-8')
            else:
                xml_bytes = xml_response
            xml_bytes = xml_bytes.lstrip(b'\xef\xbb\xbf').strip()
            if not xml_bytes:
                return ""
            root = etree.fromstring(xml_bytes)
            for el in root.iter():
                tag = el.tag if isinstance(el.tag, str) else ""
                if tag.endswith("ticket") or tag == "ticket":
                    if el.text and el.text.strip():
                        return el.text.strip()
            for el in root.iter():
                if "faultstring" in str(el.tag).lower() and el.text:
                    logger.warning("sendSummary fault: %s", el.text[:200])
                    return ""
            return ""
        except Exception as e:
            logger.warning("Error extrayendo ticket: %s", e)
            return ""
