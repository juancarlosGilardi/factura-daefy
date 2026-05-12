#!/usr/bin/env pwsh
# Setup completo en una laptop nueva — TODO automatizado.
#
# Prerequisitos (instalar ANTES de correr este script):
#   1. Python 3.11 64-bit:   winget install Python.Python.3.11 --source winget
#   2. Git:                  winget install Git.Git
#   3. Clave SSH del VPS:    copiar ~/.ssh/claude-vps  desde la laptop vieja (USB)
#                            -> guardar en C:\Users\<usuario>\.ssh\claude-vps
#                            -> permisos: solo el usuario actual
#
# Uso:
#     powershell -ExecutionPolicy Bypass -File scripts\setup_nueva_laptop.ps1
#
# Esto descarga del VPS:
#   - config.json + certificado + logo (de /opt/handoff-secrets/)
#   - DBFs de Daefy (de /tmp/migracion_hermanas/daefy/DATA/)
# Y prepara el venv + dependencias + smoke test.

$ErrorActionPreference = "Stop"

# -------------------------------------------------------------------------
# CONFIG (editar si tu user/laptop es distinto)
# -------------------------------------------------------------------------
$VPS_USER  = "root"
$VPS_HOST  = "153.75.224.8"
$SSH_KEY   = "$env:USERPROFILE\.ssh\claude-vps"
$PROYECTO  = "$env:USERPROFILE\Claude Proyectos\Factura-mdb"

