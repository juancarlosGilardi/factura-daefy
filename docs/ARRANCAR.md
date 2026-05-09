# Cómo arrancar Factura-mdb

Guía detallada paso a paso. Si solo quieres lo mínimo, ve al [README](../README.md).

## Pre-requisitos

- Windows 10/11 (preferido) o Linux/macOS para desarrollo (en Linux/macOS NO hay driver Access — solo se puede compilar y revisar código).
- Python 3.11 o superior. Verifica con `python --version`.
- Git (opcional, para clonar / versionar tus cambios).

## Paso 1 — Clonar / descargar

Si no tienes el código, copia toda la carpeta `Factura-mdb/` a donde la quieras tener (típicamente `C:/Users/<tu-usuario>/Factura-mdb/`).

## Paso 2 — Crear entorno virtual

Recomendado para no contaminar tu Python global.

PowerShell:

```powershell
cd "C:/Users/<tu-usuario>/Factura-mdb"
python -m venv .venv
.\.venv\Scripts\Activate.ps1
```

Bash / Git Bash:

```bash
cd "C:/Users/<tu-usuario>/Factura-mdb"
python -m venv .venv
source .venv/Scripts/activate
```

Verifica que está activo:

```bash
where python      # PowerShell o Git Bash
# Debe apuntar a .venv/Scripts/python.exe
```

## Paso 3 — Instalar dependencias

```bash
pip install --upgrade pip
pip install -r backend/requirements.txt
```

Toma 1-3 minutos. weasyprint+lxml+cryptography son los más pesados.

## Paso 4 — Instalar el driver Access ODBC

**Esto NO es un paquete pip.** Es un instalador `.exe` de Microsoft (gratis).

1. Verifica si ya lo tienes:

   ```python
   python -c "import pyodbc; print([d for d in pyodbc.drivers() if 'Access' in d])"
   ```

   Si imprime una lista no vacía (`['Microsoft Access Driver (*.mdb, *.accdb)']`), ya está. Salta al paso 5.

2. Si está vacío, descarga del sitio oficial:

   https://www.microsoft.com/en-us/download/details.aspx?id=54920

3. **Importante:** la arquitectura tiene que coincidir con tu Python.

   Verifica tu arquitectura Python:

   ```python
   python -c "import struct; print(struct.calcsize('P')*8, 'bits')"
   # 64 bits  → instala AccessDatabaseEngine_X64.exe
   # 32 bits  → instala AccessDatabaseEngine.exe (x86)
   ```

4. Si tienes Office 32-bit instalado y necesitas el driver 64-bit (o viceversa), agrega `/quiet` al instalador para evitar el bloqueo:

   ```cmd
   AccessDatabaseEngine_X64.exe /quiet
   ```

5. Reinicia tu terminal y reverifica con el comando de python del paso 1.

## Paso 5 — Configurar `config.json`

```bash
# PowerShell
copy config.example.json config.json

# Bash
cp config.example.json config.json

notepad config.json
```

Edita los campos:

| Campo | Qué poner |
|---|---|
| `mdb.path` | Ruta absoluta al `.mdb` del cliente. Ejemplo: `"C:/SIAP/db_bancos.mdb"` |
| `empresa.ruc` | RUC de 11 dígitos del cliente. |
| `empresa.razon_social` | Razón social tal como aparece en RENIEC/SUNAT. |
| `empresa.direccion`, `ubigeo`, `departamento`, `provincia`, `distrito` | Datos fiscales de la empresa. |
| `sunat.ambiente` | Dejar `"beta"` hasta que el cliente apruebe producción. |
| `sunat.sol_user`, `sol_pass` | En BETA: `"MODDATOS"` / `"MODDATOS"`. En producción: las credenciales SOL reales. |
| `certificado.path` | Path al `.pfx` del cliente. Recomendado: `"certs/RUC.pfx"` (relativo). |
| `certificado.password` | Password del `.pfx`. **Pídela al cliente — no la inventes.** |
| `configuracion.aplica_detraccion`, `detraccion_codigo`, `detraccion_porcentaje` | Solo si el cliente factura servicios sujetos a detracción. |

Guarda y cierra.

## Paso 6 — Colocar el certificado

Copia el `.pfx` del cliente a `certs/`:

```bash
cp /ruta/donde/tienes/el/cert.pfx certs/RUC.pfx
```

