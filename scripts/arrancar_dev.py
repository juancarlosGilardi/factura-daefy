"""Arranca uvicorn local con env vars defensivas.

Uso:
    python scripts/arrancar_dev.py

Lee `config.json` para asegurar que existe; si no existe, sugiere
copiar `config.example.json`. Luego invoca uvicorn con --reload sobre
backend/app.main:app.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CONFIG = PROJECT_ROOT / "config.json"


def main() -> int:
    if not CONFIG.exists():
        print("ERROR: config.json no existe.", file=sys.stderr)
        print(f"       Copia {PROJECT_ROOT / 'config.example.json'} a "
              f"{CONFIG}", file=sys.stderr)
        print("       y rellena los valores reales antes de arrancar.",
              file=sys.stderr)
        return 1

    try:
        json.loads(CONFIG.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        print(f"ERROR: config.json inválido: {exc}", file=sys.stderr)
        return 1

    # Defaults defensivos: BETA si no se setea explícito; debug ON
    os.environ.setdefault("FACTURA_MDB_DEBUG", "1")
    os.environ.setdefault("FACTURA_MDB_SUNAT_ENV", "beta")

    port = os.environ.get("FACTURA_MDB_PORT", "9876")
    cmd = [
        sys.executable, "-m", "uvicorn", "app.main:app",
        "--app-dir", "backend",
        "--host", "127.0.0.1",
        "--port", port,
        "--reload",
    ]
    print(f">>> Arrancando uvicorn en http://127.0.0.1:{port}")
    print(f">>> SUNAT env: {os.environ['FACTURA_MDB_SUNAT_ENV'].upper()}")
    print(">>> Ctrl+C para detener")
    print()
    try:
        return subprocess.call(cmd, cwd=PROJECT_ROOT)
    except KeyboardInterrupt:
        print("\n>>> Detenido por usuario.")
        return 0


if __name__ == "__main__":
    sys.exit(main())
