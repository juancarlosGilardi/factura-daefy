"""Orquestador de importación MDB → Factura-mdb.

Flujo:

1. Conecta al `.mdb` y lo inspecciona (valida que existan las tablas
   requeridas).
2. Si el caller pidió `importar_clientes`, recorre `EF2CLIENTES`
   haciendo upsert por `(tipo_documento, numero_documento)`.
3. Si pidió `importar_productos`, detecta productos únicos en
   `TBVENTA_DET` (SIAP no siempre tiene tabla explícita) y los crea
   con `INSERT OR IGNORE`.
4. Si pidió `importar_comprobantes`, recorre `TBVENTA_CAB` (con filtro
   opcional `desde_fecha`), saltea los ya existentes (clave única
   `tipo+serie+correlativo`), y luego inserta los detalles asociados.

La importación es transaccional por **lote** (cada N comprobantes hace
commit) para que un error puntual no pierda todo lo previo. Devuelve
`ResultadoImportacion` con conteos y lista de errores.

Diseño deliberadamente síncrono para el MVP — facilidad de debugging.
Cuando crezca el volumen, mover a `BackgroundTasks` o un job dedicado.
"""
from __future__ import annotations

import logging
from datetime import date
from pathlib import Path
from typing import Any, Optional

from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ...models.cliente import Cliente
from ...models.comprobante import Comprobante, ComprobanteDetalle
from ...models.producto import Producto
from .connection import MDBConnectionError, cerrar_silencioso, conectar
from .mappers import (
    clave_comprobante_de_detalle,
    mdb_cliente_to_dict,
    mdb_comprobante_to_dict,
    mdb_detalle_to_dict,
    mdb_producto_unico_to_dict,
)
from .schema_inspector import MDBInspection, inspeccionar

logger = logging.getLogger("factura_mdb.mdb")

# Cuántos comprobantes procesar antes de hacer commit.
BATCH_COMMIT = 100


class ErrorImportacion(BaseModel):
    """Un fallo puntual durante la importación, no fatal."""

    tabla: str
    fila: int = 0
    clave: Optional[str] = None
    error: str


class ResultadoImportacion(BaseModel):
    """Resumen final que devuelve el endpoint."""

    clientes_creados: int = 0
    clientes_actualizados: int = 0
    clientes_saltados: int = 0  # filas vacías o sin doc/nombre
    productos_creados: int = 0
    productos_saltados: int = 0
    comprobantes_creados: int = 0
    comprobantes_saltados: int = 0  # ya existían
    detalles_creados: int = 0
    errores: list[ErrorImportacion] = Field(default_factory=list)
    inspeccion: Optional[dict] = None  # resultado del schema_inspector

    @property
    def total_filas_creadas(self) -> int:
        return (
            self.clientes_creados
            + self.productos_creados
            + self.comprobantes_creados
            + self.detalles_creados
        )


