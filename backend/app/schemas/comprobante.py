"""Schemas de Comprobante (factura, boleta, NC, ND)."""
import re
from datetime import date, datetime, timedelta
from decimal import Decimal, ROUND_HALF_UP
from typing import Optional, Literal, List, Any
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


def _q2(v) -> Decimal:
    return Decimal(str(v or 0)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


# Bug C12/H9 / cat 07: tipos de afectacion gratuita
_TIPOS_AFECTACION_GRATUITA = {
    "11", "12", "13", "14", "15", "16", "17",
    "21", "31", "32", "33", "34", "35", "36",
}

from .common import (
    TipoAfectacionIGV, MOTIVOS_NC_VALIDOS, MOTIVOS_ND_VALIDOS,
    DETRACCIONES_VALIDAS, TipoOperacion,
)


# Series válidas (incluye legacy SIAP). Permitimos series tradicionales y mixtas.
RE_SERIE_FACTURA = re.compile(r"^F[A-Z0-9]{3}$")          # F001, FXYZ
RE_SERIE_BOLETA = re.compile(r"^B[A-Z0-9]{3}$")           # B001, BXYZ
RE_SERIE_NC_FACTURA = re.compile(r"^FC[A-Z0-9]{2}$")      # FC01
RE_SERIE_NC_BOLETA = re.compile(r"^BC[A-Z0-9]{2}$")       # BC01
RE_SERIE_ND_FACTURA = re.compile(r"^FD[A-Z0-9]{2}$")      # FD01
RE_SERIE_ND_BOLETA = re.compile(r"^BD[A-Z0-9]{2}$")       # BD01

TIPOS_DOC_VALIDOS = {"01", "03", "07", "08"}
TIPOS_DOC_CLIENTE = {"0", "1", "4", "6", "7", "A", "B", "C"}


class ComprobanteDetalleIn(BaseModel):
    orden: int = Field(1, ge=1)
    producto_id: Optional[int] = None
    codigo: Optional[str] = Field(None, max_length=40)
    descripcion: str = Field(..., min_length=1, max_length=500)
    unidad_medida: str = Field("NIU", max_length=8)

    cantidad: float = Field(..., gt=0, le=999999999.999)
    valor_unitario: float = Field(..., ge=0, le=9999999999.99)
    precio_unitario: Optional[float] = Field(None, ge=0, le=9999999999.99)
    descuento_pct: float = Field(0.0, ge=0, le=100)
    descuento_monto: float = Field(0.0, ge=0, le=9999999999.99)

    tipo_afectacion_igv: TipoAfectacionIGV = "10"
    igv_pct: float = Field(18.0, ge=0, le=100)
    isc_pct: float = Field(0.0, ge=0, le=100)

    @model_validator(mode="after")
    def _validar_descuento(self):
        bruto = (self.cantidad or 0) * (self.valor_unitario or 0)
        if (self.descuento_monto or 0) > bruto:
            raise ValueError(
                f"descuento_monto ({self.descuento_monto}) no puede ser mayor que "
                f"cantidad*valor_unitario ({bruto})"
            )
        return self

    @model_validator(mode="after")
    def _validar_monto_minimo(self):
        # Bug C12/H9: rechazar lineas con monto efectivo <= 0 al redondear a 2 decimales
        # (caso clasico: cantidad=1, valor_unitario=0.001 -> 0.00 efectivo -> SUNAT 2655)
        # Permitir gratuitas (valor_unitario=0 es valido en cat 07 11-36)
        if self.tipo_afectacion_igv in _TIPOS_AFECTACION_GRATUITA:
            return self
        bruto = (self.cantidad or 0) * (self.valor_unitario or 0)
        if _q2(bruto) <= 0:
            raise ValueError(
                f"Línea con monto efectivo cero al redondear. "
                f"cantidad={self.cantidad}, valor_unitario={self.valor_unitario}"
            )
        return self


class ComprobanteDetalleOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    orden: int
    producto_id: Optional[int] = None
    codigo: Optional[str] = None
    descripcion: str
    unidad_medida: str
    cantidad: float
    valor_unitario: float
    precio_unitario: float
    descuento_pct: float
    descuento_monto: float
    valor_venta: float
    igv_pct: float
    igv_monto: float
    tipo_afectacion_igv: str
    isc_pct: float
    isc_monto: float
    icbper_monto: float
    total_linea: float


class ComprobanteIn(BaseModel):
    """Body para emitir un comprobante."""
    tipo_documento: Literal["01", "03", "07", "08"]
    serie: str = Field(..., min_length=4, max_length=4)
    correlativo: Optional[int] = Field(
        None, ge=1, description="Si se omite, el backend asigna el siguiente disponible"
    )

    fecha_emision: date
    fecha_vencimiento: Optional[date] = None
    hora_emision: Optional[str] = Field(None, max_length=8)
    moneda: Literal["PEN", "USD", "EUR"] = "PEN"
    tipo_cambio: float = Field(1.0, gt=0)
    # Bug D8: tipo_operacion validado contra cat 17 SUNAT
    tipo_operacion: TipoOperacion = "0101"

    cliente_id: Optional[int] = None
    cliente_tipo_doc: Literal["0", "1", "4", "6", "7", "A", "B", "C"]
    cliente_numero_doc: str = Field(..., min_length=1, max_length=20)
    cliente_razon_social: str = Field(..., min_length=1, max_length=200)
    cliente_direccion: Optional[str] = Field(None, max_length=300)

    forma_pago: Literal["Contado", "Credito"] = "Contado"
    forma_pago_json: Optional[Any] = None

    # Detracción/percepción opcionales
    detraccion_codigo: Optional[str] = Field(None, max_length=5)
    detraccion_tasa: Optional[float] = Field(None, ge=0, le=100)
    detraccion_monto: Optional[float] = Field(None, ge=0)
    detraccion_cta_bn: Optional[str] = Field(None, max_length=30)
    percepcion_pct: Optional[float] = Field(None, ge=0, le=100)
    percepcion_monto: Optional[float] = Field(None, ge=0)

    # Doc referencia (NC/ND obligatorio)
    documento_referencia_tipo: Optional[str] = Field(None, max_length=2)
    documento_referencia_serie: Optional[str] = Field(None, max_length=13)
    motivo_codigo: Optional[str] = Field(None, max_length=2)
    motivo_descripcion: Optional[str] = Field(None, max_length=300)
    comprobante_ref_id: Optional[int] = None

    observaciones: Optional[str] = None

    items: List[ComprobanteDetalleIn] = Field(..., min_length=1)

    @field_validator("serie")
    @classmethod
    def _serie_format(cls, v):
        v = v.upper().strip()
        if len(v) != 4:
            raise ValueError("Serie debe tener exactamente 4 caracteres")
        if not re.match(r"^[A-Z0-9]{4}$", v):
            raise ValueError("Serie sólo acepta letras mayúsculas y dígitos")
        return v

    @field_validator("hora_emision", mode="before")
    @classmethod
    def _normalizar_hora(cls, v):
        # Bug A7/H6: SUNAT exige IssueTime como HH:MM:SS. Aceptar HH:MM legacy y
        # normalizar agregando ":00".
        if v is None:
            return v
        s = str(v).strip()
        if len(s) == 5 and s[2] == ":":
            s = s + ":00"
        return s

    @field_validator("hora_emision")
    @classmethod
    def _hora(cls, v):
        if v is None:
            return v
        # Tras normalizar, exigir formato estricto HH:MM:SS
        if not re.match(r"^([01]\d|2[0-3]):[0-5]\d:[0-5]\d$", v):
            raise ValueError("hora_emision formato HH:MM:SS (HH 00-23, MM/SS 00-59)")
        return v

    @field_validator("fecha_emision")
    @classmethod
    def _validar_fecha_emision(cls, v):
        # Bug D5: fecha_emision sin rango → permitir solo ventana razonable
        # (30 días pasado para emisiones tardías legítimas, 1 día futuro para
        # zonas horarias).
        hoy = date.today()
        if v < hoy - timedelta(days=30):
            raise ValueError(
                f"fecha_emision muy antigua (más de 30 días). Hoy: {hoy}"
            )
        if v > hoy + timedelta(days=1):
            raise ValueError(f"fecha_emision en futuro. Hoy: {hoy}")
        return v

    @model_validator(mode="after")
    def _validar_serie_vs_tipo(self):
        td = self.tipo_documento
        s = self.serie
        # Series tradicionales SIAP — permitidas para retrocompatibilidad
        if td == "01":
            # Factura: F### (incluye serie legacy con dígitos/letras tras 'F')
            if not (RE_SERIE_FACTURA.match(s)):
                raise ValueError(f"Serie inválida para factura (01): {s}. Debe ser FXXX")
        elif td == "03":
            if not RE_SERIE_BOLETA.match(s):
                raise ValueError(f"Serie inválida para boleta (03): {s}. Debe ser BXXX")
        elif td == "07":
            if not (RE_SERIE_NC_FACTURA.match(s) or RE_SERIE_NC_BOLETA.match(s)
                    or RE_SERIE_FACTURA.match(s) or RE_SERIE_BOLETA.match(s)):
                raise ValueError(
                    f"Serie inválida para NC (07): {s}. Recomendado FCxx o BCxx"
                )
        elif td == "08":
            if not (RE_SERIE_ND_FACTURA.match(s) or RE_SERIE_ND_BOLETA.match(s)
                    or RE_SERIE_FACTURA.match(s) or RE_SERIE_BOLETA.match(s)):
                raise ValueError(
                    f"Serie inválida para ND (08): {s}. Recomendado FDxx o BDxx"
                )
        return self

    @model_validator(mode="after")
    def _validar_referencia(self):
        if self.tipo_documento in ("07", "08"):
            faltantes = []
            if not self.documento_referencia_tipo:
                faltantes.append("documento_referencia_tipo")
            if not self.documento_referencia_serie:
                faltantes.append("documento_referencia_serie")
            if not self.motivo_codigo:
                faltantes.append("motivo_codigo")
            if faltantes:
                raise ValueError(
                    f"NC/ND requiere: {', '.join(faltantes)}"
                )
        return self

    @model_validator(mode="after")
    def _validar_motivo_codigo(self):
        # SUNAT cat 09 (NC) / cat 10 (ND): códigos cerrados.
        if self.tipo_documento == "07":
            if self.motivo_codigo and self.motivo_codigo not in MOTIVOS_NC_VALIDOS:
                raise ValueError(
                    f"motivo_codigo inválido para NC (cat 09 SUNAT): {self.motivo_codigo}. "
                    f"Válidos: {sorted(MOTIVOS_NC_VALIDOS)}"
                )
        elif self.tipo_documento == "08":
            if self.motivo_codigo and self.motivo_codigo not in MOTIVOS_ND_VALIDOS:
                raise ValueError(
                    f"motivo_codigo inválido para ND (cat 10 SUNAT): {self.motivo_codigo}. "
                    f"Válidos: {sorted(MOTIVOS_ND_VALIDOS)}"
                )
        return self

    @model_validator(mode="after")
    def _validar_moneda_extranjera(self):
        # Para moneda extranjera exigir un tipo de cambio realista (>= 1.5).
        # Para PEN forzamos 1.0 silenciosamente.
        if self.moneda != "PEN":
            if self.tipo_cambio is None or self.tipo_cambio < 1.5:
                raise ValueError(
                    f"Para moneda {self.moneda} debes indicar un tipo_cambio realista (>= 1.5). "
                    "Para PEN puedes omitirlo."
                )
        else:
            if self.tipo_cambio != 1.0:
                object.__setattr__(self, "tipo_cambio", 1.0)
        return self

    @model_validator(mode="after")
    def _validar_doc_segun_tipo(self):
        # La factura electrónica (01) exige cliente con RUC (tipo_doc = '6').
        if self.tipo_documento == "01" and self.cliente_tipo_doc != "6":
            raise ValueError(
                "La factura electrónica (01) exige cliente con RUC (tipo doc 6)"
            )
        # Bug A5/H1: boleta (03) con monto > S/ 700 exige cliente con RUC.
        # SUNAT validación 3104. Estimación gruesa con IGV 18%.
        if self.tipo_documento == "03":
            total_estimado = sum(
                (it.cantidad or 0) * (it.valor_unitario or 0) * 1.18
                for it in (self.items or [])
            )
            if total_estimado > 700 and self.cliente_tipo_doc != "6":
                raise ValueError(
                    f"Boleta con monto estimado S/ {total_estimado:.2f} > 700 "
                    "exige cliente con RUC (tipo doc 6). SUNAT validación 3104."
                )
        return self

    @model_validator(mode="after")
    def _validar_fecha_vencimiento(self):
        # Bug C16/G7: fecha_vencimiento no puede ser anterior a fecha_emision
        if self.fecha_vencimiento and self.fecha_emision:
            if self.fecha_vencimiento < self.fecha_emision:
                raise ValueError(
                    f"fecha_vencimiento ({self.fecha_vencimiento}) no puede ser "
                    f"anterior a fecha_emision ({self.fecha_emision})"
                )
        return self

    @model_validator(mode="after")
    def _validar_detraccion_codigo(self):
        # Bug D4: detraccion_codigo debe existir en cat 54 SUNAT
        if self.detraccion_codigo and self.detraccion_codigo not in DETRACCIONES_VALIDAS:
            raise ValueError(
                f"detraccion_codigo inválido (cat 54 SUNAT): {self.detraccion_codigo}. "
                f"Válidos: {sorted(DETRACCIONES_VALIDAS)}"
            )
        return self

    @model_validator(mode="after")
    def _validar_credito(self):
        # Bug D7: forma_pago='Credito' exige fecha_vencimiento o cuotas
        if self.forma_pago == "Credito":
            if not self.fecha_vencimiento and not self.forma_pago_json:
                raise ValueError(
                    "forma_pago='Credito' exige fecha_vencimiento o forma_pago_json con cuotas"
                )
        return self

    @model_validator(mode="after")
    def _validar_total_overflow(self):
        # Bug D9: cap defensivo al total para evitar overflow numérico SUNAT
        total = sum(
            (it.cantidad or 0) * (it.valor_unitario or 0)
            for it in (self.items or [])
        )
        if total > 999_999_999.99:
            raise ValueError(
                f"Total de comprobante excede límite SUNAT: {total}"
            )
        return self


class ComprobanteOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    tipo_documento: str
    serie: str
    correlativo: int
    numero_completo: str

    fecha_emision: date
    fecha_vencimiento: Optional[date] = None
    hora_emision: Optional[str] = None
    moneda: str
    tipo_cambio: float
    tipo_operacion: str

    cliente_id: Optional[int] = None
    cliente_tipo_doc: str
    cliente_numero_doc: str
    cliente_razon_social: str
    cliente_direccion: Optional[str] = None

    total_gravado: float
    total_exonerado: float
    total_inafecto: float
    total_exportacion: float
    total_gratuito: float
    total_descuento: float
    subtotal: float
    total_igv: float
    total_isc: float
    total_icbper: float
    total_venta: float
    total_pen: float
    monto_letras: Optional[str] = None

    detraccion_codigo: Optional[str] = None
    detraccion_tasa: Optional[float] = None
    detraccion_monto: Optional[float] = None
    detraccion_cta_bn: Optional[str] = None
    percepcion_pct: Optional[float] = None
    percepcion_monto: Optional[float] = None

    forma_pago: str
    forma_pago_json: Optional[Any] = None

    doc_referencia_tipo: Optional[str] = None
    doc_referencia_serie: Optional[str] = None
    motivo_nc_codigo: Optional[str] = None
    motivo_nc_descripcion: Optional[str] = None

    estado: str
    cdr_codigo: Optional[str] = None
    cdr_descripcion: Optional[str] = None
    xml_path: Optional[str] = None
    cdr_path: Optional[str] = None
    pdf_path: Optional[str] = None
    qr_data: Optional[str] = None

    observaciones: Optional[str] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    detalles: List[ComprobanteDetalleOut] = []


class ComprobanteListItem(BaseModel):
    """Versión slim para listados en grilla."""
    model_config = ConfigDict(from_attributes=True)

    id: int
    tipo_documento: str
    serie: str
    correlativo: int
    numero_completo: str
    fecha_emision: date
    moneda: str
    cliente_numero_doc: str
    cliente_razon_social: str
    total_venta: float
    estado: str
    cdr_codigo: Optional[str] = None
    pdf_path: Optional[str] = None


class ComprobanteListResponse(BaseModel):
    items: List[ComprobanteListItem]
    total: int
    limit: int
    offset: int


class ProximoCorrelativoOut(BaseModel):
    serie: str
    proximo_correlativo: int
    ultimo_emitido: Optional[int] = None


class AnularIn(BaseModel):
    motivo: str = Field(..., min_length=3, max_length=300)
