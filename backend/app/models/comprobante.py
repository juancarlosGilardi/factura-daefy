"""Comprobante electrónico — factura, boleta, NC, ND."""
from sqlalchemy import (Column, Integer, String, Boolean, DateTime, Float, Date, Text,
                        ForeignKey, UniqueConstraint, JSON)
from sqlalchemy.sql import func
from ..core.database import Base


class Comprobante(Base):
    __tablename__ = "comprobantes"
    __table_args__ = (
        UniqueConstraint("tipo_documento", "serie", "correlativo", name="uq_comp"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    tipo_documento = Column(String(2), nullable=False)  # 01, 03, 07, 08
    serie = Column(String(4), nullable=False)
    correlativo = Column(Integer, nullable=False)
    numero_completo = Column(String(13), nullable=False)  # F001-00000123

    fecha_emision = Column(Date, nullable=False)
    fecha_vencimiento = Column(Date, nullable=True)
    hora_emision = Column(String(8), nullable=True)
    moneda = Column(String(3), nullable=False, default="PEN")
    tipo_cambio = Column(Float, nullable=False, default=1.0)
    tipo_operacion = Column(String(4), nullable=False, default="0101")

    # Cliente snapshot (para no perder datos si el cliente se elimina)
    cliente_id = Column(Integer, ForeignKey("clientes.id", ondelete="SET NULL"), nullable=True)
    cliente_tipo_doc = Column(String(2), nullable=False)
    cliente_numero_doc = Column(String(20), nullable=False)
    cliente_razon_social = Column(String(200), nullable=False)
    cliente_direccion = Column(String(300), nullable=True)

    # Totales
    total_gravado = Column(Float, nullable=False, default=0.0)
    total_exonerado = Column(Float, nullable=False, default=0.0)
    total_inafecto = Column(Float, nullable=False, default=0.0)
    total_exportacion = Column(Float, nullable=False, default=0.0)
    total_gratuito = Column(Float, nullable=False, default=0.0)
    total_descuento = Column(Float, nullable=False, default=0.0)
    subtotal = Column(Float, nullable=False, default=0.0)
    total_igv = Column(Float, nullable=False, default=0.0)
    total_isc = Column(Float, nullable=False, default=0.0)
    total_icbper = Column(Float, nullable=False, default=0.0)
    total_venta = Column(Float, nullable=False, default=0.0)
    total_pen = Column(Float, nullable=False, default=0.0)
    monto_letras = Column(String(500), nullable=True)

    # Detracción / percepción
    detraccion_codigo = Column(String(5), nullable=True)
    detraccion_tasa = Column(Float, nullable=True)
    detraccion_monto = Column(Float, nullable=True)
    detraccion_cta_bn = Column(String(30), nullable=True)
    percepcion_pct = Column(Float, nullable=True)
    percepcion_monto = Column(Float, nullable=True)

    # Forma pago
    forma_pago = Column(String(20), nullable=False, default="Contado")
    forma_pago_json = Column(JSON, nullable=True)  # cuotas si crédito

    # Doc referencia (para NC/ND)
    doc_referencia_tipo = Column(String(2), nullable=True)
    doc_referencia_serie = Column(String(13), nullable=True)
    doc_referencia_motivo = Column(String(2), nullable=True)
    motivo_nc_codigo = Column(String(2), nullable=True)
    motivo_nc_descripcion = Column(String(300), nullable=True)
    comprobante_ref_id = Column(Integer, ForeignKey("comprobantes.id"), nullable=True)

    # Estado y SUNAT
    estado = Column(String(20), nullable=False, default="P")  # P pendiente, A aceptado, R rechazado, B baja
    cdr_codigo = Column(String(10), nullable=True)
    cdr_descripcion = Column(String(500), nullable=True)
    cdr_hash = Column(String(120), nullable=True)
    xml_path = Column(String(500), nullable=True)
    cdr_path = Column(String(500), nullable=True)
    pdf_path = Column(String(500), nullable=True)
    xml_hash = Column(String(64), nullable=True)
    qr_data = Column(Text, nullable=True)

    observaciones = Column(Text, nullable=True)

    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())


class ComprobanteDetalle(Base):
    __tablename__ = "comprobante_detalles"

    id = Column(Integer, primary_key=True, autoincrement=True)
    comprobante_id = Column(Integer, ForeignKey("comprobantes.id", ondelete="CASCADE"),
                            nullable=False, index=True)
    orden = Column(Integer, nullable=False, default=1)

    producto_id = Column(Integer, ForeignKey("productos.id", ondelete="SET NULL"), nullable=True)
    codigo = Column(String(40), nullable=True)
    descripcion = Column(String(500), nullable=False)
    unidad_medida = Column(String(8), nullable=False, default="NIU")

    cantidad = Column(Float, nullable=False, default=1.0)
    valor_unitario = Column(Float, nullable=False, default=0.0)
    precio_unitario = Column(Float, nullable=False, default=0.0)
    descuento_pct = Column(Float, nullable=False, default=0.0)
    descuento_monto = Column(Float, nullable=False, default=0.0)

    valor_venta = Column(Float, nullable=False, default=0.0)
    igv_pct = Column(Float, nullable=False, default=18.0)
    igv_monto = Column(Float, nullable=False, default=0.0)
    tipo_afectacion_igv = Column(String(2), nullable=False, default="10")

    isc_pct = Column(Float, nullable=False, default=0.0)
    isc_monto = Column(Float, nullable=False, default=0.0)
    icbper_monto = Column(Float, nullable=False, default=0.0)
    total_linea = Column(Float, nullable=False, default=0.0)


class EnvioSunat(Base):
    """Bitácora de envíos SUNAT (1 comprobante puede tener varios intentos)."""
    __tablename__ = "envios_sunat"

    id = Column(Integer, primary_key=True, autoincrement=True)
    comprobante_id = Column(Integer, ForeignKey("comprobantes.id", ondelete="CASCADE"), nullable=False, index=True)
    intento = Column(Integer, nullable=False, default=1)
    estado = Column(String(20), nullable=False, default="pendiente")
    codigo_respuesta = Column(String(10), nullable=True)
    descripcion_respuesta = Column(String(500), nullable=True)
    xml_nombre = Column(String(120), nullable=True)
    xml_path = Column(String(500), nullable=True)
    cdr_path = Column(String(500), nullable=True)
    error_detalle = Column(Text, nullable=True)
    enviado_at = Column(DateTime, nullable=True)
    recibido_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, server_default=func.now())
