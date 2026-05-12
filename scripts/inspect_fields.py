"""Lista los campos de ventas.dbf y verifica si hay un USEGRA."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

import dbf as dbflib
from app.core.db_adapter import get_dbf_path

path = get_dbf_path() / "ventas.dbf"
t = dbflib.Table(str(path), codepage="cp1252")
t.open(mode=dbflib.READ_ONLY)
print(f"Tabla: {path}")
print(f"Total: {len(t)} registros")
print()
print("Campos:")
for f in t.field_names:
    print(f"  - {f}")
print()
print("Buscando campos parecidos a 'USEGRA':")
for f in t.field_names:
    if "USE" in f.upper() or "GRA" in f.upper() or "FMDB" in f.upper():
        print(f"  >>> {f}")
print()
# Última fila como ejemplo
fields = list(t.field_names)
ult = list(t)[-1]
print("Últimos 5 registros (ULTIMOS escritos por nosotros anoche):")
for r in list(t)[-5:]:
    print("  ", {f: r[f] for f in ("CODIGO","DOCUMENTO","SER_DOCUME","NUM_DOCUME","CLIENTE","TOTAL")
                if f in fields})
t.close()
