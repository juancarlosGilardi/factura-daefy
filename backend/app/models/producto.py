"""Maestro de productos / servicios."""
from sqlalchemy import Column, Integer, String, Boolean, DateTime, Float
from sqlalchemy.sql import func
from ..core.database import Base


class Producto(Base):
    __tablename__ = "productos"

    id = Column(Integer, primary_key=True, autoincrement=True)
    codigo = Column(String(40), nullable=False, unique=True)
    descripcion = Column(String(300), nullable=False)
    unidad_medida = Column(String(8), nullable=False, default="NIU")
    valor_unitario = Column(Float, nullable=False, default=0.0)
    moneda = Column(String(3), nullable=False, default="PEN")
    tipo_afectacion_igv = Column(String(2), nullable=False, default="10")  # cat 07
    incluye_igv = Column(Boolean, nullable=False, default=False)  # si valor_unitario ya tiene IGV
    activo = Column(Boolean, nullable=False, default=True)
    notas = Column(String(500), nullable=True)

    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())
