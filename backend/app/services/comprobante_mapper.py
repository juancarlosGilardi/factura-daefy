"""
Mapper: convierte modelos SQLAlchemy Factura-mdb (DB) a modelos Pydantic (XML).
Puente entre Comprobante/ComprobanteDetalle y los generadores XML UBL 2.1.
"""
from decimal import Decimal
from typing import List, Optional

from .xml_models import (
    FacturaRequest, NotaCreditoRequest, NotaDebitoRequest,
    ResumenDiarioRequest, ComunicacionBajaRequest,
    Cliente, Item, FormaPago, Cuota, Detraccion,
    TipoCambio, AllowanceCharge, DocumentoReferencia,
    DocumentoResumen, DocumentoBaja,
)
from .xml_models.common import Percepcion


def _dec(value) -> Decimal:
    """Convierte float/int/None a Decimal de forma segura."""
    if value is None:
        return Decimal("0")
    return Decimal(str(value))


def _str_date(date_obj) -> str:
    """Convierte date/datetime a string YYYY-MM-DD."""
    if date_obj is None:
        return ""
    if isinstance(date_obj, str):
        return date_obj
    return date_obj.strftime("%Y-%m-%d")


def _build_cliente(comp) -> Cliente:
    return Cliente(
        tipo_doc=comp.cliente_tipo_doc or "6",
        numero=comp.cliente_numero_doc or "",
        razon_social=comp.cliente_razon_social or "",
        direccion=comp.cliente_direccion,
    )


def _build_forma_pago(comp) -> FormaPago:
    fp_json = getattr(comp, "forma_pago_json", None)
    if not fp_json:
        return FormaPago(tipo="Contado")

    tipo = fp_json.get("tipo", "Contado")
    if tipo == "Credito":
        cuotas_raw = fp_json.get("cuotas", []) or []
        cuotas = [
            Cuota(
                numero=c.get("numero", i + 1),
                monto=_dec(c.get("monto", 0)),
                fecha_vencimiento=c.get("fecha_vencimiento", ""),
            )
            for i, c in enumerate(cuotas_raw)
        ]
        return FormaPago(
            tipo="Credito",
            monto_pendiente=_dec(fp_json.get("monto_pendiente", 0)),
            cuotas=cuotas,
        )
    return FormaPago(tipo="Contado")


def _build_detraccion(comp) -> Optional[Detraccion]:
    """Construye el Detraccion si el comprobante la tiene configurada.

    La cuenta del Banco de la Nacion se acepta en dos formatos:
      - con guiones: XX-XXX-XXXXXXX (14 chars)
      - solo digitos: XXXXXXXXXXXX (12 digitos) — se hace pad a 14
    Se normaliza a 14 digitos para el XML.
    """
    if not comp.detraccion_codigo or not comp.detraccion_tasa:
        return None
    cta = (comp.detraccion_cta_bn or "").strip()
    digitos = "".join(ch for ch in cta if ch.isdigit())
    if len(digitos) == 14:
        cta_digitos = digitos
    elif len(digitos) == 12:
        cta_digitos = digitos.zfill(14)
    else:
        # Formato invalido — no generar detraccion XML
        return None
    return Detraccion(
        codigo_bien_servicio=comp.detraccion_codigo,
        porcentaje=_dec(comp.detraccion_tasa),
        cuenta_banco_nacion=cta_digitos,
    )


def _build_percepcion(comp) -> Optional[Percepcion]:
    """Construye la Percepcion (cat 53) si el comprobante la tiene configurada.

    Factura-mdb almacena solo percepcion_pct y percepcion_monto a nivel cabecera.
    Asume regimen '01' (venta interna) por defecto. La base se deriva del
    total de venta (que en SUNAT NO incluye percepcion), y monto_total es
    base + percepcion.
    """
    pct = getattr(comp, "percepcion_pct", None)
    monto = getattr(comp, "percepcion_monto", None)
    if not pct or not monto or float(pct) <= 0 or float(monto) <= 0:
        return None
    base = _dec(comp.total_venta or 0)
    monto_perc = _dec(monto)
    if base <= 0:
        return None
    return Percepcion(
        codigo_regimen="01",
        porcentaje=_dec(pct),
        monto_base=base,
        monto_percepcion=monto_perc,
        monto_total=base + monto_perc,
    )


def _build_tipo_cambio(comp) -> Optional[TipoCambio]:
    if comp.moneda == "PEN" or not comp.tipo_cambio or comp.tipo_cambio <= 0:
        return None
    return TipoCambio(
        tasa=_dec(comp.tipo_cambio),
        fecha=_str_date(comp.fecha_emision),
    )


def _build_descuento_global(comp) -> Optional[List[AllowanceCharge]]:
    """Descuento global a nivel comprobante.

    Modelo Factura-mdb no tiene descuento_global_pct/monto a nivel de comprobante
    (solo `total_descuento`). Si en el futuro se agrega el campo, se mapea aqui.
    Por ahora, no se generan descuentos globales en el XML.
    """
    return None


