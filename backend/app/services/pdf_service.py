"""Shim que adapta `pdf_generator.generar_pdf_comprobante` al contrato
que el Agente B definió en `api/comprobantes.py` (svc.generar_pdf(db, id)).

Persiste el PDF en `%APPDATA%/Factura-mdb/storage/pdf/<nombre>.pdf` y devuelve la ruta.
"""
from __future__ import annotations

import logging
from pathlib import Path

from sqlalchemy.orm import Session

from ..core.config import storage_dir
from ..models.empresa import Empresa, Configuracion
from ..models.comprobante import Comprobante, ComprobanteDetalle
from .pdf_generator import generar_pdf_comprobante

logger = logging.getLogger("factura_mdb.pdf")


def _nombre_pdf(empresa: Empresa, comp: Comprobante) -> str:
    return (
        f"{empresa.ruc}-{comp.tipo_documento}-{comp.serie}-"
        f"{str(comp.correlativo).zfill(8)}"
    )


def generar_pdf(db: Session, comprobante_id: int) -> Path:
    comp = db.query(Comprobante).filter(Comprobante.id == comprobante_id).first()
    if not comp:
        raise ValueError(f"Comprobante {comprobante_id} no encontrado")

    empresa = db.query(Empresa).first()
    if not empresa:
        raise ValueError("No hay Empresa configurada")
    config = db.query(Configuracion).first()

    detalles = (
        db.query(ComprobanteDetalle)
          .filter(ComprobanteDetalle.comprobante_id == comp.id)
          .order_by(ComprobanteDetalle.orden.asc())
          .all()
    )

    pdf_bytes = generar_pdf_comprobante(comp, detalles, empresa, config)

    pdf_dir = storage_dir() / "pdf"
    pdf_dir.mkdir(parents=True, exist_ok=True)
    pdf_path = pdf_dir / f"{_nombre_pdf(empresa, comp)}.pdf"
    pdf_path.write_bytes(pdf_bytes)

    # Persistir ruta en BD si el modelo lo soporta
    if hasattr(comp, "pdf_path"):
        comp.pdf_path = str(pdf_path)
        db.commit()

    logger.info("PDF generado: %s", pdf_path)
    return pdf_path
