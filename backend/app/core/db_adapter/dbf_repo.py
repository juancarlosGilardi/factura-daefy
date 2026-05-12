"""Repositorios de lectura desde DBF (GECOPE / Visual FoxPro 9).

Espejo de `mdb_repo.py` y `sqlite_repo.py` pero leyendo archivos `.dbf`
del ERP GECOPE con `dbfread` (encoding cp1252, memos .fpt).

Diferencias clave vs SIAP MDB:
    - Sin SQL: filtramos/paginamos en Python al recorrer el iterador.
    - `cliente.CODIGO` es el documento del cliente (no F2CODCLI).
    - `ventas.CODIGO` es int autoincremental; lo usamos como FK al cargar
      detalles desde `ventas_detalle.dbf`.
    - `articulo.dbf` SÍ tiene una tabla maestra de productos (a diferencia
      de SIAP que los infiere de TBVENTA_DET).
    - Empresa: combina `datos_compañia.dbf` (RUC, razón social, ubigeo)
      con `config.json` (certificado, logo, credenciales SUNAT).

dbfread devuelve filas como `OrderedDict` con keys en uppercase. Los
mappers (`backend/app/services/dbf_importer/mappers.py`) tienen helper
`_g()` que acepta tanto dict como acceso por atributo.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Optional

# Nota: ya no usamos dbfread directamente — el helper _dbf() abajo usa la
# libreria `dbf` (Ethan Furman) que es mas permisiva con FPTs corruptos.

from ...services.dbf_importer import mappers as dbf_mappers
from . import get_dbf_path
from .mdb_repo import _synth_id

logger = logging.getLogger("factura_mdb.dbf.repo")

DBF_ENC = "cp1252"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class _DbfWrapper:
    """Wrapper iterable que devuelve dicts (compatible con dbfread.DBF).

    Usa la libreria `dbf` (Ethan Furman) que es mas permisiva con FPTs
    corruptos o placeholder y con campos tipo G (general/picture).
    dbfread valida estrictamente cada bloque memo y falla en setups donde
    el FPT no existe o esta truncado.
    """

    def __init__(self, path: Path):
        import dbf as _dbflib
        self._lib = _dbflib
        self._path = path
        self._field_names: list[str] = []

    @property
    def field_names(self) -> list[str]:
        if not self._field_names:
            t = self._lib.Table(str(self._path), codepage=DBF_ENC)
            t.open(mode=self._lib.READ_ONLY)
            self._field_names = list(t.field_names)
            t.close()
        return self._field_names

    def __iter__(self):
        t = self._lib.Table(str(self._path), codepage=DBF_ENC)
        t.open(mode=self._lib.READ_ONLY)
        try:
            for rec in t:
                d = {}
                for f in t.field_names:
                    try:
                        val = rec[f]
                        # campos G/M memo -> None si no se pueden leer
                        if isinstance(val, (bytes, bytearray)):
                            val = None
                        if isinstance(val, str):
                            val = val.rstrip()
                    except Exception:  # noqa: BLE001
                        val = None
                    # Tambien exponemos el nombre en MAYUSCULAS para
                    # compatibilidad con dbfread (lowernames=False)
                    d[f] = val
                    d[f.upper()] = val
                yield d
        finally:
            t.close()


def _dbf(name: str):
    """Abre un DBF de GECOPE — devuelve iterable de dicts.

    Usa la libreria `dbf` (no `dbfread`) porque es mas permisiva con FPTs
    placeholder o campos memo G corruptos. Compatibilidad con codigo
    que esperaba dbfread.DBF: itera dicts y expone `.field_names`.
    """
    path: Path = get_dbf_path() / name
    if not path.exists():
        for child in path.parent.iterdir():
            if child.name.lower() == name.lower():
                path = child
                break
    return _DbfWrapper(path)


def _q_match(text: str, q: str) -> bool:
    """Match LIKE %q% case-insensitive en Python."""
    return q.lower() in (text or "").lower()


# ---------------------------------------------------------------------------
# Cliente — cliente.dbf
# ---------------------------------------------------------------------------

class ClienteRepoDBF:
    """Lectura de clientes desde cliente.dbf."""

    @staticmethod
    def _iter_all() -> list[dict]:
        """Carga TODOS los clientes (mapeados) en memoria.

        Con 30k registros y ~68 columnas, esto pesa ~5-10MB. Aceptable
        para uso single-user (no caching adicional). Si crece, evaluar
        cache TTL.
        """
        out: list[dict] = []
        for row in _dbf("cliente.dbf"):
            d = dbf_mappers.dbf_cliente_to_dict(row)
            if d:
                out.append(d)
        return out

    @staticmethod
    def listar(
        q: Optional[str] = None,
        tipo_documento: Optional[str] = None,
        activo: Optional[bool] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[dict], int]:
        items = ClienteRepoDBF._iter_all()

        if q:
            qs = q.strip()
            items = [
                c for c in items
                if _q_match(c.get("razon_social") or "", qs)
                or _q_match(c.get("numero_documento") or "", qs)
            ]
        if tipo_documento:
            items = [c for c in items if c.get("tipo_documento") == tipo_documento]
        if activo is not None:
            items = [c for c in items if bool(c.get("activo")) == bool(activo)]

        items.sort(key=lambda c: (c.get("razon_social") or "").lower())
        total = len(items)
        return items[offset:offset + limit], total

    @staticmethod
    def obtener(cliente_id: int) -> Optional[dict]:
        """Devuelve un cliente por su id sintético."""
        for row in _dbf("cliente.dbf"):
            d = dbf_mappers.dbf_cliente_to_dict(row)
            if d and d["id"] == int(cliente_id):
                return d
        return None

    @staticmethod
    def obtener_por_codigo(codigo: str) -> Optional[dict]:
        """Búsqueda exacta por CODIGO (documento) — más rápida que `obtener`."""
        cod_target = (codigo or "").strip()
        if not cod_target:
            return None
        for row in _dbf("cliente.dbf"):
            if (row.get("CODIGO") or "").strip() == cod_target:
                return dbf_mappers.dbf_cliente_to_dict(row)
        return None

    @staticmethod
    def buscar(q: str, limit: int = 20) -> list[dict]:
        items, _ = ClienteRepoDBF.listar(q=q, limit=limit, offset=0)
        return items


# ---------------------------------------------------------------------------
# Producto — articulo.dbf
# ---------------------------------------------------------------------------

class ProductoRepoDBF:
    """Lectura de productos desde articulo.dbf.

    A diferencia de SIAP (que los infiere de TBVENTA_DET), GECOPE tiene
    una tabla maestra real. Por simplicidad cargamos todo en memoria
    (12k filas ≈ 2-3MB).
    """

    @staticmethod
    def _cargar_todos() -> dict[str, dict]:
        productos: dict[str, dict] = {}
        for row in _dbf("articulo.dbf"):
            d = dbf_mappers.dbf_producto_to_dict(row)
            if d:
                productos[d["codigo"]] = d
        return productos

    @staticmethod
    def listar(
        q: Optional[str] = None,
        activo: Optional[bool] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[dict], int]:
        productos = list(ProductoRepoDBF._cargar_todos().values())

        if q:
            qn = q.strip().lower()
            productos = [
                p for p in productos
                if qn in (p.get("codigo") or "").lower()
                or qn in (p.get("descripcion") or "").lower()
            ]
        if activo is not None:
            productos = [p for p in productos if bool(p.get("activo")) == bool(activo)]

        productos.sort(key=lambda p: (p.get("descripcion") or "").lower())
        total = len(productos)
        return productos[offset:offset + limit], total

    @staticmethod
    def obtener(producto_id: int) -> Optional[dict]:
        for p in ProductoRepoDBF._cargar_todos().values():
            if p["id"] == int(producto_id):
                return p
        return None

    @staticmethod
    def obtener_por_codigo(codigo: str) -> Optional[dict]:
        return ProductoRepoDBF._cargar_todos().get(codigo)

    @staticmethod
    def buscar(q: str, limit: int = 20) -> list[dict]:
        items, _ = ProductoRepoDBF.listar(q=q, limit=limit, offset=0)
        return items


# ---------------------------------------------------------------------------
# Comprobante — ventas.dbf + ventas_detalle.dbf
# ---------------------------------------------------------------------------

class ComprobanteRepoDBF:
    """Lectura de comprobantes desde ventas.dbf + ventas_detalle.dbf."""

    @staticmethod
    def _cargar_cab() -> list[tuple[dict, Any]]:
        """Devuelve [(dict_mapeado, codigo_gecope), …] ordenado por fecha desc."""
        out: list[tuple[dict, Any]] = []
        for row in _dbf("ventas.dbf"):
            d = dbf_mappers.dbf_comprobante_to_dict(row)
            if d:
                out.append((d, d.get("_gecope_codigo")))
        # Orden: fecha_emision DESC, serie, correlativo DESC
        out.sort(
            key=lambda t: (
                t[0].get("fecha_emision") or "",
                t[0].get("serie") or "",
                -(t[0].get("correlativo") or 0),
            ),
            reverse=False,
        )
        # Convertir a orden descendente real por fecha (reverse trick)
        out.sort(
            key=lambda t: (
                str(t[0].get("fecha_emision") or "9999-12-31"),
            ),
            reverse=True,
        )
        return out

    @staticmethod
    def listar(
        filtros: Optional[dict] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[dict], int]:
        f = filtros or {}
        rows = ComprobanteRepoDBF._cargar_cab()
        items: list[dict] = []

        for d, _cod in rows:
            if f.get("tipo_documento") and d.get("tipo_documento") != f["tipo_documento"]:
                continue
            if f.get("serie") and d.get("serie") != str(f["serie"]).upper()[:4]:
                continue
            if f.get("estado") and d.get("estado") != f["estado"]:
                continue
            if f.get("fecha_desde"):
                fd = f["fecha_desde"]
                fe = d.get("fecha_emision")
                if fe and str(fe) < str(fd):
                    continue
            if f.get("fecha_hasta"):
                fh = f["fecha_hasta"]
                fe = d.get("fecha_emision")
                if fe and str(fe) > str(fh):
                    continue
            if f.get("q"):
                qs = str(f["q"]).strip().lower()
                hay = (
                    qs in (d.get("numero_completo") or "").lower()
                    or qs in (d.get("cliente_numero_doc") or "").lower()
                    or qs in (d.get("cliente_razon_social") or "").lower()
                )
                if not hay:
                    continue
            if f.get("cliente_id"):
                # cliente_id sintético desde CODIGO del cliente. Para
                # matchear, computamos synth_id sobre cliente_numero_doc.
                target = int(f["cliente_id"])
                # numero_documento del cliente sin padding GECOPE
                doc = d.get("cliente_numero_doc") or ""
                # Probar variantes con/sin padding 11
                candidates = {doc, doc.lstrip("0"), doc.zfill(11)}
                if not any(
                    _synth_id("cli_dbf", c) == target for c in candidates if c
                ):
                    continue

            items.append(d)

        total = len(items)
        return items[offset:offset + limit], total

    @staticmethod
    def obtener(comprobante_id: int) -> Optional[dict]:
        """Devuelve comprobante con detalles, o None."""
        cid = int(comprobante_id)
        target_cod: Any = None
        target_dict: Optional[dict] = None

        for row in _dbf("ventas.dbf"):
            d = dbf_mappers.dbf_comprobante_to_dict(row)
            if d and d["id"] == cid:
                target_dict = d
                target_cod = d.get("_gecope_codigo")
                break

        if not target_dict:
            return None

        # Cargar detalles por FK ventas_detalle.CODIGO = ventas.CODIGO
        detalles: list[dict] = []
        orden = 1
        for row in _dbf("ventas_detalle.dbf"):
            row_cod = row.get("CODIGO")
            if row_cod == target_cod or str(row_cod) == str(target_cod):
                # Saltar líneas anuladas
                if bool(row.get("ANULADO")) or bool(row.get("REGISTRO_A")):
                    continue
                d = dbf_mappers.dbf_detalle_to_dict(row, orden=orden)
                if d:
                    d["id"] = _synth_id(
                        "det_dbf", cid, orden,
                        d.get("codigo") or "",
                        d.get("descripcion") or "",
                    )
                    d["comprobante_id"] = cid
                    d["producto_id"] = (
                        _synth_id("prod_dbf", d["codigo"]) if d.get("codigo") else None
                    )
                    detalles.append(d)
                    orden += 1

        target_dict["detalles"] = detalles
        return target_dict

    @staticmethod
    def proximo_correlativo(
        serie: str, tipo_documento: Optional[str] = None
    ) -> dict:
        """Devuelve el próximo correlativo libre para una serie."""
        serie_n = serie.upper()[:4]
        ultimo = 0
        gecope_doc = (
            dbf_mappers.SUNAT_A_GECOPE_DOC.get(tipo_documento)
            if tipo_documento else None
        )
        for row in _dbf("ventas.dbf"):
            if (row.get("SER_DOCUME") or "").strip() != serie_n:
                continue
            if gecope_doc and (row.get("DOCUMENTO") or "").strip() != gecope_doc:
                continue
            num_raw = (row.get("NUM_DOCUME") or "0").strip()
            try:
                n = int(num_raw)
            except (TypeError, ValueError):
                continue
            if n > ultimo:
                ultimo = n
        return {
            "serie": serie_n,
            "proximo_correlativo": ultimo + 1,
            "ultimo_emitido": ultimo or None,
        }

    @staticmethod
    def listar_series() -> list[dict]:
        """Devuelve series existentes con su último correlativo."""
        ultimos: dict[str, int] = {}
        for row in _dbf("ventas.dbf"):
            s = (row.get("SER_DOCUME") or "").strip()
            if not s:
                continue
            try:
                n = int((row.get("NUM_DOCUME") or "0").strip())
            except (TypeError, ValueError):
                continue
            if n > ultimos.get(s, 0):
                ultimos[s] = n
        items = [{"serie": s, "ultimo": n} for s, n in ultimos.items()]
        items.sort(key=lambda x: x["serie"])
        return items

    @staticmethod
    def top_productos(
        desde: Optional[Any] = None,
        hasta: Optional[Any] = None,
        limit: int = 10,
    ) -> list[dict]:
        """Top productos por ingresos (suma de IMPORTE de ventas_detalle).

        Filtra por fecha_emision del comprobante padre (desde/hasta) y
        descarta ventas con REGISTRO_A o ANULADO.
        """
        # Mapa CODIGO_ventas → fecha_emision
        fechas: dict[Any, Any] = {}
        anulados: set[Any] = set()
        for row in _dbf("ventas.dbf"):
            cod = row.get("CODIGO")
            if cod is None:
                continue
            if bool(row.get("REGISTRO_A")) or bool(row.get("DATA_BAJA")):
                anulados.add(cod)
                continue
            fechas[cod] = row.get("FECHA_EMIS")

        agreg: dict[str, dict] = {}
        for row in _dbf("ventas_detalle.dbf"):
            cod = row.get("CODIGO")
            if cod is None or cod in anulados:
                continue
            fe = fechas.get(cod)
            if desde and fe and str(fe) < str(desde):
                continue
            if hasta and fe and str(fe) > str(hasta):
                continue
            if bool(row.get("ANULADO")) or bool(row.get("REGISTRO_A")):
                continue
            nombre = (row.get("NOMBRE_ART") or "").strip() or "S/D"
            cant = float(row.get("CANTIDAD") or 0)
            imp = float(row.get("IMPORTE") or 0)
            slot = agreg.setdefault(
                nombre, {"producto": nombre, "cantidad": 0.0, "total": 0.0}
            )
            slot["cantidad"] += cant
            slot["total"] += imp

        out = sorted(agreg.values(), key=lambda x: x["total"], reverse=True)[:limit]
        for o in out:
            o["cantidad"] = round(o["cantidad"], 2)
            o["total"] = round(o["total"], 2)
        return out


# ---------------------------------------------------------------------------
# Empresa — datos_compañia.dbf + config.json
# ---------------------------------------------------------------------------

class EmpresaRepoDBF:
    """Datos de empresa desde datos_compañia.dbf, complementados con config.json.

    GECOPE guarda RUC, razón social, dirección y ubigeo en el DBF.
    El config.json provee certificado, credenciales SUNAT y logo_path
    (porque el LOGO del DBF es OLE binario que no podemos parsear).
    """

    @staticmethod
    def obtener() -> dict:
        """Devuelve dict con la misma forma que EmpresaRepoMDB.obtener().

        Mergea:
            1. Datos persistidos en `datos_compañia.dbf` (RUC, razón
               social, dirección, ubigeo, contacto).
            2. config.json (certificado, SUNAT env, logo path, etc.)
            3. _FAKE_EMPRESA_MDB del main como fallback total.
        """
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

        # Sobrescribir con datos del DBF si existen
        try:
            t = _dbf("datos_compañia.dbf")
            for row in t:
                d = dbf_mappers.dbf_empresa_to_dict(row)
                # Solo sobrescribimos campos no vacíos
                for k, v in d.items():
                    if v and k in base:
                        base[k] = v
                break  # única fila
        except Exception as exc:  # noqa: BLE001
            logger.warning("No se pudo leer datos_compañia.dbf: %s", exc)

        return base
