# Instalador Windows de Daefy Facturación

Genera `dist/DaefyFacturacion-Setup-1.0.0.exe` — un instalador Windows
auto-contenido (~46 MB) que el cliente final puede ejecutar con doble click,
sin necesidad de instalar Python, ni dependencias, ni configurar nada
manualmente. El wizard pregunta la ruta de los DBFs de GECOPE y deja la
app lista para abrirse con un shortcut en el escritorio.

## Resumen del flujo de build

```
┌────────────────┐    ┌──────────────────────┐    ┌────────────────────┐
│ scripts/       │    │ PyInstaller          │    │ Inno Setup         │
│  launcher.py   │ ─► │  scripts/factura.spec│ ─► │  scripts/installer │
│  (entry point) │    │  → dist/daefy-       │    │   .iss             │
│                │    │    facturacion/      │    │  → dist/Daefy-     │
│                │    │  (~120 MB carpeta)   │    │    Facturacion-    │
│                │    │                      │    │    Setup-1.0.0.exe │
│                │    │                      │    │  (~46 MB compr.)   │
└────────────────┘    └──────────────────────┘    └────────────────────┘
```

Todo orquestado por `scripts/build_installer.ps1`.

## Archivos del proyecto del instalador

| Archivo | Función |
|---|---|
| `scripts/launcher.py` | Entry point del .exe. Levanta uvicorn en `127.0.0.1:9876`, abre el navegador. Detecta si ya hay un server corriendo (idempotente). |
| `scripts/factura.spec` | Config PyInstaller. Modo one-folder. Empaqueta backend FastAPI + assets + libs (lxml, signxml, weasyprint, dbf, dbfread, etc). |
| `scripts/installer.iss` | Script Inno Setup. Wizard con: carpeta destino + selector de carpeta DBFs + tarea desktop icon. Genera `config.json` post-install. |
| `scripts/build_installer.ps1` | Orquestador. Verifica dependencias, limpia, corre PyInstaller, corre Inno Setup. |

## Pre-requisitos

- **Windows 10/11** (no funciona en Linux/Mac).
- **Python 3.11+** instalado (3.13 testeado), con `pip` accesible.
- **PowerShell 5.1+** (incluido en Windows).
- **winget** (incluido desde Windows 10 1809+) — solo si querés instalar Inno Setup automáticamente.
- Repo Factura-mdb clonado en local con todas las dependencias instaladas:

```powershell
pip install -r backend\requirements.txt
pip install pyinstaller
```

## Build completo (un solo comando)

Desde la raíz del proyecto:

```powershell
.\scripts\build_installer.ps1 -InstallInnoIfMissing
```

El flag `-InstallInnoIfMissing` instala Inno Setup vía winget si no está
en el sistema. Sin él, el script falla con instrucciones para instalarlo
manualmente.

Tiempo total esperado: **3-6 minutos** (la mayor parte es PyInstaller).

Salida final:

```
dist\
├── daefy-facturacion\               ← carpeta intermedia (PyInstaller)
│   ├── daefy-facturacion.exe        ← entry point del launcher
│   └── _internal\                   ← bytecode + DLLs + assets
└── DaefyFacturacion-Setup-1.0.0.exe ← instalador final para distribuir
```

## Builds parciales

### Solo regenerar el .exe (sin reempaquetar el instalador)

```powershell
.\scripts\build_installer.ps1 -SkipInnoSetup
```

Útil cuando estás iterando sobre el código del backend y querés probar
rápido el .exe del launcher sin esperar la compresión LZMA del instalador.

### Solo reempaquetar el instalador (sin recompilar Python)

```powershell
.\scripts\build_installer.ps1 -SkipPyInstaller
```

Útil cuando solo cambias `installer.iss` (mensajes del wizard, layout de
shortcuts, etc) y `dist/daefy-facturacion/` ya está generado.

## Pasos manuales (sin el orquestador)

Si querés correr cada paso a mano para debuggear:

```powershell
# 1. Limpiar
Remove-Item -Recurse -Force build, dist -ErrorAction SilentlyContinue

# 2. PyInstaller
pyinstaller --noconfirm --clean scripts\factura.spec

# 3. Inno Setup (path puede variar)
& "$env:LocalAppData\Programs\Inno Setup 6\ISCC.exe" scripts\installer.iss
```

Nota: `winget install JRSoftware.InnoSetup` instala Inno Setup en
`%LocalAppData%\Programs\Inno Setup 6\` (no en `Program Files`) cuando
se ejecuta sin admin. El script `build_installer.ps1` busca en ambas
ubicaciones.

## Qué hace el wizard del instalador (cliente final)

1. **Pantalla de bienvenida.**
2. **Carpeta destino** — default `C:\Program Files\Daefy-Facturacion\`.
   Requiere admin (UAC). Si el cliente quiere instalar sin admin,
   puede elegir `C:\Daefy-Facturacion\` o similar.
3. **Carpeta de DBFs de GECOPE** — pantalla custom con file picker.
   Default sugerido: `C:\GECOPE\DATA`. Verifica que existan
   `cliente.dbf`, `ventas.dbf`, `ventas_detalle.dbf`, `articulo.dbf`.
   Si faltan, advierte pero deja continuar (el cliente puede corregir
   editando `config.json` después).
4. **Tareas opcionales** — crear shortcut en escritorio (sí por default).
5. **Instalación** — copia archivos, genera `config.json` con la ruta DBF
   ingresada, copia certificado y logo.
6. **Final** — ofrece lanzar Daefy Facturación inmediatamente.

## Configuración generada (`config.json`)

El instalador escribe un `config.json` en la carpeta de instalación con:

```jsonc
{
  "dbf":  { "path": "<ruta-elegida-en-wizard>" },
  "mdb":  { "mode": "dbf", "path": "" },
  "empresa": {
    "ruc": "20615413071",
    "razon_social": "DAEFY S.A.C.",
    /* ... datos hardcoded del cliente DAEFY ... */
  },
  "sunat": {
    "ambiente": "beta",        // ← BETA por default, NO producción
    "sol_user": "DAEFY123",
    "sol_pass": "Udenthol123"
  },
  "certificado": {
    "path": "certs/daefy.pfx",
    "password": "Udenthol123"
  }
}
```

**Importante:** `sunat.ambiente` siempre se setea a `"beta"`. Para emitir
en producción real el cliente debe editar `config.json` manualmente y
cambiar a `"produccion"` (y reemplazar credenciales SOL por las reales).

Si el `config.json` ya existe (reinstalación), **NO se sobreescribe** —
preservamos los ajustes del cliente.

## Comportamiento del shortcut "Daefy Facturación"

El shortcut apunta a `daefy-facturacion.exe` con working directory en la
carpeta de instalación. Al hacer doble click:

1. Verifica si el puerto `127.0.0.1:9876` ya está ocupado.
2. **Si ya hay un server corriendo:** abre el navegador en
   `http://127.0.0.1:9876/` y termina el proceso (idempotente).
