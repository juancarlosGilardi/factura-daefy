"""Schemas de Comunicación de Baja (RA)."""
from datetime import date, datetime
from typing import Optional, List
from pydantic import BaseModel, ConfigDict, Field


class ComunicacionBajaItemIn(BaseModel):
    comprobante_id: int
    motivo: Optional[str] = Field(None, max_length=300)


class ComunicacionBajaItemOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    comprobante_id: int
    motivo: Optional[str] = None


class ComunicacionBajaIn(BaseModel):
    """Body para enviar una comunicación de baja."""
    fecha_documentos: Optional[date] = Field(
        None, description="Si se omite, se infiere del primer comprobante"
    )
    fecha_comunicacion: Optional[date] = Field(
        None, description="Si se omite, se usa hoy"
    )
    motivo: str = Field(
        "ANULACION POR ERROR EN LA EMISION", min_length=3, max_length=300
    )
    items: List[ComunicacionBajaItemIn] = Field(..., min_length=1)


class ComunicacionBajaOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    fecha_documentos: date
    fecha_comunicacion: date
    correlativo: int
    nombre_archivo: str

    estado: str
    ticket: Optional[str] = None
    cdr_codigo: Optional[str] = None
    cdr_descripcion: Optional[str] = None
    xml_path: Optional[str] = None
    cdr_path: Optional[str] = None
    motivo: str

    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    items: List[ComunicacionBajaItemOut] = []


class BajaListResponse(BaseModel):
    items: List[ComunicacionBajaOut]
    total: int
    limit: int
    offset: int
