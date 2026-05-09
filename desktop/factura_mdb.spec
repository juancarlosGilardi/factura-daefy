# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec para Factura-mdb.
#
# Uso:
#   pyinstaller desktop/factura_mdb.spec
#
# Genera:
#   dist/factura-mdb.exe          (Windows, one-file)

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
APP_NAME = "factura-mdb"
ICON = str(SPEC_DIR / "icon.ico") if (SPEC_DIR / "icon.ico").exists() else None
ENTRYPOINT = str(SPEC_DIR / "main.py")
ONEFILE = True

# ---------------------------------------------------------------------------
# Datas: templates + static
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

# Plantillas internas (factura_a4.html para PDF)
service_templates = BACKEND_DIR / "app" / "services" / "templates"
for p in service_templates.rglob("*"):
    if p.is_file():
        rel = p.relative_to(BACKEND_DIR / "app").parent
        datas.append((str(p), str(Path("app") / rel)))

# Recursos extras de paquetes
try:
    datas += collect_data_files("signxml")
except Exception:
    pass
try:
    datas += collect_data_files("weasyprint")
except Exception:
    pass

# ---------------------------------------------------------------------------
# Hidden imports
# ---------------------------------------------------------------------------
hiddenimports = []
hiddenimports += collect_submodules("uvicorn")
hiddenimports += collect_submodules("fastapi")
hiddenimports += collect_submodules("sqlalchemy")
hiddenimports += collect_submodules("signxml")
hiddenimports += collect_submodules("lxml")
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
]

# ---------------------------------------------------------------------------
# Análisis
# ---------------------------------------------------------------------------
a = Analysis(
    [ENTRYPOINT],
    pathex=[str(BACKEND_DIR), str(SPEC_DIR)],
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
        "matplotlib.tests",
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
        console=False,
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
        console=False,
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