def _build_items(detalles) -> List[Item]:
    items = []
    for det in detalles:
        icbper_monto = None
        cantidad = _dec(det.cantidad)
        icbper_total = _dec(getattr(det, "icbper_monto", 0))
        if icbper_total > 0 and cantidad > 0:
            icbper_monto = icbper_total / cantidad

        isc_tasa = None
        isc_pct = _dec(getattr(det, "isc_pct", 0))
        if isc_pct > 0:
            isc_tasa = isc_pct

        descuento = None
        descuento_monto = _dec(getattr(det, "descuento_monto", 0))
        if descuento_monto > 0:
            descuento_pct = _dec(getattr(det, "descuento_pct", 0))
            base_neto = _dec(det.valor_venta) + descuento_monto
            descuento = AllowanceCharge(
                charge_indicator=False,
                codigo_motivo="00",
                porcentaje=descuento_pct if descuento_pct > 0 else None,
                monto=descuento_monto,
                monto_base=base_neto if base_neto > 0 else _dec(det.valor_venta),
            )

        items.append(Item(
            codigo=det.codigo or str(det.orden),
            descripcion=det.descripcion,
            cantidad=cantidad,
            unidad=det.unidad_medida or "NIU",
            valor_unitario=_dec(det.valor_unitario),
            afectacion_igv=det.tipo_afectacion_igv or "10",
            descuento=descuento,
            icbper_monto=icbper_monto,
            isc_tasa=isc_tasa,
        ))
    return items


def _padding(comp) -> int:
    if comp.numero_completo and "-" in comp.numero_completo:
        return len(comp.numero_completo.split("-")[1])
    return 8


def comprobante_to_factura_request(comp, empresa, detalles) -> FacturaRequest:
    """Convierte Comprobante (tipo 01/03) + Empresa + Detalles a FacturaRequest."""
    return FacturaRequest(
        ruc_emisor=empresa.ruc,
        razon_social_emisor=empresa.razon_social,
        ubigeo_emisor=empresa.ubigeo or "150101",
        direccion_emisor=empresa.direccion or "",
        departamento_emisor=empresa.departamento,
        provincia_emisor=empresa.provincia,
        distrito_emisor=empresa.distrito,
        tipo_documento=comp.tipo_documento,
        serie=comp.serie,
        numero=comp.correlativo,
        numero_padding=_padding(comp),
        fecha_emision=_str_date(comp.fecha_emision),
        hora_emision=comp.hora_emision or "00:00:00",
        fecha_vencimiento=_str_date(comp.fecha_vencimiento) if comp.fecha_vencimiento else None,
        moneda=comp.moneda or "PEN",
        tipo_operacion=comp.tipo_operacion if comp.tipo_operacion and comp.tipo_operacion != "0101" else None,
        cliente=_build_cliente(comp),
        forma_pago=_build_forma_pago(comp),
        detraccion=_build_detraccion(comp),
        percepcion=_build_percepcion(comp),
        tipo_cambio=_build_tipo_cambio(comp),
        descuentos_globales=_build_descuento_global(comp),
        items=_build_items(detalles),
    )


_MOTIVO_NC = {
    "01": "Anulacion de la operacion",
    "02": "Anulacion por error en el RUC",
    "03": "Correccion por error en la descripcion",
    "04": "Descuento global",
    "05": "Descuento por item",
    "06": "Devolucion total",
    "07": "Devolucion por item",
    "08": "Bonificacion",
    "09": "Disminucion en el valor",
    "10": "Otros conceptos",
    "11": "Ajustes de operaciones de exportacion",
    "12": "Ajustes afectos al IVAP",
    "13": "Ajustes - Loss",
}

_MOTIVO_ND = {
    "01": "Intereses por mora",
    "02": "Aumento en el valor",
    "03": "Penalidades/ otros conceptos",
    "10": "Ajustes de operaciones de exportacion",
    "11": "Ajustes afectos al IVAP",
}


def comprobante_to_nota_credito_request(comp, empresa, detalles) -> NotaCreditoRequest:
    """Convierte Comprobante (tipo 07) a NotaCreditoRequest."""
    codigo_motivo = comp.doc_referencia_motivo or comp.motivo_nc_codigo or "01"
    descripcion_motivo = (
        comp.motivo_nc_descripcion
        or _MOTIVO_NC.get(codigo_motivo, "Otros conceptos")
    )
    return NotaCreditoRequest(
        ruc_emisor=empresa.ruc,
        razon_social_emisor=empresa.razon_social,
        ubigeo_emisor=empresa.ubigeo or "150101",
        direccion_emisor=empresa.direccion or "",
        tipo_documento="07",
        serie=comp.serie,
        numero=comp.correlativo,
        numero_padding=_padding(comp),
        fecha_emision=_str_date(comp.fecha_emision),
        hora_emision=comp.hora_emision or "00:00:00",
        moneda=comp.moneda or "PEN",
        cliente=_build_cliente(comp),
        forma_pago=_build_forma_pago(comp),
        documento_referencia=DocumentoReferencia(
            id=comp.doc_referencia_serie or "",
            tipo_doc=comp.doc_referencia_tipo or "01",
            codigo_motivo=codigo_motivo,
            descripcion_motivo=descripcion_motivo,
        ),
        tipo_cambio=_build_tipo_cambio(comp),
        descuentos_globales=_build_descuento_global(comp),
        items=_build_items(detalles),
    )


