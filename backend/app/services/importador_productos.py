"""Importador de productos NUEVOS desde DBF de empresa matriz (IDIVSA)
hacia el DBF local de Daefy.

Caso de uso:
    Daefy es subsidiaria de IDIVSA. IDIVSA mantiene el catálogo maestro
    de productos en su propio `articulo.dbf`. Cuando IDIVSA agrega
    productos nuevos, Daefy quiere copiarlos a SU `articulo.dbf` local.

Flujo:
    1. Leer `articulo.dbf` de la matriz (ruta indicada por el usuario;
       puede estar en network share o copia local).
    2. Comparar con `articulo.dbf` local — la llave de comparación es
       el campo `CODIGO` (en `articulo.dbf` la llave es `CODIGO`; el
       campo `ARTICULO` solo aparece en `ventas_detalle.dbf`).
    3. Listar los productos NUEVOS (existen en matriz pero NO en local).
    4. Insertar los seleccionados en el DBF local replicando todos los
       campos compatibles entre ambas tablas.
    5. Reindexar CDX (best-effort) para que GECOPE/VFP9 los vea.

Notas de diseño:
    - Usamos la libreria `dbf` (Ethan Furman) — la misma que usan
      `dbf_repo.py` y `dbf_writer.py` — para mantener consistencia y
      tolerancia a FPTs placeholder.
    - NO actualizamos productos existentes (eso es otro caso de uso).
    - Si un campo existe en la matriz pero no en el schema local, se
      ignora silenciosamente (y viceversa).
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import dbf as _dbflib

from ..core.db_adapter import get_dbf_path

logger = logging.getLogger("factura_mdb.importador_productos")

DBF_ENC = "cp1252"


def _normalizar_codigo(valor: Any) -> str:
    """Strip + uppercase del CODIGO para comparación case-insensitive."""
    if valor is None:
        return ""
    if isinstance(valor, bytes):
        try:
            valor = valor.decode("cp1252", errors="replace")
        except Exception:  # noqa: BLE001
            return ""
    return str(valor).strip().upper()


def _resolver_articulo_local() -> Path:
    """Devuelve la ruta absoluta al articulo.dbf local de Daefy."""
    base = get_dbf_path()
    candidato = base / "articulo.dbf"
    if candidato.exists():
        return candidato
    # Fallback case-insensitive (FAT/NTFS pueden tener mayúsculas mezcladas)
    for child in base.iterdir():
        if child.name.lower() == "articulo.dbf":
            return child
    raise RuntimeError(f"articulo.dbf no encontrado en {base}")


def _validar_ruta_matriz(ruta: str) -> Path:
    """Valida y devuelve Path hacia el articulo.dbf de la matriz."""
    if not ruta or not str(ruta).strip():
        raise ValueError("La ruta del DBF matriz es obligatoria")
    p = Path(str(ruta).strip())
    if not p.exists():
        raise FileNotFoundError(f"No existe el archivo: {p}")
    if not p.is_file():
        raise ValueError(f"La ruta no es un archivo: {p}")
    if p.suffix.lower() != ".dbf":
        raise ValueError(
            f"El archivo debe ser .dbf (recibido: {p.suffix})"
        )
    return p


def _leer_codigos_locales() -> set[str]:
    """Devuelve el set de CODIGOs (normalizados) presentes en el DBF local."""
    path = _resolver_articulo_local()
    codigos: set[str] = set()
    t = _dbflib.Table(str(path), codepage=DBF_ENC)
    t.open(mode=_dbflib.READ_ONLY)
    try:
        for rec in t:
            try:
                cod = _normalizar_codigo(rec["CODIGO"])
                if cod:
                    codigos.add(cod)
            except Exception:  # noqa: BLE001
                continue
    finally:
        t.close()
    return codigos


def _row_a_dict(rec: Any, field_names: list[str]) -> dict[str, Any]:
    """Convierte una fila DBF a dict serializable con todos sus campos."""
    out: dict[str, Any] = {}
    for f in field_names:
        try:
            val = rec[f]
        except Exception:  # noqa: BLE001
            val = None
        if isinstance(val, (bytes, bytearray)):
            # Campos memo binarios (FOTO, OLE) — descartamos para JSON
            val = None
        if isinstance(val, str):
            val = val.rstrip()
        out[f] = val
    return out


def comparar_con_matriz(ruta_dbf_matriz: str) -> dict[str, Any]:
    """Compara articulo.dbf matriz vs local y devuelve productos NUEVOS.

    Args:
        ruta_dbf_matriz: ruta absoluta al articulo.dbf de IDIVSA.

    Returns:
        dict con:
            - "nuevos": lista de dicts (cada uno es un row de la matriz
              que NO existe en local), incluye CODIGO, NOMBRE, COSTO, etc.
            - "existentes": int, cantidad de productos que ya están en local
              (existen en ambos).
            - "matriz_total": int, total de productos en la matriz.
    """
    path_matriz = _validar_ruta_matriz(ruta_dbf_matriz)

    # 1. Cargar codigos locales en memoria (set para lookup O(1))
    codigos_locales = _leer_codigos_locales()
    logger.info(
        "Comparación: %d productos locales en %s",
        len(codigos_locales), _resolver_articulo_local(),
    )

    # 2. Recorrer matriz
    nuevos: list[dict] = []
    existentes = 0
    matriz_total = 0
    vistos_en_matriz: set[str] = set()  # dedupe por si la matriz tiene dups

    t = _dbflib.Table(str(path_matriz), codepage=DBF_ENC)
    t.open(mode=_dbflib.READ_ONLY)
    try:
        field_names = list(t.field_names)
        for rec in t:
            matriz_total += 1
            try:
                cod_norm = _normalizar_codigo(rec["CODIGO"])
            except Exception:  # noqa: BLE001
                continue
            if not cod_norm:
                continue
            if cod_norm in vistos_en_matriz:
                # duplicado en la propia matriz → ignoramos
                continue
            vistos_en_matriz.add(cod_norm)

            if cod_norm in codigos_locales:
                existentes += 1
            else:
                d = _row_a_dict(rec, field_names)
                nuevos.append(d)
    finally:
        t.close()

    # Orden estable por CODIGO para que el frontend muestre lista predecible
    nuevos.sort(key=lambda d: _normalizar_codigo(d.get("CODIGO")))

    logger.info(
        "Comparación matriz=%s → nuevos=%d existentes=%d total_matriz=%d",
        path_matriz, len(nuevos), existentes, matriz_total,
    )
    return {
        "nuevos": nuevos,
        "existentes": existentes,
        "matriz_total": matriz_total,
    }


def importar_desde_matriz(
    ruta_dbf_matriz: str,
    codigos_seleccionados: list[str],
) -> dict[str, Any]:
    """Inserta en articulo.dbf local los productos seleccionados de la matriz.

    Args:
        ruta_dbf_matriz: ruta al articulo.dbf de IDIVSA.
        codigos_seleccionados: lista de CODIGOs a importar (case-insensitive).

    Returns:
        dict con:
            - "importados": int, cantidad efectivamente insertada.
            - "errores": list[dict], cada uno con `codigo` y `motivo`.
    """
    path_matriz = _validar_ruta_matriz(ruta_dbf_matriz)
    path_local = _resolver_articulo_local()

    if not codigos_seleccionados:
        return {"importados": 0, "errores": []}

    seleccion_norm: set[str] = {
        _normalizar_codigo(c) for c in codigos_seleccionados if c
    }
    if not seleccion_norm:
        return {"importados": 0, "errores": []}

    # 1. Cargar codigos locales (para evitar duplicar si por accidente
    #    el usuario seleccionó algo que ya existe).
    codigos_locales = _leer_codigos_locales()

    # 2. Cargar de la matriz solo las filas seleccionadas, mapeando por CODIGO
    filas_a_insertar: dict[str, dict] = {}
    matriz_field_names: list[str] = []
    t_mat = _dbflib.Table(str(path_matriz), codepage=DBF_ENC)
    t_mat.open(mode=_dbflib.READ_ONLY)
    try:
        matriz_field_names = list(t_mat.field_names)
        for rec in t_mat:
            try:
                cod_norm = _normalizar_codigo(rec["CODIGO"])
            except Exception:  # noqa: BLE001
                continue
            if cod_norm in seleccion_norm and cod_norm not in filas_a_insertar:
                filas_a_insertar[cod_norm] = _row_a_dict(rec, matriz_field_names)
    finally:
        t_mat.close()

    importados = 0
    errores: list[dict] = []

    # Códigos seleccionados que no aparecen en la matriz
    no_encontrados = seleccion_norm - set(filas_a_insertar.keys())
    for cod in no_encontrados:
        errores.append({
            "codigo": cod,
            "motivo": "No encontrado en el DBF matriz",
        })

    # 3. Insertar en local
    t_loc = _dbflib.Table(str(path_local), codepage=DBF_ENC)
    t_loc.open(mode=_dbflib.READ_WRITE)
    try:
        local_field_names = set(t_loc.field_names)
        for cod_norm, fila in filas_a_insertar.items():
            if cod_norm in codigos_locales:
                errores.append({
                    "codigo": cod_norm,
                    "motivo": "Ya existe en el DBF local (saltado)",
                })
                continue
            try:
                row_data: dict[str, Any] = {}
                for f, v in fila.items():
                    if f in local_field_names:
                        row_data[f] = v
                t_loc.append(row_data)
                importados += 1
            except Exception as exc:  # noqa: BLE001
                logger.exception("Error insertando %s", cod_norm)
                errores.append({
                    "codigo": cod_norm,
                    "motivo": f"Error al insertar: {exc}",
                })
    finally:
        t_loc.close()

    logger.info(
        "Importación: importados=%d errores=%d (matriz=%s)",
        importados, len(errores), path_matriz,
    )

    # 4. Reindex CDX (best-effort). Si CodeBase no está disponible
    #    seguimos: el reader usa la lib `dbf` que tolera ausencia de CDX.
    if importados > 0:
        try:
            from codebasetools import cbTools  # type: ignore[import-not-found]
            cbt = cbTools()
            if cbt.use(str(path_local), exclusive=True):
                try:
                    cbt.reindex()
                except Exception:  # noqa: BLE001
                    pass
                cbt.closetable()
        except Exception as exc:  # noqa: BLE001
            logger.debug("reindex CDX skipped: %s", exc)

    return {"importados": importados, "errores": errores}
