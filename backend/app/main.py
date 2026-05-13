"""Factura-mdb — backend FastAPI local que opera contra MDB/DBF/SQLite.

Coexiste con SIAP legacy del cliente (mismo archivo .mdb compartido).
Modo lecto-escritura desde el primer arranque (no hay modo SQLite ni
flujo de onboarding cloud).

Flujo:
1. Carga `config.json` y valida que el .mdb está accesible.
2. Sirve la UI Jinja+Alpine en http://127.0.0.1:<puerto>.
3. Operaciones REST bajo /api/* (clientes, productos, comprobantes,
   reportes, comunicación de baja, resumen diario).

SUNAT está en BETA por default. Para emitir en producción se debe setear
explícitamente `sunat.ambiente = "produccion"` en `config.json` o la env
var `FACTURA_MDB_SUNAT_ENV=produccion`.
"""
from __future__ import annotations

import logging
import mimetypes
from contextlib import asynccontextmanager
from pathlib import Path

# CRÍTICO: en Windows, mimetypes lee del registro y a veces devuelve text/plain
# para .js, lo que rompe los module scripts del SPA React. Forzar tipos correctos.
mimetypes.add_type("application/javascript", ".js")
mimetypes.add_type("application/javascript", ".mjs")
mimetypes.add_type("text/css", ".css")
mimetypes.add_type("application/json", ".json")
mimetypes.add_type("image/svg+xml", ".svg")

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from .core.config import log_dir, settings
from .core.db_adapter import describe_mode, get_dbf_path, get_mdb_path, is_dbf_mode
from .services.mdb_importer.connection import conectar


# ---------------------------------------------------------------------------
# Logging — archivo + stdout. Carpeta de logs creada por config.log_dir().
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.DEBUG if settings.DEBUG else logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.FileHandler(log_dir() / "factura_mdb.log", encoding="utf-8"),
        logging.StreamHandler(),
    ],
)
logger = logging.getLogger("factura_mdb")


# ---------------------------------------------------------------------------
# Empresa y configuración fake — se rellenan desde config.json al arrancar.
# Mantienen interface compatible con los templates Jinja heredados (que
# acceden a atributos tipo `empresa.razon_social`, `config.igv_rate`...).
# ---------------------------------------------------------------------------
class _EmpresaFromConfig:
    """Empresa construida desde config.json + RUC dinámico del .mdb."""

    def __init__(self) -> None:
        emp = settings.EMPRESA or {}
        self.id = 1
        self.ruc = emp.get("ruc", settings.RUC or "")
        self.razon_social = emp.get("razon_social", "")
        self.nombre_comercial = emp.get("nombre_comercial") or self.razon_social
        self.direccion = emp.get("direccion", "")
        self.ubigeo = emp.get("ubigeo", "")
        self.departamento = emp.get("departamento", "")
        self.provincia = emp.get("provincia", "")
        self.distrito = emp.get("distrito", "")
        self.telefono = emp.get("telefono")
        self.email = emp.get("email")
        self.sitio_web = emp.get("sitio_web")
        # Resolver logo_path como absoluto (relativo a PROJECT_ROOT en config.json)
        _logo = emp.get("logo_path")
        if _logo:
            from pathlib import Path as _P
            _p = _P(_logo)
            if not _p.is_absolute():
                _p = settings.PROJECT_ROOT / _p
            self.logo_path = str(_p.resolve()) if _p.exists() else None
        else:
            self.logo_path = None
        self.sol_user = settings.SOL_USER
        self.sol_pass = settings.SOL_PASS
        self.sunat_env = settings.SUNAT_ENV
        self.certificado_path = str(settings.CERT_PATH) if settings.CERT_PATH else None
        self.certificado_pass = settings.CERT_PASS
        self.certificado_vence = None
        self.created_at = None
        self.updated_at = None


class _ConfigFromConfig:
    """Configuración global construida desde config.json."""

    def __init__(self) -> None:
        cfg = (settings._config.get("configuracion") or {}) if hasattr(settings, "_config") else {}
        self.id = 1
        self.igv_rate = float(cfg.get("igv_rate", 18.0))
        self.formato_impresion = cfg.get("formato_impresion", "A4")
        self.cuenta_bcp = cfg.get("cuenta_bcp")
        self.cuenta_bcp_moneda = cfg.get("cuenta_bcp_moneda", "PEN")
        self.cta_banco_nacion = cfg.get("cta_banco_nacion")
        self.aplica_detraccion = bool(cfg.get("aplica_detraccion", False))
        self.detraccion_codigo = cfg.get("detraccion_codigo")
        self.detraccion_porcentaje = float(cfg.get("detraccion_porcentaje", 0.0))
        self.pie_pagina = cfg.get("pie_pagina")
        self.moneda_default = cfg.get("moneda_default", "PEN")
        self.auto_envio_sunat = bool(cfg.get("auto_envio_sunat", False))
        self.backup_automatico = bool(cfg.get("backup_automatico", False))
        self.onboarding_completado = True  # No hay onboarding en Factura-mdb
        self.created_at = None
        self.updated_at = None


