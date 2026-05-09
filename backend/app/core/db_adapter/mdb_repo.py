"""Repositorios de lectura desde .mdb (SIAP legacy).

Sprint 1: solo lectura. Reusa `mdb_importer.connection.conectar()` y los
mappers existentes (`mdb_cliente_to_dict`, etc.) para devolver dicts con
las MISMAS keys que los modelos SQLAlchemy de Factura-mdb — así el frontend
Jinja+Alpine y los Pydantic schemas funcionan sin cambios.

Diseño:
- Cada repo es una clase con métodos estáticos `listar(...)`, `obtener(id)`.
- Cada llamada abre y cierra una conexión pyodbc fresca (no hay pool).
  Para Sprint 1 con poco tráfico (single-tenant local) es aceptable;
  Sprint 2 evaluamos un pool si hace falta.
- Access JET no soporta LIMIT/OFFSET nativo: usamos `TOP {limit+offset}`
  y descartamos `offset` filas en Python.
- Como no hay un `id` autoincremental en el .mdb, sintetizamos:
    - Cliente: usamos F2CODCLI (string corto, p.ej. '0001') y lo
      convertimos a int cuando es posible. Si no, hash determinístico.
    - Producto: hash determinístico de F5CODPRO (no hay PK numérica).
    - Comprobante: hash determinístico de tipo+serie+correlativo.
  Estos IDs se generan con `_synth_id()` y son estables entre llamadas
  (mismo input → mismo output), así los enlaces "/clientes/{id}" funcionan.
"""
from __future__ import annotations

import logging
import zlib
from contextlib import contextmanager
from datetime import date as _date, datetime
from typing import Any, Iterator, Optional

from ...services.mdb_importer.connection import conectar
from ...services.mdb_importer import mappers
from . import get_mdb_path

logger = logging.getLogger("factura_mdb.mdb.repo")


# ---------------------------------------------------------------------------
# Conexión y helpers
# ---------------------------------------------------------------------------
@contextmanager
def mdb_cursor() -> Iterator[Any]:
    """Context manager para cursor pyodbc. Cierra todo al salir."""
    cn = conectar(get_mdb_path())
    try:
        cur = cn.cursor()
        try:
            yield cur
        finally:
            try:
                cur.close()
            except Exception:  # noqa: BLE001
                pass
    finally:
        try:
            cn.close()
        except Exception:  # noqa: BLE001
            pass


def _synth_id(*parts: Any) -> int:
    """Genera un int positivo determinístico a partir de strings.

    Útil para sintetizar `id` cuando el .mdb no tiene PK autoincremental.
    Usa CRC32 (32 bits, no negativo) y deja un rango razonable
    (≤ 2_147_483_647 para encajar en Integer SQLite/Pydantic).
    """
    raw = "|".join("" if p is None else str(p) for p in parts)
    return zlib.crc32(raw.encode("utf-8", errors="replace")) & 0x7FFFFFFF


def _f2codcli_to_int(f2codcli: Any) -> int:
    """Cliente: intenta usar F2CODCLI como int (suelen ser '0001', '0002').

    Si trae letras, hace fallback a synth_id estable.
    """
    s = str(f2codcli or "").strip()
    if s.isdigit():
        try:
            return int(s)
        except ValueError:
            pass
    return _synth_id("cli", s)


def _escape_like(s: str) -> str:
    """Escapa wildcards Access (`*`, `?`, `[`, `#`) para LIKE seguro."""
    return (
        s.replace("[", "[[]")
         .replace("*", "[*]")
         .replace("?", "[?]")
         .replace("#", "[#]")
    )


# ---------------------------------------------------------------------------
# Cliente
# ---------------------------------------------------------------------------
def _row_to_cliente_dict(row: Any) -> Optional[dict]:
    """Convierte una fila EF2CLIENTES a dict compatible con ClienteOut.

    Inyecta `id`, `created_at`, `updated_at` que el .mdb no tiene
    (None para timestamps; los templates aceptan Optional).
    """
    base = mappers.mdb_cliente_to_dict(row)
    if not base:
        return None
    f2codcli = getattr(row, "F2CODCLI", None)
    base["id"] = _f2codcli_to_int(f2codcli)
    # Campos extra que ClienteOut espera y el mapper no rellena
    base.setdefault("distrito", None)
    base.setdefault("provincia", None)
    base.setdefault("departamento", None)
    base.setdefault("created_at", None)
    base.setdefault("updated_at", None)
    return base


