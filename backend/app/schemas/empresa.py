"""Schemas para Empresa, Configuración y Onboarding."""
from datetime import datetime
from typing import Optional, Literal
from pydantic import BaseModel, ConfigDict, Field, field_validator, EmailStr

from .common import validar_ruc_modulo11


# ---------- Empresa ----------

class EmpresaBase(BaseModel):
    razon_social: str = Field(..., min_length=2, max_length=200)
    nombre_comercial: Optional[str] = Field(None, max_length=200)
    direccion: Optional[str] = Field(None, max_length=300)
    ubigeo: Optional[str] = Field(None, max_length=6, min_length=6)
    departamento: Optional[str] = Field(None, max_length=80)
    provincia: Optional[str] = Field(None, max_length=80)
    distrito: Optional[str] = Field(None, max_length=80)
    telefono: Optional[str] = Field(None, max_length=40)
    email: Optional[str] = Field(None, max_length=120)
    sitio_web: Optional[str] = Field(None, max_length=200)

    @field_validator("ubigeo")
    @classmethod
    def _ubigeo_digits(cls, v):
        if v is None:
            return v
        if not v.isdigit():
            raise ValueError("Ubigeo debe ser de 6 dígitos")
        return v


class EmpresaIn(EmpresaBase):
    """Datos de empresa al crear (incluye RUC)."""
    ruc: str = Field(..., min_length=11, max_length=11)
    sol_user: Optional[str] = Field(None, max_length=60)
    sol_pass: Optional[str] = Field(None, max_length=120)
    sunat_env: Literal["beta", "produccion"] = "beta"

    @field_validator("ruc")
    @classmethod
    def _ruc_check(cls, v):
        if not validar_ruc_modulo11(v):
            raise ValueError("RUC inválido (módulo 11)")
        if not v.startswith(("10", "15", "17", "20")):
            raise ValueError("RUC debe empezar con 10, 15, 17 o 20")
        return v


class EmpresaUpdate(EmpresaBase):
    """Update — no permite cambiar RUC."""
    sol_user: Optional[str] = Field(None, max_length=60)
    sol_pass: Optional[str] = Field(None, max_length=120)
    razon_social: Optional[str] = Field(None, min_length=2, max_length=200)


class EmpresaOut(EmpresaBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    ruc: str
    logo_path: Optional[str] = None
    sol_user: Optional[str] = None
    sunat_env: str = "beta"
    certificado_path: Optional[str] = None
    certificado_vence: Optional[datetime] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


# ---------- Configuración ----------

class ConfiguracionBase(BaseModel):
    # Bug D4: igv_rate no puede pasar de 30 (techo defensivo)
    igv_rate: float = Field(18.0, ge=0, le=30)
    formato_impresion: Literal["A4", "A5", "TICKET58", "TICKET80"] = "A4"
    cuenta_bcp: Optional[str] = Field(None, max_length=200)
    cuenta_bcp_moneda: Optional[Literal["PEN", "USD", "EUR"]] = "PEN"
    cta_banco_nacion: Optional[str] = Field(None, max_length=30)
    aplica_detraccion: bool = False
    detraccion_codigo: Optional[str] = Field(None, max_length=5)
    detraccion_porcentaje: float = Field(0.0, ge=0, le=100)
    pie_pagina: Optional[str] = Field(None, max_length=500)
    moneda_default: Literal["PEN", "USD", "EUR"] = "PEN"
    auto_envio_sunat: bool = True
    backup_automatico: bool = True


class ConfiguracionIn(ConfiguracionBase):
    onboarding_completado: bool = False


class ConfiguracionUpdate(BaseModel):
    # Bug D4: igv_rate no puede pasar de 30
    igv_rate: Optional[float] = Field(None, ge=0, le=30)
    formato_impresion: Optional[Literal["A4", "A5", "TICKET58", "TICKET80"]] = None
    cuenta_bcp: Optional[str] = Field(None, max_length=200)
    cuenta_bcp_moneda: Optional[Literal["PEN", "USD", "EUR"]] = None
    cta_banco_nacion: Optional[str] = Field(None, max_length=30)
    aplica_detraccion: Optional[bool] = None
    detraccion_codigo: Optional[str] = Field(None, max_length=5)
    detraccion_porcentaje: Optional[float] = Field(None, ge=0, le=100)
    pie_pagina: Optional[str] = Field(None, max_length=500)
    moneda_default: Optional[Literal["PEN", "USD", "EUR"]] = None
    auto_envio_sunat: Optional[bool] = None
    backup_automatico: Optional[bool] = None


class ConfiguracionOut(ConfiguracionBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    onboarding_completado: bool
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


# ---------- Combo ----------

class EmpresaConfigOut(BaseModel):
    """Empresa + Configuración en un solo response."""
    empresa: Optional[EmpresaOut] = None
    configuracion: Optional[ConfiguracionOut] = None


# ---------- Onboarding ----------

class OnboardingIn(BaseModel):
    """Datos enviados desde el wizard de bienvenida.

    El certificado .pfx se manda como multipart en otro field; este
    schema cubre los datos JSON. El form-data combina ambos.
    """
    ruc: str = Field(..., min_length=11, max_length=11)
    razon_social: str = Field(..., min_length=2, max_length=200)
    nombre_comercial: Optional[str] = Field(None, max_length=200)
    direccion: Optional[str] = Field(None, max_length=300)
    ubigeo: Optional[str] = Field(None, max_length=6, min_length=6)
    departamento: Optional[str] = Field(None, max_length=80)
    provincia: Optional[str] = Field(None, max_length=80)
    distrito: Optional[str] = Field(None, max_length=80)
    telefono: Optional[str] = Field(None, max_length=40)
    email: Optional[str] = Field(None, max_length=120)

    sol_user: str = Field(..., max_length=60)
    sol_pass: str = Field(..., max_length=120)
    sunat_env: Literal["beta", "produccion"] = "beta"

    cert_pass: str = Field(..., max_length=200, description="Password del .pfx/.p12")

    @field_validator("ruc")
    @classmethod
    def _ruc_check(cls, v):
        if not validar_ruc_modulo11(v):
            raise ValueError("RUC inválido (módulo 11)")
        if not v.startswith(("10", "15", "17", "20")):
            raise ValueError("RUC debe empezar con 10, 15, 17 o 20")
        return v


class OnboardingEstado(BaseModel):
    completado: bool
    tiene_empresa: bool
    tiene_certificado: bool
    sunat_env: Optional[str] = None
    ruc: Optional[str] = None


# ---------- Conexión SUNAT ----------

class ProbarSunatIn(BaseModel):
    """Datos para probar conexión SUNAT (todos opcionales: si vacíos,
    usa los datos guardados en la BD)."""
    sol_user: Optional[str] = None
    sol_pass: Optional[str] = None
    sunat_env: Optional[Literal["beta", "produccion"]] = None


class ProbarSunatOut(BaseModel):
    ok: bool
    mensaje: str
    detalle: Optional[str] = None


class AmbienteIn(BaseModel):
    ambiente: Literal["beta", "produccion"]
