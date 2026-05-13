"""Importador desde GECOPE (Visual FoxPro DBF).

Modo paralelo al `mdb_importer/` (SIAP MDB) y al `sqlite_repo/writer`
(VPS SQLite). Lee/escribe archivos `.dbf` con encoding `cp1252` y memos
`.fpt` usando `dbfread` (lectura) y `dbf` (escritura).

Convenciones GECOPE:
    - cliente.dbf       — 30k clientes (CODIGO = DOI con padding a 11)
    - articulo.dbf      — 12k productos (CODIGO = código interno, UNIDAD_MED
                          es código SUNAT directo: NIU, ZZ, KGM, ...)
    - ventas.dbf        — cabecera (CODIGO autoincremental int, DOCUMENTO
                          interno '000001'=Factura, '000002'=Boleta,
                          '000003'=NC, '000004'=ND).
    - ventas_detalle.dbf— detalle (CODIGO FK a ventas.CODIGO, ARTICULO
                          FK a articulo.CODIGO).
    - datos_compañia.dbf — única fila con datos de empresa (RUC en CODIGO).

Los datos del ERP son escritos por GECOPE en Visual FoxPro 9; tras cada
INSERT/UPDATE desde Factura-mdb se llama `reindex_dbf()` para refrescar
los índices `.cdx`.
"""
