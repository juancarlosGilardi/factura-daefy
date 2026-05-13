"""Daefy Facturación (Nativo) — entry point para el .exe del instalador Windows
con UI nativa basada en pywebview (WebView2 / Edge Chromium).

Diferencias con `scripts/launcher.py` (browser-based, puerto 9876):
- En lugar de abrir el navegador del sistema, levanta una ventana nativa de
  pywebview que carga la SPA local. El usuario nunca ve un navegador.
- Puerto FIJO 9877 (DIFERENTE del 9876 del browser-based) para que ambos
  instaladores puedan coexistir lado a lado en la misma máquina sin colisión.
- Idempotente: si ya hay un server en :9877, abre solo la ventana (la app
  reutiliza el server existente). Si encuentra otra instancia con ventana
  abierta, intenta traerla al frente cerrando la ventana nueva inmediatamente.
- Cuando la ventana se cierra, el proceso termina (y el server uvicorn que
  corre en thread daemon muere con él).

Resolución de paths (igual que `launcher.py`):
- Cuando corre como .exe (PyInstaller), `config.json`, `certs/` y `storage/`
  viven junto al ejecutable (típicamente `%ProgramFiles%\\Daefy-Facturacion-Nativo\\`).
- En dev plano, todo es relativo a la raíz del repo.

Flujo:
1. Resuelve install_dir, crea carpetas runtime, parsea config.json y exporta
   a env vars (`FACTURA_MDB_*`) que `app.core.config.Settings` ya respeta.
2. Levanta uvicorn en thread daemon en :9877 (si no había uno ya).
3. Espera hasta 30s a que `/api/health` responda.
4. Crea ventana pywebview 1280x800 con título "Daefy Facturación".
5. `webview.start()` bloquea el main thread; al cerrar la ventana retorna y
   el proceso termina (el thread daemon de uvicorn muere con él).
"""
from __future__ import annotations

import logging
import os
import socket
import sys
import threading
import time
from pathlib import Path


# ---------------------------------------------------------------------------
# Constantes — puerto distinto al browser-based para coexistencia.
# ---------------------------------------------------------------------------
HOST = "127.0.0.1"
PORT = 9877
URL = f"http://{HOST}:{PORT}/"

WINDOW_TITLE = "Daefy Facturación"
WINDOW_WIDTH = 1280
WINDOW_HEIGHT = 800


# ---------------------------------------------------------------------------
# Resolución de rutas — distinta para .exe (PyInstaller) vs Python plano.
# ---------------------------------------------------------------------------
def _is_frozen() -> bool:
    """True cuando corremos dentro del .exe generado por PyInstaller."""
    return getattr(sys, "frozen", False)


def _install_dir() -> Path:
    """Carpeta de instalación del cliente.

    En modo .exe es la carpeta donde vive el ejecutable
    (típicamente ``C:\\Program Files\\Daefy-Facturacion-Nativo\\``).
    En modo dev es la raíz del repo.
    """
    if _is_frozen():
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def _ensure_project_layout() -> Path:
    """Crea las carpetas runtime (logs, storage, backups) en la carpeta de
    instalación y devuelve esa ruta. NO toca config.json — eso lo crea Inno
    Setup con los datos del wizard.
    """
    install = _install_dir()
    for sub in ("logs", "storage", "storage/xml", "storage/cdr", "storage/pdf", "backups"):
        (install / sub).mkdir(parents=True, exist_ok=True)
    return install


