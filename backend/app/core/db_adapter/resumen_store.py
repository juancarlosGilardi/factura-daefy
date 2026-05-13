"""Almacenamiento de Resumen Diario (RC) — modo MDB/SQLite.

Espejo de ``baja_store.BajaStore`` pero para FMDB_RESUMENES.

Esquema:
    id, correlativo, fecha_referencia, fecha_comunicacion,
    nombre_archivo, tipo_resumen ('RC'),
    estado ('P'|'A'|'R'|'B'),
    ticket, cdr_codigo, cdr_descripcion,
    xml_path, cdr_path,
    total_documentos, total_gravado, total_igv, total,
    items_json (lista de {comprobante_id, condicion, tipo_doc, serie,
                          correlativo, total, ...}),
    created_at, updated_at
"""
from __future__ import annotations

import json
import logging
from datetime import date as _date, datetime
from typing import Any, Optional

from . import is_sqlite_mode

logger = logging.getLogger("factura_mdb.resumen_store")


def _cursor_ctx():
    if is_sqlite_mode():
        from .sqlite_writer import sqlite_write_cursor
        return sqlite_write_cursor()
    from .mdb_lock import write_cursor
    return write_cursor()


def _read_cursor_ctx():
    if is_sqlite_mode():
        from .sqlite_repo import sqlite_cursor
        return sqlite_cursor()
    from .mdb_repo import mdb_cursor
    return mdb_cursor()


def _now_iso() -> str:
    return datetime.now().isoformat()


def _now() -> datetime:
    return datetime.now()


def _to_date(v: Any) -> Optional[_date]:
    if v is None:
        return None
    if isinstance(v, _date) and not isinstance(v, datetime):
        return v
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, str):
        try:
            return _date.fromisoformat(v[:10])
        except ValueError:
            return None
    return None


def _str_date(d: Any) -> str:
    if d is None:
        return ""
    if isinstance(d, str):
        return d[:10]
    if isinstance(d, datetime):
        return d.date().isoformat()
    if isinstance(d, _date):
        return d.isoformat()
    return str(d)


def _to_dt(d: Any) -> Optional[datetime]:
    if d is None:
        return None
    if isinstance(d, datetime):
        return d
    if isinstance(d, _date):
        return datetime.combine(d, datetime.min.time())
    if isinstance(d, str):
        try:
            return datetime.fromisoformat(d[:10])
        except ValueError:
            return None
    return None


_DDL_SQLITE = """
CREATE TABLE IF NOT EXISTS FMDB_RESUMENES (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    correlativo         INTEGER NOT NULL,
    fecha_referencia    TEXT NOT NULL,
    fecha_comunicacion  TEXT NOT NULL,
    nombre_archivo      TEXT,
    tipo_resumen        TEXT DEFAULT 'RC',
    estado              TEXT DEFAULT 'P',
    ticket              TEXT,
    cdr_codigo          TEXT,
    cdr_descripcion     TEXT,
    xml_path            TEXT,
    cdr_path            TEXT,
    total_documentos    INTEGER DEFAULT 0,
    total_gravado       REAL DEFAULT 0,
    total_igv           REAL DEFAULT 0,
    total               REAL DEFAULT 0,
    items_json          TEXT NOT NULL,
    created_at          TEXT,
    updated_at          TEXT
)
"""

_DDL_MDB = """
CREATE TABLE FMDB_RESUMENES (
    id                  COUNTER PRIMARY KEY,
    correlativo         INTEGER NOT NULL,
    fecha_referencia    DATETIME,
    fecha_comunicacion  DATETIME,
    nombre_archivo      VARCHAR(60),
    tipo_resumen        VARCHAR(2),
    estado              VARCHAR(1),
    ticket              VARCHAR(50),
    cdr_codigo          VARCHAR(10),
    cdr_descripcion     VARCHAR(500),
    xml_path            VARCHAR(500),
    cdr_path            VARCHAR(500),
    total_documentos    INTEGER,
    total_gravado       DOUBLE,
    total_igv           DOUBLE,
    total               DOUBLE,
    items_json          MEMO,
    created_at          DATETIME,
    updated_at          DATETIME
)
"""


def asegurar_tabla() -> None:
    if is_sqlite_mode():
        with _cursor_ctx() as cur:
            cur.execute(_DDL_SQLITE)
        return
    try:
        with _cursor_ctx() as cur:
            cur.execute(_DDL_MDB)
            logger.info("Tabla FMDB_RESUMENES creada en MDB")
    except Exception as e:  # noqa: BLE001
        msg = str(e).lower()
        if (
            "ya existe" in msg
            or "already exists" in msg
            or "existe en la base" in msg
            or "exists" in msg
        ):
            logger.debug("FMDB_RESUMENES ya existe (ok)")
        else:
            raise