class ClienteRepoMDB:
    """Lectura de clientes desde EF2CLIENTES."""

    # Columnas mínimas que necesitamos del MDB. Las demás existen en AFISCA
    # pero no aportan a la vista actual.
    _COLS = (
        "F2CODCLI", "F2NEWRUC", "F2NOMCLI", "F2DOCCLI", "F2DIRCLI",
        "F2TELCLI", "F2TIPDOC", "F2EMAIL", "F2DISCLI", "F2CELULAR",
    )

    @staticmethod
    def listar(
        q: Optional[str] = None,
        tipo_documento: Optional[str] = None,
        activo: Optional[bool] = None,  # MDB no tiene flag activo, se ignora
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[dict], int]:
        """Devuelve (items, total_count).

        - q: busca por F2NOMCLI, F2DOCCLI, F2NEWRUC (LIKE %q%).
        - tipo_documento: Factura-mdb usa "1","6","4","7" (cat 06).
          Como SIAP guarda F2TIPDOC con códigos heterogéneos ('J','N',...),
          este filtro lo aplicamos en Python tras mapear con
          `normalizar_tipo_documento_mdb()` para no perder filas.
        """
        # WHERE para Access (búsqueda con LIKE)
        where_parts: list[str] = []
        params: list[Any] = []
        if q:
            safe = _escape_like(q.strip())
            wild = f"%{safe}%"
            where_parts.append(
                "(F2NOMCLI LIKE ? OR F2DOCCLI LIKE ? OR F2NEWRUC LIKE ?)"
            )
            params += [wild, wild, wild]
        where_sql = (" WHERE " + " AND ".join(where_parts)) if where_parts else ""

        cols_sql = ", ".join(ClienteRepoMDB._COLS)

        with mdb_cursor() as cur:
            # Total (sin filtro tipo_documento — se filtra después en Python)
            cur.execute(f"SELECT COUNT(*) FROM EF2CLIENTES{where_sql}", *params)
            total_raw = int(cur.fetchone()[0] or 0)

            # Datos. Si tipo_documento no está, podemos paginar en SQL con
            # TOP {limit+offset}. Si sí está, traemos hasta un cap mayor y
            # filtramos en Python (Access no permite paginar fácilmente).
            top_n = (limit + offset) if not tipo_documento else max(
                (limit + offset) * 4, 1000
            )
            cur.execute(
                f"SELECT TOP {top_n} {cols_sql} FROM EF2CLIENTES"
                f"{where_sql} ORDER BY F2NOMCLI",
                *params,
            )
            rows = cur.fetchall()

        items: list[dict] = []
        for row in rows:
            d = _row_to_cliente_dict(row)
            if not d:
                continue
            if tipo_documento and d.get("tipo_documento") != tipo_documento:
                continue
            items.append(d)

        # Calcular total real cuando filtramos por tipo en Python.
        # Para Sprint 1 esto es aproximado: si el cap no alcanzó, total
        # puede subestimar. Lo dejamos documentado en FACTURA_MDB_MDB_LAB.md.
        if tipo_documento:
            total = len(items)  # mejor aproximación que tenemos
            paged = items[offset:offset + limit]
            return paged, total

        # Sin filtro tipo_documento: total = COUNT(*) y paginamos en Python.
        paged = items[offset:offset + limit]
        return paged, total_raw

    @staticmethod
    def obtener(cliente_id: int) -> Optional[dict]:
        """Devuelve un cliente por su `id` sintético.

        Como F2CODCLI es VARCHAR (p.ej. '0001'), buscamos por su int
        directo y, si falla, recorremos buscando por synth_id (raro,
        sólo si F2CODCLI tiene letras).
        """
        cols_sql = ", ".join(ClienteRepoMDB._COLS)
        with mdb_cursor() as cur:
            # Intento 1: F2CODCLI numérico igual al id (caso 99% AFISCA)
            try:
                # Access no acepta cmp directo entre int y string en F2CODCLI,
                # convertimos via VAL().
                cur.execute(
                    f"SELECT {cols_sql} FROM EF2CLIENTES "
                    "WHERE VAL(F2CODCLI) = ?",
                    int(cliente_id),
                )
                row = cur.fetchone()
                if row:
                    d = _row_to_cliente_dict(row)
                    if d and d["id"] == cliente_id:
                        return d
            except Exception as e:  # noqa: BLE001
                logger.debug("Lookup numérico falló: %s", e)

            # Intento 2 (fallback raro): recorrer todo y matchear por synth.
            cur.execute(f"SELECT {cols_sql} FROM EF2CLIENTES")
            for row in cur.fetchall():
                d = _row_to_cliente_dict(row)
                if d and d["id"] == cliente_id:
                    return d
        return None

    @staticmethod
    def buscar(q: str, limit: int = 20) -> list[dict]:
        """Autocomplete: top N por documento o razón social."""
        items, _ = ClienteRepoMDB.listar(q=q, limit=limit, offset=0)
        return items