# ---------------------------------------------------------------------------
# Sub-rutinas
# ---------------------------------------------------------------------------
def _importar_clientes(
    conn: Any, db: Session, resultado: ResultadoImportacion
) -> None:
    cur = conn.cursor()
    try:
        cur.execute(
            "SELECT F2NEWRUC, F2NOMCLI, F2DOCCLI, F2DIRCLI, F2TELCLI, "
            "F2TIPDOC FROM EF2CLIENTES"
        )
        # F2EMAIL y F2UBIGEO opcionales: si faltan, _get() devuelve None.
        existentes_cols = {c[0] for c in cur.description}
        # Si hay email/ubigeo, los traemos en una segunda query para no
        # romper en MDB que carecen de esas columnas.
        cur.close()
    except Exception as e:  # noqa: BLE001
        resultado.errores.append(
            ErrorImportacion(tabla="EF2CLIENTES", error=f"No se pudo leer: {e}")
        )
        return

    # Construimos SELECT dinámico con sólo las columnas existentes.
    candidatas = ("F2NEWRUC", "F2NOMCLI", "F2DOCCLI", "F2DIRCLI",
                  "F2TELCLI", "F2TIPDOC", "F2EMAIL", "F2UBIGEO")
    cur = conn.cursor()
    try:
        cur.execute("SELECT TOP 1 * FROM EF2CLIENTES")
        cols_reales = {c[0] for c in cur.description}
    finally:
        cur.close()

    cols_a_leer = [c for c in candidatas if c in cols_reales]
    select_sql = "SELECT " + ", ".join(cols_a_leer) + " FROM EF2CLIENTES"

    cur = conn.cursor()
    try:
        cur.execute(select_sql)
        rows = cur.fetchall()
    except Exception as e:  # noqa: BLE001
        resultado.errores.append(
            ErrorImportacion(tabla="EF2CLIENTES", error=f"SELECT falló: {e}")
        )
        return
    finally:
        cur.close()

    logger.info("MDB: leídos %d clientes", len(rows))

    for idx, row in enumerate(rows, start=1):
        try:
            data = mdb_cliente_to_dict(row)
            if not data:
                resultado.clientes_saltados += 1
                continue
            existente = (
                db.query(Cliente)
                .filter_by(
                    tipo_documento=data["tipo_documento"],
                    numero_documento=data["numero_documento"],
                )
                .first()
            )
            if existente:
                # Actualizamos sólo los campos que aporten valor.
                for k, v in data.items():
                    if v is not None and v != "":
                        setattr(existente, k, v)
                resultado.clientes_actualizados += 1
            else:
                db.add(Cliente(**data))
                resultado.clientes_creados += 1
            if idx % BATCH_COMMIT == 0:
                db.commit()
        except IntegrityError as e:
            db.rollback()
            resultado.errores.append(ErrorImportacion(
                tabla="EF2CLIENTES",
                fila=idx,
                clave=str(data.get("numero_documento") if data else None),
                error=f"Conflicto BD: {e.orig}",
            ))
        except Exception as e:  # noqa: BLE001
            db.rollback()
            resultado.errores.append(ErrorImportacion(
                tabla="EF2CLIENTES", fila=idx, error=str(e),
            ))

    db.commit()


def _importar_productos(
    conn: Any, db: Session, resultado: ResultadoImportacion
) -> None:
    """Genera maestro de productos a partir de filas únicas en TBVENTA_DET.

    Toma valor_unitario y unidad_medida del último uso visto (no es
    perfecto, pero es lo que se puede inferir sin tabla de productos).
    """
    cur = conn.cursor()
    try:
        cur.execute(
            "SELECT F5CODPRO, F5NOMPRO, F7CODMED, F3VALVTAUNIT, F3AFECTO "
            "FROM TBVENTA_DET WHERE F5CODPRO IS NOT NULL"
        )
        rows = cur.fetchall()
    except Exception as e:  # noqa: BLE001
        resultado.errores.append(
            ErrorImportacion(tabla="TBVENTA_DET", error=f"SELECT productos falló: {e}")
        )
        return
    finally:
        cur.close()

    # Deduplicar por código (último gana)
    productos_unicos: dict[str, dict] = {}
    for r in rows:
        cod = (str(r.F5CODPRO or "").strip().upper())
        if not cod:
            continue
        data = mdb_producto_unico_to_dict(
            codigo=cod,
            descripcion=r.F5NOMPRO,
            unidad_medida=r.F7CODMED,
            valor_unitario=r.F3VALVTAUNIT,
            afecto=r.F3AFECTO,
        )
        if data:
            productos_unicos[data["codigo"]] = data

    logger.info("MDB: %d productos únicos detectados", len(productos_unicos))

    # Existentes en Factura-mdb para no duplicar
    existentes_codigos = set(
        c[0] for c in db.execute(select(Producto.codigo)).all()
    )

    for cod, data in productos_unicos.items():
        try:
            if cod in existentes_codigos:
                resultado.productos_saltados += 1
                continue
            db.add(Producto(**data))
            resultado.productos_creados += 1
        except Exception as e:  # noqa: BLE001
            resultado.errores.append(ErrorImportacion(
                tabla="TBVENTA_DET", clave=cod, error=str(e),
            ))

    try:
        db.commit()
    except IntegrityError as e:
        db.rollback()
        resultado.errores.append(ErrorImportacion(
            tabla="productos", error=f"Commit falló: {e.orig}",
        ))