_FAKE_EMPRESA_MDB = _EmpresaFromConfig()
_FAKE_CONFIG_MDB = _ConfigFromConfig()


# ---------------------------------------------------------------------------
# Lifespan — verifica que la BD del modo activo es accesible al arrancar.
# ---------------------------------------------------------------------------
@asynccontextmanager
async def lifespan(_app: FastAPI):
    logger.info("Factura-mdb v%s arrancando...", settings.APP_VERSION)
    logger.info("Modo BD: %s", describe_mode())
    logger.info("Modo SUNAT: %s", settings.SUNAT_ENV.upper())
    if settings.SUNAT_ENV == "produccion":
        logger.warning(
            "MODO PRODUCCIÓN ACTIVO. Las emisiones llegarán a SUNAT real."
        )
    try:
        if is_dbf_mode():
            # Modo DBF (Visual FoxPro / GECOPE): valida que la carpeta exista
            # y que las tablas claves estén presentes.
            dbf_dir = get_dbf_path()
            required = ["cliente.dbf", "ventas.dbf", "ventas_detalle.dbf"]
            faltan = [t for t in required if not (dbf_dir / t).exists()]
            if faltan:
                raise RuntimeError(
                    f"Faltan tablas DBF en {dbf_dir}: {', '.join(faltan)}"
                )
            logger.info("DBF accesible: %s (%d tablas)", dbf_dir, len(required))
        else:
            # Modo MDB (Access) — abre y cierra para verificar conexión.
            cn = conectar(get_mdb_path())
            cn.close()
            logger.info("MDB accesible: %s", describe_mode())
    except Exception as exc:  # noqa: BLE001
        logger.exception("No se pudo abrir la BD: %s", exc)
        # No raise: dejamos arrancar para que la UI muestre el error.
    yield
    logger.info("Factura-mdb cerrado")


# ---------------------------------------------------------------------------
# FastAPI app
# ---------------------------------------------------------------------------
app = FastAPI(
    title="Factura-mdb",
    description=(
        "Facturador SUNAT que lee/escribe directo a .mdb (Access) compartido "
        "con SIAP legacy del cliente."
    ),
    version=settings.APP_VERSION,
    lifespan=lifespan,
    docs_url="/docs" if settings.DEBUG else None,
    redoc_url=None,
)


# ---------------------------------------------------------------------------
# Middleware: restringir a loopback (la app es local — nadie de la LAN
# debería tocar el API).
# ---------------------------------------------------------------------------
_LOOPBACK_HOSTS = {"127.0.0.1", "::1", "localhost"}


@app.middleware("http")
async def restrict_to_loopback(request: Request, call_next):
    client_host = request.client.host if request.client else ""
    if client_host not in _LOOPBACK_HOSTS:
        return JSONResponse(
            status_code=403,
            content={"detail": "Acceso restringido a localhost"},
        )
    return await call_next(request)


@app.middleware("http")
async def _security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "same-origin"
    response.headers["Permissions-Policy"] = (
        "camera=(), microphone=(), geolocation=()"
    )
    return response


# ---------------------------------------------------------------------------
# Templates y static
# ---------------------------------------------------------------------------
TEMPLATES_DIR = Path(__file__).parent / "templates"
STATIC_DIR = Path(__file__).parent / "static"
SPA_DIR = STATIC_DIR / "spa"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

# Frontend React SPA (FacturaPE-MultiEmpresa) compilado en static/spa.
# Servimos /assets/* para JS/CSS y dejamos el catch-all al final del archivo
# que devuelve index.html para todas las rutas no-API.
if (SPA_DIR / "assets").is_dir():
    app.mount(
        "/assets",
        StaticFiles(directory=str(SPA_DIR / "assets")),
        name="spa_assets",
    )


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------
@app.get("/api/health")
def health():
    """Estado del servicio."""
    from .core.db_adapter import is_dbf_mode, is_mdb_mode

    bd_ok = False
    bd_msg = describe_mode()
    try:
        if is_dbf_mode():
            get_dbf_path()
            bd_ok = True
        elif is_mdb_mode():
            cn = conectar(get_mdb_path())
            cn.close()
            bd_ok = True
    except Exception as exc:  # noqa: BLE001
        bd_ok = False
        bd_msg = str(exc)

    return {
        "status": "ok" if bd_ok else "degraded",
        "service": "factura-mdb",
        "version": settings.APP_VERSION,
        "db_mode": settings.MDB_MODE,
        "sunat_env": settings.SUNAT_ENV,
        "db_ok": bd_ok,
        "db_status": bd_msg,
        "ruc": settings.RUC or _FAKE_EMPRESA_MDB.ruc,
    }


