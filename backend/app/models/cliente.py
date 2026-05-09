"""Maestro de clientes."""
from sqlalchemy import Column, Integer, String, Boolean, DateTime, UniqueConstraint
from sqlalchemy.sql import func
from ..core.database import Base


class Cliente(Base):
    __tablename__ = "clientes"
    __table_args__ = (
        UniqueConstraint("tipo_documento", "numero_documento", name="uq_cliente_doc"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    tipo_documento = Column(String(2), nullable=False, default="6")  # SUNAT cat 06
    numero_documento = Column(String(20), nullable=False)
    razon_social = Column(String(200), nullable=False)
    direccion = Column(String(300), nullable=True)
    ubigeo = Column(String(6), nullable=True)
    distrito = Column(String(80), nullable=True)
    provincia = Column(String(80), nullable=True)
    departamento = Column(String(80), nullable=True)
    email = Column(String(120), nullable=True)
    telefono = Column(String(40), nullable=True)
    activo = Column(Boolean, nullable=False, default=True)

    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())
