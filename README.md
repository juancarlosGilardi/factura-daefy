# Factura-mdb

**Facturador electrónico SUNAT que lee y escribe directamente sobre el archivo `.mdb` (Microsoft Access) del cliente.** Coexiste con SIAP legacy compartiendo la misma base de datos.

Aplicación de escritorio (Windows) basada en FastAPI + PyWebView, con UI Jinja+Alpine. Modo single-tenant: una instancia por cliente, configurada por `config.json`.

## Para qué sirve

Los seis clientes que aún operan con SIAP DOS/legacy generan sus comprobantes en un `.mdb` (Access) compartido en red. Este proyecto les permite emitir facturas, boletas, notas de crédito y notas de débito **electrónicas a SUNAT** sin migrar de base ni cambiar el flujo del SIAP que ya conocen.

Es un laboratorio temporal — sirve para validar comportamiento real con datos productivos antes de invertir en una versión SQLite escalada (otro proyecto distinto).

**Cliente piloto:** AFISCA S.A.C. (RUC 20518470591).

## Stack

- **Backend:** Python 3.11+, FastAPI, pyodbc (Access driver)
- **Firma + XML SUNAT:** lxml, signxml, cryptography
- **PDF:** weasyprint, qrcode
- **UI:** Jinja2 + Alpine.js + Tailwind (compilado, en `static/`)
- **Wrapper desktop:** PyWebView + EdgeChromium (Windows)

## Cómo arrancar (5 pasos)

```bash
# 1. Instalar dependencias
pip install -r backend/requirements.txt

# 2. Asegurar driver Access ODBC
#    Descargar: https://www.microsoft.com/en-us/download/details.aspx?id=54920
#    Elige x64 si tu Python es x64.

# 3. Configurar
cp config.example.json config.json
# Edita config.json:
#   - mdb.path → ruta al .mdb del cliente
#   - certificado.password → la real
#   - sunat.ambiente → "beta" (default seguro)

# 4. Verificar
python scripts/verificar_setup.py

# 5. Arrancar
python scripts/arrancar_dev.py
# Abrir http://127.0.0.1:9876
```

Para el detalle por paso ver [docs/ARRANCAR.md](docs/ARRANCAR.md).

## Reglas operativas

- **SUNAT BETA por default.** Producción solo si lo configuras explícitamente y el cliente lo aprueba.
- **El `.mdb` es del cliente** y vive donde el cliente lo tiene. Nunca lo muevas a `data/` si es producción real — actualiza `config.json` apuntando a la ruta original.
- **`config.json` está en `.gitignore`.** No lo subas (contiene la password del cert).
- **El cert (`certs/RUC.pfx`) está en `.gitignore`.** Solo el `.gitkeep` se versiona.

## Estructura

```
Factura-mdb/
├── backend/app/         FastAPI + adapter MDB + servicios SUNAT/PDF
├── certs/               Certificados .pfx (no versionados)
├── data/                Bases .mdb del cliente (no versionados)
├── desktop/             PyWebView entry + spec PyInstaller
├── docs/                Guías ARRANCAR / EMITIR / COMPILAR / SCHEMA
├── scripts/             verificar_setup.py + arrancar_dev.py
├── storage/             XMLs, CDRs y PDFs generados (no versionados)
├── config.example.json  Plantilla pública
├── config.json          Configuración real (no versionado)
└── pyproject.toml
```

## Estado del proyecto

| Sprint | Estado | Descripción |
| --- | --- | --- |
| 1 — Lectura MDB | hecho | CRUD lectura clientes/productos/comprobantes/series |
| 2 — Escritura MDB | hecho | INSERT EF2CLIENTES + TBVENTA_CAB/DET, soft-delete, `FMDB_PRODUCTOS` aux |
| 3 — Emisión SUNAT BETA | pendiente | Firma XML + envío SOAP + recepción CDR + persistencia en .mdb |
| 4 — Producción | pendiente | Solo después de éxito Sprint 3 con AFISCA |

## Documentación

- [`CLAUDE.md`](CLAUDE.md) — briefing autosuficiente para Claude Code
- [`HANDOFF_TECNICO.md`](HANDOFF_TECNICO.md) — guía técnica completa
- [`docs/ARRANCAR.md`](docs/ARRANCAR.md) — instalación detallada
- [`docs/MDB_SCHEMA_SIAP.md`](docs/MDB_SCHEMA_SIAP.md) — schema del .mdb SIAP
- [`docs/EMITIR_BETA.md`](docs/EMITIR_BETA.md) — pruebas de emisión a BETA
- [`docs/COMPILAR_EXE.md`](docs/COMPILAR_EXE.md) — generación del .exe

## Licencia

Propietario. Uso interno.
