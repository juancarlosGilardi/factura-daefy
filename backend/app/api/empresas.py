"""Endpoints de empresa y configuración global."""
import logging
import shutil
from pathlib import Path
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form
from sqlalchemy.orm import Session

from ..core.database import get_db
from ..core import config as core_config
from ..core.db_adapter import is_mdb_mode
from ..models.empresa import Empresa, Configuracion
from ..schemas.empresa import (
    EmpresaUpdate, EmpresaOut, ConfiguracionUpdate, ConfiguracionOut,
    EmpresaConfigOut, AmbienteIn,
)
from ..schemas.common import MessageResponse

logger = logging.getLogger("factura_mdb.api.empresas")

router = APIRouter(prefix="/api/empresa", tags=["empresa"])

_MDB_WRITE_NOT_IMPL = (
    "Escritura al MDB no implementada todavía (Sprint 2). "
    "Modo actual: FACTURA_MDB_DB_MODE=mdb (solo lectura)."
)


def _empresa_mdb_dict() -> dict:
    """Datos de empresa para modo MDB.

    Sprint 2: el RUC se lee del .mdb real (EF2ALMACENES.F2RUCALM, única
    columna con RUC operativo); los demás campos siguen viniendo del
    hardcoded `_FAKE_EMPRESA_MDB` porque el .mdb no tiene tabla de
    empresa formal con razón social, dirección SUNAT, etc.
    """
    from ..core.db_adapter.mdb_repo import EmpresaRepoMDB
    return EmpresaRepoMDB.obtener()


def _config_mdb_dict() -> dict:
    from ..main import _FAKE_CONFIG_MDB
    return {
        c: getattr(_FAKE_CONFIG_MDB, c)
        for c in (
            "id", "igv_rate", "formato_impresion", "cuenta_bcp",
            "cuenta_bcp_moneda", "cta_banco_nacion", "aplica_detraccion",
            "detraccion_codigo", "detraccion_porcentaje", "pie_pagina",
            "moneda_default", "auto_envio_sunat", "backup_automatico",
            "onboarding_completado", "created_at", "updated_at",
        )
    }


def _get_empresa_or_404(db: Session) -> Empresa:
    empresa = db.query(Empresa).first()
    if empresa is None:
        raise HTTPException(status_code=404, detail="Empresa no configurada. Completa el onboarding.")
    return empresa


def _get_config_or_create(db: Session) -> Configuracion:
    config = db.query(Configuracion).first()
    if config is None:
        config = Configuracion()
        db.add(config)
        db.commit()
        db.refresh(config)
    return config


@router.get("", response_model=EmpresaConfigOut)
def obtener_empresa(db: Session = Depends(get_db)):
    """Devuelve empresa + configuración (combinados)."""
    if is_mdb_mode():
        return EmpresaConfigOut(
            empresa=_empresa_mdb_dict(),
            configuracion=_config_mdb_dict(),
        )
    empresa = db.query(Empresa).first()
    config = _get_config_or_create(db)
    return EmpresaConfigOut(empresa=empresa, configuracion=config)


@router.put("", response_model=EmpresaOut)
def actualizar_empresa(payload: EmpresaUpdate, db: Session = Depends(get_db)):
    """Actualiza datos de empresa (NO permite cambiar RUC)."""
    if is_mdb_mode():
        raise HTTPException(status_code=501, detail=_MDB_WRITE_NOT_IMPL)
    empresa = _get_empresa_or_404(db)

    data = payload.model_dump(exclude_unset=True)
    # Bloquear cambio de RUC defensivamente
    data.pop("ruc", None)

    for k, v in data.items():
        if hasattr(empresa, k):
            setattr(empresa, k, v)

    db.commit()
    db.refresh(empresa)
    return empresa


@router.put("/configuracion", response_model=ConfiguracionOut)
def actualizar_configuracion(payload: ConfiguracionUpdate, db: Session = Depends(get_db)):
    """Actualiza la configuración global (formato impresión, IGV, cuentas, etc.)."""
    if is_mdb_mode():
        raise HTTPException(status_code=501, detail=_MDB_WRITE_NOT_IMPL)
    config = _get_config_or_create(db)
    data = payload.model_dump(exclude_unset=True)
    for k, v in data.items():
        if hasattr(config, k):
            setattr(config, k, v)
    db.commit()
    db.refresh(config)
    return config


@router.post("/cert", response_model=MessageResponse)
async def reupload_certificado(
    cert: UploadFile = File(...),
    cert_pass: str = Form(...),
    db: Session = Depends(get_db),
):
    """Reemplaza el certificado digital."""
    empresa = _get_empresa_or_404(db)

    nombre = (cert.filename or "").lower()
    if not nombre.endswith((".pfx", ".p12")):
        raise HTTPException(status_code=422, detail="El certificado debe ser .pfx o .p12")

    cert_dst = core_config.cert_dir() / f"cert_{empresa.ruc}{Path(nombre).suffix}"
    try:
        with cert_dst.open("wb") as f:
            shutil.copyfileobj(cert.file, f)
    except OSError as e:
        raise HTTPException(status_code=500, detail=f"No se pudo guardar el certificado: {e}")
    finally:
        await cert.close()

    empresa.certificado_path = str(cert_dst)
    empresa.certificado_pass = cert_pass
    db.commit()
    return MessageResponse(ok=True, mensaje="Certificado actualizado")


@router.post("/sunat-env", response_model=EmpresaOut)
def cambiar_ambiente_sunat(payload: AmbienteIn, db: Session = Depends(get_db)):
    """Cambia entre beta y producción."""
    empresa = _get_empresa_or_404(db)
    empresa.sunat_env = payload.ambiente
    db.commit()
    db.refresh(empresa)
    logger.info(f"Ambiente SUNAT cambiado a {empresa.sunat_env}")
    return empresa


# ---------- Aliases para el frontend (Agente C) ----------
@router.post("/certificado", response_model=MessageResponse)
async def reupload_certificado_alias(
    certificado: UploadFile = File(...),
    password: str = Form(...),
    db: Session = Depends(get_db),
):
    """Alias de /cert con field names que el frontend (Agente C) envía."""
    return await reupload_certificado(cert=certificado, cert_pass=password, db=db)


# Router /api/empresas (plural) — alias para frontend legacy
plural_router = APIRouter(prefix="/api/empresas", tags=["empresa-aliases"])


@plural_router.get("/actual", response_model=EmpresaConfigOut)
def obtener_empresa_actual_alias(db: Session = Depends(get_db)):
    """Alias plural de GET /api/empresa para frontend legacy."""
    return obtener_empresa(db)


# Router de top-level /api/configuracion (sin prefijo empresa) — alias para C
config_router = APIRouter(prefix="/api/configuracion", tags=["configuracion"])


@config_router.get("", response_model=ConfiguracionOut)
def get_configuracion_alias(db: Session = Depends(get_db)):
    if is_mdb_mode():
        return ConfiguracionOut(**_config_mdb_dict())
    return _get_config_or_create(db)


@config_router.put("", response_model=ConfiguracionOut)
def put_configuracion_alias(payload: ConfiguracionUpdate,
                              db: Session = Depends(get_db)):
    return actualizar_configuracion(payload, db)
