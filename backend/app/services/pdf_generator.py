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


_FORMATO_TEMPLATES = {
    "A4":     "factura_a4.html",
    "A5":     "factura_a5.html",
    "TICKET": "factura_ticket.html",
}


def generar_pdf_comprobante(comprobante, detalles, empresa, config,
                             formato: str = "A4") -> bytes:
    """Genera PDF de representacion impresa con QR.

    formato: "A4" (default, hoja completa), "A5" (media hoja), "TICKET"
    (impresora termica 80mm). Si no se reconoce, cae a A4.
    """
    fmt = (formato or "A4").upper()
    template_name = _FORMATO_TEMPLATES.get(fmt, "factura_a4.html")
    template = env.get_template(template_name)

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


# ─────────────────────────────────────────────────────────────────────────
# Cotización y Pedido — comparten template (cotizacion_gecope.html) que
# decide el título según `pedido.tipo_pedido` ("COTIZACION" o "PEDIDO").
# Se construyen desde un comprobante existente: misma data, otro layout.
# ─────────────────────────────────────────────────────────────────────────

def _html_to_pdf(html: str) -> bytes:
    """Convierte HTML a PDF usando xhtml2pdf (fallback a WeasyPrint)."""
    try:
        from weasyprint import HTML
        return HTML(string=html).write_pdf()
    except (ImportError, OSError):
        pass
    from xhtml2pdf import pisa
    result = io.BytesIO()
    status = pisa.CreatePDF(io.StringIO(html), dest=result)
    if status.err:
        raise RuntimeError("xhtml2pdf reportó errores al generar el PDF")
    return result.getvalue()


def _construir_pedido_dict(comprobante, detalles, tipo: str,
                            validez_dias: int = 15,
                            condicion_pago: str = "",
                            vendedor: str = "",
                            observaciones: str = "") -> tuple[dict, list[dict]]:
    """Construye un dict 'pedido' (compatible con cotizacion_gecope.html)
    a partir del comprobante existente.

    Args:
        comprobante: objeto/dict del comprobante origen.
        detalles: lista de objetos/dicts de items.
        tipo: 'COTIZACION' | 'PEDIDO'.
        validez_dias: solo aplica para cotización.
        condicion_pago: ej. 'Contado', 'Crédito 30 días'.
        vendedor: nombre/código del vendedor.
        observaciones: texto libre.
    """
    def _g(obj, attr, default=None):
        if isinstance(obj, dict):
            return obj.get(attr, default)
        return getattr(obj, attr, default)

    pedido = {
        "tipo_pedido": tipo,
        "numero_completo": _g(comprobante, "numero_completo", "") or "PROFORMA",
        "fecha_emision": _g(comprobante, "fecha_emision"),
        "fecha_vencimiento": _g(comprobante, "fecha_vencimiento"),
        "moneda": _g(comprobante, "moneda", "PEN"),
        "cliente_tipo_doc": _g(comprobante, "cliente_tipo_doc", ""),
        "cliente_numero_doc": _g(comprobante, "cliente_numero_doc", ""),
        "cliente_razon_social": _g(comprobante, "cliente_razon_social", ""),
        "cliente_direccion": _g(comprobante, "cliente_direccion", ""),
        "comprobante_id": _g(comprobante, "id"),
        "total_gravado": _g(comprobante, "total_gravado", 0),
        "total_exonerado": _g(comprobante, "total_exonerado", 0),
        "total_inafecto": _g(comprobante, "total_inafecto", 0),
        "total_igv": _g(comprobante, "total_igv", 0),
        "total_venta": _g(comprobante, "total_venta", 0),
        "descuento_global_pct": _g(comprobante, "descuento_global_pct", 0),
        "descuento_global_monto": _g(comprobante, "descuento_global_monto", 0),
        "validez_dias": validez_dias,
        "condicion_pago": condicion_pago or _g(comprobante, "forma_pago", ""),
        "vendedor": vendedor or _g(comprobante, "codigo_trabajador", ""),
        "observaciones": observaciones or _g(comprobante, "observaciones", ""),
        "estado": "Por confirmar" if tipo == "PEDIDO" else "Vigente",
    }

    # Items: mapear `valor_venta` → `total` (que es lo que el template usa)
    items_pdf = []
    for d in detalles:
        items_pdf.append({
            "codigo": _g(d, "codigo", ""),
            "descripcion": _g(d, "descripcion", ""),
            "unidad_medida": _g(d, "unidad_medida", "NIU"),
            "cantidad": float(_g(d, "cantidad", 0) or 0),
            "precio_unitario": float(_g(d, "precio_unitario", 0) or 0),
            "total": float(_g(d, "valor_venta", 0) or _g(d, "total", 0) or 0),
        })

    return pedido, items_pdf


def generar_pdf_cotizacion(comprobante, detalles, empresa,
                            validez_dias: int = 15,
                            condicion_pago: str = "",
                            vendedor: str = "",
                            observaciones: str = "") -> bytes:
    """Genera PDF de COTIZACIÓN a partir de un comprobante.

    Útil para enviarle al cliente una propuesta antes de emitir factura.
    NO es un documento SUNAT — es interno.
    """
    pedido, items_pdf = _construir_pedido_dict(
        comprobante, detalles, "COTIZACION",
        validez_dias=validez_dias, condicion_pago=condicion_pago,
        vendedor=vendedor, observaciones=observaciones,
    )
    logo_path, logo_b64 = resolver_logo(empresa)
    template = env.get_template("cotizacion_gecope.html")
    html = template.render(
        pedido=pedido,
        detalles=items_pdf,
        empresa=empresa,
        logo_b64=logo_b64,
        logo_path=logo_path,
    )
    return _html_to_pdf(html)


def generar_pdf_pedido(comprobante, detalles, empresa,
                        condicion_pago: str = "",
                        vendedor: str = "",
                        observaciones: str = "") -> bytes:
    """Genera PDF de NOTA DE PEDIDO a partir de un comprobante.

    Se usa cuando el cliente confirma una cotización: documenta el pedido
    en firme antes de emitir la factura. NO es un documento SUNAT.
    """
    pedido, items_pdf = _construir_pedido_dict(
        comprobante, detalles, "PEDIDO",
        condicion_pago=condicion_pago, vendedor=vendedor,
        observaciones=observaciones,
    )
    logo_path, logo_b64 = resolver_logo(empresa)
    template = env.get_template("pedido_gecope.html")
    html = template.render(
        pedido=pedido,
        detalles=items_pdf,
        empresa=empresa,
        logo_b64=logo_b64,
        logo_path=logo_path,
    )
    return _html_to_pdf(html)
