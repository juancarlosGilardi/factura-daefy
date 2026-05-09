"""Modelos para Factura (01) y Boleta (03)."""
from __future__ import annotations
from decimal import Decimal
from typing import List, Optional
from pydantic import BaseModel, Field, field_validator

from .common import (
    Cliente, Item, FormaPago, Detraccion, Percepcion,
    AllowanceCharge, GuiaRemision, TipoCambio, Anticipo,
)


class FacturaRequest(BaseModel):
    # Datos del emisor
    ruc_emisor: str = Field(..., min_length=11, max_length=11, pattern=r"^\d{11}$")
    razon_social_emisor: str
    ubigeo_emisor: str = Field(..., description="UBIGEO 6 digitos")
    direccion_emisor: str
    urbanizacion_emisor: Optional[str] = None
    provincia_emisor: Optional[str] = None
    departamento_emisor: Optional[str] = None
    distrito_emisor: Optional[str] = None
    codigo_local: str = Field("0000")

    # Cabecera del comprobante
    tipo_documento: str = Field("01", description="'01'=Factura, '03'=Boleta")
    serie: str = Field(..., description="Ej: F001, B001")
    numero: int = Field(..., ge=1)
    numero_padding: int = Field(default=8, description="7 u 8 digitos")
    fecha_emision: str = Field(..., description="YYYY-MM-DD")
    hora_emision: str = Field("00:00:00", description="HH:MM:SS")
    fecha_vencimiento: Optional[str] = Field(None, description="YYYY-MM-DD")
    moneda: str = Field("PEN", description="ISO 4217: PEN, USD, EUR")
    orden_compra: Optional[str] = None

    # Participantes
    cliente: Cliente

    # Condiciones de pago
    forma_pago: FormaPago = Field(default_factory=lambda: FormaPago(tipo="Contado"))

    # Operaciones especiales (mutuamente excluyentes: detraccion vs percepcion)
    detraccion: Optional[Detraccion] = None
    percepcion: Optional[Percepcion] = None

    # Descuentos/cargos globales
    descuentos_globales: Optional[List[AllowanceCharge]] = None

    # Moneda extranjera
    tipo_cambio: Optional[TipoCambio] = None

    # Anticipo / prepago
    anticipo: Optional[Anticipo] = None

    # Exportacion
    tipo_operacion: Optional[str] = Field(None, description="Override para listID, ej: '0200'")
    incoterm: Optional[str] = None
    puerto_embarque: Optional[str] = None

    # Items
    items: List[Item] = Field(..., min_length=1)

    # Documento relacionado
    guia_remision: Optional[GuiaRemision] = None

    @field_validator("tipo_documento")
    @classmethod
    def validar_tipo(cls, v: str) -> str:
        if v not in ("01", "03"):
            raise ValueError("tipo_documento debe ser '01' (Factura) o '03' (Boleta)")
        return v

    @field_validator("serie")
    @classmethod
    def validar_serie(cls, v: str) -> str:
        return v.upper()

    model_config = {"json_encoders": {Decimal: str}}
