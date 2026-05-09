"""Empresa y configuración Factura-mdb.

Modelo single-tenant: existe UNA fila en `empresas` y UNA en `configuracion`.
"""
from sqlalchemy import Column, Integer, String, Boolean, DateTime, Float
from sqlalchemy.sql import func
from ..core.database import Base


class Empresa(Base):
    __tablename__ = "empresas"

    id = Column(Integer, primary_key=True, autoincrement=True)
    ruc = Column(String(11), nullable=False, unique=True)
    razon_social = Column(String(200), nullable=False)
    nombre_comercial = Column(String(200), nullable=True)
    direccion = Column(String(300), nullable=True)
    ubigeo = Column(String(6), nullable=True)
    departamento = Column(String(80), nullable=True)
    provincia = Column(String(80), nullable=True)
    distrito = Column(String(80), nullable=True)
    telefono = Column(String(40), nullable=True)
    email = Column(String(120), nullable=True)
    sitio_web = Column(String(200), nullable=True)
    logo_path = Column(String(500), nullable=True)

    # Credenciales SUNAT
    sol_user = Column(String(60), nullable=True)
    sol_pass = Column(String(120), nullable=True)
    sunat_env = Column(String(12), nullable=False, default="beta")  # beta | produccion

    # Certificado digital
    certificado_path = Column(String(500), nullable=True)
    certificado_pass = Column(String(200), nullable=True)
    certificado_vence = Column(DateTime, nullable=True)

    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())


class Configuracion(Base):
    """Configuraciones globales de la app (formato PDF, IGV rate, opciones UX)."""
    __tablename__ = "configuracion"

    id = Column(Integer, primary_key=True, autoincrement=True)
    igv_rate = Column(Float, nullable=False, default=18.0)
    formato_impresion = Column(String(20), nullable=False, default="A4")
    cuenta_bcp = Column(String(200), nullable=True)
    cuenta_bcp_moneda = Column(String(3), nullable=True, default="PEN")
    cta_banco_nacion = Column(String(30), nullable=True)
    aplica_detraccion = Column(Boolean, nullable=False, default=False)
    detraccion_codigo = Column(String(5), nullable=True)
    detraccion_porcentaje = Column(Float, nullable=False, default=0.0)
    pie_pagina = Column(String(500), nullable=True)
    moneda_default = Column(String(3), nullable=False, default="PEN")
    auto_envio_sunat = Column(Boolean, nullable=False, default=True)
    backup_automatico = Column(Boolean, nullable=False, default=True)
    onboarding_completado = Column(Boolean, nullable=False, default=False)

    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())
