"""Factura-mdb — entry point desktop con PyWebView.

Levanta el backend FastAPI en un thread con uvicorn en puerto aleatorio,
luego abre una ventana WebKit/EdgeChromium apuntando a localhost:<puerto>.

Cuando se cierra la ventana, el thread daemon muere y la app termina.
"""
from __future__ import annotations

import socket
import sys
import threading
import time
from pathlib import Path

import uvicorn
import webview

# Asegurar que el backend sea importable
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "backend"))

from app.core.config import settings  # noqa: E402
from app.main import app  # noqa: E402


def find_free_port() -> int:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def run_server(port: int) -> None:
    config = uvicorn.Config(
        app,
        host="127.0.0.1",
        port=port,
        log_level="info",
        access_log=False,
    )
    server = uvicorn.Server(config)
    server.run()


def wait_for_server(port: int, timeout: float = 10.0) -> bool:
    """Polling al puerto hasta que el server responda o pase timeout."""
    import urllib.error
    import urllib.request

    deadline = time.time() + timeout
    url = f"http://127.0.0.1:{port}/api/health"
    while time.time() < deadline:
        try:
            urllib.request.urlopen(url, timeout=0.5)
            return True
        except (urllib.error.URLError, ConnectionResetError, OSError):
            time.sleep(0.2)
    return False


def main() -> None:
    port = find_free_port()

    server_thread = threading.Thread(
        target=run_server, args=(port,), daemon=True,
    )
    server_thread.start()

    if not wait_for_server(port):
        print(f"ERROR: el server no respondió en :{port}", file=sys.stderr)
        sys.exit(1)

    url = f"http://127.0.0.1:{port}/"
    webview.create_window(
        title="Factura-mdb — Facturación SUNAT (.mdb)",
        url=url,
        width=1280,
        height=820,
        min_size=(1024, 700),
        resizable=True,
        confirm_close=False,
    )
    gui = "edgechromium" if sys.platform.startswith("win") else None
    webview.start(debug=settings.DEBUG, gui=gui)


if __name__ == "__main__":
    main()
