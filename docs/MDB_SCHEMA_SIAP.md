# Schema del .mdb SIAP

Lo que sabemos del schema SIAP tras inspeccionar 291 tablas en el `.mdb` de AFISCA. Este documento es la referencia para escribir nuevas queries o features.

## Tablas principales que usa Factura-mdb

| Tabla | Tipo | Uso |
|---|---|---|
| `EF2CLIENTES` | Maestro | CRUD de clientes desde Factura-mdb. |
| `TBVENTA_CAB` | Transaccional | Cabecera de comprobantes (factura/boleta/NC/ND). |
| `TBVENTA_DET` | Transaccional | Detalle de comprobantes. |
| `EF2ALMACENES` | Maestro | Única fuente del RUC operativo de la empresa. |
| `FMDB_PRODUCTOS` | Auxiliar (creada por Factura-mdb) | Catálogo de productos propio (NO existe en SIAP nativo). |

Las demás 286 tablas SIAP (compras, inventarios, contabilidad, planillas, etc.) NO se tocan ni se leen.

---

## EF2CLIENTES — clientes

| Columna | Tipo Access | Notas |
|---|---|---|
| `F2CODCLI` | VARCHAR(4) | PK lógica. **SIN padding** (ejemplos vistos: `'1'`, `'1160'`). El writer respeta esa convención usando `str(MAX+1)`. |
| `F2NEWRUC` | VARCHAR(11) | RUC para tipo='J'. NULL para naturales. |
| `F2DOCCLI` | VARCHAR(15) | DNI/CE/Pas para tipo='N'. NULL para jurídicos. |
| `F2NOMCLI` | VARCHAR(120) | Razón social o nombre completo. |
| `F2DIRCLI` | VARCHAR(120) | Dirección. |
| `F2TELCLI` | VARCHAR(30) | Teléfono. |
| `F2TIPDOC` | CHAR(1) | `'J'` (jurídico/RUC) o `'N'` (natural/DNI/CE/Pas). |
| `F2EMAIL` | VARCHAR(150) | Email. |
| `F2DISCLI` | VARCHAR(80) | Distrito (texto libre, no normalizado). |
| `F2CELULAR` | VARCHAR(30) | Celular. |
| `ESTADO` | BIT | True=activo, False=desactivado. **Puede ser NULL** — interpretar NULL como activo. |
| `F4ENVIADO` | BIT | Heredado, no se usa para clientes. |
| `FECING` | DATETIME | Fecha de ingreso al sistema. |
| `FECREG` | DATETIME | Fecha de registro. |
| `FecMod` | DATETIME | Fecha de última modificación. |

### Mapeo Factura-mdb ↔ SIAP

| Factura-mdb / SUNAT cat 06 | F2TIPDOC SIAP |
|---|---|
| `'6'` (RUC) | `'J'` |
| `'1'` (DNI) | `'N'` |
| `'4'` (CE) | `'N'` |
| `'7'` (Pas) | `'N'` |
| `'0'` (S/D) | `'N'` |

`mdb_writer.ClienteWriterMDB._sinsol_tipodoc_to_siap()` aplica esta conversión.

### Nota sobre F2DOCCLI vs F2NEWRUC

SIAP separa el documento del cliente en **dos columnas distintas** según el tipo:

- Si `F2TIPDOC='J'`: el RUC va en `F2NEWRUC`, `F2DOCCLI` queda NULL.
- Si `F2TIPDOC='N'`: el DNI/CE/Pas va en `F2DOCCLI`, `F2NEWRUC` queda NULL.

Las queries de lectura en `mdb_repo.ClienteRepoMDB` consideran ambas columnas y normalizan al campo único `numero_documento` que el frontend espera.

---

## TBVENTA_CAB — cabecera comprobantes

PK lógica compuesta: `(F4TIPODOCU, F4SERDOC, F4NUMDOC)`.

