"""Configuración del proyecto Factura-mdb.

Carga `config.json` en la raíz del proyecto. Si no existe, usa valores por
defecto sólo para desarrollo (NO recomendado para uso real).

Reglas:
- SUNAT BETA por default. Producción solo si env var FACTURA_MDB_SUNAT_ENV
  o config.json -> sunat.ambiente lo dice explícitamente.
- Cert dentro de la carpeta del proyecto (`certs/`). El path en
  `config.json` puede ser relativo (resuelto desde PROJECT_ROOT) o absoluto.
- El .mdb del cliente NO debe vivir dentro de `Factura-mdb/`; el path en
  `config.json` es típicamente absoluto a una ruta del cliente
  (p.ej. `C:/SIAP/db_bancos.mdb`).
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Optional


PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent  # Factura-mdb/
CONFIG_PATH = PROJECT_ROOT / "config.json"


class Settings:
    """Settings simples (sin pydantic_settings) — todo viene de config.json + env."""

    def __init__(self) -> None:
        self._config: dict[str, Any] = {}
        if CONFIG_PATH.exists():
            try:
                # 'utf-8-sig' tolera config.json guardado por Notepad de Windows
                # con BOM (EF BB BF) — el 'utf-8' estricto revienta con
                # "Unexpected UTF-8 BOM" y deja al server sin arrancar.
                self._config = json.loads(CONFIG_PATH.read_text(encoding="utf-8-sig"))
            except Exception as exc:  # noqa: BLE001
                raise RuntimeError(
                    f"config.json inválido en {CONFIG_PATH}: {exc}"
                ) from exc

        self.PROJECT_ROOT = PROJECT_ROOT
        self.APP_NAME = "Factura-mdb"
        self.APP_VERSION = "0.1.0"

        # Modo de BD (mdb, dbf, sqlite)
        mdb_section = self._config.get("mdb") or {}
        self.MDB_MODE: str = (
            os.environ.get("FACTURA_MDB_MODE")
            or mdb_section.get("mode")
            or "mdb"
        ).strip().lower()

        # MDB
        mdb_path_raw = (
            os.environ.get("FACTURA_MDB_PATH")
            or mdb_section.get("path")
            or ""
        )
        self.MDB_PATH: Path = self._resolve_path(mdb_path_raw) if mdb_path_raw else Path("")

        # DBF
        dbf_section = self._config.get("dbf") or {}
        dbf_path_raw = (
            os.environ.get("FACTURA_DBF_PATH")
            or dbf_section.get("path")
            or ""
        )
        self.DBF_PATH: Path = self._resolve_path(dbf_path_raw) if dbf_path_raw else Path("")

        # Empresa
        self.EMPRESA: dict[str, Any] = self._config.get("empresa") or {}
        self.RUC: str = (
            os.environ.get("FACTURA_MDB_RUC")
            or self.EMPRESA.get("ruc", "")
        )

        # SUNAT — BETA por default. Producción solo si env var/config explícitos.
        env_override = os.environ.get("FACTURA_MDB_SUNAT_ENV", "").strip().lower()
        config_env = (self._config.get("sunat") or {}).get("ambiente", "beta").strip().lower()
        candidate = env_override if env_override in ("beta", "produccion") else config_env
        self.SUNAT_ENV: str = candidate if candidate in ("beta", "produccion") else "beta"

        sunat_section = self._config.get("sunat") or {}
        self.SOL_USER: str = (
            os.environ.get("FACTURA_MDB_SOL_USER")
            or sunat_section.get("sol_user")
            or "MODDATOS"
        )
        self.SOL_PASS: str = (
            os.environ.get("FACTURA_MDB_SOL_PASS")
            or sunat_section.get("sol_pass")
            or "MODDATOS"
        )

        # Certificado
        cert_section = self._config.get("certificado") or {}
        cert_path_raw = (
            os.environ.get("FACTURA_MDB_CERT_PATH")
            or cert_section.get("path")
            or ""
        )
        self.CERT_PATH: Optional[Path] = (
            self._resolve_path(cert_path_raw) if cert_path_raw else None
        )
        self.CERT_PASS: str = (
            os.environ.get("FACTURA_MDB_CERT_PASS")
            or cert_section.get("password")
            or ""
        )

        # Storage paths
        self.STORAGE_DIR: Path = PROJECT_ROOT / "storage"
        self.XML_DIR: Path = self.STORAGE_DIR / "xml"
        self.CDR_DIR: Path = self.STORAGE_DIR / "cdr"
        self.PDF_DIR: Path = self.STORAGE_DIR / "pdf"
        for d in (self.STORAGE_DIR, self.XML_DIR, self.CDR_DIR, self.PDF_DIR):
            d.mkdir(parents=True, exist_ok=True)

        # SUNAT URLs (constantes oficiales)
        self.SUNAT_BETA_URL = "https://e-beta.sunat.gob.pe/ol-ti-itcpfegem-beta/billService"
        self.SUNAT_PROD_URL = "https://e-factura.sunat.gob.pe/ol-ti-itcpfegem/billService"
        self.SUNAT_BAJA_BETA = self.SUNAT_BETA_URL
        self.SUNAT_BAJA_PROD = self.SUNAT_PROD_URL

        # Server
        self.HOST: str = os.environ.get("FACTURA_MDB_HOST", "127.0.0.1")
        try:
            self.PORT: int = int(os.environ.get("FACTURA_MDB_PORT", "0"))
        except ValueError:
            self.PORT = 0

        self.DEBUG: bool = bool(int(os.environ.get("FACTURA_MDB_DEBUG", "1")))
        # Compatibilidad con backup_service heredado (usa settings.DATA_DIR).
        # En Factura-mdb usamos PROJECT_ROOT como data_dir lógico.
        self.DATA_DIR: Path = PROJECT_ROOT

    # -----------------------------------------------------------------
    # Helpers
    # -----------------------------------------------------------------
    def _resolve_path(self, raw: str) -> Path:
        """Resuelve un path: si es absoluto se devuelve tal cual; si es
        relativo se resuelve respecto a PROJECT_ROOT."""
        if not raw:
            return Path("")
        p = Path(raw)
        return p if p.is_absolute() else (PROJECT_ROOT / p).resolve()

    def reload(self) -> None:
        """Recarga el config.json. Útil en tests; no se usa en producción."""
        self.__init__()  # type: ignore[misc]


settings = Settings()


# ----------------------------------------------------------------------
# Funciones helper compartidas (mismo API que el código heredado para no romper imports)
# ----------------------------------------------------------------------
def db_path() -> Path:
    """Path al .mdb activo."""
    return settings.MDB_PATH


def cert_dir() -> Path:
    """Carpeta donde se guardan los certificados (relativa al proyecto)."""
    p = PROJECT_ROOT / "certs"
    p.mkdir(parents=True, exist_ok=True)
    return p


def storage_dir() -> Path:
    """Carpeta de generados (XMLs, CDRs, PDFs)."""
    return settings.STORAGE_DIR


def log_dir() -> Path:
    """Carpeta de logs (creada al vuelo)."""
    p = PROJECT_ROOT / "logs"
    p.mkdir(parents=True, exist_ok=True)
    return p


def backup_dir() -> Path:
    """Carpeta de backups del .mdb (creada al vuelo)."""
    p = PROJECT_ROOT / "backups"
    p.mkdir(parents=True, exist_ok=True)
    return p


def db_url() -> str:
    """Mantenido por compatibilidad con módulos legacy SQLite que importan.
    En Factura-mdb NO se usa SQLAlchemy/SQLite — devolvemos un URL de memoria.
    """
    return "sqlite:///:memory:"


def save_config_json(config_dict: dict) -> None:
    """Guarda un diccionario en config.json.
    Mantiene la estructura existente y sobrescribe solo los campos proporcionados.
    """
    try:
        # Cargar config existente o empezar con vacío.
        # 'utf-8-sig' por consistencia con __init__: si el cliente editó el
        # archivo en Notepad puede haberle metido BOM.
        if CONFIG_PATH.exists():
            current = json.loads(CONFIG_PATH.read_text(encoding="utf-8-sig"))
        else:
            current = {}

        # Actualizar con los nuevos valores (merge profundo por sección)
        for key, value in config_dict.items():
            if isinstance(value, dict) and key in current and isinstance(current[key], dict):
                current[key].update(value)
            else:
                current[key] = value

        # Guardar con formato legible
        CONFIG_PATH.write_text(
            json.dumps(current, indent=2, ensure_ascii=False),
            encoding="utf-8"
        )
    except Exception as exc:
        raise RuntimeError(
            f"No se pudo guardar config.json en {CONFIG_PATH}: {exc}"
        ) from exc
