"""Cálculos de totales de comprobante.

Vive en el módulo API porque es lógica de validación previa al envío SUNAT;
el Agente A puede recalcular si lo necesita en su servicio. Mantiene los
totales coherentes para mostrar en UI antes de aceptar la firma.
"""
from typing import List
from ..models.comprobante import Comprobante, ComprobanteDetalle
from ..schemas.comprobante import ComprobanteIn, ComprobanteDetalleIn


# Tipos de afectación IGV (cat 07)
GRAVADAS = {"10", "11", "12", "13", "14", "15", "16", "17"}
EXONERADAS = {"20", "21"}
INAFECTAS = {"30", "31", "32", "33", "34", "35", "36"}
EXPORTACION = {"40"}
GRATUITAS = {"11", "12", "13", "14", "15", "16", "17", "21", "31", "32", "33", "34", "35", "36"}


def _round(v: float, n: int = 2) -> float:
    return round(float(v) + 1e-9, n)


def calcular_linea(item: ComprobanteDetalleIn, igv_rate_default: float) -> dict:
    """Calcula los montos derivados de un ítem.

    Devuelve un dict con los campos necesarios para crear ComprobanteDetalle.
    """
    cantidad = float(item.cantidad)
    valor_unit = float(item.valor_unitario)
    # Bug D1: usar `or` colapsa el valor 0 al default. Si el ítem trae igv_pct=0
    # explícito (ej. exonerado/inafecto), debemos respetarlo.
    igv_pct = float(item.igv_pct if item.igv_pct is not None else igv_rate_default)
    isc_pct = float(item.isc_pct if item.isc_pct is not None else 0.0)
    desc_pct = float(item.descuento_pct if item.descuento_pct is not None else 0.0)
    desc_monto = float(item.descuento_monto if item.descuento_monto is not None else 0.0)

    bruto = cantidad * valor_unit
    if desc_pct > 0 and desc_monto == 0.0:
        desc_monto = bruto * (desc_pct / 100.0)
    valor_venta = max(0.0, bruto - desc_monto)

    afect = item.tipo_afectacion_igv or "10"
    if afect in GRAVADAS:
        igv_monto = valor_venta * (igv_pct / 100.0)
    else:
        igv_monto = 0.0
    isc_monto = valor_venta * (isc_pct / 100.0) if isc_pct else 0.0

    precio_unit = item.precio_unitario
    if precio_unit is None:
        if afect in GRAVADAS and cantidad:
            precio_unit = (valor_venta + igv_monto) / cantidad
        else:
            precio_unit = valor_unit

    total_linea = _round(valor_venta + igv_monto + isc_monto)

    return dict(
        orden=item.orden,
        producto_id=item.producto_id,
        codigo=item.codigo,
        descripcion=item.descripcion,
        unidad_medida=item.unidad_medida,
        cantidad=_round(cantidad, 4),
        valor_unitario=_round(valor_unit, 6),
        precio_unitario=_round(float(precio_unit), 6),
        descuento_pct=_round(desc_pct, 2),
        descuento_monto=_round(desc_monto, 2),
        valor_venta=_round(valor_venta, 2),
        igv_pct=_round(igv_pct, 2),
        igv_monto=_round(igv_monto, 2),
        tipo_afectacion_igv=afect,
        isc_pct=_round(isc_pct, 2),
        isc_monto=_round(isc_monto, 2),
        icbper_monto=0.0,
        total_linea=total_linea,
    )


def calcular_totales(payload: ComprobanteIn, igv_rate_default: float) -> dict:
    """Devuelve totales y la lista de líneas calculadas."""
    lineas: List[dict] = [calcular_linea(it, igv_rate_default) for it in payload.items]

    total_gravado = 0.0
    total_exonerado = 0.0
    total_inafecto = 0.0
    total_exportacion = 0.0
    total_gratuito = 0.0
    total_descuento = 0.0
    total_igv = 0.0
    total_isc = 0.0
    total_icbper = 0.0

    for d in lineas:
        afect = d["tipo_afectacion_igv"]
        vv = d["valor_venta"]
        es_gratuito = afect in GRATUITAS and afect not in {"10"}
        if afect in GRAVADAS:
            total_gravado += vv
        elif afect in EXONERADAS:
            total_exonerado += vv
        elif afect in INAFECTAS:
            total_inafecto += vv
        elif afect in EXPORTACION:
            total_exportacion += vv
        if es_gratuito:
            total_gratuito += vv
        total_descuento += d["descuento_monto"]
        total_igv += d["igv_monto"]
        total_isc += d["isc_monto"]
        total_icbper += d["icbper_monto"]

    subtotal = total_gravado + total_exonerado + total_inafecto + total_exportacion
    total_venta = subtotal + total_igv + total_isc + total_icbper
    total_pen = total_venta * (payload.tipo_cambio or 1.0)

    return dict(
        lineas=lineas,
        total_gravado=_round(total_gravado),
        total_exonerado=_round(total_exonerado),
        total_inafecto=_round(total_inafecto),
        total_exportacion=_round(total_exportacion),
        total_gratuito=_round(total_gratuito),
        total_descuento=_round(total_descuento),
        subtotal=_round(subtotal),
        total_igv=_round(total_igv),
        total_isc=_round(total_isc),
        total_icbper=_round(total_icbper),
        total_venta=_round(total_venta),
        total_pen=_round(total_pen),
    )