| Columna | Tipo Access | Notas |
|---|---|---|
| `F4TIPODOCU` | VARCHAR(2) | `'01'` factura, `'03'` boleta, `'07'` NC, `'08'` ND. |
| `F4SERDOC` | VARCHAR(4) | Serie (`F001`, `B001`, ...). |
| `F4NUMDOC` | VARCHAR(7) | Correlativo **CON padding** `"0001234"`. Generar con `f"{n:07d}"`. |
| `F4FECEMI` | DATETIME | Fecha emisión. |
| `F4TIPMON` | CHAR(1) | `'S'` PEN, `'D'` USD. |
| `F4TIPCAM` | DOUBLE | Tipo de cambio. 1.0 si PEN. |
| `F2RUCCLI` | VARCHAR(11) | RUC del cliente (si tipo=6). |
| `F2NOMCLI` | VARCHAR(120) | Razón social del cliente. |
| `F2DIRCLI` | VARCHAR(120) | Dirección del cliente. |
| `F2CODCLI` | VARCHAR(4) | FK lógica a EF2CLIENTES. |
| `F4SUBTOT` | DOUBLE | Total gravado (sin IGV). |
| `F4TOTIGV` | DOUBLE | IGV calculado. |
| `F4SUBFACINAF` | DOUBLE | Total inafecto. |
| `F4MONTOEXONERADO` | DOUBLE | Total exonerado. |
| `F4TOTFAC` | DOUBLE | Total venta (gravado + inafecto + exonerado + IGV). |
| `F4BASIMP` | DOUBLE | Base imponible (alias de gravado). |
| `F4ESTNUL` | BIT | True=anulado, False/NULL=vigente. |
| `F4ESTEMI` | BIT | True=emitido y aceptado por SUNAT (post envío). |
| `F4ENVIADO` | BIT | True=enviado a SUNAT. |
| `F4ESTFAC` | BIT | Flag SIAP — facturado. |
| `F4ESTVAL` | BIT | Flag SIAP — validado. |
| `F4CONTABLE` | BIT | Flag SIAP — contabilizado. |
| `F4CHECK` | BIT | Flag SIAP — chequeado. |
| `F4LOCAL` | BIT | Flag SIAP — local. |
| `F4ESTRECHAZO` | BIT | True=rechazado por SUNAT. |
| `F4VB` | BIT | Flag SIAP — visto bueno. |
| `F4DETRACCIONAPLICA` | BIT | True si tiene detracción. |
| `F4DETRACCIONPORC` | DOUBLE | % detracción (12.0 para 037 servicios). |
| `F4DETRACCIONMONTO` | DOUBLE | Monto detracción (S/. ó USD según moneda). |
| `F4FORPAG` | VARCHAR(3) | `'001'` Contado, `'003'` Crédito. |
| `F4FECGRA` | DATETIME | Fecha grabación. |
| `F4USEGRA` | VARCHAR(15) | Usuario que grabó. **Factura-mdb pone `"FMDB"`** para distinguir del usuario SIAP. |
| `F4DRAWBACK` | BIT | True si aplica drawback (caso especial). |
| `F4PERCEPCION` | BIT | True si tiene percepción. |
| `F4CDR` | VARCHAR(15) | Código CDR SUNAT (post envío). `"0"` aceptado, otros = error. |
| `F4CDRFECHA` | DATETIME | Fecha de recepción del CDR. |
| `F4CODEHASH` | VARCHAR(255) | Hash del XML firmado (DigestValue). |
| `F4OBSERVA` | VARCHAR(255) | Observaciones. **Factura-mdb usa prefijo `"[Anulado Factura-mdb]"` o `"[CDR ...]"`** para distinguir. |
| `F4FECMOD` | DATETIME | Fecha última modificación. |
| `F4USEMOD` | VARCHAR(15) | Usuario última modificación. |
| `F4TIPREF` | VARCHAR(2) | Tipo doc referenciado (NC/ND a doc original). |
| `F4SERREF` | VARCHAR(4) | Serie del doc referenciado. |
| `F4NUMREF` | VARCHAR(7) | Número del doc referenciado. |
| `F4SERGUI` | VARCHAR(4) | Serie de la guía de remisión asociada. |
| `F4NUMGUI` | VARCHAR(20) | Número de la guía. |

---

## TBVENTA_DET — detalle comprobantes

FK lógica a `TBVENTA_CAB` por `(F4TIPODOCU, F4SERDOC, F4NUMDOC)`.

| Columna | Tipo Access | Notas |
|---|---|---|
| `F4TIPODOCU`, `F4SERDOC`, `F4NUMDOC` | — | FK compuesta a TBVENTA_CAB. |
| `F5CODPRO` | VARCHAR(10) | Código de producto. **Puede ser NULL** (item sin código). |
| `F5NOMPRO` | VARCHAR(255) | Descripción del item. |
| `F7CODMED` | VARCHAR(3) | Código de unidad SIAP (legacy). Mapeo: |
| | | `101` → NIU (unidad) |
| | | `102` → KGM (kilogramo) |
| | | `103` → LTR (litro) |
| | | `104` → MTR (metro) |
| | | `201` → ZZ (servicio) |
| `F3CANPRO` | DOUBLE | Cantidad. |
| `F3VALVTAUNIT` | DOUBLE | Valor unitario sin IGV. |
| `F3PREUNI` | DOUBLE | Precio unitario con IGV. |
| `F3VALVTA` | DOUBLE | Valor total del item (cantidad × valor unit). |
| `F3IGV` | DOUBLE | IGV del item. |
| `F3PREVTA` | DOUBLE | Total con IGV (precio × cantidad). |
| `F3VALBRUTO`, `F3PREBRU` | DOUBLE | Aliases de F3VALVTA / F3PREVTA. |
| `F3AFECTO` | BIT | True si afecto a IGV (catálogo 7 SUNAT, códigos 10-17). |
| `F3ITEM` | INTEGER | Número de orden del item dentro del comprobante (1, 2, 3, ...). |
| `F4FECEMI` | DATETIME | Redundante; copia de TBVENTA_CAB. |
| `F4TIPMON` | CHAR(1) | Redundante; copia de TBVENTA_CAB. |

---

## EF2ALMACENES — única fuente del RUC operativo

