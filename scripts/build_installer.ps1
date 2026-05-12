<#
.SYNOPSIS
    Orquesta el build completo del instalador Windows de Daefy Facturación.

.DESCRIPTION
    Pasos:
      1. Verifica/instala dependencias (PyInstaller, Inno Setup).
      2. Limpia build/ y dist/ previos.
      3. Corre PyInstaller con scripts/factura.spec → dist/daefy-facturacion/.
      4. Corre Inno Setup con scripts/installer.iss → dist/DaefyFacturacion-Setup-1.0.0.exe.
      5. Reporta tamaño final y ruta del instalador.

    El script asume que:
      - Estás en Windows con Python 3.11+ instalado y `pip` accesible.
      - El venv del proyecto (si lo usas) está activo.
      - Tienes permisos de admin si Inno Setup necesita instalarse vía winget.

.PARAMETER SkipPyInstaller
    Salta el paso PyInstaller (útil si ya generaste dist/daefy-facturacion/
    y solo querés reempaquetar con Inno Setup).

.PARAMETER SkipInnoSetup
    Salta el paso Inno Setup (útil para probar solo el .exe del launcher).

.PARAMETER InstallInnoIfMissing
    Si Inno Setup no está instalado, intenta instalarlo con winget.
    Sin esta flag, falla con un mensaje explicativo.

.EXAMPLE
    .\scripts\build_installer.ps1

.EXAMPLE
    .\scripts\build_installer.ps1 -SkipPyInstaller   # solo reempaquetar

.EXAMPLE
    .\scripts\build_installer.ps1 -InstallInnoIfMissing
#>

[CmdletBinding()]
param(
    [switch]$SkipPyInstaller,
    [switch]$SkipInnoSetup,
    [switch]$InstallInnoIfMissing
)

$ErrorActionPreference = 'Stop'

# ----------------------------------------------------------------------------
# Setup
# ----------------------------------------------------------------------------
$ScriptDir   = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectRoot = Split-Path -Parent $ScriptDir
$DistDir     = Join-Path $ProjectRoot 'dist'
$BuildDir    = Join-Path $ProjectRoot 'build'
$SpecFile    = Join-Path $ScriptDir   'factura.spec'
$IssFile     = Join-Path $ScriptDir   'installer.iss'

Write-Host ""
Write-Host "===============================================================" -ForegroundColor Cyan
Write-Host "  Daefy Facturación — Build del instalador Windows" -ForegroundColor Cyan
Write-Host "===============================================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "Project root : $ProjectRoot"
Write-Host "Spec file    : $SpecFile"
Write-Host "ISS file     : $IssFile"
Write-Host "Output dir   : $DistDir"
Write-Host ""

# ----------------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------------
function Find-InnoSetup {
    # winget instala Inno Setup en %LocalAppData%\Programs (no en Program Files)
    # cuando no se ejecuta como admin. Cubrimos ambas rutas.
    $candidates = @(
        "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
        "${env:ProgramFiles}\Inno Setup 6\ISCC.exe",
        "${env:LocalAppData}\Programs\Inno Setup 6\ISCC.exe",
        "${env:ProgramFiles(x86)}\Inno Setup 5\ISCC.exe",
        "${env:ProgramFiles}\Inno Setup 5\ISCC.exe",
        "${env:LocalAppData}\Programs\Inno Setup 5\ISCC.exe"
    )
    foreach ($c in $candidates) {
        if ($c -and (Test-Path $c)) { return $c }
    }
    return $null
}

function Install-InnoSetupViaWinget {
    Write-Host "Instalando Inno Setup vía winget..." -ForegroundColor Yellow
    $winget = Get-Command winget -ErrorAction SilentlyContinue
    if (-not $winget) {
        throw "winget no está disponible. Instala Inno Setup manualmente desde https://jrsoftware.org/isdl.php"
    }
    & winget install --id JRSoftware.InnoSetup -e --accept-source-agreements --accept-package-agreements
    if ($LASTEXITCODE -ne 0) {
        throw "winget falló al instalar Inno Setup (exit $LASTEXITCODE)."
    }
    Write-Host "Inno Setup instalado." -ForegroundColor Green
}

function Get-FileSizeMB {
    param([string]$Path)
    if (-not (Test-Path $Path)) { return 0 }
    $bytes = (Get-Item $Path).Length
    return [math]::Round($bytes / 1MB, 1)
}

# ----------------------------------------------------------------------------
# 1. Verificar Python + PyInstaller
# ----------------------------------------------------------------------------
Write-Host "[1/5] Verificando Python y PyInstaller..." -ForegroundColor Cyan

$python = Get-Command python -ErrorAction SilentlyContinue
if (-not $python) {
    throw "Python no encontrado en PATH. Instala Python 3.11+ desde https://python.org"
}
$pyVersion = & python --version
Write-Host "  Python: $pyVersion"