class ResumenStore:
    """Repo + Writer combinado para FMDB_RESUMENES."""

    @staticmethod
    def proximo_correlativo(fecha_comunicacion: _date) -> int:
        from datetime import timedelta
        asegurar_tabla()
        with _read_cursor_ctx() as cur:
            if is_sqlite_mode():
                cur.execute(
                    "SELECT MAX(correlativo) FROM FMDB_RESUMENES "
                    "WHERE fecha_comunicacion = ?",
                    [_str_date(fecha_comunicacion)],
                )
            else:
                dt = _to_dt(fecha_comunicacion)
                if dt is None:
                    return 1
                dt_next = dt + timedelta(days=1)
                cur.execute(
                    "SELECT MAX(correlativo) FROM FMDB_RESUMENES "
                    "WHERE fecha_comunicacion >= ? AND fecha_comunicacion < ?",
                    dt, dt_next,
                )
            row = cur.fetchone()
            try:
                last = int(list(vars(row).values())[0] or 0) if hasattr(row, "__dict__") else int(row[0] or 0)
            except (TypeError, ValueError):
                last = 0
        return last + 1

    @staticmethod
    def crear(
        *,
        fecha_referencia: _date,
        fecha_comunicacion: _date,
        correlativo: int,
        nombre_archivo: str,
        items: list[dict],
        total_documentos: int,
        total_gravado: float,
        total_igv: float,
        total: float,
    ) -> int:
        asegurar_tabla()
        now_v = _now_iso() if is_sqlite_mode() else _now()
        fec_ref = _str_date(fecha_referencia) if is_sqlite_mode() else _to_dt(fecha_referencia)
        fec_com = _str_date(fecha_comunicacion) if is_sqlite_mode() else _to_dt(fecha_comunicacion)
        items_json = json.dumps(items, default=str)[:65000]

        with _cursor_ctx() as cur:
            cur.execute(
                "INSERT INTO FMDB_RESUMENES ("
                "  correlativo, fecha_referencia, fecha_comunicacion,"
                "  nombre_archivo, tipo_resumen, estado,"
                "  total_documentos, total_gravado, total_igv, total,"
                "  items_json, created_at, updated_at"
                ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    int(correlativo), fec_ref, fec_com,
                    (nombre_archivo or "")[:60], "RC", "P",
                    int(total_documentos),
                    float(total_gravado or 0),
                    float(total_igv or 0),
                    float(total or 0),
                    items_json,
                    now_v, now_v,
                ],
            )
            if is_sqlite_mode():
                cur.execute("SELECT last_insert_rowid()")
                row = cur.fetchone()
                new_id = int(list(vars(row).values())[0]) if hasattr(row, "__dict__") else int(row[0])
            else:
                cur.execute("SELECT @@IDENTITY")
                row = cur.fetchone()
                new_id = int(row[0])
        logger.info("Resumen creado en FMDB_RESUMENES id=%d nombre=%s",
                     new_id, nombre_archivo)
        return new_id

    @staticmethod
    def obtener(resumen_id: int) -> Optional[dict]:
        asegurar_tabla()
        with _read_cursor_ctx() as cur:
            cur.execute(
                "SELECT id, correlativo, fecha_referencia, fecha_comunicacion,"
                " nombre_archivo, tipo_resumen, estado, ticket, cdr_codigo,"
                " cdr_descripcion, xml_path, cdr_path, total_documentos,"
                " total_gravado, total_igv, total, items_json,"
                " created_at, updated_at "
                "FROM FMDB_RESUMENES WHERE id = ?",
                [int(resumen_id)] if is_sqlite_mode() else (int(resumen_id),),
            )
            row = cur.fetchone()
            if not row:
                return None
            return _row_to_resumen_dict(row)

    @staticmethod
    def listar(
        estado: Optional[str] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[dict], int]:
        asegurar_tabla()
        where = ""
        params: list[Any] = []
        if estado:
            where = " WHERE estado = ?"
            params.append(estado)

        with _read_cursor_ctx() as cur:
            cur.execute(f"SELECT COUNT(*) FROM FMDB_RESUMENES{where}", params)
            row0 = cur.fetchone()
            try:
                total = int(list(vars(row0).values())[0] or 0) if hasattr(row0, "__dict__") else int(row0[0] or 0)
            except (TypeError, ValueError):
                total = 0

            if is_sqlite_mode():
                cur.execute(
                    "SELECT id, correlativo, fecha_referencia, fecha_comunicacion,"
                    " nombre_archivo, tipo_resumen, estado, ticket, cdr_codigo,"
                    " cdr_descripcion, xml_path, cdr_path, total_documentos,"
                    " total_gravado, total_igv, total, items_json,"
                    " created_at, updated_at "
                    f"FROM FMDB_RESUMENES{where} "
                    "ORDER BY fecha_comunicacion DESC, id DESC "
                    "LIMIT ? OFFSET ?",
                    params + [limit, offset],
                )
            else:
                cur.execute(
                    f"SELECT TOP {limit + offset} id, correlativo,"
                    " fecha_referencia, fecha_comunicacion, nombre_archivo,"
                    " tipo_resumen, estado, ticket, cdr_codigo, cdr_descripcion,"
                    " xml_path, cdr_path, total_documentos, total_gravado,"
                    " total_igv, total, items_json, created_at, updated_at "
                    f"FROM FMDB_RESUMENES{where} "
                    "ORDER BY fecha_comunicacion DESC, id DESC",
                    *params,
                )
            rows = cur.fetchall()

        items = [_row_to_resumen_dict(r) for r in rows]
        items = [i for i in items if i is not None]
        if not is_sqlite_mode():
            items = items[offset:offset + limit]
        return items, total

    @staticmethod
    def actualizar(
        resumen_id: int,
        *,
        estado: Optional[str] = None,
        ticket: Optional[str] = None,
        cdr_codigo: Optional[str] = None,
        cdr_descripcion: Optional[str] = None,
        xml_path: Optional[str] = None,
        cdr_path: Optional[str] = None,
        nombre_archivo: Optional[str] = None,
    ) -> bool:
        asegurar_tabla()
        sets: list[str] = []
        vals: list[Any] = []
        if estado is not None:
            sets.append("estado = ?")
            vals.append(estado[:1])
        if ticket is not None:
            sets.append("ticket = ?")
            vals.append(ticket[:50])
        if cdr_codigo is not None:
            sets.append("cdr_codigo = ?")
            vals.append((cdr_codigo or "")[:10])
        if cdr_descripcion is not None:
            sets.append("cdr_descripcion = ?")
            vals.append((cdr_descripcion or "")[:500])
        if xml_path is not None:
            sets.append("xml_path = ?")
            vals.append(xml_path[:500])
        if cdr_path is not None:
            sets.append("cdr_path = ?")
            vals.append(cdr_path[:500])
        if nombre_archivo is not None:
            sets.append("nombre_archivo = ?")
            vals.append(nombre_archivo[:60])

        if not sets:
            return False

        sets.append("updated_at = ?")
        vals.append(_now_iso() if is_sqlite_mode() else _now())
        vals.append(int(resumen_id))

        with _cursor_ctx() as cur:
            cur.execute(
                f"UPDATE FMDB_RESUMENES SET {', '.join(sets)} WHERE id = ?",
                vals,
            )
            updated = cur.rowcount
        return updated > 0

    @staticmethod
    def comprobante_ids_en_resumen_activo() -> set[int]:
        """Devuelve IDs de comprobantes que ya estan en algun resumen no anulado."""
        asegurar_tabla()
        ids: set[int] = set()
        with _read_cursor_ctx() as cur:
            cur.execute(
                "SELECT items_json, estado FROM FMDB_RESUMENES "
                "WHERE estado IS NULL OR estado <> 'B'"
            )
            for r in cur.fetchall():
                try:
                    raw = r[0] if not hasattr(r, "items_json") else r.items_json
                    items = json.loads(raw or "[]")
                except (json.JSONDecodeError, TypeError):
                    continue
                for it in items:
                    cid = it.get("comprobante_id")
                    if cid:
                        try:
                            ids.add(int(cid))
                        except (TypeError, ValueError):
                            pass
        return ids


