"""Schemas de Cliente."""
from datetime import datetime
from typing import Optional, Literal, List
from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator, model_validator

from .common import validar_documento_identidad


def _no_html_strict(v):
    """Bug D3: rechaza HTML / XSS embebido en campos de texto libre."""
    if v is None:
        return v
    s = str(v)
    if "<" in s or ">" in s:
        raise ValueError("El texto no puede contener < o >")
    return s


# Tipos de documento aceptados por SUNAT (cat 06)
TIPOS_DOC_VALIDOS = {"0", "1", "4", "6", "7", "A", "B", "C"}


class ClienteBase(BaseModel):
    tipo_documento: Literal["0", "1", "4", "6", "7", "A", "B", "C"] = "6"
    numero_documento: str = Field(..., min_length=1, max_length=20)
    razon_social: str = Field(..., min_length=2, max_length=200)
    direccion: Optional[str] = Field(None, max_length=300)
    ubigeo: Optional[str] = Field(None, max_length=6, min_length=6)
    distrito: Optional[str] = Field(None, max_length=80)
    provincia: Optional[str] = Field(None, max_length=80)
    departamento: Optional[str] = Field(None, max_length=80)
    email: Optional[EmailStr] = Field(None, max_length=120)
    telefono: Optional[str] = Field(None, max_length=40)

    @field_validator("razon_social", "direccion", "distrito", "provincia",
                     "departamento", mode="before")
    @classmethod
    def _no_html(cls, v):
        return _no_html_strict(v)

    @field_validator("ubigeo")
    @classmethod
    def _ubigeo_digits(cls, v):
        if v is None:
            return v
        if not v.isdigit():
            raise ValueError("Ubigeo debe ser de 6 dígitos")
        return v

    @model_validator(mode="after")
    def _validar_doc(self):
        validar_documento_identidad(self.tipo_documento, self.numero_documento)
        return self


class ClienteIn(ClienteBase):
    activo: bool = True


class ClienteUpdate(BaseModel):
    tipo_documento: Optional[Literal["0", "1", "4", "6", "7", "A", "B", "C"]] = None
    numero_documento: Optional[str] = Field(None, max_length=20)
    razon_social: Optional[str] = Field(None, min_length=2, max_length=200)
    direccion: Optional[str] = Field(None, max_length=300)
    ubigeo: Optional[str] = Field(None, max_length=6, min_length=6)
    distrito: Optional[str] = Field(None, max_length=80)
    provincia: Optional[str] = Field(None, max_length=80)
    departamento: Optional[str] = Field(None, max_length=80)
    email: Optional[EmailStr] = Field(None, max_length=120)
    telefono: Optional[str] = Field(None, max_length=40)
    activo: Optional[bool] = None

    @field_validator("razon_social", "direccion", "distrito", "provincia",
                     "departamento", mode="before")
    @classmethod
    def _no_html(cls, v):
        return _no_html_strict(v)

    @field_validator("ubigeo")
    @classmethod
    def _ubigeo_digits(cls, v):
        if v is None:
            return v
        if not v.isdigit():
            raise ValueError("Ubigeo debe ser de 6 dígitos")
        return v

    @model_validator(mode="after")
    def _validar_doc(self):
        # Solo validar si vienen ambos campos en el PUT
        if self.tipo_documento and self.numero_documento:
            validar_documento_identidad(self.tipo_documento, self.numero_documento)
        return self


class ClienteOut(BaseModel):
    """Lectura: tolerante con datos legacy (no re-ejecuta validadores duros)."""
    model_config = ConfigDict(from_attributes=True)

    id: int
    tipo_documento: str = "6"
    numero_documento: str
    razon_social: str
    direccion: Optional[str] = None
    ubigeo: Optional[str] = None
    distrito: Optional[str] = None
    provincia: Optional[str] = None
    departamento: Optional[str] = None
    email: Optional[str] = None
    telefono: Optional[str] = None
    activo: bool = True
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


class ClienteListResponse(BaseModel):
    items: List[ClienteOut]
    total: int
    limit: int
    offset: int
