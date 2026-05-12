"""Regenera el .fpt de ventas.dbf y ventas_detalle.dbf usando CodeBase-Tools.

PROBLEMA: anoche cree un .fpt placeholder de 512 bytes (header valido pero
sin bloques de contenido). La libreria `dbf` lo acepta, CodeBase lo acepta,
pero `dbfread` valida estrictamente los bloques memo (campos M y G) y
falla con "unpack requires a buffer of 8 bytes" cuando intenta leer un
bloque inexistente referenciado por un campo G (QR_IMAGEN, GUIA_QR_IM).

SOLUCION: usar CodeBase-Tools para hacer `pack()` de la tabla en EXCLUSIVE.
CodeBase regenera el .fpt limpio cuando hace pack, con bloques validos
para los campos G (vacios pero existentes).
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

from codebasetools import cbTools  # type: ignore[import-not-found]
from app.core.db_adapter import get_dbf_path


def regenerar_fpt(table_name: str) -> bool:
    dbf_path = get_dbf_path() / table_name
    if not dbf_path.exists():
        print(f"[SKIP] {table_name} no existe")
        return False

    print(f"\n== {table_name} ==")
    print(f"  Path: {dbf_path}")

    cbt = cbTools()
    try:
        ok = cbt.use(str(dbf_path), exclusive=True)
        if not ok:
            print(f"  [FAIL] use(): {cbt.cErrorMessage}")
            return False
        print(f"  [OK] abierto EXCLUSIVE")

        # pack() reescribe el DBF (y el FPT) en archivo nuevo, limpiando
        # bloques memo huerfanos o invalidos.
        if cbt.pack():
            print(f"  [OK] pack() ejecutado")
        else:
            print(f"  [WARN] pack() fallo: {cbt.cErrorMessage}")

        # reindex() regenera el CDX
        if cbt.reindex():
            print(f"  [OK] reindex() ejecutado")
        else:
            print(f"  [WARN] reindex() fallo: {cbt.cErrorMessage}")

        cbt.closetable()
        return True
    except Exception as exc:
        print(f"  [FAIL] {exc}")
        try:
            cbt.closetable()
        except Exception:
            pass
        return False


def main() -> int:
    for table in ("ventas.dbf", "ventas_detalle.dbf", "cliente.dbf"):
        regenerar_fpt(table)
    print("\n== Verificacion con dbfread ==")
    from dbfread import DBF
    for table in ("ventas.dbf", "ventas_detalle.dbf"):
        try:
            t = DBF(str(get_dbf_path() / table), encoding='cp1252',
                    ignore_missing_memofile=True)
            n = sum(1 for _ in t)
            print(f"  [OK] {table}: {n} registros leibles con dbfread")
        except Exception as exc:
            print(f"  [FAIL] {table}: {exc}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
