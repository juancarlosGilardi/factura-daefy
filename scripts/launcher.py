"""Daefy Facturación — entry point para el .exe del instalador Windows.

Diferencias con `desktop/main.py`:
- NO abre una ventana PyWebView; abre el navegador del sistema en
  http://127.0.0.1:9876.
- El puerto es FIJO (9876) para que el shortcut de escritorio pueda
  reabrir la app sin levantar un nuevo servidor (idempotente).
- Si detecta que el server ya está arriba, solo abre el navegador y sale.
- Cuando se ejecuta dentro del .exe (PyInstaller), resuelve el config.json
  y los certs respecto al directorio de instalación
  (``%ProgramFiles%\\Daefy-Facturacion\\``), no respecto al exe descomprimido.

Flujo:
1. Detecta si ya hay un server en :9876. Si sí, abre el navegador y termina.
2. Si no, levanta uvicorn en un thread daemon y espera a que responda.
3. Abre http://127.0.0.1:9876/ en el navegador por defecto.
4. Bloquea hasta que el server termine (Ctrl+C o cierre del proceso).
"""
from __future__ import annotations

import logging
import os
import socket
import sys
import threading
import time
import webbrowser
from pathlib import Path


# ---------------------------------------------------------------------------
# Constantes — el puerto es fijo para que el shortcut sea idempotente.
# ---------------------------------------------------------------------------
HOST = "127.0.0.1"
PORT = 9876
URL = f"http://{HOST}:{PORT}/"


# ---------------------------------------------------------------------------
# Resolución de rutas — distinta para .exe (PyInstaller) vs Python plano.
# ---------------------------------------------------------------------------
def _is_frozen() -> bool:
    """True cuando corremos dentro del .exe generado por PyInstaller."""
    return getattr(sys, "frozen", False)


def _install_dir() -> Path:
    """Carpeta de instalación del cliente.

    En modo .exe es la carpeta donde vive el ejecutable (típicamente
    ``C:\\Program Files\\Daefy-Facturacion\\``). En modo dev es la raíz
    del repo.
    """
    if _is_frozen():
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def _ensure_project_layout() -> Path:
    """Crea las carpetas runtime (logs, storage, backups) en la carpeta de
    instalación y devuelve esa ruta. No toca config.json — eso lo crea el
    instalador Inno Setup con los datos del wizard.
    """
    install = _install_dir()
    for sub in ("logs", "storage", "storage/xml", "storage/cdr", "storage/pdf", "backups"):
        (install / sub).mkdir(parents=True, exist_ok=True)
    return install


def _setup_environment(install_dir: Path) -> None:
    """Apunta los módulos del backend al directorio de instalación.

    Importante: `app.core.config.PROJECT_ROOT` se calcula como
    `Path(__file__).parents[3]`, lo cual sirve cuando corremos como módulo
    Python normal pero NO cuando estamos dentro del .exe (donde los .py
    viven en `%TEMP%/_MEIxxxxx/`). Para que `config.json` se lea desde el
    directorio de instalación cambiamos el cwd y añadimos vars de entorno
    que `app.core.config.Settings` ya respeta.
    """
    # Cambiar cwd al install_dir — necesario para que rutas relativas
    # (`certs/daefy.pfx`, `data/logo_demo.png`) resuelvan bien.
    os.chdir(install_dir)

    # Si existe config.json en install_dir, parsearlo y exportar las vars
    # que `Settings` lee — así `PROJECT_ROOT` interno no importa.
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

            # Path DBF (si está definido, lo absolutizamos)
            dbf_path = dbf.get("path", "")
            if dbf_path:
                p = Path(dbf_path)
                if not p.is_absolute():
                    p = install_dir / p
                os.environ.setdefault("FACTURA_DBF_PATH", str(p))

            # Path MDB (si aplica)
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

            # SUNAT — BETA siempre por default desde el instalador
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
        except Exception as exc:  # noqa: BLE001
            print(f"[launcher] Aviso: no se pudo parsear config.json: {exc}", file=sys.stderr)

    # Server fijo en 127.0.0.1:9876
    os.environ.setdefault("FACTURA_MDB_HOST", HOST)
    os.environ.setdefault("FACTURA_MDB_PORT", str(PORT))
    # Producción del .exe: NO debug (oculta /docs).
    os.environ.setdefault("FACTURA_MDB_DEBUG", "0")


# ---------------------------------------------------------------------------
# Detección de server existente — el shortcut debe ser idempotente.
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
# Servidor — uvicorn en thread daemon para mantener el proceso vivo
# mientras el navegador esté abierto.
# ---------------------------------------------------------------------------
def _run_server() -> None:
    # Import diferido — `setup_environment()` ya configuró os.environ.
    import uvicorn

    from app.main import app  # noqa: WPS433

    config = uvicorn.Config(
        app,
        host=HOST,
        port=PORT,
        log_level="info",
        access_log=False,
        # Sin reload ni workers extra: es un proceso desktop.
    )
    server = uvicorn.Server(config)
    server.run()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
def main() -> int:
    install_dir = _ensure_project_layout()

    # Logging básico a `logs/launcher.log` — útil cuando no hay consola.
    log_file = install_dir / "logs" / "launcher.log"
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=[
            logging.FileHandler(log_file, encoding="utf-8"),
            logging.StreamHandler(),
        ],
    )
    log = logging.getLogger("daefy.launcher")
    log.info("Daefy Facturación arrancando desde %s", install_dir)

    # Preparar entorno antes de importar app.main (que lee config.json).
    _setup_environment(install_dir)

    # Si ya hay un server en :9876, solo abre el navegador y sal.
    if _is_server_up():
        log.info("Server ya activo en %s — abriendo navegador.", URL)
        webbrowser.open(URL)
        return 0

    log.info("Levantando server en %s", URL)
    server_thread = threading.Thread(target=_run_server, daemon=True)
    server_thread.start()

    if not _wait_for_server(timeout=30.0):
        log.error("El server no respondió en :%d después de 30s.", PORT)
        return 1

    log.info("Server listo. Abriendo navegador en %s", URL)
    webbrowser.open(URL)

    # Bloquear hasta que el server termine. El usuario cierra esta ventana
    # (o el proceso termina cuando reinicia Windows) para detener el server.
    try:
        while server_thread.is_alive():
            server_thread.join(timeout=1.0)
    except KeyboardInterrupt:
        log.info("Detenido por el usuario (Ctrl+C).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