def _row_to_resumen_dict(row: Any) -> Optional[dict]:
    if row is None:
        return None

    def g(name: str, idx: int) -> Any:
        try:
            return getattr(row, name)
        except AttributeError:
            try:
                return row[idx]
            except (IndexError, TypeError):
                return None

    items_raw = g("items_json", 16) or "[]"
    try:
        items = json.loads(items_raw) if isinstance(items_raw, str) else (items_raw or [])
    except (json.JSONDecodeError, TypeError):
        items = []

    fec_ref = _to_date(g("fecha_referencia", 2))
    fec_com = _to_date(g("fecha_comunicacion", 3))
    if not fec_ref or not fec_com:
        return None

    out_items = []
    for idx, it in enumerate(items, start=1):
        out_items.append({
            "id": idx,
            "comprobante_id": int(it.get("comprobante_id") or 0),
            "condicion": str(it.get("condicion") or "1"),
        })

    return {
        "id": int(g("id", 0)),
        "correlativo": int(g("correlativo", 1) or 0),
        "fecha_referencia": fec_ref,
        "fecha_comunicacion": fec_com,
        "nombre_archivo": (g("nombre_archivo", 4) or "")[:60],
        "tipo_resumen": (g("tipo_resumen", 5) or "RC")[:2],
        "estado": (g("estado", 6) or "P")[:1],
        "ticket": g("ticket", 7),
        "cdr_codigo": g("cdr_codigo", 8),
        "cdr_descripcion": g("cdr_descripcion", 9),
        "xml_path": g("xml_path", 10),
        "cdr_path": g("cdr_path", 11),
        "total_documentos": int(g("total_documentos", 12) or 0),
        "total_gravado": float(g("total_gravado", 13) or 0),
        "total_igv": float(g("total_igv", 14) or 0),
        "total": float(g("total", 15) or 0),
        "created_at": None,
        "updated_at": None,
        "items": out_items,
        "_items_raw": items,
    }
