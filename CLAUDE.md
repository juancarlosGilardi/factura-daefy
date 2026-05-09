# Factura-mdb — briefing para Claude Code

Aplicación Python aislada que emite comprobantes electrónicos SUNAT leyendo y escribiendo **directamente sobre un archivo `.mdb` (Microsoft Access)** del cliente. Coexiste con SIAP legacy compartiendo la misma BD.

## Reglas duras (NO negociables)

1. **SUNAT BETA por default.** Producción solo si `config.json -> sunat.ambiente == "produccion"` o env var `FACTURA_MDB_SUNAT_ENV=produccion` — y solo después de aprobación expresa del cliente.
2. **NUNCA voseo argentino.** El usuario es peruano. Usar siempre "tú/tienes/puedes/decides", nunca "vos/tenés/querés/decidís".
3. **NUNCA implementar tema oscuro.** El usuario no usa dark mode.
4. **El cert está dentro del proyecto** (`certs/RUC.pfx`), nunca apuntando fuera. El path en `config.json` es relativo a la raíz.
5. **`config.json` y `certs/*.pfx` y `data/*.mdb` están en `.gitignore`.** Nunca commitearlos.
6. **El `.mdb` del cliente NO vive en `data/`** si es producción real — `config.json -> mdb.path` apunta a la ruta original del cliente.
7. **Modo MDB es el ÚNICO modo.** No hay SQLite. `is_mdb_mode()` siempre es True. `is_sqlite_mode()` siempre False.
8. **Coexistencia con SIAP:** el .mdb se abre en modo compartido (no exclusivo). Si SIAP lo tiene exclusivo, los writers fallan con `MDBLockTimeout`.

## Stack

- Python 3.11+, FastAPI, uvicorn, pyodbc (driver Access ODBC)
- lxml + signxml + cryptography (firma XML SUNAT UBL 2.1)
- weasyprint + qrcode (PDFs A4)
- Jinja2 + Alpine.js (UI server-side rendered)
- PyWebView (wrapper desktop) + PyInstaller (build .exe)

## Estructura

```
backend/app/
  core/
    config.py             Settings desde config.json + env vars
    database.py           STUB (no SQLite) — solo Base para imports legacy
    db_adapter/
      __init__.py         is_mdb_mode()=True siempre. get_mdb_path()
      mdb_repo.py         Lectura: ClienteRepoMDB, ProductoRepoMDB, ComprobanteRepoMDB, EmpresaRepoMDB
      mdb_writer.py       Escritura: Cliente/Producto/ComprobanteWriterMDB
      mdb_lock.py         write_cursor() context, MDBLockTimeout
  api/                    Routers REST FastAPI (clientes, productos, comprobantes, ...)
  models/                 Modelos SQLAlchemy heredados (no se materializan en BD)
  schemas/                Pydantic v2 schemas
  services/
    mdb_importer/         Conexión pyodbc + mappers SIAP→dict
    xml_generators/       UBL 2.1 invoice/credit/debit/voided/summary
    xml_models/           Modelos pydantic para XML
    firma_digital.py      Firma XADES sobre XML
    sunat_client.py       Cliente SOAP a SUNAT
    sunat_test_service.py getStatus dummy a SUNAT
    comprobante_service.py orquesta firma+envío
    pdf_generator.py      A4 con QR
    ...
  templates/              Jinja2 (dashboard, list/form/detail por entidad)
  static/                 CSS compilado, JS, img
  main.py                 FastAPI app, lifespan, middlewares, routers
desktop/
  main.py                 PyWebView entry → uvicorn local + ventana
  factura_mdb.spec        PyInstaller config
scripts/
  verificar_setup.py      Smoke test (cert + .mdb + driver + SUNAT)
  arrancar_dev.py         Helper uvicorn local
docs/                     ARRANCAR / EMITIR_BETA / COMPILAR_EXE / MDB_SCHEMA_SIAP
config.example.json       Plantilla pública (versionada)
config.json               Configuración real (NO versionada — en .gitignore)
HANDOFF_TECNICO.md        Guía técnica completa
README.md                 Briefing público
```

## Cómo arrancar

```bash
pip install -r backend/requirements.txt
# Asegurar driver Access: https://www.microsoft.com/en-us/download/details.aspx?id=54920
cp config.example.json config.json   # editar y poner valores reales
python scripts/verificar_setup.py     # debe dar OK en todos los checks
python scripts/arrancar_dev.py        # uvicorn http://127.0.0.1:9876
```

## Estado del proyecto

| Sprint | Estado | Detalle |
| --- | --- | --- |
| 1 Lectura MDB | hecho | EF2CLIENTES, TBVENTA_CAB/DET, EF2ALMACENES, deduplicación productos |
| 2 Escritura MDB | hecho | INSERT clientes + comprobantes (CAB+DET), MDBLockTimeout, tabla aux FMDB_PRODUCTOS |
| 3 Emisión SUNAT BETA | pendiente | Adaptar `comprobante_service.emitir_y_enviar` para que el flujo MDB funcione end-to-end con la misma firma SUNAT que la rama SQLite |
| 4 Producción | pendiente | Solo después de éxito Sprint 3 con AFISCA |

## Schema SIAP (resumen — ver MDB_SCHEMA_SIAP.md para detalle)

Tablas principales:
- `EF2CLIENTES` — clientes. PK F2CODCLI VARCHAR(4) sin padding ('1', '1160')
- `TBVENTA_CAB` — cabecera comprobantes. Compuesta (F4TIPODOCU, F4SERDOC, F4NUMDOC)
- `TBVENTA_DET` — detalle comprobantes
- `EF2ALMACENES` — única fuente del RUC operativo (F2RUCALM)

Convenciones SIAP:
- F2TIPDOC: 'J' (jurídico/RUC) | 'N' (natural/DNI/CE/Pas)
- F4TIPMON: 'S' (PEN) | 'D' (USD)
- F4NUMDOC: VARCHAR(7) con padding "0001234"
- F4FORPAG: '001' (Contado) | '003' (Crédito)
- F4ESTNUL: True = anulado | False/NULL = vigente

Limitaciones JET Access:
- No soporta LIMIT/OFFSET (usar TOP n + paginar Python)
- No serialización fuerte → puede tener race condition con SIAP en correlativos. `mdb_writer.ComprobanteWriterMDB` lo documenta.
- BIT puede ser True/False/NULL — siempre verificar `is None`.

## Pendientes inmediatos (Sprint 3)

Ver `HANDOFF_TECNICO.md` -> "Sprint 3 pendiente". Resumen:

1. Verificar que `comprobante_service.emitir_y_enviar()` funciona en modo MDB (hoy asume sesión SQLAlchemy con modelos `Comprobante`/`Empresa`).
2. Adaptar para que reciba el dict del comprobante creado por `ComprobanteWriterMDB.crear()` y la empresa desde `EmpresaRepoMDB.obtener()`.
3. Persistir post-envío (CDR, hash, estado) actualizando `TBVENTA_CAB.F4ENVIADO`, `F4CDR`, `F4CDRFECHA`, `F4CODEHASH`.
4. Probar end-to-end con AFISCA en BETA (cert real, MODDATOS/MODDATOS, RUC 20518470591).

## Tono y estilo

- Comentarios y mensajes en español neutro peruano.
- Logs con prefijo `factura_mdb.*` (`logging.getLogger("factura_mdb.api.X")`).
- Errores accionables: cada `raise` con mensaje que sugiera qué hacer.
- En docs y UI: tono directo, sin jerga técnica innecesaria. El usuario es contador/empresario, no programador.
