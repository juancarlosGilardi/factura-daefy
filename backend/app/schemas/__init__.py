"""Schemas Pydantic v2 para Factura-mdb — request/response de la API REST."""
from .common import MessageResponse, TicketConsultaOut
from .empresa import (
    EmpresaIn, EmpresaOut, EmpresaUpdate,
    ConfiguracionIn, ConfiguracionOut, ConfiguracionUpdate,
    OnboardingIn, OnboardingEstado, EmpresaConfigOut,
    ProbarSunatIn, ProbarSunatOut, AmbienteIn,
)
from .cliente import ClienteIn, ClienteOut, ClienteUpdate, ClienteListResponse
from .producto import ProductoIn, ProductoOut, ProductoUpdate, ProductoListResponse
from .comprobante import (
    ComprobanteDetalleIn, ComprobanteDetalleOut,
    ComprobanteIn, ComprobanteOut, ComprobanteListItem, ComprobanteListResponse,
    ProximoCorrelativoOut, AnularIn,
)
from .resumen import (
    ResumenDiarioItemIn, ResumenDiarioItemOut,
    ResumenDiarioIn, ResumenDiarioOut, ResumenListResponse,
    ComprobantePendienteResumen,
)
from .baja import (
    ComunicacionBajaItemIn, ComunicacionBajaItemOut,
    ComunicacionBajaIn, ComunicacionBajaOut, BajaListResponse,
)

__all__ = [
    "MessageResponse", "TicketConsultaOut",
    "EmpresaIn", "EmpresaOut", "EmpresaUpdate",
    "ConfiguracionIn", "ConfiguracionOut", "ConfiguracionUpdate",
    "OnboardingIn", "OnboardingEstado", "EmpresaConfigOut",
    "ProbarSunatIn", "ProbarSunatOut", "AmbienteIn",
    "ClienteIn", "ClienteOut", "ClienteUpdate", "ClienteListResponse",
    "ProductoIn", "ProductoOut", "ProductoUpdate", "ProductoListResponse",
    "ComprobanteDetalleIn", "ComprobanteDetalleOut",
    "ComprobanteIn", "ComprobanteOut", "ComprobanteListItem", "ComprobanteListResponse",
    "ProximoCorrelativoOut", "AnularIn",
    "ResumenDiarioItemIn", "ResumenDiarioItemOut",
    "ResumenDiarioIn", "ResumenDiarioOut", "ResumenListResponse",
    "ComprobantePendienteResumen",
    "ComunicacionBajaItemIn", "ComunicacionBajaItemOut",
    "ComunicacionBajaIn", "ComunicacionBajaOut", "BajaListResponse",
]