# ---------------------------------------------------------------------------
# Contexto base para templates Jinja
# ---------------------------------------------------------------------------
def _ctx(request: Request) -> dict:
    """Contexto base — empresa+config desde config.json, RUC dinámico del .mdb
    si está disponible (ver `EmpresaRepoMDB.obtener()`)."""
    empresa_obj = _FAKE_EMPRESA_MDB
    try:
        from .core.db_adapter.mdb_repo import EmpresaRepoMDB
        ruc_real = (EmpresaRepoMDB.obtener() or {}).get("ruc")
        if ruc_real and ruc_real != empresa_obj.ruc:
            # Sincronización suave: el RUC del .mdb gana cuando difiere
            # del config.json (alerta visible en UI/logs).
            logger.warning(
                "RUC del .mdb (%s) difiere del config.json (%s); usando .mdb",
                ruc_real, empresa_obj.ruc,
            )
            empresa_obj.ruc = ruc_real
    except Exception:  # noqa: BLE001
        # Si no se puede leer el .mdb, seguimos con el del config.
        logger.debug("EmpresaRepoMDB.obtener() falló; usando RUC del config")
    return {
        "request": request,
        "empresa": empresa_obj,
        "config": _FAKE_CONFIG_MDB,
    }


# ---------------------------------------------------------------------------
# Páginas (Jinja heredadas) — DESACTIVADAS para que el SPA React tome control.
# El catch-all al final del archivo sirve index.html para todas las rutas
# no-API. Mantenemos las rutas heredadas comentadas como referencia.
# ---------------------------------------------------------------------------
# @app.get("/", ...)              -> SPA React
# @app.get("/comprobantes", ...)  -> SPA React
# @app.get("/comprobantes/emitir", ...) -> SPA React (NuevaEmision.jsx)
# @app.get("/clientes", ...)      -> SPA React
# @app.get("/productos", ...)     -> SPA React


# Comunicación de baja
@app.get("/comunicacion-baja", response_class=HTMLResponse)
def page_comunicacion_baja(request: Request):
    return templates.TemplateResponse("comunicacion_baja/list.html", _ctx(request))


@app.get("/comunicacion-baja/nueva", response_class=HTMLResponse)
def page_comunicacion_baja_nueva(request: Request):
    return templates.TemplateResponse("comunicacion_baja/nueva.html", _ctx(request))


# Resumen diario
@app.get("/resumen-diario", response_class=HTMLResponse)
def page_resumen_diario(request: Request):
    return templates.TemplateResponse("resumen_diario/list.html", _ctx(request))


@app.get("/resumen-diario/nuevo", response_class=HTMLResponse)
def page_resumen_diario_nuevo(request: Request):
    return templates.TemplateResponse("resumen_diario/nuevo.html", _ctx(request))


# Reportes y configuración
@app.get("/reportes", response_class=HTMLResponse)
def page_reportes(request: Request):
    return templates.TemplateResponse("reportes/index.html", _ctx(request))


@app.get("/configuracion", response_class=HTMLResponse)
def page_configuracion(request: Request):
    return templates.TemplateResponse("configuracion.html", _ctx(request))


# ---------------------------------------------------------------------------
# Routers REST
# ---------------------------------------------------------------------------
from .api import (  # noqa: E402
    clientes,
    comprobantes,
    comunicacion_baja,
    email_envio,
    empresas,
    importar,
    legacy_emision,
    productos,
    reportes,
    resumen_diario,
)

# Router legacy (compatibilidad con el SPA React de FacturaPE-MultiEmpresa).
# Debe ir ANTES del catch-all del SPA y antes de comprobantes.router para
# que /api/comprobantes/emitir matchee primero el shape antiguo.
app.include_router(legacy_emision.router)

app.include_router(empresas.router)
app.include_router(empresas.plural_router)
app.include_router(empresas.config_router)
app.include_router(clientes.router)
app.include_router(productos.router)
# Importador de productos NUEVOS desde DBF de empresa matriz (IDIVSA → Daefy).
# Comparte prefijo /api/productos con productos.router pero solo agrega
# rutas POST estaticas (no choca con /{producto_id}).
app.include_router(importar.router)
app.include_router(comprobantes.router)
app.include_router(comprobantes.correlativos_router)
app.include_router(email_envio.router)
app.include_router(comunicacion_baja.router)
app.include_router(resumen_diario.router)
app.include_router(reportes.router)
app.include_router(cotizaciones_pedidos.router)
app.include_router(email_envio.router)


# ---------------------------------------------------------------------------
# Catch-all del SPA — DEBE ir despues de todos los routers REST y de los
# mounts de /assets, /static. Sirve index.html para cualquier ruta que NO
# sea /api, /docs, /openapi, /static o /assets.
# ---------------------------------------------------------------------------
_SPA_INDEX = SPA_DIR / "index.html"


@app.get("/{full_path:path}", include_in_schema=False)
async def spa_catch_all(full_path: str):
    """Devuelve index.html para que React Router resuelva la ruta."""
    excluded_prefixes = ("api/", "api", "docs", "openapi", "static/",
                         "assets/", "redoc")
    if any(full_path == p.rstrip("/") or full_path.startswith(p)
           for p in excluded_prefixes):
        raise HTTPException(status_code=404, detail="Not found")
    if not _SPA_INDEX.exists():
        raise HTTPException(
            status_code=503,
            detail="SPA no disponible. Falta backend/app/static/spa/index.html",
        )
    return FileResponse(str(_SPA_INDEX), media_type="text/html")