$pyInstaller = Get-Command pyinstaller -ErrorAction SilentlyContinue
if (-not $pyInstaller) {
    Write-Host "  PyInstaller no instalado. Instalando..." -ForegroundColor Yellow
    & python -m pip install --quiet pyinstaller
    if ($LASTEXITCODE -ne 0) { throw "Falló pip install pyinstaller" }
    $pyInstaller = Get-Command pyinstaller -ErrorAction SilentlyContinue
    if (-not $pyInstaller) { throw "PyInstaller sigue no encontrado tras instalar." }
}
Write-Host "  PyInstaller: $((& pyinstaller --version))"

# ----------------------------------------------------------------------------
# 2. Verificar Inno Setup
# ----------------------------------------------------------------------------
Write-Host ""
Write-Host "[2/5] Verificando Inno Setup..." -ForegroundColor Cyan

$iscc = Find-InnoSetup
if (-not $iscc) {
    if ($SkipInnoSetup) {
        Write-Host "  Inno Setup no encontrado, pero -SkipInnoSetup activo. OK." -ForegroundColor Yellow
    } elseif ($InstallInnoIfMissing) {
        Install-InnoSetupViaWinget
        $iscc = Find-InnoSetup
        if (-not $iscc) {
            throw "Inno Setup instalado pero no se encontró ISCC.exe. Reinicia la terminal e intenta de nuevo."
        }
    } else {
        Write-Host ""
        Write-Host "ERROR: Inno Setup no está instalado." -ForegroundColor Red
        Write-Host "Opciones:" -ForegroundColor Yellow
        Write-Host "  1. Re-ejecutar con -InstallInnoIfMissing (instala vía winget)."
        Write-Host "  2. Instalar manualmente desde https://jrsoftware.org/isdl.php"
        Write-Host "  3. Re-ejecutar con -SkipInnoSetup para solo generar el .exe."
        throw "Inno Setup no encontrado."
    }
} else {
    Write-Host "  ISCC: $iscc"
}

# ----------------------------------------------------------------------------
# 3. Limpieza
# ----------------------------------------------------------------------------
Write-Host ""
Write-Host "[3/5] Limpiando build/ y dist/ previos..." -ForegroundColor Cyan
foreach ($d in @($BuildDir, $DistDir)) {
    if (Test-Path $d) {
        Write-Host "  Removiendo $d"
        Remove-Item -Recurse -Force $d
    }
}
New-Item -ItemType Directory -Path $DistDir -Force | Out-Null

# ----------------------------------------------------------------------------
# 4. PyInstaller
# ----------------------------------------------------------------------------
if (-not $SkipPyInstaller) {
    Write-Host ""
    Write-Host "[4/5] Corriendo PyInstaller (puede tardar 2-5 min)..." -ForegroundColor Cyan
    Push-Location $ProjectRoot
    try {
        & pyinstaller --noconfirm --clean --distpath "$DistDir" --workpath "$BuildDir" "$SpecFile"
        if ($LASTEXITCODE -ne 0) {
            throw "PyInstaller falló con exit code $LASTEXITCODE"
        }
    } finally {
        Pop-Location
    }

    $exePath = Join-Path $DistDir 'daefy-facturacion\daefy-facturacion.exe'
    if (-not (Test-Path $exePath)) {
        throw "PyInstaller terminó OK pero no se encontró $exePath"
    }
    Write-Host ""
    Write-Host "  EXE generado: $exePath" -ForegroundColor Green
    Write-Host "  Tamaño carpeta: $(Get-FileSizeMB (Join-Path $DistDir 'daefy-facturacion')) MB (estimado)"
} else {
    Write-Host ""
    Write-Host "[4/5] Saltando PyInstaller (-SkipPyInstaller)." -ForegroundColor Yellow
}

# ----------------------------------------------------------------------------
# 5. Inno Setup
# ----------------------------------------------------------------------------
if (-not $SkipInnoSetup) {
    Write-Host ""
    Write-Host "[5/5] Corriendo Inno Setup..." -ForegroundColor Cyan
    & "$iscc" "$IssFile"
    if ($LASTEXITCODE -ne 0) {
        throw "Inno Setup falló con exit code $LASTEXITCODE"
    }

    $installerPath = Join-Path $DistDir 'DaefyFacturacion-Setup-1.0.0.exe'
    if (-not (Test-Path $installerPath)) {
        throw "Inno Setup terminó OK pero no se encontró $installerPath"
    }
    $sizeMB = Get-FileSizeMB $installerPath
    Write-Host ""
    Write-Host "===============================================================" -ForegroundColor Green
    Write-Host "  BUILD EXITOSO" -ForegroundColor Green
    Write-Host "===============================================================" -ForegroundColor Green
    Write-Host "  Instalador: $installerPath"
    Write-Host "  Tamaño    : $sizeMB MB"
    Write-Host ""
    Write-Host "Próximos pasos:"
    Write-Host "  1. Probar el instalador localmente: doble click."
    Write-Host "  2. Verificar que crea config.json con la ruta DBF correcta."
    Write-Host "  3. Distribuir el .exe al cliente."
    Write-Host ""
} else {
    Write-Host ""
    Write-Host "[5/5] Saltando Inno Setup (-SkipInnoSetup)." -ForegroundColor Yellow
    Write-Host ""
    Write-Host "Build parcial completado. Solo se genero el .exe del launcher."
    Write-Host ""
}