# ---------------------------------------------------------------------------
# Producto
# ---------------------------------------------------------------------------
def _row_to_producto_dict(row: Any) -> Optional[dict]:
    """Convierte una fila TBVENTA_DET (deduplicada) a dict ProductoOut."""
    base = mappers.mdb_producto_unico_to_dict(
        codigo=getattr(row, "F5CODPRO", None),
        descripcion=getattr(row, "F5NOMPRO", None),
        unidad_medida=getattr(row, "F7CODMED", None),
        valor_unitario=getattr(row, "F3VALVTAUNIT", None),
        afecto=getattr(row, "F3AFECTO", None),
    )
    if not base:
        return None
    base["id"] = _synth_id("prod", base["codigo"])
    base.setdefault("notas", None)
    base.setdefault("created_at", None)
    base.setdefault("updated_at", None)
    return base


class ProductoRepoMDB:
    """Lectura de productos: combina FMDB_PRODUCTOS + F5CODPRO de TBVENTA_DET.

    SIAP no tiene tabla maestra de productos; la inferimos del histórico
    de ventas (un código por producto, descripción del último uso visto).

    Sprint 2: si existe la tabla auxiliar FMDB_PRODUCTOS, sus filas
    tienen prioridad sobre las inferidas de TBVENTA_DET (mismo código).
    """

    @staticmethod
    def _cargar_factura_mdb_productos() -> dict[str, dict]:
        """Trae productos de FMDB_PRODUCTOS (creados desde Factura-mdb).

        Si la tabla no existe (no se ha creado ningún producto desde
        Factura-mdb todavía), devuelve dict vacío sin lanzar excepción.
        """
        productos: dict[str, dict] = {}
        try:
            with mdb_cursor() as cur:
                cur.execute(
                    "SELECT codigo, descripcion, unidad_medida, valor_unitario, "
                    "moneda, tipo_afectacion_igv, incluye_igv, notas, activo "
                    "FROM FMDB_PRODUCTOS"
                )
                rows = cur.fetchall()
            for r in rows:
                cod = (r[0] or "").strip()
                if not cod:
                    continue
                productos[cod] = {
                    "id": _synth_id("prod", cod),
                    "codigo": cod,
                    "descripcion": (r[1] or "").strip(),
                    "unidad_medida": (r[2] or "NIU").strip(),
                    "valor_unitario": float(r[3] or 0.0),
                    "moneda": (r[4] or "PEN").strip(),
                    "tipo_afectacion_igv": (r[5] or "10").strip(),
                    "incluye_igv": bool(r[6]) if r[6] is not None else False,
                    "notas": r[7],
                    "activo": bool(r[8]) if r[8] is not None else True,
                    "created_at": None,
                    "updated_at": None,
                }
        except Exception as e:  # noqa: BLE001
            logger.debug("FMDB_PRODUCTOS no disponible: %s", e)
        return productos

    @staticmethod
    def _cargar_todos() -> dict[str, dict]:
        """Trae productos del MDB indexados por código.

        Combina dos fuentes:
        1. F5CODPRO únicos en TBVENTA_DET (productos históricos SIAP).
        2. FMDB_PRODUCTOS (creados desde Factura-mdb Sprint 2).
        Las entradas de #2 tienen prioridad sobre #1 (sobreescriben).
        """
        with mdb_cursor() as cur:
            cur.execute(
                "SELECT F5CODPRO, F5NOMPRO, F7CODMED, F3VALVTAUNIT, F3AFECTO "
                "FROM TBVENTA_DET WHERE F5CODPRO IS NOT NULL"
            )
            rows = cur.fetchall()

        # Deduplicar por código (último gana, igual que el importer)
        productos: dict[str, dict] = {}
        for r in rows:
            d = _row_to_producto_dict(r)
            if d:
                productos[d["codigo"]] = d

        # Merge: FMDB_PRODUCTOS tiene prioridad
        propios = ProductoRepoMDB._cargar_factura_mdb_productos()
        productos.update(propios)
        return productos

    @staticmethod
    def obtener_por_codigo(codigo: str) -> Optional[dict]:
        """Busca un producto por código exacto."""
        productos = ProductoRepoMDB._cargar_todos()
        return productos.get(codigo)

    @staticmethod
    def listar(
        q: Optional[str] = None,
        activo: Optional[bool] = None,  # MDB no tiene flag activo, se ignora
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[dict], int]:
        productos = list(ProductoRepoMDB._cargar_todos().values())

        if q:
            qn = q.strip().lower()
            productos = [
                p for p in productos
                if qn in (p.get("codigo") or "").lower()
                or qn in (p.get("descripcion") or "").lower()
            ]

        productos.sort(key=lambda p: (p.get("descripcion") or "").lower())
        total = len(productos)
        paged = productos[offset:offset + limit]
        return paged, total

    @staticmethod
    def obtener(producto_id: int) -> Optional[dict]:
        for p in ProductoRepoMDB._cargar_todos().values():
            if p["id"] == producto_id:
                return p
        return None

    @staticmethod
    def buscar(q: str, limit: int = 20) -> list[dict]:
        items, _ = ProductoRepoMDB.listar(q=q, limit=limit, offset=0)
        return items


