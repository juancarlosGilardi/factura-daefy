"""Resumen Diario (RC) y Comunicación de Baja (RA)."""
from sqlalchemy import (Column, Integer, String, DateTime, Float, Date, Text,
                        ForeignKey, UniqueConstraint)
from sqlalchemy.sql import func
from ..core.database import Base


class ResumenDiario(Base):
    __tablename__ = "resumenes_diarios"
    __table_args__ = (
        UniqueConstraint("fecha_referencia", "correlativo", name="uq_resumen"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    fecha_referencia = Column(Date, nullable=False)  # Fecha de los comprobantes incluidos
    fecha_comunicacion = Column(Date, nullable=False)  # Fecha de envío
    correlativo = Column(Integer, nullable=False)
    nombre_archivo = Column(String(120), nullable=False)  # RUC-RC-YYYYMMDD-NNN
    tipo_resumen = Column(String(2), nullable=False, default="RC")

    estado = Column(String(20), nullable=False, default="P")  # P pendiente, A aceptado, R rechazado
    ticket = Column(String(50), nullable=True)
    cdr_codigo = Column(String(10), nullable=True)
    cdr_descripcion = Column(String(500), nullable=True)
    xml_path = Column(String(500), nullable=True)
    cdr_path = Column(String(500), nullable=True)

    total_documentos = Column(Integer, nullable=False, default=0)
    total_gravado = Column(Float, nullable=False, default=0.0)
    total_igv = Column(Float, nullable=False, default=0.0)
    total = Column(Float, nullable=False, default=0.0)

    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())


class ResumenDiarioItem(Base):
    """Cada comprobante incluido en un resumen diario."""
    __tablename__ = "resumen_diario_items"

    id = Column(Integer, primary_key=True, autoincrement=True)
    resumen_id = Column(Integer, ForeignKey("resumenes_diarios.id", ondelete="CASCADE"),
                       nullable=False, index=True)
    comprobante_id = Column(Integer, ForeignKey("comprobantes.id"), nullable=False)
    condicion = Column(String(2), nullable=False, default="1")  # 1=alta, 3=baja


class ComunicacionBaja(Base):
    __tablename__ = "comunicaciones_baja"
    __table_args__ = (
        UniqueConstraint("fecha_documentos", "correlativo", name="uq_baja"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    fecha_documentos = Column(Date, nullable=False)
    fecha_comunicacion = Column(Date, nullable=False)
    correlativo = Column(Integer, nullable=False)
    nombre_archivo = Column(String(120), nullable=False)  # RUC-RA-YYYYMMDD-NNN

    estado = Column(String(20), nullable=False, default="P")
    ticket = Column(String(50), nullable=True)
    cdr_codigo = Column(String(10), nullable=True)
    cdr_descripcion = Column(String(500), nullable=True)
    xml_path = Column(String(500), nullable=True)
    cdr_path = Column(String(500), nullable=True)

    motivo = Column(String(300), nullable=False, default="ANULACION POR ERROR EN LA EMISION")

    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())


class ComunicacionBajaItem(Base):
    __tablename__ = "comunicacion_baja_items"

    id = Column(Integer, primary_key=True, autoincrement=True)
    baja_id = Column(Integer, ForeignKey("comunicaciones_baja.id", ondelete="CASCADE"),
                    nullable=False, index=True)
    comprobante_id = Column(Integer, ForeignKey("comprobantes.id"), nullable=False)
    motivo = Column(String(300), nullable=True)