def comprobante_to_nota_debito_request(comp, empresa, detalles) -> NotaDebitoRequest:
    """Convierte Comprobante (tipo 08) a NotaDebitoRequest."""
    codigo_motivo = comp.doc_referencia_motivo or "01"
    return NotaDebitoRequest(
        ruc_emisor=empresa.ruc,
        razon_social_emisor=empresa.razon_social,
        ubigeo_emisor=empresa.ubigeo or "150101",
        direccion_emisor=empresa.direccion or "",
        tipo_documento="08",
        serie=comp.serie,
        numero=comp.correlativo,
        numero_padding=_padding(comp),
        fecha_emision=_str_date(comp.fecha_emision),
        hora_emision=comp.hora_emision or "00:00:00",
        moneda=comp.moneda or "PEN",
        cliente=_build_cliente(comp),
        forma_pago=_build_forma_pago(comp),
        documento_referencia=DocumentoReferencia(
            id=comp.doc_referencia_serie or "",
            tipo_doc=comp.doc_referencia_tipo or "01",
            codigo_motivo=codigo_motivo,
            descripcion_motivo=_MOTIVO_ND.get(codigo_motivo, "Otros conceptos"),
        ),
        tipo_cambio=_build_tipo_cambio(comp),
        descuentos_globales=_build_descuento_global(comp),
        items=_build_items(detalles),
    )


def comprobantes_to_resumen_request(
    empresa,
    correlativo: str,
    fecha_documentos: str,
    fecha_comunicacion: str,
    comprobantes: list,
) -> ResumenDiarioRequest:
    """Convierte lista de Comprobantes a ResumenDiarioRequest."""
    documentos = []
    for comp in comprobantes:
        doc_ref = ""
        tipo_doc_ref = "03"
        if comp.tipo_documento in ("07", "08"):
            doc_ref = comp.doc_referencia_serie or ""
            tipo_doc_ref = comp.doc_referencia_tipo or "03"

        documentos.append(DocumentoResumen(
            tipo_doc=comp.tipo_documento,
            serie_numero=comp.numero_completo,
            tipo_doc_cliente=comp.cliente_tipo_doc or "0",
            num_doc_cliente=comp.cliente_numero_doc or "00000000",
            condicion="1",
            moneda=comp.moneda or "PEN",
            total=float(comp.total_venta or 0),
            doc_referencia=doc_ref,
            tipo_doc_referencia=tipo_doc_ref,
            gravada=float(comp.total_gravado or 0),
            exonerada=float(comp.total_exonerado or 0),
            inafecta=float(comp.total_inafecto or 0),
            exportacion=float(comp.total_exportacion or 0),
            gratuita=float(comp.total_gratuito or 0),
            igv=float(comp.total_igv or 0),
            isc=float(comp.total_isc or 0),
        ))

    return ResumenDiarioRequest(
        ruc_emisor=empresa.ruc,
        razon_social_emisor=empresa.razon_social,
        correlativo=correlativo,
        fecha_documentos=fecha_documentos,
        fecha_comunicacion=fecha_comunicacion,
        documentos=documentos,
    )


def comunicacion_to_baja_request(
    empresa,
    correlativo: str,
    fecha_documentos: str,
    fecha_comunicacion: str,
    documentos_baja: list,
) -> ComunicacionBajaRequest:
    """Convierte lista de docs (Comprobante o dict) a ComunicacionBajaRequest."""
    docs = []
    for doc in documentos_baja:
        if isinstance(doc, dict):
            docs.append(DocumentoBaja(
                tipo_doc=doc.get("tipo_doc", "01"),
                serie=doc.get("serie", ""),
                correlativo=str(doc.get("correlativo", "")),
                motivo=doc.get("motivo", "Error en emision"),
            ))
        else:
            # Comprobante SQLAlchemy
            tipo_doc = getattr(doc, "tipo_documento", None) or getattr(doc, "tipo_doc", "01")
            serie = getattr(doc, "serie", "") or ""
            correlativo = getattr(doc, "correlativo", "") or ""
            motivo = getattr(doc, "motivo", None) or getattr(doc, "observaciones", None) or "Error en emision"
            docs.append(DocumentoBaja(
                tipo_doc=tipo_doc,
                serie=serie,
                correlativo=str(correlativo),
                motivo=motivo,
            ))

    return ComunicacionBajaRequest(
        ruc_emisor=empresa.ruc,
        razon_social_emisor=empresa.razon_social,
        correlativo=correlativo,
        fecha_documentos=fecha_documentos,
        fecha_comunicacion=fecha_comunicacion,
        documentos=docs,
    )