def _importar_comprobantes(
    conn: Any,
    db: Session,
    resultado: ResultadoImportacion,
    desde_fecha: Optional[date] = None,
) -> None:
    """Importa comprobantes y luego sus detalles.

    Estrategia:
      1. Lee todos los comprobantes (con filtro opcional de fecha).
      2. Mapea cada uno; salta los ya existentes (uniq tipo+serie+corr).
      3. Después de un commit por lote, lee detalles de los **mismos**
         comprobantes recién insertados y los inserta de una vez.

    Mantiene un dict `clave→id` en memoria para no consultar BD en cada
    detalle.
    """
    where_fecha = ""
    params: list = []
    if desde_fecha:
        where_fecha = " WHERE F4FECEMI >= ?"
        # pyodbc acepta date directamente para Access.
        params.append(desde_fecha)

    cur = conn.cursor()
    sql_cab = (
        "SELECT F4TIPODOCU, F4SERDOC, F4NUMDOC, F4FECEMI, F4TIPMON, "
        "F4TIPCAM, F2RUCCLI, F2NOMCLI, F2DIRCLI, F4SUBTOT, F4TOTIGV, "
        "F4SUBFACINAF, F4MONTOEXONERADO, F4TOTFAC, F4ESTNUL, F4ESTEMI, "
        "F4ENVIADO, F4CDR, F4CDRFECHA, F4CODEHASH, F4FORPAG, "
        "F4DETRACCIONAPLICA, F4DETRACCIONPORC, F4DETRACCIONMONTO, "
        "F4TIPREF, F4SERGUI, F4NUMGUI, F2CODCLI "
        "FROM TBVENTA_CAB" + where_fecha + " ORDER BY F4FECEMI, F4TIPODOCU, "
        "F4SERDOC, F4NUMDOC"
    )

    try:
        cur.execute(sql_cab, *params) if params else cur.execute(sql_cab)
        cab_rows = cur.fetchall()
    except Exception as e:  # noqa: BLE001
        resultado.errores.append(
            ErrorImportacion(tabla="TBVENTA_CAB", error=f"SELECT falló: {e}")
        )
        return
    finally:
        cur.close()

    logger.info("MDB: leídos %d comprobantes (desde=%s)", len(cab_rows), desde_fecha)

    # Pre-cargamos las claves existentes para evitar un SELECT por fila.
    existentes_keys = set(
        (c.tipo_documento, c.serie, c.correlativo)
        for c in db.execute(
            select(Comprobante.tipo_documento, Comprobante.serie,
                   Comprobante.correlativo)
        ).all()
    )

    # Mapa que llenamos con los IDs recién insertados (clave → id).
    nuevos_ids: dict[tuple[str, str, int], int] = {}

    for idx, row in enumerate(cab_rows, start=1):
        try:
            data = mdb_comprobante_to_dict(row)
            if not data:
                resultado.comprobantes_saltados += 1
                continue
            clave = (data["tipo_documento"], data["serie"], data["correlativo"])
            if clave in existentes_keys:
                resultado.comprobantes_saltados += 1
                continue

            comp = Comprobante(**data)
            db.add(comp)
            db.flush()  # asigna comp.id sin commit
            nuevos_ids[clave] = comp.id
            existentes_keys.add(clave)
            resultado.comprobantes_creados += 1

            if idx % BATCH_COMMIT == 0:
                db.commit()
        except IntegrityError as e:
            db.rollback()
            resultado.errores.append(ErrorImportacion(
                tabla="TBVENTA_CAB",
                fila=idx,
                clave=f"{row.F4TIPODOCU}-{row.F4SERDOC}-{row.F4NUMDOC}",
                error=f"Conflicto BD: {e.orig}",
            ))
        except Exception as e:  # noqa: BLE001
            db.rollback()
            resultado.errores.append(ErrorImportacion(
                tabla="TBVENTA_CAB", fila=idx, error=str(e),
            ))

    db.commit()

    # ── Importar detalles sólo de los comprobantes recién insertados ──
    if not nuevos_ids:
        logger.info("MDB: sin comprobantes nuevos, no se importan detalles")
        return

    sql_det = (
        "SELECT F4TIPODOCU, F4SERDOC, F4NUMDOC, F5CODPRO, F5NOMPRO, "
        "F7CODMED, F3CANPRO, F3VALVTAUNIT, F3VALVTA, F3IGV, F3PREVTA, "
        "F3AFECTO, F3ITEM FROM TBVENTA_DET" + where_fecha + " ORDER BY "
        "F4TIPODOCU, F4SERDOC, F4NUMDOC, F3ITEM"
    )

    cur = conn.cursor()
    try:
        cur.execute(sql_det, *params) if params else cur.execute(sql_det)
        det_rows = cur.fetchall()
    except Exception as e:  # noqa: BLE001
        resultado.errores.append(
            ErrorImportacion(tabla="TBVENTA_DET", error=f"SELECT falló: {e}")
        )
        return
    finally:
        cur.close()

    logger.info("MDB: leídos %d detalles", len(det_rows))

    for idx, row in enumerate(det_rows, start=1):
        clave = clave_comprobante_de_detalle(row)
        if not clave or clave not in nuevos_ids:
            # detalle de un comprobante que no insertamos (ya existía
            # o estaba fuera de filtro): saltar silenciosamente.
            continue
        try:
            data = mdb_detalle_to_dict(row)
            if not data:
                continue
            data["comprobante_id"] = nuevos_ids[clave]
            db.add(ComprobanteDetalle(**data))
            resultado.detalles_creados += 1
            if idx % BATCH_COMMIT == 0:
                db.commit()
        except Exception as e:  # noqa: BLE001
            db.rollback()
            resultado.errores.append(ErrorImportacion(
                tabla="TBVENTA_DET",
                fila=idx,
                clave=f"{clave[0]}-{clave[1]}-{clave[2]}",
                error=str(e),
            ))

    db.commit()