3. **Si no:** levanta uvicorn en background, espera hasta 30s a que
   responda, abre el navegador.

Esto significa que el cliente puede hacer doble click en el shortcut
varias veces sin problema — cada click solo abre una pestaña nueva en
el navegador.

Para detener el server, el cliente debe cerrar la ventana de consola
del .exe (que se queda abierta mostrando logs de uvicorn).

## Desinstalación

El uninstaller (`unins000.exe` en la carpeta de instalación, o desde
"Aplicaciones y características" de Windows) elimina:

- `daefy-facturacion.exe` y `_internal/`
- `app/` (templates y static empaquetados)
- `config.example.json`
- Shortcuts del menú inicio y escritorio.

**Preserva:**

- `config.json` (la configuración del cliente)
- `certs/` (certificados)
- `data/` (logo, etc)
- `storage/` (XMLs, CDRs, PDFs generados)
- `logs/`
- `backups/`

Esto permite reinstalar más adelante sin perder datos. Para limpieza
total, el cliente puede borrar la carpeta entera manualmente después
de desinstalar.

## Tamaños esperados

| Archivo | Tamaño |
|---|---|
| `dist/daefy-facturacion/` (carpeta) | ~120 MB |
| `dist/DaefyFacturacion-Setup-1.0.0.exe` | ~46 MB |
| Instalado en disco | ~120 MB |

La compresión LZMA2/ultra64 del instalador reduce ~120 MB a ~46 MB.

## Personalizar para otros clientes

El instalador actualmente está hardcoded para **DAEFY S.A.C.
(RUC 20615413071)**. Para empaquetar para otro cliente:

1. Editar `scripts/installer.iss` — actualizar el bloque `WriteConfigJson`
   con el RUC, razón social, ubicación, credenciales SOL y password de
   cert del nuevo cliente.
2. Reemplazar `certs/daefy.pfx` por el cert del nuevo cliente
   (mismo nombre o actualizar la línea `Source: ...` en `[Files]`).
3. Cambiar `#define AppPublisher` y `#define AppId` (importante: el
   `AppId` debe ser un GUID único por cliente para que Windows trate
   las instalaciones como aplicaciones distintas).
4. Recompilar.

Para una solución multi-cliente más limpia, valdría la pena agregar al
wizard una pantalla extra que pida RUC/razón social y los inserte en
`config.json`. No se hizo en esta versión porque el target inicial es
solo DAEFY.

## Troubleshooting

| Síntoma | Diagnóstico / Fix |
|---|---|
| `pyinstaller: command not found` | `pip install pyinstaller` en el venv activo. |
| `ISCC.exe not found` | Re-ejecutar con `-InstallInnoIfMissing`, o instalar manual desde https://jrsoftware.org/isdl.php. |
| `winget install` falla con error de cert msstore | Usar `-source winget` (el script ya lo hace). |
| El .exe arranca pero `health` devuelve `db_ok: false` | El cliente eligió mal la carpeta DBF. Editar `config.json` → `dbf.path` con la ruta correcta. |
| `port 9876 already in use` | Ya hay otro instancia corriendo (o quedó huérfana). Cerrar la ventana de consola anterior, o matar `daefy-facturacion.exe` desde el Administrador de Tareas. |
| Tamaño del instalador > 100 MB | Revisar `excludes` en `scripts/factura.spec` — agregar paquetes no usados. |
| Browser no abre tras instalar | Verificar `logs/launcher.log` en la carpeta de instalación. El server puede estar arrancando lento (DBs grandes) — esperar 30s. |

## Próximos pasos sugeridos

- **Firma del .exe.** Para evitar el warning "Windows protegió tu PC" al
  ejecutar el instalador, firmarlo con un cert de code-signing
  (no es el mismo que el cert SUNAT). Comando:
  ```powershell
  signtool sign /f code-signing.pfx /p PASSWORD `
    /tr http://timestamp.digicert.com /td sha256 /fd sha256 `
    dist\DaefyFacturacion-Setup-1.0.0.exe
  ```
- **Auto-update.** Inno Setup soporta detectar versiones previas via
  `AppId` y ofrecer upgrade. Ya está habilitado vía el `AppId` fijo.
- **Pantalla wizard que pida la password del cert.** Útil cuando se
  empaqueta el instalador SIN incluir el cert (más seguro: el cliente
  pega su .pfx aparte).
- **Servicio de Windows.** En vez del launcher manual, registrar el
  server como servicio Windows que arranca con el sistema. Requiere
  `nssm` o similar.
