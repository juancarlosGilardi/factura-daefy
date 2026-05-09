"""Schemas de Resumen Diario (RC)."""
from datetime import date, datetime
from typing import Optional, Literal, List
from pydantic import BaseModel, ConfigDict, Field


class ResumenDiarioItemIn(BaseModel):
    comprobante_id: int
    condicion: Literal["1", "2", "3"] = "1"
    # 1 = adicionar, 2 = modificar, 3 = anular


class ResumenDiarioItemOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    comprobante_id: int
    condicion: str


class ResumenDiarioIn(BaseModel):
    """Body para crear y enviar un resumen diario."""
    fecha_referencia: date = Field(..., description="Fecha de los comprobantes")
    fecha_comunicacion: Optional[date] = Field(
        None, description="Si se omite, se usa hoy"
    )
    items: List[ResumenDiarioItemIn] = Field(..., min_length=1)


class ResumenDiarioOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    fecha_referencia: date
    fecha_comunicacion: date
    correlativo: int
    nombre_archivo: str
    tipo_resumen: str

    estado: str
    ticket: Optional[str] = None
    cdr_codigo: Optional[str] = None
    cdr_descripcion: Optional[str] = None
    xml_path: Optional[str] = None
    cdr_path: Optional[str] = None

    total_documentos: int
    total_gravado: float
    total_igv: float
    total: float

    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    items: List[ResumenDiarioItemOut] = []


class ResumenListResponse(BaseModel):
    items: List[ResumenDiarioOut]
    total: int
    limit: int
    offset: int


class ComprobantePendienteResumen(BaseModel):
    """Boleta candidata a incluir en un resumen diario."""
    id: int
    numero_completo: str
    fecha_emision: date
    cliente_numero_doc: str
    cliente_razon_social: str
    moneda: str
    total_venta: float
    estado: str
