"""SQLAlchemy models — modelo simplificado single-tenant Factura-mdb.

Solo lo necesario para emitir comprobantes electrónicos. Sin multi-tenant,
sin id_empresa repetido por todas partes (hay UNA empresa por instalación).
"""
from .empresa import Empresa, Configuracion
from .cliente import Cliente
from .producto import Producto
from .comprobante import Comprobante, ComprobanteDetalle, EnvioSunat
from .resumen import ResumenDiario, ResumenDiarioItem, ComunicacionBaja, ComunicacionBajaItem

__all__ = [
    "Empresa", "Configuracion",
    "Cliente", "Producto",
    "Comprobante", "ComprobanteDetalle", "EnvioSunat",
    "ResumenDiario", "ResumenDiarioItem",
    "ComunicacionBaja", "ComunicacionBajaItem",
]
