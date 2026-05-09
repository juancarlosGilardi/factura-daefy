"""Modelos para Nota de Credito (07) y Nota de Debito (08)."""
from __future__ import annotations
from decimal import Decimal
from typing import List, Optional
from pydantic import BaseModel, Field, field_validator

from .common import (
    Cliente, Item, FormaPago, AllowanceCharge,
    GuiaRemision, TipoCambio,
)


class DocumentoReferencia(BaseModel):
    """Referencia al comprobante original que se modifica."""
    id: str = Field(..., description="Ej: F001-123")
    tipo_doc: str = Field(..., description="Catalogo 01: '01'=Factura, '03'=Boleta")
    codigo_motivo: str = Field(..., description="Catalogo 09 (NC) o 10 (ND)")
    descripcion_motivo: str = Field(..., description="Descripcion del motivo")


class NotaCreditoRequest(BaseModel):
    ruc_emisor: str = Field(..., min_length=11, max_length=11, pattern=r"^\d{11}$")
    razon_social_emisor: str
    ubigeo_emisor: str
    direccion_emisor: str
    codigo_local: str = Field("0000")

    tipo_documento: str = Field("07")
    serie: str
    numero: int = Field(..., ge=1)
    numero_padding: int = Field(default=8, description="7 u 8 digitos")
    fecha_emision: str
    hora_emision: str = Field("00:00:00")
    moneda: str = Field("PEN")

    cliente: Cliente
    forma_pago: FormaPago = Field(default_factory=lambda: FormaPago(tipo="Contado"))

    documento_referencia: DocumentoReferencia

    tipo_cambio: Optional[TipoCambio] = None
    descuentos_globales: Optional[List[AllowanceCharge]] = None

    items: List[Item] = Field(..., min_length=1)
    guia_remision: Optional[GuiaRemision] = None

    @field_validator("serie")
    @classmethod
    def upper_serie(cls, v: str) -> str:
        return v.upper()

    model_config = {"json_encoders": {Decimal: str}}


class NotaDebitoRequest(BaseModel):
    ruc_emisor: str = Field(..., min_length=11, max_length=11, pattern=r"^\d{11}$")
    razon_social_emisor: str
    ubigeo_emisor: str
    direccion_emisor: str
    codigo_local: str = Field("0000")

    tipo_documento: str = Field("08")
    serie: str
    numero: int = Field(..., ge=1)
    numero_padding: int = Field(default=8, description="7 u 8 digitos")
    fecha_emision: str
    hora_emision: str = Field("00:00:00")
    moneda: str = Field("PEN")

    cliente: Cliente
    forma_pago: FormaPago = Field(default_factory=lambda: FormaPago(tipo="Contado"))

    documento_referencia: DocumentoReferencia

    tipo_cambio: Optional[TipoCambio] = None
    descuentos_globales: Optional[List[AllowanceCharge]] = None

    items: List[Item] = Field(..., min_length=1)
    guia_remision: Optional[GuiaRemision] = None

    @field_validator("serie")
    @classmethod
    def upper_serie(cls, v: str) -> str:
        return v.upper()

    model_config = {"json_encoders": {Decimal: str}}
