# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec para Daefy Facturación (Nativo) — variante con UI nativa
# basada en pywebview / WebView2.
#
# Diferencias vs `factura.spec` (browser-based):
#   - Entry point: `scripts/launcher_nativo.py`.
#   - Hidden imports extra: `webview`, `webview.platforms.edgechromium`, etc.
#   - Output folder: `dist/daefy-facturacion-nativo/` (carpeta DIFERENTE para
#     que pueda coexistir con la build browser-based).
#   - Console = False — la app es 100% GUI y no debe abrir ventana de consola.
#
# Uso (desde la raíz del proyecto):
#   pyinstaller scripts/factura_nativo.spec --noconfirm --clean
#
# Genera:
#   dist/daefy-facturacion-nativo/
#       daefy-facturacion-nativo.exe
#       _internal/                   (DLLs y bytecode, incluyendo WebView2 loader)
#       app/templates/, app/static/   (assets)
#       ...
#
# Inno Setup (`installer_nativo.iss`) empaqueta esa carpeta + certs/ + data/
# y produce `dist/DaefyFacturacion-Setup-Nativo-1.0.0.exe`.

import sys
from pathlib import Path
from PyInstaller.utils.hooks import collect_data_files, collect_submodules

SPEC_DIR = Path(SPECPATH).resolve()
PROJECT_ROOT = SPEC_DIR.parent
BACKEND_DIR = PROJECT_ROOT / "backend"

sys.path.insert(0, str(BACKEND_DIR))

block_cipher = None

# ---------------------------------------------------------------------------
# Configuración general
# ---------------------------------------------------------------------------
APP_NAME = "daefy-facturacion-nativo"
ENTRYPOINT = str(SPEC_DIR / "launcher_nativo.py")
ONEFILE = False  # one-folder: arranque rápido + se empaqueta con Inno Setup

# Icono (.ico) — opcional. Inno Setup también usa el mismo en el shortcut.
_ICON_CANDIDATES = [
    SPEC_DIR / "icon.ico",
    PROJECT_ROOT / "desktop" / "icon.ico",
]
ICON = next((str(p) for p in _ICON_CANDIDATES if p.exists()), None)

# ---------------------------------------------------------------------------
# Datas: templates + static + service templates (factura_a4.html para PDF)
# ---------------------------------------------------------------------------
datas = []

templates_root = BACKEND_DIR / "app" / "templates"
for p in templates_root.rglob("*"):
    if p.is_file():
        rel = p.relative_to(BACKEND_DIR / "app").parent
        datas.append((str(p), str(Path("app") / rel)))

static_root = BACKEND_DIR / "app" / "static"
for p in static_root.rglob("*"):
    if p.is_file():
        rel = p.relative_to(BACKEND_DIR / "app").parent
        datas.append((str(p), str(Path("app") / rel)))

service_templates = BACKEND_DIR / "app" / "services" / "templates"
if service_templates.exists():
    for p in service_templates.rglob("*"):
        if p.is_file():
            rel = p.relative_to(BACKEND_DIR / "app").parent
            datas.append((str(p), str(Path("app") / rel)))

# Recursos extras de paquetes externos
try:
    datas += collect_data_files("signxml")
except Exception:
    pass
try:
    datas += collect_data_files("weasyprint")
except Exception:
    pass
# pywebview trae assets propios (HTML loader, JS bridge); collect_data_files
# los empaqueta junto al exe.
try:
    datas += collect_data_files("webview")
except Exception:
    pass

# ---------------------------------------------------------------------------
# Hidden imports — paquetes que PyInstaller no detecta automáticamente.
# ---------------------------------------------------------------------------
hiddenimports = []
hiddenimports += collect_submodules("uvicorn")
hiddenimports += collect_submodules("fastapi")
hiddenimports += collect_submodules("sqlalchemy")
hiddenimports += collect_submodules("signxml")
hiddenimports += collect_submodules("lxml")
# pywebview: collect_submodules trae todos los backends (edgechromium, mshtml,
# qt, gtk, ...). PyInstaller incluirá solo los que terminen en el bundle.
hiddenimports += collect_submodules("webview")
hiddenimports += [
    "uvicorn.logging",
    "uvicorn.loops",
    "uvicorn.loops.auto",
    "uvicorn.protocols",
    "uvicorn.protocols.http",
    "uvicorn.protocols.http.auto",
    "uvicorn.protocols.websockets",
    "uvicorn.protocols.websockets.auto",
    "uvicorn.lifespan",
    "uvicorn.lifespan.on",
    "weasyprint",
    "qrcode",
    "qrcode.image.pil",
    "PIL",
    "openpyxl",
    "httpx",
    "cryptography",
    "cryptography.hazmat.backends",
    "cryptography.hazmat.bindings",
    "pyodbc",
    "dbf",
    "dbfread",
    # pywebview — backend WebView2 en Windows (default Win10+)
    "webview",
    "webview.platforms.edgechromium",
    "webview.platforms.winforms",
    "webview.window",
    "webview.util",
    "clr_loader",  # WebView2 usa clr para hablar con .NET
    # Routers del backend
    "app.api.clientes",
    "app.api.comprobantes",
    "app.api.comunicacion_baja",
    "app.api.empresas",
    "app.api.productos",
    "app.api.reportes",
    "app.api.resumen_diario",
    "app.services.dbf_importer",
    "app.services.mdb_importer",
    "app.services.xml_generators",
    "app.services.xml_models",
    "app.core.db_adapter.dbf_repo",
    "app.core.db_adapter.mdb_repo",
    "app.core.db_adapter.mdb_writer",
    "app.core.db_adapter.mdb_lock",
]

# ---------------------------------------------------------------------------
# Análisis
# ---------------------------------------------------------------------------
a = Analysis(
    [ENTRYPOINT],
    pathex=[str(BACKEND_DIR), str(SPEC_DIR), str(PROJECT_ROOT)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "pytest",
        "IPython",
        "notebook",
        "matplotlib",
        "matplotlib.tests",
        "pandas",
        "scipy",
        "tkinter",
        "tcl",
        "tk",
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

if ONEFILE:
    exe = EXE(
        pyz,
        a.scripts,
        a.binaries,
        a.zipfiles,
        a.datas,
        [],
        name=APP_NAME,
        debug=False,
        bootloader_ignore_signals=False,
        strip=False,
        upx=False,
        upx_exclude=[],
        runtime_tmpdir=None,
        console=False,  # SIN consola — app GUI nativa
        disable_windowed_traceback=False,
        target_arch=None,
        codesign_identity=None,
        entitlements_file=None,
        icon=ICON,
    )
else:
    exe = EXE(
        pyz,
        a.scripts,
        [],
        exclude_binaries=True,
        name=APP_NAME,
        debug=False,
        bootloader_ignore_signals=False,
        strip=False,
        upx=False,
        console=False,  # SIN consola — app GUI nativa
        icon=ICON,
    )
    coll = COLLECT(
        exe,
        a.binaries,
        a.zipfiles,
        a.datas,
        strip=False,
        upx=False,
        upx_exclude=[],
        name=APP_NAME,
    )
