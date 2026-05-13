"""Inspecciona campos memo de ventas.dbf y el header del .fpt."""
import os
import struct
from pathlib import Path
from dbfread import DBF

root = Path(__file__).resolve().parent.parent
daefy = root / "data" / "daefy"

dbf_path = daefy / "ventas.dbf"
fpt_path = daefy / "ventas.fpt"

print(f"DBF: {dbf_path}  ({dbf_path.stat().st_size} bytes)")
print(f"FPT: {fpt_path}  ({fpt_path.stat().st_size} bytes)")
print()

print("== Campos memo declarados ==")
t = DBF(str(dbf_path), encoding='cp1252', ignore_missing_memofile=True)
for f in t.fields:
    if f.type in ('M', 'G', 'P', 'B'):
        print(f"  {f.name:20} type={f.type} length={f.length}")
print()

print("== Header DBF byte 28 (flags) ==")
with open(dbf_path, 'rb') as fh:
    hdr = fh.read(32)
print(f"  byte 0 (tipo DBF): 0x{hdr[0]:02x}")
print(f"  byte 28 (flags):   0x{hdr[28]:02x}")
print()

print("== Header FPT actual ==")
with open(fpt_path, 'rb') as fh:
    fhdr = fh.read(32)
print(f"  bytes 0-3 next_free_block (BE): {int.from_bytes(fhdr[0:4], 'big')}")
print(f"  bytes 6-7 block_size (BE):      {int.from_bytes(fhdr[6:8], 'big')}")
print(f"  resto (hex):                    {fhdr[8:32].hex()}")
