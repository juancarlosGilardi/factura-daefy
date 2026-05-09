"""Importador MDB — migración 1-click desde el legado SIAP (Microsoft Access).

Permite a usuarios del antiguo sistema SIAP migrar sus datos
(clientes, productos detectados en ventas, comprobantes y detalles)
hacia la base SQLite local de Factura-mdb.

Submódulos:

* `connection`        — wrapper pyodbc + detección de driver Access.
* `schema_inspector`  — detecta tablas/columnas reales y rangos.
* `validators`        — sanea RUC mod-11, fechas, montos legacy.
* `mappers`           — transforma filas MDB → dicts listos para los modelos
                         SQLAlchemy de Factura-mdb.
* `importer`          — orquestador (preview + ejecución).
"""

from .connection import (  # noqa: F401
    MDBConnectionError,
    detectar_driver,
    conectar,
)
from .schema_inspector import MDBInspection, inspeccionar  # noqa: F401
from .importer import (  # noqa: F401
    ResultadoImportacion,
    importar_mdb,
)

__all__ = [
    "MDBConnectionError",
    "detectar_driver",
    "conectar",
    "MDBInspection",
    "inspeccionar",
    "ResultadoImportacion",
    "importar_mdb",
]
