"""Modelos Pydantic v2 compartidos entre todos los comprobantes."""
from __future__ import annotations
from decimal import Decimal
from typing import List, Optional
from pydantic import BaseModel, Field, field_validator, model_validator


class TipoCambio(BaseModel):
    """Tipo de cambio para facturas en moneda extranjera (PaymentExchangeRate)."""
    tasa: Decimal = Field(..., gt=0, description="Tasa de cambio, ej: 3.85")
    fecha: str = Field(..., description="Fecha del tipo de cambio YYYY-MM-DD")
    moneda_destino: str = Field("PEN", description="Moneda destino, usualmente PEN")

    model_config = {"json_encoders": {Decimal: str}}


class Anticipo(BaseModel):
    """Anticipo / pago adelantado (PrepaidPayment)."""
    id_comprobante: str = Field(..., description="ID del comprobante de anticipo, ej: F001-111")
    tipo_doc: str = Field("02", description="Tipo doc anticipo, Catalogo 12: '02'=factura de anticipo")
    ruc_emisor: str = Field(..., min_length=11, max_length=11, pattern=r"^\d{11}$",
                             description="RUC del emisor del anticipo")
    monto: Decimal = Field(..., gt=0, description="Monto del anticipo (con IGV)")

    model_config = {"json_encoders": {Decimal: str}}


class Cliente(BaseModel):
    tipo_doc: str = Field("6", description="Catalogo 06: 0=sin doc, 1=DNI, 6=RUC, 4=CE, 7=pasaporte")
    numero: str = Field(..., description="Numero de documento del cliente")
    razon_social: str = Field(..., description="Razon social o nombre del cliente")
    direccion: Optional[str] = None
    telefono: Optional[str] = None
    email: Optional[str] = None


class Cuota(BaseModel):
    numero: int = Field(..., ge=1)
    monto: Decimal = Field(..., gt=0)
    fecha_vencimiento: str = Field(..., description="YYYY-MM-DD")

    model_config = {"json_encoders": {Decimal: str}}


class FormaPago(BaseModel):
    tipo: str = Field("Contado", description="'Contado' o 'Credito'")
    monto_pendiente: Optional[Decimal] = Field(None, description="Solo para credito")
    cuotas: Optional[List[Cuota]] = Field(None, description="Solo para credito")

    @field_validator("tipo")
    @classmethod
    def validar_tipo(cls, v: str) -> str:
        if v not in ("Contado", "Credito"):
            raise ValueError('Debe ser "Contado" o "Credito"')
        return v

    @model_validator(mode="after")
    def validar_credito(self) -> FormaPago:
        if self.tipo == "Credito":
            if not self.cuotas:
                raise ValueError("Credito requiere al menos una cuota")
            if self.monto_pendiente:
                suma = sum(c.monto for c in self.cuotas)
                if abs(suma - self.monto_pendiente) > Decimal("0.01"):
                    raise ValueError(
                        f"Suma de cuotas ({suma}) != monto_pendiente ({self.monto_pendiente})"
                    )
        return self

    model_config = {"json_encoders": {Decimal: str}}


class Detraccion(BaseModel):
    codigo_bien_servicio: str = Field(..., description="Catalogo 54 SUNAT, ej: '027'")
    porcentaje: Decimal = Field(..., gt=0, le=100, description="Ej: 4 para 4%")
    cuenta_banco_nacion: str = Field(..., min_length=14, max_length=14,
                                      pattern=r"^\d{14}$")
    constancia: Optional[str] = None

    model_config = {"json_encoders": {Decimal: str}}


class Percepcion(BaseModel):
    codigo_regimen: str = Field(..., description="Catalogo 53: '01' venta interna, '02' combustible, '03' importacion")
    porcentaje: Decimal = Field(..., gt=0, le=100, description="Ej: 2 para 2%")
    monto_base: Decimal = Field(..., gt=0, description="Base imponible de la percepcion")
    monto_percepcion: Decimal = Field(..., gt=0, description="Monto a percibir")
    monto_total: Decimal = Field(..., gt=0, description="Total con percepcion incluida")

    model_config = {"json_encoders": {Decimal: str}}


class AllowanceCharge(BaseModel):
    """Descuento (charge_indicator=False) o cargo (charge_indicator=True)."""
    charge_indicator: bool = Field(False, description="False=descuento, True=cargo/percepcion")
    codigo_motivo: str = Field(..., description="Catalogo 53 SUNAT")
    porcentaje: Optional[Decimal] = Field(None, description="MultiplierFactorNumeric")
    monto: Decimal = Field(..., gt=0, description="Amount")
    monto_base: Decimal = Field(..., gt=0, description="BaseAmount")

    model_config = {"json_encoders": {Decimal: str}}


class Item(BaseModel):
    codigo: str = Field(..., description="Codigo interno del producto/servicio")
    descripcion: str
    cantidad: Decimal = Field(..., gt=0)
    unidad: str = Field(..., description="Catalogo 03 SUNAT, ej: NIU, KG, ZZ")
    valor_unitario: Decimal = Field(..., ge=0, description="Precio sin IGV")
    afectacion_igv: str = Field("10", description="Catalogo 07: 10=gravado, 20=exonerado, 30=inafecto, 11-16/31-36=gratuito, 17=IVAP, 40=exportacion")
    descuento: Optional[AllowanceCharge] = Field(None, description="Descuento por item")
    icbper_monto: Optional[Decimal] = Field(None, gt=0, description="Monto ICBPER por unidad (bolsa plastica). Ej: 0.20")
    isc_tasa: Optional[Decimal] = Field(None, ge=0, description="Tasa ISC al valor (%), ej: 17 para 17%")
    isc_sistema: str = Field("01", description="TierRange: '01'=al valor, '02'=especifico, '03'=precio al publico")
    codigo_unspsc: Optional[str] = Field(None, description="Codigo UNSPSC/NCM del producto, ej: '44121618'")

    model_config = {"json_encoders": {Decimal: str}}


class GuiaRemision(BaseModel):
    serie_numero: str = Field(..., description="Ej: T001-00000012")
    tipo_doc: str = Field("09", description="Catalogo 01: 09=guia de remision")


class DatosEmisor(BaseModel):
    """Datos del emisor. Si se omite se toman del request principal."""
    ubigeo: str = Field(..., description="Codigo UBIGEO 6 digitos")
    direccion: str
    urbanizacion: Optional[str] = None
    provincia: Optional[str] = None
    departamento: Optional[str] = None
    distrito: Optional[str] = None
    codigo_local: str = Field("0000", description="Codigo local del establecimiento")
    telefono: Optional[str] = None
    email: Optional[str] = None