# ---------------------------------------------------------------------------
# Comprobante
# ---------------------------------------------------------------------------
def _row_to_comprobante_dict(row: Any) -> Optional[dict]:
    """Convierte una fila TBVENTA_CAB a dict ComprobanteListItem/Out."""
    base = mappers.mdb_comprobante_to_dict(row)
    if not base:
        return None
    base["id"] = _synth_id(
        "comp", base["tipo_documento"], base["serie"], base["correlativo"]
    )
    # Campos opcionales que ComprobanteOut/ListItem esperan
    base.setdefault("cliente_id", None)
    base.setdefault("created_at", None)
    base.setdefault("updated_at", None)
    base.setdefault("xml_path", None)
    base.setdefault("cdr_path", None)
    base.setdefault("pdf_path", None)
    base.setdefault("qr_data", None)
    base.setdefault("monto_letras", None)
    base.setdefault("forma_pago_json", None)
    base.setdefault("hora_emision", None)
    base.setdefault("fecha_vencimiento", None)
    base.setdefault("motivo_nc_codigo", None)
    base.setdefault("motivo_nc_descripcion", None)
    base.setdefault("detraccion_cta_bn", None)
    base.setdefault("percepcion_pct", None)
    base.setdefault("percepcion_monto", None)
    return base


def _row_to_detalle_dict(row: Any, comprobante_id: int) -> Optional[dict]:
    """Convierte una fila TBVENTA_DET a dict ComprobanteDetalleOut."""
    base = mappers.mdb_detalle_to_dict(row)
    if not base:
        return None
    base["id"] = _synth_id(
        "det", comprobante_id, base.get("orden", 0),
        base.get("codigo") or "", base.get("descripcion") or "",
    )
    base["comprobante_id"] = comprobante_id
    base.setdefault("producto_id", None)
    return base


