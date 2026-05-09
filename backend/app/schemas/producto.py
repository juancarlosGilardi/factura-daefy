"""Schemas de Producto / Servicio."""
from datetime import datetime
from typing import Optional, Literal, List
from pydantic import BaseModel, ConfigDict, Field

from .common import TipoAfectacionIGV


class ProductoBase(BaseModel):
    codigo: str = Field(..., min_length=1, max_length=40)
    descripcion: str = Field(..., min_length=1, max_length=300)
    unidad_medida: str = Field("NIU", max_length=8)
    valor_unitario: float = Field(0.0, ge=0, le=9999999999.99)
    moneda: Literal["PEN", "USD", "EUR"] = "PEN"
    tipo_afectacion_igv: TipoAfectacionIGV = "10"
    incluye_igv: bool = False
    notas: Optional[str] = Field(None, max_length=500)


class ProductoIn(ProductoBase):
    activo: bool = True


class ProductoUpdate(BaseModel):
    codigo: Optional[str] = Field(None, min_length=1, max_length=40)
    descripcion: Optional[str] = Field(None, min_length=1, max_length=300)
    unidad_medida: Optional[str] = Field(None, max_length=8)
    valor_unitario: Optional[float] = Field(None, ge=0, le=9999999999.99)
    moneda: Optional[Literal["PEN", "USD", "EUR"]] = None
    tipo_afectacion_igv: Optional[TipoAfectacionIGV] = None
    incluye_igv: Optional[bool] = None
    notas: Optional[str] = Field(None, max_length=500)
    activo: Optional[bool] = None


class ProductoOut(BaseModel):
    """Lectura: tolerante con datos legacy (no fuerza Literal en tipo_afectacion_igv)."""
    model_config = ConfigDict(from_attributes=True)

    id: int
    codigo: str
    descripcion: str
    unidad_medida: str = "NIU"
    valor_unitario: float = 0.0
    moneda: str = "PEN"
    tipo_afectacion_igv: str = "10"
    incluye_igv: bool = False
    notas: Optional[str] = None
    activo: bool = True
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


class ProductoListResponse(BaseModel):
    items: List[ProductoOut]
    total: int
    limit: int
    offset: int