| Columna | Tipo | Notas |
|---|---|---|
| `F2RUCALM` | VARCHAR(11) | **RUC real del cliente.** Única columna con RUC operativo en todo el .mdb. |
| `F2NOMALM` | VARCHAR(80) | Nombre del almacén (NO razón social — suele ser `"ECONOMATO"`, `"ALM01"`...). |
| `F2DIRALM` | VARCHAR(120) | Dirección del almacén. |

`EmpresaRepoMDB.obtener()`:

```python
SELECT TOP 1 F2RUCALM, F2NOMALM, F2DIRALM 
FROM EF2ALMACENES 
WHERE F2RUCALM IS NOT NULL AND LEN(F2RUCALM) = 11
```

Devuelve el RUC para sincronizar con `config.json -> empresa.ruc`. Si difieren, gana el `.mdb` con un `logger.warning`.

---

## FMDB_PRODUCTOS — tabla auxiliar Factura-mdb

**No existe en SIAP nativo.** La crea `ProductoWriterMDB.asegurar_tabla()` al primer INSERT de producto desde Factura-mdb.

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

SIAP no abre esta tabla en sus queries; queries de SIAP siguen funcionando normales.

`ProductoRepoMDB.listar()` combina:
1. Productos inferidos del histórico `TBVENTA_DET` (deduplicados por `F5CODPRO`).
2. Productos en `FMDB_PRODUCTOS` (con prioridad sobre los inferidos del mismo código).

---

## Convenciones de nombres SIAP

Los nombres de columnas en SIAP siguen un patrón histórico:

| Prefijo | Uso |
|---|---|
| `F2*` | Maestros (clientes, almacenes, ...). Ej: `F2CODCLI`, `F2NOMALM`. |
| `F3*` | Detalles (items de venta/compra). Ej: `F3CANPRO`, `F3IGV`. |
| `F4*` | Cabeceras de transacciones (comprobantes). Ej: `F4FECEMI`, `F4TOTFAC`. |
| `F5*` | Productos. Ej: `F5CODPRO`, `F5NOMPRO`. |
| `F7*` | Catálogos auxiliares (unidades de medida, tipos). Ej: `F7CODMED`. |
| `EF2*` | Maestros con prefijo "Empresa". Ej: `EF2CLIENTES`, `EF2ALMACENES`. |
| `TB*` | Tablas transaccionales. Ej: `TBVENTA_CAB`, `TBVENTA_DET`. |

Las columnas suelen tener prefijo de la "tabla base" (no necesariamente la actual). Ejemplo: `F2NOMCLI` aparece tanto en `EF2CLIENTES` como en `TBVENTA_CAB` (denormalizado para histórico).

---

## Limitaciones de Access JET (que afectan a Factura-mdb)

1. **Sin LIMIT/OFFSET nativo.** Usamos `SELECT TOP n` y paginamos en Python.

2. **Sin transacciones serializables fuertes.** Dos procesos pueden calcular el mismo `MAX(F4NUMDOC)+1` simultáneamente y crear comprobantes con el mismo correlativo. Mitigación: lock de aplicación (Sprint 3 — no implementado).

3. **BIT puede ser NULL.** Tres valores posibles: True / False / NULL. Siempre verificar `is None` antes de `bool()`.

4. **String comparison case-insensitive por default.** `F2NOMCLI = 'TEST'` matchea `'test'`. No depender de case-sensitivity.

5. **Operadores LIKE distintos.** Access usa `*` como wildcard (no `%`). pyodbc traduce automáticamente, pero hay que escapar `[`, `]`, `*`, `?`, `#` con `mdb_repo._escape_like()`.

6. **Sin booleanos directos en SQL.** `WHERE flag = TRUE` no siempre funciona; usar `WHERE flag = -1` (valor real interno) o pasar `True` como parámetro.

7. **Sin LEN() en algunos drivers viejos.** Verificar con `LEN(col) = N` o `Len(col) = N`. En la práctica funciona en `Microsoft Access Driver (*.mdb, *.accdb)` moderno.

8. **Sin tipo BIGINT.** `INTEGER` es 32 bits. Para sintetizar IDs grandes, evitar valores > 2_147_483_647 (CRC32 ya lo cumple).

9. **Sin CTE / window functions.** Queries deben hacerse con subqueries o agregaciones simples.

---

## Apéndice — query útil para inspeccionar

Listar todas las tablas del `.mdb` y su número de filas:

```python
import pyodbc
cn = pyodbc.connect('DRIVER={Microsoft Access Driver (*.mdb, *.accdb)};DBQ=/ruta/al/db.mdb;', readonly=True)
cur = cn.cursor()
for table in cur.tables(tableType="TABLE"):
    name = table.table_name
    if name.startswith("MSys"):
        continue  # tablas internas Access
    cur.execute(f"SELECT COUNT(*) FROM [{name}]")
    n = cur.fetchone()[0]
    print(f"{name:40s} {n:>10d}")
cn.close()
```

Listar columnas de una tabla:

```python
cur.execute("SELECT * FROM EF2CLIENTES WHERE 1=0")
for col in cur.description:
    print(col[0], col[1].__name__, col[3])  # nombre, tipo Python, tamaño
```
