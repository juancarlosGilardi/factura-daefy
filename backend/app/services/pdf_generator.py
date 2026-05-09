"""Generacion de PDF representacion impresa con QR (Res. 113-2024 SUNAT).

Branding blaugrana en plantilla `factura_a4.html` (azul #004D98 + grana #A50044).
"""
import os
import io
import base64
import logging
import qrcode
from jinja2 import Environment, FileSystemLoader

from .pdf_helpers import registrar_filtros, resolver_logo


logger = logging.getLogger(__name__)


# Plantillas de PDF estan en services/templates/ (junto a este archivo)
TEMPLATES_DIR = os.path.join(os.path.dirname(__file__), "templates")
env = Environment(loader=FileSystemLoader(TEMPLATES_DIR))
registrar_filtros(env)


def _generar_qr_b64(comprobante, empresa) -> str:
    """Genera QR segun especificacion SUNAT (Res. 113-2024/SUNAT).

    Cadena oficial:
        RUC|TIPO_DOC|SERIE|NUMERO|IGV|TOTAL|FECHA_EMI|TIPO_DOC_CLI|NRO_DOC_CLI|HASH_XML|CDR_HASH

    El ultimo campo (CDR_HASH) se completa solo cuando SUNAT acepta el comprobante.
    """
    serie = ""
    correlativo = ""
    if comprobante.numero_completo and "-" in comprobante.numero_completo:
        parts = comprobante.numero_completo.split("-")
        serie = parts[0]
        correlativo = parts[-1]

    fecha = str(comprobante.fecha_emision) if comprobante.fecha_emision else ""
    cdr_hash = getattr(comprobante, "cdr_hash", None) or ""

    # Bug C9/C10/F12/H7: Resolución 113-2024 SUNAT.
    # Cadena QR: RUC | TipoDoc | Serie | Correlativo | TotalIGV | Total | FechaEmision |
    #            TipoDocCliente | NroDocCliente [| HashCDR]
    # NO incluir xml_hash. cdr_hash solo si existe (no campos vacíos al final).
    campos = [
        empresa.ruc or "",
        comprobante.tipo_documento or "",
        serie,
        correlativo,
        f"{(comprobante.total_igv or 0):.2f}",
        f"{(comprobante.total_venta or 0):.2f}",
        fecha,
        comprobante.cliente_tipo_doc or "",
        comprobante.cliente_numero_doc or "",
    ]
    # Validar campos críticos no vacíos para evitar QR inutilizable
    if not (campos[0] and campos[1] and campos[2] and campos[3] and campos[6]
            and campos[7] and campos[8]):
        logger.warning(
            "QR con campos vacios para comprobante %s: %s",
            getattr(comprobante, "id", "?"), campos,
        )
    if cdr_hash:
        campos.append(cdr_hash)
    cadena = "|".join(campos)

    qr = qrcode.QRCode(
        version=None,
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=4,
        border=2,
    )
    qr.add_data(cadena)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode()


_TIPO_NOMBRES = {
    "01": "FACTURA ELECTRONICA",
    "03": "BOLETA DE VENTA ELECTRONICA",
    "07": "NOTA DE CREDITO ELECTRONICA",
    "08": "NOTA DE DEBITO ELECTRONICA",
}


def generar_pdf_comprobante(comprobante, detalles, empresa, config,
                             formato: str = "A4") -> bytes:
    """Genera PDF de representacion impresa con QR (blaugrana).

    En Factura-mdb solo soportamos formato A4 (factura_a4.html). El parametro
    `formato` se mantiene por compatibilidad pero por ahora solo acepta "A4".
    """
    template = env.get_template("factura_a4.html")

    qr_b64 = _generar_qr_b64(comprobante, empresa)

    logo_path, logo_b64 = resolver_logo(empresa)
    logger.debug("PDF logo: path=%s b64_len=%d", logo_path, len(logo_b64))

    html = template.render(
        comprobante=comprobante,
        detalles=detalles,
        empresa=empresa,
        config=config,
        qr_b64=qr_b64,
        tipo_nombre=_TIPO_NOMBRES.get(
            comprobante.tipo_documento, "COMPROBANTE ELECTRONICO"
        ),
        logo_b64=logo_b64,
        logo_path=logo_path,
    )

    # Preferir WeasyPrint
    weasy_err: Exception | None = None
    try:
        from weasyprint import HTML
        return HTML(string=html).write_pdf()
    except (ImportError, OSError) as e:
        weasy_err = e
        logger.debug("WeasyPrint no disponible (%s), intentando xhtml2pdf", e)

    # Fallback xhtml2pdf (solo si genera PDF real)
    try:
        from xhtml2pdf import pisa
        result = io.BytesIO()
        pisa_status = pisa.CreatePDF(io.StringIO(html), dest=result)
        if not pisa_status.err:
            return result.getvalue()
        raise RuntimeError("xhtml2pdf reportó errores al generar el PDF")
    except ImportError:
        pass

    # NO devolvemos HTML como si fuera PDF (rompería visores).
    raise RuntimeError(
        f"No hay motor PDF disponible. WeasyPrint falló ({weasy_err}). "
        "Verifica instalación de Pango/Cairo o instala xhtml2pdf."
    )