def _setup_environment(install_dir: Path) -> None:
    """Apunta los módulos del backend al directorio de instalación.

    Igual que en `launcher.py`: cambia cwd al install_dir y exporta env vars
    leyendo `config.json` para que `app.core.config.Settings` las recoja.
    Forza puerto 9877 y SUNAT BETA por default.
    """
    os.chdir(install_dir)

    config_path = install_dir / "config.json"
    if config_path.exists():
        try:
            import json

            cfg = json.loads(config_path.read_text(encoding="utf-8"))
            mdb = cfg.get("mdb") or {}
            dbf = cfg.get("dbf") or {}
            sunat = cfg.get("sunat") or {}
            cert = cfg.get("certificado") or {}

            # Modo BD
            mode = (mdb.get("mode") or "dbf").strip().lower()
            os.environ.setdefault("FACTURA_MDB_MODE", mode)

            # Path DBF
            dbf_path = dbf.get("path", "")
            if dbf_path:
                p = Path(dbf_path)
                if not p.is_absolute():
                    p = install_dir / p
                os.environ.setdefault("FACTURA_DBF_PATH", str(p))

            # Path MDB
            mdb_path = mdb.get("path", "")
            if mdb_path:
                p = Path(mdb_path)
                if not p.is_absolute():
                    p = install_dir / p
                os.environ.setdefault("FACTURA_MDB_PATH", str(p))

            # Cert
            cert_path = cert.get("path", "")
            if cert_path:
                p = Path(cert_path)
                if not p.is_absolute():
                    p = install_dir / p
                os.environ.setdefault("FACTURA_MDB_CERT_PATH", str(p))
            if cert.get("password"):
                os.environ.setdefault("FACTURA_MDB_CERT_PASS", cert["password"])

            # SUNAT — BETA siempre por default (NUNCA producción desde el instalador)
            os.environ.setdefault(
                "FACTURA_MDB_SUNAT_ENV",
                (sunat.get("ambiente") or "beta").strip().lower(),
            )
            if sunat.get("sol_user"):
                os.environ.setdefault("FACTURA_MDB_SOL_USER", sunat["sol_user"])
            if sunat.get("sol_pass"):
                os.environ.setdefault("FACTURA_MDB_SOL_PASS", sunat["sol_pass"])

            # RUC
            ruc = (cfg.get("empresa") or {}).get("ruc")
            if ruc:
                os.environ.setdefault("FACTURA_MDB_RUC", ruc)

            # Puerto opcional desde config.json (sobrescribe el default global)
            port_cfg = cfg.get("port")
            if port_cfg:
                global PORT, URL  # noqa: PLW0603
                try:
                    PORT = int(port_cfg)
                    URL = f"http://{HOST}:{PORT}/"
                except (TypeError, ValueError):
                    pass
        except Exception as exc:  # noqa: BLE001
            print(f"[launcher_nativo] Aviso: no se pudo parsear config.json: {exc}",
                  file=sys.stderr)

    os.environ.setdefault("FACTURA_MDB_HOST", HOST)
    os.environ.setdefault("FACTURA_MDB_PORT", str(PORT))
    # Producción del .exe: NO debug (oculta /docs).
    os.environ.setdefault("FACTURA_MDB_DEBUG", "0")


# ---------------------------------------------------------------------------
# Detección de server existente — la app es idempotente.
# ---------------------------------------------------------------------------
def _is_server_up(timeout: float = 0.5) -> bool:
    """True si algo ya está respondiendo en :PORT."""
    try:
        with socket.create_connection((HOST, PORT), timeout=timeout):
            return True
    except OSError:
        return False


def _wait_for_server(timeout: float = 30.0) -> bool:
    """Polling al puerto hasta que responda o pase el timeout."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if _is_server_up(timeout=0.3):
            return True
        time.sleep(0.25)
    return False


# ---------------------------------------------------------------------------
# Servidor — uvicorn en thread daemon. Muere cuando se cierra la ventana.
# ---------------------------------------------------------------------------
def _run_server() -> None:
    # Import diferido — `_setup_environment()` ya configuró os.environ.
    import uvicorn

    from app.main import app  # noqa: WPS433

    config = uvicorn.Config(
        app,
        host=HOST,
        port=PORT,
        log_level="warning",  # menos ruido para el modo desktop
        access_log=False,
    )
    server = uvicorn.Server(config)
    server.run()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
def main() -> int:
    install_dir = _ensure_project_layout()

    log_file = install_dir / "logs" / "launcher_nativo.log"
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=[
            logging.FileHandler(log_file, encoding="utf-8"),
            logging.StreamHandler(),
        ],
    )
    log = logging.getLogger("daefy.launcher_nativo")
    log.info("Daefy Facturación (Nativo) arrancando desde %s", install_dir)

    _setup_environment(install_dir)

    # Si ya hay un server en :9877, no lo levantamos otra vez. La ventana se
    # crea igual y carga la URL — el usuario verá la app ya corriendo. Si
    # quería traer al frente otra ventana ya abierta, eso es manejado por el
    # WM de Windows (WebView2 abre ventana propia y queda al frente).
    if _is_server_up():
        log.info("Server ya activo en %s — solo abriendo ventana.", URL)
    else:
        log.info("Levantando server en %s", URL)
        server_thread = threading.Thread(target=_run_server, daemon=True)
        server_thread.start()

        if not _wait_for_server(timeout=30.0):
            log.error("El server no respondió en :%d después de 30s.", PORT)
            return 1
        log.info("Server listo en %s", URL)

    # Crear y arrancar la ventana nativa. webview.start() bloquea hasta que
    # el usuario cierre la ventana. Cuando retorna, el proceso termina y
    # uvicorn (thread daemon) muere con él.
    try:
        import webview  # noqa: WPS433
    except ImportError as exc:
        log.exception("pywebview no está instalado: %s", exc)
        return 2

    log.info("Abriendo ventana nativa pywebview (%dx%d)", WINDOW_WIDTH, WINDOW_HEIGHT)
    webview.create_window(
        WINDOW_TITLE,
        URL,
        width=WINDOW_WIDTH,
        height=WINDOW_HEIGHT,
        resizable=True,
        confirm_close=False,
    )
    # En Windows, gui="edgechromium" fuerza WebView2 (default en Win10+).
    try:
        webview.start(gui="edgechromium")
    except Exception:  # noqa: BLE001
        # Fallback al GUI por default si edgechromium no está disponible.
        log.warning("edgechromium no disponible; intentando GUI por default")
        webview.start()

    log.info("Ventana cerrada por el usuario. Saliendo.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
