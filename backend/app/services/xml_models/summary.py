"""Modelo para Resumen Diario (SummaryDocuments)."""
from __future__ import annotations
from typing import List, Optional
from pydantic import BaseModel, Field


class DocumentoResumen(BaseModel):
    tipo_doc: str = Field(..., description="'03'=Boleta, '07'=NC, '08'=ND")
    serie_numero: str = Field(..., description="Ej: B001-1001")
    tipo_doc_cliente: str = Field(..., description="0=sin doc, 1=DNI, 6=RUC")
    num_doc_cliente: str
    condicion: str = Field(..., description="'1'=Adicion, '2'=Modificacion, '3'=Anulacion")
    moneda: str = Field("PEN")
    total: float = 0.0
    doc_referencia: str = Field("", description="Serie-Numero del doc original, ej: B001-757")
    tipo_doc_referencia: str = Field("03")
    gravada: float = 0.0
    exonerada: float = 0.0
    inafecta: float = 0.0
    exportacion: float = 0.0
    gratuita: float = 0.0
    igv: float = 0.0
    isc: float = 0.0
    otros_tributos: float = 0.0


class ResumenDiarioRequest(BaseModel):
    ruc_emisor: str = Field(..., min_length=11, max_length=11)
    razon_social_emisor: str
    correlativo: str = Field(..., description="Ej: 00001")
    fecha_documentos: str = Field(..., description="YYYY-MM-DD")
    fecha_comunicacion: str = Field(..., description="YYYY-MM-DD")
    documentos: List[DocumentoResumen] = Field(..., min_length=1)