$env:GIT_SSH_COMMAND = "ssh -i `"$SSH_KEY`" -o StrictHostKeyChecking=no"

Write-Host "=================================================================="
Write-Host "  Setup Factura-mdb (worktree Daefy) en laptop nueva"
Write-Host "=================================================================="
Write-Host ""
Write-Host "Proyecto destino: $PROYECTO"
Write-Host "SSH key:          $SSH_KEY"
Write-Host "VPS:              $VPS_USER@$VPS_HOST"
Write-Host ""

# -------------------------------------------------------------------------
# 0. Verificar prerequisitos
# -------------------------------------------------------------------------
Write-Host "[0/8] Verificando prerequisitos..."

$pyVer = (py -3.11 -c "import sys; print(sys.version_info[:2])" 2>$null)
if (-not $pyVer) { throw "Python 3.11 NO encontrado. Instala con: winget install Python.Python.3.11" }
Write-Host "  Python 3.11: OK"

$gitVer = (git --version 2>$null)
if (-not $gitVer) { throw "Git NO encontrado. Instala con: winget install Git.Git" }
Write-Host "  Git: $gitVer"

if (-not (Test-Path $SSH_KEY)) {
    throw "SSH key NO encontrada en $SSH_KEY. Copiala desde la laptop vieja."
}
Write-Host "  SSH key: $SSH_KEY OK"

# Probar acceso al VPS
$test = (ssh -i $SSH_KEY -o ConnectTimeout=10 -o StrictHostKeyChecking=no "${VPS_USER}@${VPS_HOST}" "echo OK" 2>&1)
if ($test -notmatch "OK") { throw "No puedo conectar al VPS: $test" }
Write-Host "  Conexion VPS: OK"
Write-Host ""

# -------------------------------------------------------------------------
# 1. Clonar repo
# -------------------------------------------------------------------------
Write-Host "[1/8] Clonando Factura-mdb desde VPS..."
if (Test-Path $PROYECTO) {
    Write-Host "  $PROYECTO ya existe. Saltando clone (usa git pull si quieres actualizar)."
} else {
    git clone "ssh://${VPS_USER}@${VPS_HOST}/opt/git/factura-mdb.git" "$PROYECTO"
}
Set-Location $PROYECTO
Write-Host ""

# -------------------------------------------------------------------------
# 2. Checkout branch Daefy (worktree)
# -------------------------------------------------------------------------
Write-Host "[2/8] Checkout branch Daefy..."
git fetch origin claude/angry-wright-c69d3a 2>&1 | Out-Host
$worktreePath = "$PROYECTO\.claude\worktrees\angry-wright-c69d3a"
if (-not (Test-Path $worktreePath)) {
    git worktree add "$worktreePath" claude/angry-wright-c69d3a 2>&1 | Out-Host
} else {
    Write-Host "  Worktree ya existe."
}
Set-Location $worktreePath
Write-Host "  Branch activo: $(git branch --show-current)"
Write-Host ""

# -------------------------------------------------------------------------
# 3. Descargar secretos del VPS
# -------------------------------------------------------------------------
Write-Host "[3/8] Descargando secretos del VPS (/opt/handoff-secrets/)..."
New-Item -ItemType Directory -Force -Path "certs"      | Out-Null
New-Item -ItemType Directory -Force -Path "data\daefy" | Out-Null

scp -i "$SSH_KEY" "${VPS_USER}@${VPS_HOST}:/opt/handoff-secrets/config.json"       "config.json"
scp -i "$SSH_KEY" "${VPS_USER}@${VPS_HOST}:/opt/handoff-secrets/CT2505125697.pfx"  "certs\CT2505125697.pfx"
scp -i "$SSH_KEY" "${VPS_USER}@${VPS_HOST}:/opt/handoff-secrets/logo_demo.png"     "data\logo_demo.png"
Write-Host "  config.json, cert.pfx, logo descargados."
Write-Host ""

# -------------------------------------------------------------------------
# 4. Descargar DBFs de Daefy (con .cdx y .fpt regenerados)
# -------------------------------------------------------------------------
Write-Host "[4/8] Descargando DBFs de Daefy (5.6 MB comprimido, ~94 MB expandido)..."
# Versión completa con .cdx regenerados está en /opt/handoff-secrets/daefy_completo.tar.gz
scp -i "$SSH_KEY" "${VPS_USER}@${VPS_HOST}:/opt/handoff-secrets/daefy_completo.tar.gz" "data\daefy_completo.tar.gz"

Push-Location "data"
if (Test-Path "daefy") { Remove-Item -Recurse -Force "daefy" }
tar -xzf "daefy_completo.tar.gz"
Remove-Item "daefy_completo.tar.gz"
Pop-Location
Write-Host "  DBFs en data\daefy\  (con .cdx ya regenerados, no hace falta correr regenerar_fpt)"
Write-Host ""

# -------------------------------------------------------------------------
# 5. Crear venv Python 3.11
# -------------------------------------------------------------------------
Write-Host "[5/8] Creando venv Python 3.11..."
if (Test-Path ".venv") {
    Write-Host "  .venv ya existe. Saltando."
} else {
    py -3.11 -m venv .venv
}
$py = "$worktreePath\.venv\Scripts\python.exe"
Write-Host "  Python: $(& $py --version)"
Write-Host ""

# -------------------------------------------------------------------------
# 6. Instalar dependencias
# -------------------------------------------------------------------------
Write-Host "[6/8] Instalando dependencias (esto tarda 2-3 min)..."
& $py -m pip install --upgrade --trusted-host pypi.org --trusted-host files.pythonhosted.org pip 2>&1 | Select-Object -Last 2 | Out-Host
& $py -m pip install --trusted-host pypi.org --trusted-host files.pythonhosted.org `
    -r "backend\requirements.txt" `
    xhtml2pdf tzdata certifi 2>&1 | Select-Object -Last 3 | Out-Host
Write-Host "  Dependencias OK."
Write-Host ""

# -------------------------------------------------------------------------
# 7. Instalar CodeBase-Tools (vendor)
# -------------------------------------------------------------------------
Write-Host "[7/8] Instalando CodeBase-Tools..."
if (-not (Test-Path "vendor\Python-CodeBase-Tools")) {
    New-Item -ItemType Directory -Force -Path "vendor" | Out-Null
    git clone https://github.com/MPSystemsServices/Python-CodeBase-Tools.git "vendor\Python-CodeBase-Tools" 2>&1 | Select-Object -Last 3 | Out-Host
}
$cbtSrc = "$worktreePath\vendor\Python-CodeBase-Tools\CBToolsInstallDir\codebasetools"
$cbtDst = "$worktreePath\.venv\Lib\site-packages\codebasetools"
if (Test-Path $cbtDst) { Remove-Item -Recurse -Force $cbtDst }
Copy-Item -Recurse $cbtSrc $cbtDst

# Patch __init__.py para que cargue las DLLs correctamente
$initContent = @'
"""Python-CodeBase-Tools — parche Factura-mdb para cargar DLLs en Windows."""
import os as _os
import sys as _sys
_this_dir = _os.path.dirname(_os.path.abspath(__file__))
if _this_dir not in _sys.path:
    _sys.path.insert(0, _this_dir)
if hasattr(_os, "add_dll_directory"):
    _os.add_dll_directory(_this_dir)
_os.environ["PATH"] = _this_dir + _os.pathsep + _os.environ.get("PATH", "")
from .CodeBaseTools import _cbTools, TableObj  # noqa: F401,E402
cbTools = _cbTools
Table = TableObj
'@
Set-Content -Path "$cbtDst\__init__.py" -Value $initContent -Encoding utf8
Write-Host "  CodeBase-Tools instalado."
Write-Host ""

# -------------------------------------------------------------------------
# 8. Smoke test
# -------------------------------------------------------------------------
Write-Host "[8/8] Smoke test..."
& $py "scripts\probar_setup_local.py" 2>&1 | Select-String -Pattern "OK|FAIL|WARN" | Out-Host
Write-Host ""

Write-Host "=================================================================="
Write-Host "  SETUP COMPLETO."
Write-Host "=================================================================="
Write-Host ""
Write-Host "Para arrancar el server:"
Write-Host "  cd `"$worktreePath`""
Write-Host "  .\.venv\Scripts\python.exe scripts\arrancar_dev.py"
Write-Host ""
Write-Host "Para probar emision SUNAT BETA end-to-end:"
Write-Host "  .\.venv\Scripts\python.exe scripts\probar_sunat_directo.py"
Write-Host ""
Write-Host "Server quedara en: http://127.0.0.1:9876/docs"
