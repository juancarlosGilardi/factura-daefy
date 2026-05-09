"""Inspección del esquema de un archivo MDB legacy SIAP.

Antes de importar, queremos:
  * Confirmar que el .mdb tenga las tablas mínimas esperadas.
  * Listar columnas reales de cada tabla (versiones distintas de SIAP
    tienen variantes — p.ej. F4ENVIADO existe en algunas, no en otras).
  * Devolver conteos y rango de fechas para que la UI muestre un preview
    confiable antes de tocar la BD de Factura-mdb.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from .connection import MDBConnectionError

logger = logging.getLogger("factura_mdb.mdb")


# Tablas requeridas para considerar el .mdb "compatible" con SIAP.
TABLAS_REQUERIDAS: tuple[str, ...] = (
    "EF2CLIENTES",
    "TBVENTA_CAB",
    "TBVENTA_DET",
)

# Columnas que `mappers` consume. Se reportan como faltantes en
# `MDBInspection.columnas_faltantes` para diagnóstico — el mapper
# las trata como opcionales (devuelve None) si no existen.
COLUMNAS_ESPERADAS: dict[str, tuple[str, ...]] = {
    "EF2CLIENTES": (
        "F2NEWRUC", "F2NOMCLI", "F2DOCCLI", "F2DIRCLI",
        "F2TELCLI", "F2TIPDOC", "F2EMAIL", "F2UBIGEO",
    ),
    "TBVENTA_CAB": (
        "F4TIPODOCU", "F4SERDOC", "F4NUMDOC", "F4FECEMI", "F4TIPMON",
        "F4TIPCAM", "F2RUCCLI", "F2NOMCLI", "F2DIRCLI",
        "F4SUBTOT", "F4TOTIGV", "F4SUBFACINAF", "F4MONTOEXONERADO",
        "F4TOTFAC", "F4ESTNUL", "F4ESTEMI", "F4ENVIADO", "F4CDR",
        "F4CDRFECHA", "F4CODEHASH", "F4FORPAG",
        "F4DETRACCIONAPLICA", "F4DETRACCIONPORC", "F4DETRACCIONMONTO",
        "F4TIPREF", "F4SERGUI", "F4NUMGUI", "F2CODCLI",
    ),
    "TBVENTA_DET": (
        "F4TIPODOCU", "F4SERDOC", "F4NUMDOC", "F5CODPRO", "F5NOMPRO",
        "F7CODMED", "F3CANPRO", "F3VALVTAUNIT", "F3VALVTA",
        "F3IGV", "F3PREVTA", "F3AFECTO", "F3ITEM",
    ),
}


@dataclass
class MDBInspection:
    """Resultado de inspeccionar un archivo .mdb."""

    tablas_encontradas: list[str] = field(default_factory=list)
    tablas_faltantes: list[str] = field(default_factory=list)
    columnas_por_tabla: dict[str, list[str]] = field(default_factory=dict)
    columnas_faltantes: dict[str, list[str]] = field(default_factory=dict)

    n_clientes: int = 0
    n_comprobantes: int = 0
    n_detalles: int = 0
    n_productos_unicos: int = 0

    rango_fechas: tuple[date | None, date | None] = (None, None)

    # Métricas adicionales útiles para el preview
    comprobantes_por_tipo: dict[str, int] = field(default_factory=dict)
    versiones_detectadas: list[str] = field(default_factory=list)

    @property
    def es_compatible(self) -> bool:
        """`True` si el .mdb tiene las tablas mínimas para importar."""
        return not self.tablas_faltantes

    def to_dict(self) -> dict[str, Any]:
        """Serializa a JSON-friendly para devolver desde la API."""
        return {
            "tablas_encontradas": self.tablas_encontradas,
            "tablas_faltantes": self.tablas_faltantes,
            "columnas_por_tabla": self.columnas_por_tabla,
            "columnas_faltantes": self.columnas_faltantes,
            "n_clientes": self.n_clientes,
            "n_comprobantes": self.n_comprobantes,
            "n_detalles": self.n_detalles,
            "n_productos_unicos": self.n_productos_unicos,
            "rango_fechas": [
                self.rango_fechas[0].isoformat() if self.rango_fechas[0] else None,
                self.rango_fechas[1].isoformat() if self.rango_fechas[1] else None,
            ],
            "comprobantes_por_tipo": self.comprobantes_por_tipo,
            "versiones_detectadas": self.versiones_detectadas,
            "es_compatible": self.es_compatible,
        }


# ---------------------------------------------------------------------------
# Helpers internos
# ---------------------------------------------------------------------------
def _listar_tablas(conn: Any) -> list[str]:
    """Devuelve los nombres de tablas USER del catálogo."""
    cur = conn.cursor()
    tablas: list[str] = []
    try:
        for row in cur.tables(tableType="TABLE"):
            nombre = row.table_name
            if nombre and not nombre.startswith("MSys"):
                tablas.append(nombre)
    finally:
        cur.close()
    return sorted(tablas)


def _listar_columnas(conn: Any, tabla: str) -> list[str]:
    """Devuelve nombres de columnas de la tabla (vacío si no existe)."""
    cur = conn.cursor()
    try:
        cur.execute(f"SELECT TOP 1 * FROM [{tabla}]")
        return [c[0] for c in cur.description]
    except Exception:  # noqa: BLE001
        # Tabla inexistente o sin permisos — devolvemos lista vacía.
        return []
    finally:
        cur.close()


def _contar(conn: Any, sql: str) -> int:
    cur = conn.cursor()
    try:
        cur.execute(sql)
        row = cur.fetchone()
        return int(row[0]) if row and row[0] is not None else 0
    except Exception as e:  # noqa: BLE001
        logger.debug("Conteo MDB falló (%s): %s", sql, e)
        return 0
    finally:
        cur.close()


def _detectar_versiones(columnas: dict[str, list[str]]) -> list[str]:
    """Heurística simple para identificar la "edad" del .mdb.

    No es exacto pero ayuda a comunicarle al usuario qué tan
    actualizado/antiguo es su SIAP.
    """
    notas: list[str] = []
    cab = set(columnas.get("TBVENTA_CAB", []))

    if "F4DETRACCIONAPLICA" in cab:
        notas.append("Soporta detracción explícita")
    if "F4ENVIADO" in cab:
        notas.append("Track de envío SUNAT")
    if "F4CDR" in cab:
        notas.append("CDR almacenado")
    if "F4MONTOEXONERADO" in cab:
        notas.append("Diferencia exonerado/inafecto")
    if not notas:
        notas.append("Versión muy temprana — sólo campos básicos")
    return notas


# ---------------------------------------------------------------------------
# API pública
# ---------------------------------------------------------------------------
def inspeccionar(conn: Any) -> MDBInspection:
    """Inspecciona un MDB ya abierto con `connection.conectar()`.

    No modifica la BD. Lectura pura.
    """
    insp = MDBInspection()

    todas = _listar_tablas(conn)
    requeridas = set(TABLAS_REQUERIDAS)
    presentes = [t for t in todas if t.upper() in {r.upper() for r in requeridas}]
    nombres_presentes_upper = {t.upper() for t in presentes}

    insp.tablas_encontradas = todas
    insp.tablas_faltantes = sorted(
        r for r in requeridas if r.upper() not in nombres_presentes_upper
    )

    # Columnas por tabla (sólo las requeridas para diagnóstico fino).
    for tabla in TABLAS_REQUERIDAS:
        cols = _listar_columnas(conn, tabla)
        if cols:
            insp.columnas_por_tabla[tabla] = cols
            esperadas = set(COLUMNAS_ESPERADAS.get(tabla, ()))
            faltan = sorted(esperadas - set(cols))
            if faltan:
                insp.columnas_faltantes[tabla] = faltan

    if insp.tablas_faltantes:
        # Sin tablas requeridas no tiene sentido seguir contando.
        return insp

    # Conteos
    insp.n_clientes = _contar(conn, "SELECT COUNT(*) FROM EF2CLIENTES")
    insp.n_comprobantes = _contar(conn, "SELECT COUNT(*) FROM TBVENTA_CAB")
    insp.n_detalles = _contar(conn, "SELECT COUNT(*) FROM TBVENTA_DET")
    # Access JET no soporta COUNT(DISTINCT col): usamos subquery + GROUP BY.
    insp.n_productos_unicos = _contar(
        conn,
        "SELECT COUNT(*) FROM (SELECT DISTINCT F5CODPRO FROM TBVENTA_DET "
        "WHERE F5CODPRO IS NOT NULL) AS sub",
    )

    # Rango de fechas (puede fallar en MDB muy antiguos; tratamos suave)
    cur = conn.cursor()
    try:
        cur.execute(
            "SELECT MIN(F4FECEMI), MAX(F4FECEMI) FROM TBVENTA_CAB "
            "WHERE F4FECEMI IS NOT NULL"
        )
        row = cur.fetchone()
        if row:
            min_d = row[0].date() if hasattr(row[0], "date") else row[0]
            max_d = row[1].date() if hasattr(row[1], "date") else row[1]
            insp.rango_fechas = (min_d, max_d)
    except Exception as e:  # noqa: BLE001
        logger.debug("Rango fechas MDB falló: %s", e)
    finally:
        cur.close()

    # Comprobantes por tipo (01, 03, 07, 08)
    cur = conn.cursor()
    try:
        cur.execute(
            "SELECT F4TIPODOCU, COUNT(*) FROM TBVENTA_CAB "
            "WHERE F4TIPODOCU IS NOT NULL GROUP BY F4TIPODOCU"
        )
        for r in cur.fetchall():
            tipo = (r[0] or "").strip()
            if tipo:
                insp.comprobantes_por_tipo[tipo] = int(r[1])
    except Exception:  # noqa: BLE001
        logger.debug("Conteo por tipo MDB falló", exc_info=True)
    finally:
        cur.close()

    insp.versiones_detectadas = _detectar_versiones(insp.columnas_por_tabla)
    return insp


def inspeccionar_archivo(mdb_path: Any) -> MDBInspection:
    """Helper que abre, inspecciona y cierra. Útil para tests/CLI."""
    from .connection import conectar, cerrar_silencioso

    conn = None
    try:
        conn = conectar(mdb_path)
        return inspeccionar(conn)
    finally:
        cerrar_silencioso(conn)
