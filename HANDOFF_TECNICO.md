# Factura-mdb — Handoff Técnico

> Guía completa para que cualquier desarrollador (o sesión de Claude Code) pueda retomar este proyecto desde cero. Incluye arquitectura, schema SIAP, sprints completados, pendientes para Sprint 3 y troubleshooting.

---

## Índice

1. [Visión general y origen del código](#1-visión-general-y-origen-del-código)
2. [Reglas operativas](#2-reglas-operativas)
3. [Stack y dependencias](#3-stack-y-dependencias)
4. [Estructura del proyecto](#4-estructura-del-proyecto)
5. [Configuración (config.json + env vars)](#5-configuración-configjson--env-vars)
6. [Adapter MDB — repos y writers](#6-adapter-mdb--repos-y-writers)
7. [Schema SIAP — tablas y convenciones](#7-schema-siap--tablas-y-convenciones)
8. [Flujo SUNAT (firma + envío + CDR)](#8-flujo-sunat-firma--envío--cdr)
9. [Cómo arrancar (paso a paso)](#9-cómo-arrancar-paso-a-paso)
10. [Cómo agregar features](#10-cómo-agregar-features)
11. [Cómo compilar el .exe](#11-cómo-compilar-el-exe)
12. [Sprints completados](#12-sprints-completados)
13. [Sprint 3 pendiente — emisión SUNAT desde MDB](#13-sprint-3-pendiente--emisión-sunat-desde-mdb)
14. [Troubleshooting](#14-troubleshooting)
15. [Limitaciones conocidas](#15-limitaciones-conocidas)
16. [Smoke tests](#16-smoke-tests)

---

## 1. Visión general y origen del código

**Factura-mdb** es un facturador electrónico SUNAT de escritorio (Windows) que **lee y escribe directamente sobre un archivo `.mdb` (Microsoft Access)** del cliente. Coexiste con **SIAP legacy** — la app DOS/Windows tradicional del usuario — compartiendo el mismo archivo.

### Para qué sirve

Los seis clientes finales del usuario aún operan con SIAP y emiten comprobantes en su `.mdb`. Necesitan emitir esos comprobantes **electrónicos a SUNAT** sin cambiar el flujo del SIAP que ya conocen. Factura-mdb es la herramienta que abre el `.mdb` por encima del SIAP y agrega:
- Firma XML UBL 2.1
- Envío SOAP a SUNAT (BETA o producción)
- Recepción y persistencia del CDR
- Generación del PDF A4 con QR

### Origen del código

El backend MDB (lectura + escritura) se heredó de un proyecto interno previo (`SinSol-mdb`, sólo lectura/escritura de `.mdb`) y se aisló completamente para este proyecto. Lo que vino prestado:

- `core/db_adapter/mdb_repo.py` — repos de lectura sobre EF2CLIENTES, TBVENTA_CAB/DET, EF2ALMACENES.
- `core/db_adapter/mdb_writer.py` — writers (INSERT/UPDATE) sobre EF2CLIENTES y TBVENTA_CAB/DET.
- `core/db_adapter/mdb_lock.py` — context manager `write_cursor()` con manejo de lock.
- `services/mdb_importer/connection.py` — wrapper pyodbc + detección de driver.
- `services/mdb_importer/mappers.py` — mapeos SIAP→dict que respetan convenciones SUNAT.
- `services/firma_digital.py`, `sunat_client.py`, `xml_generators/`, `pdf_generator.py` — flujo SUNAT estable.
- Templates Jinja+Alpine completos (clientes, productos, comprobantes, reportes).

Lo que NO vino:
- Modo SQLite (en este proyecto no existe; ver `database.py` que es un stub).
- Onboarding cloud / activación de licencia.
- Backup automático (`backup_service.py` viene compilado pero no se monta en `main.py`).
- Importación CSV/Excel/MDB→SQLite.
- Auto-updater.
- Landing público / auth-backend.

Toda referencia visible a "SinSol" se renombró a "Factura-mdb" o a equivalentes neutros (`SINSOL_PRODUCTOS` → `FMDB_PRODUCTOS`, `getLogger("sinsol.*")` → `getLogger("factura_mdb.*")`).

### Cliente piloto

**AFISCA S.A.C.** — RUC 20518470591, cuenta de detracción 037 (12%), sunat_env de prueba, certificado en `certs/20518470591.pfx` (originario `CT2505125697.pfx` de la bóveda compartida del usuario).

---

## 2. Reglas operativas

| # | Regla | Por qué |
|---|---|---|
| 1 | **SUNAT BETA por default**. Producción solo si env var `FACTURA_MDB_SUNAT_ENV=produccion` o `config.json -> sunat.ambiente == "produccion"`. | Evitar emitir comprobantes reales por accidente. |
| 2 | **Cert dentro del proyecto** en `certs/RUC.pfx`. Path en `config.json` siempre **relativo** a la raíz del proyecto. | Aislamiento total. Si se mueve el proyecto, el cert se mueve con él. |
| 3 | **`config.json` + `certs/*.pfx` + `data/*.mdb`** están en `.gitignore`. | Contienen secretos / datos del cliente. |
| 4 | **El .mdb del cliente vive donde el cliente lo tiene** (típicamente en una ruta de red o `C:/SIAP/...`). En `data/` solo van copias de prueba. | Debe coincidir con la ruta que SIAP también usa. |
| 5 | **Modo MDB es el único modo**. `is_mdb_mode()` siempre es True. `is_sqlite_mode()` siempre False. | Este proyecto es exclusivamente MDB; el SQLite se hereda como código muerto que el linter ignorará. |
| 6 | **Coexistencia con SIAP**: el `.mdb` se abre en modo compartido. Si SIAP lo tiene exclusivo (rara vez), los writers fallan con `MDBLockTimeout` y devolvemos 503 con mensaje accionable. | Access permite multi-usuario por default. |
| 7 | **Comunicación con el usuario**: español neutro peruano. Nunca "vos/tenés/querés". Siempre "tú/tienes/puedes". Sin emojis. | Usuario es contador peruano. |
| 8 | **Sin tema oscuro**. | Usuario no usa dark mode. |

---

## 3. Stack y dependencias

```text
Python      3.11+
FastAPI     0.110-0.115
uvicorn     0.27-0.31 (ASGI server)
pyodbc      5.0+      (driver Access ODBC)
SQLAlchemy  2.0+      (importado por compatibilidad heredada — no se usa)
pydantic    2.5+
lxml        5.1+      (XML)
signxml     4.0+      (firma XADES)
cryptography 42.0+    (PKCS12)
weasyprint  60.0+     (PDF)
qrcode      7.4+      (QR del PDF)
openpyxl    3.1+      (export reportes a XLSX)
jinja2      3.1+
httpx       0.26+
pywebview   5.0+      (wrapper desktop)
```

Driver requerido **fuera** de pip:
- **Microsoft Access Database Engine 2016 Redistributable** — gratis, descarga oficial:
  https://www.microsoft.com/en-us/download/details.aspx?id=54920
- Elige x64 si tu Python es x64 (el caso típico). Si Python es x86, usa la x86 (no se pueden mezclar).

---

## 4. Estructura del proyecto

```
Factura-mdb/
├── README.md                    Briefing público (140 líneas)
├── CLAUDE.md                    Briefing autosuficiente para Claude Code
├── HANDOFF_TECNICO.md           ← Este archivo
├── pyproject.toml               metadatos + ruff config
├── .gitignore                   ignora config.json, certs/*.pfx, data/*.mdb, ...
├── config.example.json          plantilla pública (versionada)
├── config.json                  configuración real (NO versionada — gitignore)
├── certs/
│   ├── .gitkeep
│   └── 20518470591.pfx          cert AFISCA (NO versionado)
├── data/
│   └── .gitkeep                 .mdb del cliente NO va versionado
├── storage/
│   ├── xml/.gitkeep
│   ├── cdr/.gitkeep
│   └── pdf/.gitkeep
├── backend/
│   ├── requirements.txt
│   └── app/
│       ├── __init__.py
│       ├── main.py              ← FastAPI app + lifespan + middlewares + routers
│       ├── core/
│       │   ├── __init__.py
│       │   ├── config.py        ← Settings desde config.json + env vars
│       │   ├── database.py      ← STUB (no SQLite real). Solo para imports legacy
│       │   └── db_adapter/
│       │       ├── __init__.py  ← is_mdb_mode() siempre True. get_mdb_path()
│       │       ├── mdb_repo.py  ← ClienteRepoMDB, ProductoRepoMDB, ComprobanteRepoMDB, EmpresaRepoMDB
│       │       ├── mdb_writer.py ← ClienteWriterMDB, ProductoWriterMDB, ComprobanteWriterMDB
│       │       └── mdb_lock.py  ← write_cursor() context, MDBLockTimeout
│       ├── api/
│       │   ├── __init__.py
│       │   ├── _deps.py         ← escape_like, helpers compartidos
│       │   ├── _comprobante_calc.py ← calcular_linea, calcular_totales
│       │   ├── clientes.py
│       │   ├── productos.py
│       │   ├── comprobantes.py  ← incluye correlativos_router
│       │   ├── empresas.py      ← incluye plural_router, config_router
│       │   ├── comunicacion_baja.py
│       │   ├── resumen_diario.py
│       │   └── reportes.py
│       ├── models/              ← SQLAlchemy models heredados (NO se materializan)
│       │   ├── __init__.py
│       │   ├── empresa.py
│       │   ├── cliente.py
│       │   ├── producto.py
│       │   ├── comprobante.py
│       │   └── resumen.py
│       ├── schemas/             ← Pydantic v2 (request/response)
│       │   ├── __init__.py
│       │   ├── common.py
│       │   ├── empresa.py
│       │   ├── cliente.py
│       │   ├── producto.py
│       │   ├── comprobante.py
│       │   ├── resumen.py
│       │   └── baja.py
│       ├── services/
│       │   ├── __init__.py
│       │   ├── firma_digital.py     ← XADES con cryptography + signxml
│       │   ├── sunat_client.py      ← SOAP a SUNAT (sendBill, getStatus, ...)
│       │   ├── sunat_test_service.py ← getStatus dummy
│       │   ├── comprobante_service.py ← orquesta firma + envío + CDR
│       │   ├── baja_service.py
│       │   ├── resumen_service.py
│       │   ├── comprobante_mapper.py ← model SQLA → schemas SUNAT
│       │   ├── xml_service.py
│       │   ├── xml_generators/      ← invoice/credit_note/debit_note/voided/summary
│       │   ├── xml_models/          ← pydantic models para XML
│       │   ├── pdf_generator.py
│       │   ├── pdf_helpers.py
│       │   ├── pdf_service.py
│       │   ├── excel_export.py
│       │   ├── excel_importer.py
│       │   ├── backup_service.py    ← NO montado en main.py
│       │   ├── mdb_importer/        ← conexión + mappers SIAP
│       │   │   ├── __init__.py
│       │   │   ├── connection.py    ← detectar_driver(), conectar()
│       │   │   ├── mappers.py       ← mdb_*_to_dict(), normalizar_*
│       │   │   ├── importer.py      ← legacy importer (no se usa en este proyecto)
│       │   │   ├── schema_inspector.py
│       │   │   └── validators.py
│       │   └── templates/
│       │       └── factura_a4.html  ← Jinja para PDF
│       ├── static/
│       │   ├── css/                 ← Tailwind compilado
│       │   ├── js/
│       │   └── img/
│       └── templates/
│           ├── base.html
│           ├── dashboard.html
│           ├── configuracion.html
│           ├── components/          ← _modal, _pagination, _search, _toast, _status_badge
│           ├── clientes/            ← list.html, form.html
│           ├── productos/           ← list.html, form.html
│           ├── comprobantes/        ← list.html, emitir.html, detail.html
│           ├── comunicacion_baja/   ← list.html, nueva.html
│           ├── resumen_diario/      ← list.html, nuevo.html
│           └── reportes/index.html
├── desktop/
│   ├── main.py                  ← PyWebView entry point
│   ├── factura_mdb.spec         ← PyInstaller config
│   └── icon.svg
├── scripts/
│   ├── verificar_setup.py       ← smoke test: cert + .mdb + driver + cert.password + RUC
│   └── arrancar_dev.py          ← uvicorn local con env vars defensivas
└── docs/
    ├── ARRANCAR.md              ← instalación detallada
    ├── EMITIR_BETA.md           ← pruebas de emisión SUNAT BETA
    ├── COMPILAR_EXE.md          ← generación del .exe con PyInstaller
    └── MDB_SCHEMA_SIAP.md       ← schema SIAP detallado
```

---

## 5. Configuración (config.json + env vars)

### `config.json` (raíz)

```jsonc
{
  "mdb": { "path": "C:/ruta/al/db_bancos.mdb" },
  "empresa": {
    "ruc": "20518470591",
    "razon_social": "AFISCA S.A.C.",
    "direccion": "CAL. ENRIQUE PALACIOS NRO. 335 INT. 702",
    "ubigeo": "150122",
    "departamento": "LIMA",
    "provincia": "LIMA",
    "distrito": "MIRAFLORES"
  },
  "sunat": {
    "ambiente": "beta",
    "sol_user": "MODDATOS",
    "sol_pass": "MODDATOS"
  },
  "certificado": {
    "path": "certs/20518470591.pfx",
    "password": "AfiscA205184"
  },
  "configuracion": {
    "igv_rate": 18.0,
    "formato_impresion": "A4",
    "moneda_default": "PEN",
    "aplica_detraccion": true,
    "detraccion_codigo": "037",
    "detraccion_porcentaje": 12.0,
    "auto_envio_sunat": false
  }
}
```

### Env vars (sobrescriben `config.json`)

| Variable | Override de | Default |
|---|---|---|
| `FACTURA_MDB_PATH` | `mdb.path` | (vacío) |
| `FACTURA_MDB_RUC` | `empresa.ruc` | (vacío) |
| `FACTURA_MDB_SUNAT_ENV` | `sunat.ambiente` | `beta` |
| `FACTURA_MDB_SOL_USER` | `sunat.sol_user` | `MODDATOS` |
| `FACTURA_MDB_SOL_PASS` | `sunat.sol_pass` | `MODDATOS` |
| `FACTURA_MDB_CERT_PATH` | `certificado.path` | (vacío) |
| `FACTURA_MDB_CERT_PASS` | `certificado.password` | (vacío) |
| `FACTURA_MDB_HOST` | — | `127.0.0.1` |
| `FACTURA_MDB_PORT` | — | `0` (auto) |
| `FACTURA_MDB_DEBUG` | — | `1` |

Reglas:
- Si `FACTURA_MDB_SUNAT_ENV` está seteada y vale `produccion` o `beta`, gana sobre `config.json`.
- Si vale otra cosa o no está, se usa `config.json -> sunat.ambiente`.
- Si `config.json` tampoco lo dice, default es `beta`.

### Resolución de paths

`Settings._resolve_path()` resuelve paths relativos respecto a `PROJECT_ROOT` (la raíz del proyecto) y deja absolutos como están. Esto permite escribir `certs/20518470591.pfx` en `config.json` y que funcione sin importar dónde se invoque la app.

---

## 6. Adapter MDB — repos y writers

### Diseño general

El adapter está en `backend/app/core/db_adapter/`. Tres módulos:

#### `mdb_repo.py` — lectura

Cada clase es un facade estático con `listar()`, `obtener()`, `buscar()`. Devuelve `dict` con las **mismas keys** que los modelos SQLAlchemy de la rama heredada (Pydantic schemas funcionan sin cambios).

| Repo | Tabla SIAP | Notas |
|---|---|---|
| `ClienteRepoMDB` | `EF2CLIENTES` | F2CODCLI VARCHAR(4) sin padding. Filtro por tipo_documento se hace en Python (códigos heterogéneos). |
| `ProductoRepoMDB` | `TBVENTA_DET` (deduplicado) + `FMDB_PRODUCTOS` (aux) | SIAP no tiene tabla maestra; productos inferidos del histórico. Tabla aux con prioridad. |
| `ComprobanteRepoMDB` | `TBVENTA_CAB` + `TBVENTA_DET` | PK compuesta (F4TIPODOCU, F4SERDOC, F4NUMDOC). |
| `EmpresaRepoMDB` | `EF2ALMACENES` (RUC real) + `config.json` (resto) | SIAP no tiene tabla de empresa formal. |

#### IDs sintéticos

El `.mdb` no tiene PKs autoincrement. Los repos sintetizan `id` con CRC32 estable:

```python
def _synth_id(*parts: Any) -> int:
    raw = "|".join("" if p is None else str(p) for p in parts)
    return zlib.crc32(raw.encode("utf-8")) & 0x7FFFFFFF  # 31 bits, no negativo
```

- **Cliente**: `int(F2CODCLI)` cuando es numérico (caso 99% AFISCA), `synth_id("cli", F2CODCLI)` en fallback.
- **Producto**: `synth_id("prod", F5CODPRO)`.
- **Comprobante**: `synth_id("comp", tipo, serie, correlativo)`.

Mismo input → mismo output, así los enlaces `/clientes/{id}` son estables entre llamadas.

#### `mdb_writer.py` — escritura

Tres clases con `crear()`, `actualizar()`, `borrar()`/`anular()`.

##### `ClienteWriterMDB`
- INSERT en `EF2CLIENTES` con padding 4 dígitos vs sin padding (respeta lo que vio en datos: `'1'`, `'1160'`).
- F2NEWRUC para tipo_doc='6' (RUC), F2DOCCLI para otros.
- Soft-delete: marca ESTADO=False.

##### `ProductoWriterMDB`
- INSERT/UPDATE en tabla auxiliar **`FMDB_PRODUCTOS`** (creada al primer insert con `CREATE TABLE`).
- NO contamina `TBVENTA_DET` ni queries SIAP.
- `ProductoRepoMDB.listar()` combina ambas fuentes con prioridad a `FMDB_PRODUCTOS`.

##### `ComprobanteWriterMDB`
- INSERT en `TBVENTA_CAB` + `TBVENTA_DET` en una transacción.
- Genera F4NUMDOC con padding 7 (`f"{n:07d}"`).
- F4TIPMON: 'S' (PEN) | 'D' (USD).
- F4FORPAG: '001' (Contado) | '003' (Crédito).
- Estado inicial: F4ESTNUL=False, F4ESTEMI=False, F4ENVIADO=False (borrador local).
- Marca al usuario como `"FMDB"` en F4USEGRA / F4USEMOD para distinguir de SIAP (`"SIAP"` u otros).

##### `mdb_lock.py`

Context manager `write_cursor()` que:
1. Abre conexión read/write con `pyodbc` (autocommit=False).
2. Yield el cursor al caller.
3. Commit al salir sin error.
4. Rollback en excepción.
5. Si Access tiene el archivo en exclusivo (rara vez), envuelve el error en `MDBLockTimeout` con mensaje "Cierra SIAP e intenta de nuevo".

Helpers: `is_locked()` (existencia del .ldb), `wait_for_unlock(timeout_s)` (poll hasta liberarse). Ambos sólo informativos — la decisión de escribir es del writer.

### Cómo lo usan los routers

Cada handler comienza con:

```python
if is_mdb_mode():
    from ..core.db_adapter.mdb_repo import ClienteRepoMDB
    items, total = ClienteRepoMDB.listar(...)
    return ClienteListResponse(items=items, total=total, ...)

# Resto del código SQLite (heredado, NUNCA se ejecuta porque is_mdb_mode siempre True)
```

Como `is_mdb_mode()` es `True` siempre, las ramas SQLite son código muerto inalcanzable. Se mantienen porque facilitan el merge si en el futuro se quisiera reactivar el SQLite.

---

## 7. Schema SIAP — tablas y convenciones

### Tablas principales (de las 291 que tiene un .mdb SIAP típico)

#### `EF2CLIENTES` — clientes

| Columna | Tipo | Notas |
|---|---|---|
| `F2CODCLI` | VARCHAR(4) | PK lógica; sin padding observado. Casos: `'1'`, `'1160'`. |
| `F2NEWRUC` | VARCHAR(11) | RUC para tipo='J'. NULL para naturales. |
| `F2DOCCLI` | VARCHAR(15) | DNI/CE/Pas para tipo='N'. NULL para jurídicos. |
| `F2NOMCLI` | VARCHAR(120) | Razón social o nombre completo. |
| `F2DIRCLI` | VARCHAR(120) | Dirección. |
| `F2TELCLI` | VARCHAR(30) | Teléfono. |
| `F2TIPDOC` | CHAR(1) | `'J'` (jurídico/RUC) o `'N'` (natural). |
| `F2EMAIL` | VARCHAR(150) | Email. |
| `F2DISCLI` | VARCHAR(80) | Distrito (opcional). |
| `F2CELULAR` | VARCHAR(30) | Celular (opcional). |
| `ESTADO` | BIT | True=activo, False=desactivado. |
| `F4ENVIADO` | BIT | Heredado, no se usa para clientes. |
| `FECING`, `FECREG`, `FecMod` | DATETIME | Auditoría. |

#### `TBVENTA_CAB` — cabecera comprobantes

| Columna | Tipo | Notas |
|---|---|---|
| `F4TIPODOCU` | VARCHAR(2) | '01' factura, '03' boleta, '07' NC, '08' ND. |
| `F4SERDOC` | VARCHAR(4) | Serie (F001, B001, ...). |
| `F4NUMDOC` | VARCHAR(7) | Correlativo con padding "0001234". |
| `F4FECEMI` | DATETIME | Fecha emisión. |
| `F4TIPMON` | CHAR(1) | `'S'` PEN, `'D'` USD. |
| `F4TIPCAM` | DOUBLE | Tipo de cambio (1.0 si PEN). |
| `F2RUCCLI` | VARCHAR(11) | RUC del cliente (si tipo='6'). |
| `F2NOMCLI` | VARCHAR(120) | Razón social del cliente. |
| `F2DIRCLI` | VARCHAR(120) | Dirección del cliente. |
| `F2CODCLI` | VARCHAR(4) | FK a EF2CLIENTES. |
| `F4SUBTOT` | DOUBLE | Total gravado (sin IGV). |
| `F4TOTIGV` | DOUBLE | IGV calculado. |
| `F4SUBFACINAF` | DOUBLE | Total inafecto. |
| `F4MONTOEXONERADO` | DOUBLE | Total exonerado. |
| `F4TOTFAC` | DOUBLE | Total venta (gravado + inafecto + exonerado + IGV). |
| `F4BASIMP` | DOUBLE | Base imponible (alias gravado). |
| `F4ESTNUL` | BIT | True=anulado. |
| `F4ESTEMI` | BIT | True=emitido (post envío SUNAT). |
| `F4ENVIADO` | BIT | True=enviado a SUNAT. |
| `F4ESTFAC`, `F4ESTVAL`, `F4CONTABLE`, `F4CHECK`, `F4LOCAL`, `F4ESTRECHAZO`, `F4VB` | BIT | Flags SIAP variados. |
| `F4DETRACCIONAPLICA` | BIT | True si hay detracción. |
| `F4DETRACCIONPORC` | DOUBLE | % detracción (12.0 para 037). |
| `F4DETRACCIONMONTO` | DOUBLE | Monto detracción. |
| `F4FORPAG` | VARCHAR(3) | `'001'` Contado, `'003'` Crédito. |
| `F4FECGRA` | DATETIME | Fecha grabación (now). |
| `F4USEGRA` | VARCHAR(15) | Usuario que grabó. Factura-mdb pone `"FMDB"` para distinguir. |
| `F4DRAWBACK`, `F4PERCEPCION` | BIT | Casos especiales. |
| `F4CDR` | VARCHAR(15) | Código CDR SUNAT (post envío). |
| `F4CDRFECHA` | DATETIME | Fecha CDR. |
| `F4CODEHASH` | VARCHAR(255) | Hash del XML firmado. |
| `F4OBSERVA` | VARCHAR(255) | Observaciones (Factura-mdb usa `"[Anulado Factura-mdb] motivo..."`). |
| `F4FECMOD`, `F4USEMOD` | DATETIME, VARCHAR(15) | Auditoría modificación. |
| `F4TIPREF`, `F4SERGUI`, `F4NUMGUI` | VARCHAR | Referencia (NC/ND a doc original) y guía remisión. |

#### `TBVENTA_DET` — detalle comprobantes

| Columna | Tipo | Notas |
|---|---|---|
| `F4TIPODOCU`, `F4SERDOC`, `F4NUMDOC` | — | FK a TBVENTA_CAB (compuesta). |
| `F5CODPRO` | VARCHAR(10) | Código de producto (puede ser NULL). |
| `F5NOMPRO` | VARCHAR(255) | Descripción del item. |
| `F7CODMED` | VARCHAR(3) | Código de unidad SIAP. Mapeo: `101=NIU`, `102=KGM`, `103=LTR`, `104=MTR`, `201=ZZ`. |
| `F3CANPRO` | DOUBLE | Cantidad. |
| `F3VALVTAUNIT` | DOUBLE | Valor unitario sin IGV. |
| `F3PREUNI` | DOUBLE | Precio unitario con IGV. |
| `F3VALVTA` | DOUBLE | Valor total (cant × valor unit). |
| `F3IGV` | DOUBLE | IGV del item. |
| `F3PREVTA` | DOUBLE | Total con IGV (precio × cant). |
| `F3VALBRUTO`, `F3PREBRU` | DOUBLE | Aliases. |
| `F3AFECTO` | BIT | True si afecto a IGV (catálogo 7 SUNAT, códigos 10-17). |
| `F3ITEM` | INTEGER | Número de orden del item. |
| `F4FECEMI` | DATETIME | Redundante; copia de TBVENTA_CAB. |
| `F4TIPMON` | CHAR(1) | Redundante; copia de TBVENTA_CAB. |

#### `EF2ALMACENES` — única fuente del RUC operativo

| Columna | Tipo | Notas |
|---|---|---|
| `F2RUCALM` | VARCHAR(11) | **RUC real del cliente** — única columna con RUC operativo en el .mdb. |
| `F2NOMALM` | VARCHAR(80) | Nombre del almacén (NO razón social). |
| `F2DIRALM` | VARCHAR(120) | Dirección del almacén. |

`EmpresaRepoMDB.obtener()` lee TOP 1 de aquí para sincronizar el RUC con `config.json -> empresa.ruc`. Si difieren, gana el `.mdb` (con un `logger.warning`).

#### `FMDB_PRODUCTOS` — tabla auxiliar Factura-mdb (NO existe en SIAP nativo)

Creada por `ProductoWriterMDB.asegurar_tabla()` al primer INSERT. Schema:

```sql
CREATE TABLE FMDB_PRODUCTOS (
  codigo VARCHAR(40) PRIMARY KEY,
  descripcion VARCHAR(250),
  unidad_medida VARCHAR(10),
  valor_unitario DOUBLE,
  moneda VARCHAR(3),
  tipo_afectacion_igv VARCHAR(2),
  incluye_igv BIT,
  notas VARCHAR(255),
  activo BIT,
  created_at DATETIME,
  updated_at DATETIME
)
```

SIAP no abre esta tabla; queries de SIAP siguen funcionando normales.

### Convenciones SIAP (resumen ejecutivo)

| Convención | Detalle |
|---|---|
| F2TIPDOC | `'J'` (jurídico/RUC) | `'N'` (natural/DNI/CE/Pas). Mapeo SinSol: cat 06 → '6' RUC=J, '1' DNI/'4' CE/'7' Pas/'0' S/D=N. |
| F4NUMDOC | VARCHAR(7) **CON padding** "0001234". Generar con `f"{n:07d}"`. |
| F2CODCLI | VARCHAR(4) **SIN padding**. Generar con `str(n)`. |
| F4TIPMON | `'S'` (PEN) | `'D'` (USD). EUR cae a 'S' por default (SIAP no lo soporta). |
| F4FORPAG | `'001'` (Contado) | `'003'` (Crédito). |
| F4ESTNUL | True=anulado, False/NULL=vigente. |
| F4ENVIADO | True=ya se envió a SUNAT y CDR aceptado. |
| F7CODMED | Códigos SIAP legacy (101=NIU, 102=KGM, ...). Ver mapeo en `ComprobanteWriterMDB.crear()`. |
| BIT | Puede ser True/False/**NULL**. Siempre verificar `is None` antes de `bool()`. |
| Fechas | DATETIME en todas. Las fechas de emisión se almacenan con time=00:00:00. |
| Texto | Sin acentos en algunos casos heredados; el writer respeta lo que viene en payload. |

---

## 8. Flujo SUNAT (firma + envío + CDR)

### Servicios involucrados

```
api/comprobantes.py                      ← endpoint /api/comprobantes/{id}/enviar-sunat
  └─ services/comprobante_service.py    ← orquesta firma + envío
      ├─ services/comprobante_mapper.py ← model SQLA → schemas SUNAT
      ├─ services/xml_generators/       ← genera XML UBL 2.1
      │   ├─ invoice_generator.py       ← factura/boleta (F/B)
      │   ├─ credit_note_generator.py   ← NC
      │   ├─ debit_note_generator.py    ← ND
      │   ├─ voided_generator.py        ← Comunicación de baja (RA)
      │   └─ summary_generator.py       ← Resumen diario (RC)
      ├─ services/firma_digital.py      ← firma XADES con .pfx
      └─ services/sunat_client.py       ← SOAP a SUNAT
```

### Pasos del flujo

1. **Generar XML** (UBL 2.1) — `xml_generators/invoice_generator.py` para facturas/boletas. Usa `xml_models/invoice.py` como pydantic model.

2. **Firmar** — `firma_digital.firmar_xml(xml_bytes, cert_path, cert_pass)`:
   - Carga el `.pfx` con `cryptography.pkcs12.load_key_and_certificates()`.
   - Inserta firma XADES inline en el XML.
   - Devuelve XML firmado + hash del DigestValue.

3. **Empaquetar** — `xml_service.empaquetar_xml(xml_firmado, ruc, tipo, serie, numero)`:
   - Nombre: `{ruc}-{tipo}-{serie}-{numero}.xml`
   - Comprime en ZIP con el mismo nombre base.

4. **Enviar a SUNAT** — `sunat_client.enviar_factura(zip_bytes, filename, sol_user, sol_pass, ambiente)`:
   - Construye SOAP envelope `sendBill`.
   - POST a `https://e-beta.sunat.gob.pe/...` (BETA) o `https://e-factura.sunat.gob.pe/...` (PROD).
   - Recibe el CDR en base64 → descomprime → guarda `R-{ruc}-{tipo}-{serie}-{numero}.xml`.

5. **Persistir** — `comprobante_service.emitir_y_enviar()` actualiza el modelo (en SQLite) o el `.mdb` (en este proyecto):
   - `F4ENVIADO = True`
   - `F4ESTEMI = True`
   - `F4CDR = "0"` (código aceptado)
   - `F4CDRFECHA = now`
   - `F4CODEHASH = hash_del_xml`

6. **Generar PDF** — `pdf_generator.generar_pdf_comprobante()` usa `services/templates/factura_a4.html` con weasyprint, incluyendo QR.

### Endpoints REST relevantes

| Método | Path | Función |
|---|---|---|
| POST | `/api/comprobantes/emitir` | Crear comprobante local (TBVENTA_CAB+DET) |
| POST | `/api/comprobantes/{id}/enviar-sunat` | Firmar + enviar + persistir CDR |
| POST | `/api/comprobantes/{id}/reintentar` | Reenviar si falló |
| POST | `/api/comprobantes/{id}/anular` | Marcar F4ESTNUL=True (anulación local) |
| GET | `/api/comprobantes/{id}/xml` | Descargar XML firmado |
| GET | `/api/comprobantes/{id}/cdr` | Descargar CDR |
| GET | `/api/comprobantes/{id}/pdf` | Descargar PDF |

---

## 9. Cómo arrancar (paso a paso)

```bash
# 1. Clonar y entrar
cd "C:/Users/Acer2025/Claude Proyectos/Factura-mdb"

# 2. Crear venv (recomendado)
python -m venv .venv
.venv\Scripts\activate          # PowerShell
# source .venv/bin/activate     # Bash

# 3. Instalar dependencias
pip install -r backend/requirements.txt

# 4. Asegurar driver Access
#    Si dice "no se detectó Microsoft Access Database Engine":
#    https://www.microsoft.com/en-us/download/details.aspx?id=54920
#    Elige x64 si tu Python es x64 (revisa con `python -c "import struct; print(struct.calcsize('P')*8)"`)

# 5. Configurar
cp config.example.json config.json   # Bash
# copy config.example.json config.json   # PowerShell
notepad config.json                   # Editar a mano

# Asegúrate de:
#   - mdb.path → ruta absoluta al .mdb del cliente
#   - certificado.path → certs/RUC.pfx (relativo al proyecto)
#   - certificado.password → la real
#   - sunat.ambiente → "beta" (no toques esto sin aprobación expresa)

# 6. Verificar
python scripts/verificar_setup.py
# Debe mostrar OK en todos los checks. Si algo falla, sigue las sugerencias.

# 7. Arrancar (modo dev con reload)
python scripts/arrancar_dev.py
# Output: ">>> Arrancando uvicorn en http://127.0.0.1:9876"

# 8. Abrir
# http://127.0.0.1:9876   en tu navegador
```

### Modo desktop (sin navegador)

```bash
python desktop/main.py
# Abre una ventana EdgeChromium con el SPA embebido.
```

---

## 10. Cómo agregar features

### Añadir un endpoint nuevo

Ejemplo: agregar `/api/clientes/{id}/historial`.

1. Editar `backend/app/api/clientes.py`:

```python
@router.get("/{cliente_id}/historial")
def historial_cliente(cliente_id: int, db: Session = Depends(get_db)):
    if is_mdb_mode():
        from ..core.db_adapter.mdb_repo import ComprobanteRepoMDB
        items, total = ComprobanteRepoMDB.listar(
            filtros={"cliente_id": cliente_id},
            limit=200, offset=0,
        )
        return {"items": items, "total": total}
    # rama SQLite (no se ejecuta en Factura-mdb)
    raise HTTPException(501, "Solo MDB")
```

2. Añadir Pydantic schema si la respuesta no es trivial.

### Añadir una columna al .mdb (tabla auxiliar)

Solo se permite agregar columnas a tablas **propias** (`FMDB_PRODUCTOS`, etc.). NUNCA modificar columnas SIAP.

```python
# En mdb_writer.py extender asegurar_tabla() para hacer ALTER TABLE
# Si es columna nueva, manejar el error "ya existe" igual que el INSERT inicial.
```

### Añadir un nuevo generador XML SUNAT

1. Crear `backend/app/services/xml_generators/nuevo_generator.py` siguiendo el patrón de `invoice_generator.py`.
2. Crear `xml_models/nuevo.py` con el pydantic model.
3. Mapear desde `comprobante_mapper.py`.
4. Llamar desde `comprobante_service.py`.

---

## 11. Cómo compilar el .exe

```bash
# Activar venv
.venv\Scripts\activate

# Asegurar que PyInstaller está instalado
pip install pyinstaller

# Build
pyinstaller desktop/factura_mdb.spec

# Resultado:
# dist/factura-mdb.exe   (one-file, ~100-150 MB con weasyprint+lxml+pyodbc)
```

Notas:
- El `.exe` resultante es portable (no requiere Python instalado en la máquina destino), pero **sí requiere el driver Access ODBC** (no se puede embebido).
- En la primera ejecución descomprime al `%TEMP%/_MEIxxxx/`. Tarda 3-5 segundos en arrancar.
- Para reducir tamaño, puedes excluir paquetes no usados editando `excludes` en `factura_mdb.spec`.

Ver `docs/COMPILAR_EXE.md` para el detalle.

---

## 12. Sprints completados

### Sprint 1 — Lectura MDB (heredado)

- `ClienteRepoMDB.listar/obtener/buscar` — filtros por nombre/doc, paginación.
- `ProductoRepoMDB.listar/obtener` — combina `TBVENTA_DET` + `FMDB_PRODUCTOS`.
- `ComprobanteRepoMDB.listar/obtener/proximo_correlativo/listar_series`.
- `EmpresaRepoMDB.obtener` — RUC desde `EF2ALMACENES`.
- IDs sintéticos con CRC32.

Verificación: `GET /api/clientes`, `GET /api/productos`, `GET /api/comprobantes` devuelven datos reales del `.mdb` AFISCA.

### Sprint 2 — Escritura MDB (heredado)

- `ClienteWriterMDB.crear/actualizar/borrar` — INSERT/UPDATE/soft-delete sobre `EF2CLIENTES`.
- `ProductoWriterMDB.crear/actualizar/borrar` — sobre tabla auxiliar `FMDB_PRODUCTOS`.
- `ComprobanteWriterMDB.crear` — INSERT atómico CAB+DET con generación de F4NUMDOC (MAX+1).
- `ComprobanteWriterMDB.anular` — UPDATE F4ESTNUL=True por id sintético.
- `mdb_lock.write_cursor()` — context manager con manejo de rollback y `MDBLockTimeout`.

Verificación: POST /api/clientes, PUT/DELETE, POST /api/productos, POST /api/comprobantes/emitir crean filas reales en el `.mdb`.

---

## 13. Sprint 3 pendiente — emisión SUNAT desde MDB

**Objetivo:** que el endpoint `POST /api/comprobantes/{id}/enviar-sunat` funcione en modo MDB, persistiendo el CDR en `TBVENTA_CAB`.

### Estado actual (heredado)

- `comprobante_service.emitir_y_enviar()` está escrito asumiendo:
  - Sesión SQLAlchemy con modelos `Comprobante`, `ComprobanteDetalle`, `Empresa`.
  - Persistencia con `db.commit()` + `db.refresh()`.
- En modo MDB el endpoint hoy probablemente devuelve un 500 al intentar usar la sesión stub.

### Tareas Sprint 3

#### Tarea 3.1 — Adaptador MDB para emisión

Crear `backend/app/core/db_adapter/mdb_envio.py` con:

```python
def cargar_para_envio(comprobante_id: int) -> tuple[dict, list[dict]]:
    """Carga cabecera + detalles + datos de empresa listos para firma.
    
    Devuelve:
        (comprobante_dict, detalles_dicts) — mismas keys que ComprobanteOut.
    """
    from .mdb_repo import ComprobanteRepoMDB
    cab = ComprobanteRepoMDB.obtener(comprobante_id)
    if not cab:
        raise ValueError("Comprobante no encontrado")
    return cab, cab.get("detalles", [])


def persistir_envio(
    comprobante_id: int,
    cdr_codigo: str, cdr_descripcion: str,
    xml_path: Path, cdr_path: Path, hash_xml: str,
) -> None:
    """Actualiza TBVENTA_CAB tras envío exitoso a SUNAT."""
    from .mdb_lock import write_cursor
    # localizar (tipo,serie,numero) por id sintético recorriendo CAB
    # UPDATE F4ENVIADO=True, F4ESTEMI=True, F4CDR=cdr_codigo, F4CDRFECHA=now,
    #        F4CODEHASH=hash_xml, F4OBSERVA=cdr_descripcion
```

#### Tarea 3.2 — Adaptar `comprobante_service.emitir_y_enviar()`

Reescribir para que detecte modo MDB y use el adapter:

```python
def emitir_y_enviar(comprobante_id: int, db: Session = None) -> dict:
    if is_mdb_mode():
        from ..core.db_adapter.mdb_envio import cargar_para_envio, persistir_envio
        from ..core.db_adapter.mdb_repo import EmpresaRepoMDB
        from ..core.config import settings
        
        cab, detalles = cargar_para_envio(comprobante_id)
        empresa = EmpresaRepoMDB.obtener()
        cert_path = settings.CERT_PATH
        cert_pass = settings.CERT_PASS
        
        # 1. Generar XML (mismos generators)
        xml_bytes = invoice_generator.generar(cab, detalles, empresa)
        # 2. Firmar
        xml_firmado, hash_xml = firma_digital.firmar_xml(xml_bytes, cert_path, cert_pass)
        # 3. Guardar XML firmado en storage/xml/
        # 4. Empaquetar en ZIP
        # 5. Enviar a SUNAT
        cdr = sunat_client.enviar_factura(zip_bytes, ...)
        # 6. Guardar CDR
        # 7. Persistir en .mdb
        persistir_envio(comprobante_id, cdr.codigo, cdr.descripcion,
                        xml_path, cdr_path, hash_xml)
        # 8. Generar PDF
        pdf_path = pdf_generator.generar_pdf_comprobante(cab, detalles, empresa)
        return {"ok": True, "cdr_codigo": cdr.codigo, ...}
    
    # rama SQLite original (no se ejecuta)
    ...
```

#### Tarea 3.3 — Tests con AFISCA en BETA

1. Cert AFISCA ya está copiado en `certs/20518470591.pfx`.
2. Password en `config.json` → `AfiscA205184`.
3. SUNAT BETA con MODDATOS/MODDATOS.
4. Crear comprobante desde la UI (serie de prueba, ej: `F999`).
5. Botón "Emitir y enviar a SUNAT" → debe devolver CDR código `0` (aceptado).
6. Verificar que `F4ENVIADO=True`, `F4CDR='0'` en el `.mdb`.
7. Verificar que el PDF se generó con QR.

#### Tarea 3.4 — UI: estado del comprobante

Actualizar `templates/comprobantes/detail.html` para mostrar:
- Botón "Enviar a SUNAT" (si F4ENVIADO=False).
- Estado actual (Pendiente / Enviado / Aceptado / Rechazado / Anulado).
- Links de descarga XML / CDR / PDF.
- Si rechazado: motivo y botón "Reintentar".

#### Tarea 3.5 — Lock de aplicación

`mdb_writer.ComprobanteWriterMDB.crear()` calcula F4NUMDOC con `MAX+1` dentro de una transacción Access JET. JET **no garantiza serialización** contra otros procesos (SIAP corriendo en paralelo y emitiendo en la misma serie).

Implementar lock cooperativo con un archivo lockfile o un nombre de mutex Windows:

```python
# pseudo
with file_lock(PROJECT_ROOT / ".lock_emit_F001", timeout=30):
    ComprobanteWriterMDB.crear(...)
```

El lock solo previene colisiones entre instancias de Factura-mdb. SIAP no respeta este lock — pero en la práctica el cliente no usa SIAP y Factura-mdb a la vez en la misma serie (acuerdo operativo).

### Riesgos / decisiones pendientes

- **Concurrencia con SIAP**: ver Sprint 3.5. Acuerdo operativo con cliente: usar series distintas (ej: `F001`/`B001` SIAP, `F100`/`B100` Factura-mdb) hasta tener lock robusto.
- **Productos sin código**: SIAP permite ítems con F5CODPRO NULL. Factura-mdb los acepta pero el SUNAT puede rechazar — investigar si genera warning o rechazo.
- **Detracción USD**: bug ya corregido en versión SinSol heredada (currencyID=PEN para detracción aunque la factura sea USD). Verificar que la rama MDB lo respeta.
- **Notas de crédito desde MDB**: el flujo pide la referencia (F4SERREF, F4NUMREF). Debe estar en payload o leerse del comprobante referenciado.

---

## 14. Troubleshooting

| Síntoma | Causa probable | Fix |
|---|---|---|
| `verificar_setup.py` dice "Driver Access ODBC no detectado" | Falta el Microsoft Access Database Engine. | Instalar de https://www.microsoft.com/en-us/download/details.aspx?id=54920 (x64 si Python es x64). |
| `verificar_setup.py` falla con "Password del .pfx incorrecta" | Password en `config.json -> certificado.password` no coincide. | Verificar valor (sensitivity case). El cert AFISCA usa `AfiscA205184`. |
| 503 al crear cliente: "MDBLockTimeout" | SIAP tiene el .mdb en modo exclusivo. | Cerrar SIAP y reintentar. |
| 500 al crear cliente: "could not lock" | Idem. | Idem. |
| `pyodbc.OperationalError`: "[Microsoft][ODBC Driver Manager] Data source name not found" | Driver instalado pero arquitectura distinta a Python. | Reinstala el driver con la arquitectura correcta (x64 vs x86). |
| El RUC del .mdb sale distinto al de `config.json` | `EF2ALMACENES.F2RUCALM` tiene otro valor. | El log lo avisa con `WARNING`. Decide cuál es el correcto y actualiza el otro. |
| El PDF sale en blanco | weasyprint no encuentra GTK runtime (Windows). | Instalar GTK3 runtime o ejecutar weasyprint en venv con `pip install weasyprint[full]`. |
| Comprobante creado pero no aparece en lista | Posible cache del frontend. | Hard refresh (Ctrl+F5). |
| Producto creado pero no aparece | Tabla `FMDB_PRODUCTOS` puede haber fallado en CREATE. | Revisar logs en `logs/factura_mdb.log`. |

---

## 15. Limitaciones conocidas

1. **Filtros heterogéneos**: `tipo_documento` en clientes y `estado` en comprobantes se filtran en Python tras un cap mayor de filas. El `total` reportado puede ser aproximado.

2. **F2CODCLI sin padding**: SIAP usa `'1'`, `'1160'`. El writer respeta esa convención. Si alguna query SIAP esperaba `'0001'`, podría haber incompatibilidad.

3. **No hay lock fuerte**: ver Sprint 3.5.

4. **No hay tabla de empresa formal**: `EmpresaRepoMDB` lee solo el RUC del `.mdb`. Razón social, dirección, datos SUNAT vienen de `config.json`. Editar la empresa requiere editar el archivo a mano.

5. **Backup de .mdb**: `backup_service.py` está disponible pero no montado. Se sugiere que el usuario haga backup externo (Windows Task Scheduler con xcopy).

6. **Auto-updater**: no implementado. Para distribuir nuevas versiones del `.exe` hay que reemplazarlo manualmente.

7. **Multiidioma**: solo español peruano.

8. **Multi-empresa simultánea**: no soportado. Una instalación = un cliente. Para varios clientes, varios checkouts del proyecto cada uno con su `config.json`.

---

## 16. Smoke tests

### Verificación inicial

```bash
python scripts/verificar_setup.py
# Debe dar OK en: config.json, mdb.path existe, driver Access, cert.password,
# cert subject contiene RUC, SUNAT en BETA.
```

### Health endpoint

```bash
curl http://127.0.0.1:9876/api/health
# {
#   "status": "ok",
#   "service": "factura-mdb",
#   "version": "0.1.0",
#   "sunat_env": "beta",
#   "mdb_ok": true,
#   "mdb_status": "mdb (db_bancos.mdb)",
#   "ruc": "20518470591"
# }
```

### Lectura

```bash
# Clientes
curl -s "http://127.0.0.1:9876/api/clientes?limit=2" | python -m json.tool
# Espera 550+ clientes (AFISCA)

# Productos
curl -s "http://127.0.0.1:9876/api/productos?limit=20" | python -m json.tool
# Espera 10+ productos

# Comprobantes
curl -s "http://127.0.0.1:9876/api/comprobantes?limit=2" | python -m json.tool
# Espera 1352+ comprobantes (AFISCA)
```

### Escritura (¡con backup del .mdb antes!)

```bash
# Trabajar siempre en copia
cp "$(jq -r .mdb.path config.json)" /tmp/test_factura_mdb.mdb

# Apuntar config.json -> mdb.path al backup temporal
# (o usar env var FACTURA_MDB_PATH=/tmp/test_factura_mdb.mdb)

# 1. Crear cliente
curl -X POST http://127.0.0.1:9876/api/clientes \
  -H "Content-Type: application/json" \
  -d '{"tipo_documento":"6","numero_documento":"20100070970",
       "razon_social":"TEST SAC","direccion":"Av. Test 123"}'
# 201 con id (siguiente F2CODCLI)

# 2. Crear producto
curl -X POST http://127.0.0.1:9876/api/productos \
  -H "Content-Type: application/json" \
  -d '{"codigo":"TST-001","descripcion":"Test producto",
       "unidad_medida":"NIU","valor_unitario":50,"tipo_afectacion_igv":"10"}'
# 201 — INSERT en FMDB_PRODUCTOS

# 3. Crear comprobante
curl -X POST http://127.0.0.1:9876/api/comprobantes/emitir \
  -H "Content-Type: application/json" \
  -d '{
    "tipo_documento":"01","serie":"F999","fecha_emision":"2026-05-09",
    "moneda":"PEN","cliente_tipo_doc":"6","cliente_numero_doc":"20100070970",
    "cliente_razon_social":"TEST SAC",
    "items":[{"orden":1,"descripcion":"Producto Sprint 2","cantidad":2,
              "valor_unitario":50,"tipo_afectacion_igv":"10"}]
  }'
# 201 con numero_completo F999-00000001 (correlativo MAX+1)

# 4. Anular
curl -X POST http://127.0.0.1:9876/api/comprobantes/<id>/anular \
  -H "Content-Type: application/json" \
  -d '{"motivo":"Test anulación"}'
# {"ok": true} y F4ESTNUL=True en TBVENTA_CAB
```

### Verificación directa en .mdb

```python
import pyodbc
cn = pyodbc.connect('DRIVER={Microsoft Access Driver (*.mdb, *.accdb)};DBQ=/tmp/test_factura_mdb.mdb;')
cur = cn.cursor()

# Cliente creado
cur.execute("SELECT F2CODCLI, F2NOMCLI, F2NEWRUC FROM EF2CLIENTES WHERE F2NOMCLI LIKE '%TEST%'")
for r in cur: print(r)

# Producto en tabla aux
cur.execute("SELECT * FROM FMDB_PRODUCTOS")
for r in cur: print(r)

# Comprobante creado
cur.execute("SELECT F4SERDOC, F4NUMDOC, F4ESTNUL, F4ENVIADO FROM TBVENTA_CAB WHERE F4SERDOC='F999'")
for r in cur: print(r)
```

---

## Apéndice — convenciones internas

### Logging

- Usar `logging.getLogger("factura_mdb.modulo")`.
- Logs van a `logs/factura_mdb.log` y a stdout.
- Nivel: `DEBUG` si `FACTURA_MDB_DEBUG=1` (default), `INFO` si no.

### Estilo

- Ruff con line-length 100.
- Docstrings en español neutro peruano.
- Imports relativos dentro del paquete `app`.
- Type hints obligatorios en funciones públicas.

### Errores HTTP

- 400 / 422: validación de input.
- 404: recurso no encontrado.
- 409: conflicto (ej: cliente duplicado).
- 500: error interno (logueado con `logger.exception`).
- 502: error invocando SUNAT.
- 503: MDB no disponible (lock, archivo no encontrado en runtime).

### Mensajes de error

Cada `raise HTTPException` debe tener `detail` con un mensaje accionable:

- Bien: `"No se pudo abrir el .mdb. Cierra SIAP e intenta de nuevo."`
- Mal: `"Error 503"`.