class ComprobanteRepoMDB:
    """Lectura de comprobantes desde TBVENTA_CAB + TBVENTA_DET."""

    _COLS_CAB = (
        "F4TIPODOCU", "F4SERDOC", "F4NUMDOC", "F4FECEMI", "F4TIPMON",
        "F4TIPCAM", "F2RUCCLI", "F2NOMCLI", "F2DIRCLI",
        "F4SUBTOT", "F4TOTIGV", "F4SUBFACINAF", "F4MONTOEXONERADO",
        "F4TOTFAC", "F4ESTNUL", "F4ESTEMI", "F4ENVIADO", "F4CDR",
        "F4CDRFECHA", "F4CODEHASH", "F4FORPAG",
        "F4DETRACCIONAPLICA", "F4DETRACCIONPORC", "F4DETRACCIONMONTO",
        "F4TIPREF", "F4SERGUI", "F4NUMGUI", "F2CODCLI",
    )

    _COLS_DET = (
        "F4TIPODOCU", "F4SERDOC", "F4NUMDOC", "F5CODPRO", "F5NOMPRO",
        "F7CODMED", "F3CANPRO", "F3VALVTAUNIT", "F3VALVTA",
        "F3IGV", "F3PREVTA", "F3AFECTO", "F3ITEM",
    )

    @staticmethod
    def listar(
        filtros: Optional[dict] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[dict], int]:
        """Lista comprobantes con filtros opcionales.

        filtros (todos opcionales):
          - tipo_documento: '01'|'03'|'07'|'08'
          - serie: 4 chars
          - estado: 'P'|'A'|'R'|'B'  (calculado en Python por _decidir_estado)
          - fecha_desde, fecha_hasta: date
          - cliente_id: int (sintético, hay que mapear a F2CODCLI)
          - q: busca en numero_completo, cliente_numero_doc, cliente_razon_social
        """
        f = filtros or {}
        where_parts: list[str] = []
        params: list[Any] = []

        if f.get("tipo_documento"):
            where_parts.append("F4TIPODOCU = ?")
            params.append(str(f["tipo_documento"]))
        if f.get("serie"):
            where_parts.append("F4SERDOC = ?")
            params.append(str(f["serie"]).upper()[:4])
        if f.get("fecha_desde"):
            where_parts.append("F4FECEMI >= ?")
            params.append(f["fecha_desde"])
        if f.get("fecha_hasta"):
            where_parts.append("F4FECEMI <= ?")
            params.append(f["fecha_hasta"])
        if f.get("q"):
            safe = _escape_like(str(f["q"]).strip())
            wild = f"%{safe}%"
            where_parts.append(
                "(F2RUCCLI LIKE ? OR F2NOMCLI LIKE ? OR F4NUMDOC LIKE ?)"
            )
            params += [wild, wild, wild]

        where_sql = (" WHERE " + " AND ".join(where_parts)) if where_parts else ""
        cols_sql = ", ".join(ComprobanteRepoMDB._COLS_CAB)

        # Estado y cliente_id (sintético) los aplicamos en Python.
        with mdb_cursor() as cur:
            cur.execute(
                f"SELECT COUNT(*) FROM TBVENTA_CAB{where_sql}", *params
            )
            total_raw = int(cur.fetchone()[0] or 0)

            # Si filtramos por estado o cliente_id, traemos un cap mayor y
            # filtramos en Python.
            need_python_filter = bool(f.get("estado") or f.get("cliente_id"))
            top_n = (
                max((limit + offset) * 4, 2000)
                if need_python_filter
                else (limit + offset)
            )

            cur.execute(
                f"SELECT TOP {top_n} {cols_sql} FROM TBVENTA_CAB{where_sql} "
                "ORDER BY F4FECEMI DESC, F4SERDOC, F4NUMDOC DESC",
                *params,
            )
            rows = cur.fetchall()

        items: list[dict] = []
        for row in rows:
            d = _row_to_comprobante_dict(row)
            if not d:
                continue
            if f.get("estado") and d.get("estado") != f["estado"]:
                continue
            if f.get("cliente_id"):
                # cliente_id sintético: derivado de F2CODCLI
                cli_id_row = _f2codcli_to_int(getattr(row, "F2CODCLI", None))
                if cli_id_row != f["cliente_id"]:
                    continue
            items.append(d)

        if need_python_filter:
            total = len(items)
            return items[offset:offset + limit], total

        return items[offset:offset + limit], total_raw

    @staticmethod
    def obtener(comprobante_id: int) -> Optional[dict]:
        """Devuelve un comprobante con sus detalles, o None."""
        cols_sql = ", ".join(ComprobanteRepoMDB._COLS_CAB)
        with mdb_cursor() as cur:
            # No hay PK numérica; recorremos hasta encontrar match por synth_id.
            cur.execute(f"SELECT {cols_sql} FROM TBVENTA_CAB")
            target = None
            for row in cur.fetchall():
                d = _row_to_comprobante_dict(row)
                if d and d["id"] == comprobante_id:
                    target = (d, row)
                    break

            if not target:
                return None

            comp_dict, comp_row = target
            tipo = getattr(comp_row, "F4TIPODOCU", None)
            serie = getattr(comp_row, "F4SERDOC", None)
            numero = getattr(comp_row, "F4NUMDOC", None)

            # Cargar detalles del mismo comprobante
            cols_det_sql = ", ".join(ComprobanteRepoMDB._COLS_DET)
            cur.execute(
                f"SELECT {cols_det_sql} FROM TBVENTA_DET "
                "WHERE F4TIPODOCU = ? AND F4SERDOC = ? AND F4NUMDOC = ? "
                "ORDER BY F3ITEM",
                tipo, serie, numero,
            )
            det_rows = cur.fetchall()

        detalles: list[dict] = []
        for r in det_rows:
            d = _row_to_detalle_dict(r, comprobante_id)
            if d:
                detalles.append(d)
        comp_dict["detalles"] = detalles
        return comp_dict

    @staticmethod
    def proximo_correlativo(serie: str, tipo_documento: Optional[str] = None) -> dict:
        """Devuelve el próximo correlativo libre para una serie."""
        where = ["F4SERDOC = ?"]
        params: list[Any] = [serie.upper()[:4]]
        if tipo_documento:
            where.append("F4TIPODOCU = ?")
            params.append(tipo_documento)
        where_sql = " WHERE " + " AND ".join(where)
        with mdb_cursor() as cur:
            cur.execute(
                f"SELECT MAX(VAL(F4NUMDOC)) FROM TBVENTA_CAB{where_sql}",
                *params,
            )
            row = cur.fetchone()
            ultimo = int(row[0] or 0) if row and row[0] is not None else 0
        return {
            "serie": serie.upper()[:4],
            "proximo_correlativo": ultimo + 1,
            "ultimo_emitido": ultimo or None,
        }

    @staticmethod
    def listar_series() -> list[dict]:
        """Devuelve series existentes con su último correlativo."""
        with mdb_cursor() as cur:
            cur.execute(
                "SELECT F4SERDOC, MAX(VAL(F4NUMDOC)) FROM TBVENTA_CAB "
                "WHERE F4SERDOC IS NOT NULL GROUP BY F4SERDOC"
            )
            rows = cur.fetchall()
        items = [
            {"serie": (r[0] or "").strip(), "ultimo": int(r[1] or 0)}
            for r in rows if r[0] and (r[0] or "").strip()
        ]
        items.sort(key=lambda x: x["serie"])
        return items