# ---------------------------------------------------------------------------
# Entrada pública
# ---------------------------------------------------------------------------
def importar_mdb(
    mdb_path: Path | str,
    db: Session,
    desde_fecha: Optional[date] = None,
    importar_clientes: bool = True,
    importar_productos: bool = True,
    importar_comprobantes: bool = True,
) -> ResultadoImportacion:
    """Punto de entrada de la importación.

    Args:
        mdb_path: ruta al archivo .mdb
        db: sesión SQLAlchemy abierta (no se cierra acá; el caller
            es responsable, p.ej. la dependencia `get_db` de FastAPI)
        desde_fecha: si se especifica, sólo importa comprobantes (y sus
            detalles) con `F4FECEMI >= desde_fecha`.
        importar_clientes/productos/comprobantes: flags individuales.

    Returns:
        `ResultadoImportacion` con conteos y errores.

    Raises:
        MDBConnectionError: si no puede abrir/inspeccionar el .mdb.
            Errores granulares (de fila individual) NO levantan, sino
            que se registran en `resultado.errores`.
    """
    resultado = ResultadoImportacion()

    conn = None
    try:
        conn = conectar(mdb_path)
        insp: MDBInspection = inspeccionar(conn)
        resultado.inspeccion = insp.to_dict()

        if not insp.es_compatible:
            faltantes = ", ".join(insp.tablas_faltantes)
            raise MDBConnectionError(
                f"El archivo no es un MDB SIAP compatible. "
                f"Faltan tablas requeridas: {faltantes}"
            )

        if importar_clientes:
            _importar_clientes(conn, db, resultado)
        if importar_productos:
            _importar_productos(conn, db, resultado)
        if importar_comprobantes:
            _importar_comprobantes(conn, db, resultado, desde_fecha)

    finally:
        cerrar_silencioso(conn)

    logger.info(
        "Importación MDB terminada — "
        "clientes: %d/%d (creados/actualizados); productos: %d; "
        "comprobantes: %d (+%d detalles); errores: %d",
        resultado.clientes_creados,
        resultado.clientes_actualizados,
        resultado.productos_creados,
        resultado.comprobantes_creados,
        resultado.detalles_creados,
        len(resultado.errores),
    )
    return resultado
