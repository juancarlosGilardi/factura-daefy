# Compilar el .exe con PyInstaller

Guía para generar `dist/factura-mdb.exe` distribuible (Windows).

## Pre-requisitos

- Estás en Windows con el setup completo según [`ARRANCAR.md`](ARRANCAR.md).
- venv activo con todas las dependencias instaladas.
- PyInstaller instalado: `pip install pyinstaller`.

## Build

```bash
pyinstaller desktop/factura_mdb.spec
```

Tarda 2-5 minutos (descomprime y empaqueta lxml, weasyprint, signxml, ...).

Resultado:

```
dist/
└── factura-mdb.exe          (~100-150 MB)
build/                       (intermedios, se puede borrar)
```

## Estructura del .exe

`factura_mdb.spec` está configurado en modo **one-file** (`ONEFILE = True`):
- Todo el Python + librerías + assets se empaquetan en un solo `.exe`.
- Al ejecutarlo, descomprime al `%TEMP%/_MEIxxxx/` (3-5 segundos primera vez).
- Es portable: copia el `.exe` a otra máquina y funciona (sin Python instalado).

Si prefieres modo **one-folder** (carpeta con varios archivos, arranque más rápido), edita `factura_mdb.spec`:

```python
ONEFILE = False
```

Resultado:

```
dist/factura-mdb/
├── factura-mdb.exe
├── _internal/      (DLLs y assets)
└── ...
```

## Probar el .exe

```bash
.\dist\factura-mdb.exe
```

Abre la ventana de PyWebView con la app embebida. La consola se oculta (modo `windowed`).

Para debug (mantener consola visible para ver tracebacks):

Edita `factura_mdb.spec` y cambia:

```python
console=True,    # antes False
```

Recompila y reejecuta.

## Lo que el .exe NO incluye

- **Driver Microsoft Access ODBC.** El usuario destino debe instalarlo de:
  https://www.microsoft.com/en-us/download/details.aspx?id=54920
- **`config.json`.** Cada cliente necesita el suyo, con sus datos.
- **Cert `.pfx`.** Cada cliente tiene el suyo.
- **`.mdb` del cliente.**

## Estructura sugerida para distribuir

Crea un ZIP con:

```
factura-mdb-distribucion/
├── factura-mdb.exe              ← el binario
├── config.example.json          ← plantilla
├── certs/
│   └── (vacío — el cliente pega su .pfx aquí)
├── INSTALAR.md                  ← instrucciones de instalación para el cliente
└── docs/                        ← copia los docs útiles
```

## INSTALAR.md (sugerido para el cliente)

```markdown
# Instalación de Factura-mdb

1. Instala el driver de Microsoft Access:
   https://www.microsoft.com/en-us/download/details.aspx?id=54920
   (elige x64).

2. Copia toda la carpeta `factura-mdb-distribucion/` a `C:\Factura-mdb\`.

3. Copia tu certificado .pfx a `C:\Factura-mdb\certs\`. Renómbralo a `<TU_RUC>.pfx`.

4. Copia `config.example.json` a `config.json` y edita:
   - `mdb.path`: ruta al archivo .mdb donde tu SIAP guarda los datos.
   - `empresa.*`: tus datos.
   - `certificado.path`: `certs/<TU_RUC>.pfx`.
   - `certificado.password`: la password de tu .pfx.
   - `sunat.ambiente`: deja "beta" hasta que confirmes con tu contador.

5. Doble-click en `factura-mdb.exe`. Debe abrir la ventana de la app.

6. Si algo falla, abre `logs/factura_mdb.log` y envía el contenido al soporte.
```

## Reducir el tamaño del .exe

El `.exe` es ~100-150 MB porque incluye:
- weasyprint (~30 MB con sus dependencias)
- lxml (~10 MB)
- signxml + cryptography (~15 MB)
- pyodbc (~5 MB)
- el resto: pydantic, fastapi, uvicorn, jinja, pywebview, ...

Para reducir:

1. **Excluir paquetes opcionales** en `factura_mdb.spec`:

   ```python
   excludes=[
       "pytest", "IPython", "notebook", "matplotlib.tests",
       "pandas",   # si no se usa
       "scipy",    # si no se usa
       "tkinter",  # PyWebView usa Edge, no tk
   ],
   ```

2. **UPX** (compresión de DLLs/EXE):

   ```python
   upx=True,
   upx_exclude=["vcruntime140.dll", "python311.dll"],
   ```

   Requiere UPX instalado: https://upx.github.io/

3. **Modo one-folder** evita la descompresión doble y reduce el tamaño efectivo.

## Firmar el .exe (opcional)

Para evitar advertencias de SmartScreen al ejecutar el `.exe` en otra máquina, fírmalo con un cert de code-signing (no es el mismo del SUNAT):

```bash
signtool sign /f mi-cert-codesigning.pfx /p PASSWORD /tr http://timestamp.digicert.com /td sha256 /fd sha256 dist/factura-mdb.exe
```

Sin firma: el primer usuario verá "Windows protegió tu PC" y debe hacer click en "Más información" → "Ejecutar de todos modos".

## Troubleshooting build

| Error | Fix |
|---|---|
| `ModuleNotFoundError` al ejecutar el .exe | Agregar el módulo a `hiddenimports` en `factura_mdb.spec`. |
| `FileNotFoundError` al cargar template | El template no entró en `datas`. Verificar que `templates_root.rglob("*")` lo recoge. |
| `OSError: cannot find weasyprint dependencies` | Usuario destino no tiene GTK3 runtime. Sugerir `pip install weasyprint[full]` o instalar GTK3 manual. |
| El .exe tarda mucho en arrancar | Modo one-file descomprime al TEMP cada vez. Cambiar a one-folder. |
| `ImportError: DLL load failed` para pyodbc | Driver Access no instalado en máquina destino. |