# ---------------------------------------------------------------------------
# Empresa — datos hardcoded + RUC real desde EF2ALMACENES (única fuente
# de RUC en el .mdb investigado para AFISCA).
# ---------------------------------------------------------------------------
class EmpresaRepoMDB:
    """Lectura de datos de empresa.

    El .mdb de SIAP NO tiene tabla de empresa formal. La única columna
    con el RUC operativo de la empresa es `EF2ALMACENES.F2RUCALM` (cuyo
    F2NOMALM es el nombre del almacén, NO la razón social).

    Estrategia Sprint 2:
    - Leemos el RUC real desde EF2ALMACENES (TOP 1).
    - Razón social, dirección y datos SUNAT siguen viniendo del
      hardcoded `_FAKE_EMPRESA_MDB` definido en main.py — sólo el RUC
      se sincroniza con el archivo real.
    - Si no se puede leer EF2ALMACENES (mdb corrupto, tabla vacía),
      caemos al hardcoded para no romper.
    """

    @staticmethod
    def obtener() -> dict:
        """Devuelve dict combinando hardcoded + RUC real del .mdb."""
        from ...main import _FAKE_EMPRESA_MDB  # import diferido (circular)
        base = {
            c: getattr(_FAKE_EMPRESA_MDB, c)
            for c in (
                "id", "ruc", "razon_social", "nombre_comercial", "direccion",
                "ubigeo", "departamento", "provincia", "distrito", "telefono",
                "email", "sitio_web", "logo_path", "sol_user", "sol_pass",
                "sunat_env", "certificado_path", "certificado_pass",
                "certificado_vence", "created_at", "updated_at",
            )
        }
        # Intentar leer RUC real
        try:
            with mdb_cursor() as cur:
                cur.execute(
                    "SELECT TOP 1 F2RUCALM, F2NOMALM, F2DIRALM "
                    "FROM EF2ALMACENES WHERE F2RUCALM IS NOT NULL "
                    "AND LEN(F2RUCALM) = 11"
                )
                row = cur.fetchone()
                if row:
                    ruc_real = (row[0] or "").strip()
                    if ruc_real and ruc_real.isdigit() and len(ruc_real) == 11:
                        base["ruc"] = ruc_real
                        # nombre_comercial puede sobreescribirse si vino algo útil
                        nom = (row[1] or "").strip()
                        if nom and nom not in ("ECONOMATO",):
                            base["nombre_comercial"] = nom[:100]
        except Exception as e:  # noqa: BLE001
            logger.debug("EmpresaRepoMDB: fallback al hardcoded (%s)", e)
        return base