(Reemplaza `RUC` por los 11 dígitos del RUC del cliente para no confundirte si manejas varios.)

## Paso 7 — Verificar setup

```bash
python scripts/verificar_setup.py
```

Salida esperada (todos OK):

```
======================================================================
 Verificación de Factura-mdb
======================================================================
[ OK ]  config.json existe en la raíz del proyecto
[ OK ]  config.json es JSON válido
[ OK ]  config.json tiene secciones requeridas

======================================================================
 Conectividad con .mdb
======================================================================
[ OK ]  mdb.path está configurado
[ OK ]  El archivo .mdb existe en C:/SIAP/db_bancos.mdb

======================================================================
 Driver Access (pyodbc)
======================================================================
[ OK ]  pyodbc instalado
[ OK ]  Driver Access ODBC detectado
[ OK ]  Apertura del .mdb (readonly) y query SELECT exitosa

======================================================================
 Certificado digital (.pfx)
======================================================================
[ OK ]  certificado.path configurado en config.json
[ OK ]  Archivo .pfx existe
[ OK ]  Password del .pfx correcta (cert se abre)
[ OK ]  Subject del cert contiene el RUC declarado

======================================================================
 SUNAT
======================================================================
[ OK ]  SUNAT en BETA (default seguro).

======================================================================
 Resumen
======================================================================
[ OK ]  Todos los checks críticos pasaron. Puedes arrancar la app.
```

Si algún check falla, corrige siguiendo la sugerencia y vuelve a ejecutar.

## Paso 8 — Arrancar

### Modo desarrollo (recomendado para pruebas)

```bash
python scripts/arrancar_dev.py
```

Esto levanta uvicorn con `--reload`. Edita código y se recarga solo.

Salida:

```
>>> Arrancando uvicorn en http://127.0.0.1:9876
>>> SUNAT env: BETA
>>> Ctrl+C para detener
```

Abre **http://127.0.0.1:9876** en tu navegador.

### Modo desktop (sin navegador, ventana embebida)

```bash
python desktop/main.py
```

Abre una ventana EdgeChromium con la UI dentro.

## Paso 9 — Probar la lectura del .mdb

En el navegador:

- **Dashboard** (`/`) — KPIs y resumen.
- **Clientes** (`/clientes`) — debe listar los clientes del `.mdb` (550+ para AFISCA).
- **Productos** (`/productos`) — debe listar productos inferidos del histórico.
- **Comprobantes** (`/comprobantes`) — debe listar facturas/boletas/NC históricas.

Si los listados están vacíos:
- Revisa logs en `logs/factura_mdb.log`.
- Confirma que `config.json -> mdb.path` apunta al archivo correcto.
- Re-ejecuta `python scripts/verificar_setup.py`.

## Paso 10 — (Opcional) Probar escritura

> **¡IMPORTANTE!** Antes de cualquier escritura, **haz backup del .mdb**:
>
> ```bash
> cp /ruta/al/db.mdb /ruta/al/db.BACKUP.mdb
> ```
>
> O mejor: trabaja contra una copia en `data/`:
>
> ```bash
> cp /ruta/al/db.mdb data/test.mdb
> # Edita config.json -> mdb.path = "data/test.mdb"
> ```

Luego puedes:

- Crear cliente desde `/clientes/nuevo`.
- Crear producto desde `/productos/nuevo`.
- Emitir comprobante desde `/comprobantes/emitir` (queda en estado `Pendiente`, sin enviar a SUNAT).

Para enviar a SUNAT BETA ver [`EMITIR_BETA.md`](EMITIR_BETA.md) — esa parte es Sprint 3 y aún no está implementada para modo MDB.

## Cambiar de cliente

Para usar Factura-mdb con otro cliente:

1. Edita `config.json`:
   - `mdb.path` → ruta al `.mdb` del nuevo cliente.
   - `empresa.*` → datos del nuevo cliente.
   - `certificado.path` → cert del nuevo cliente.
   - `certificado.password` → password del nuevo cert.
2. Reinicia uvicorn (Ctrl+C y vuelve a lanzar).
3. Verifica con `python scripts/verificar_setup.py`.

Si vas a manejar **varios clientes en paralelo**, lo más limpio es tener un checkout separado del proyecto por cliente, cada uno con su `config.json` y su `certs/`.
