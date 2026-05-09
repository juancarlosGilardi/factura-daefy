"""Modelo para Comunicacion de Baja (VoidedDocuments)."""
from __future__ import annotations
from typing import List
from pydantic import BaseModel, Field


class DocumentoBaja(BaseModel):
    tipo_doc: str = Field(..., description="'01'=Factura, '07'=NC, '08'=ND")
    serie: str = Field(..., description="Ej: F001")
    correlativo: str = Field(..., description="Ej: 456")
    motivo: str = Field(..., description="Motivo de la baja")


class ComunicacionBajaRequest(BaseModel):
    ruc_emisor: str = Field(..., min_length=11, max_length=11)
    razon_social_emisor: str
    correlativo: str = Field(..., description="Ej: 00001")
    fecha_documentos: str = Field(..., description="YYYY-MM-DD")
    fecha_comunicacion: str = Field(..., description="YYYY-MM-DD")
    documentos: List[DocumentoBaja] = Field(..., min_length=1)
