"""Endpoints de Comunicación de Baja (RA)."""
import logging
from datetime import date as _date
from typing import Optional
from pathlib import Path
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse
from sqlalchemy import func
from sqlalchemy.orm import Session

from ..core.database import get_db
from ..models.comprobante import Comprobante
from ..models.resumen import ComunicacionBaja, ComunicacionBajaItem
from ..schemas.baja import (
    ComunicacionBajaIn, ComunicacionBajaOut, BajaListResponse,
)
from ..schemas.common import TicketConsultaOut
from ._deps import cargar_servicio_baja, http_502_sunat

logger = logging.getLogger("factura_mdb.api.baja")

router = APIRouter(prefix="/api/comunicacion-baja", tags=["comunicacion-baja"])


def _serializar_baja(baja: ComunicacionBaja, items: list) -> ComunicacionBajaOut:
    return ComunicacionBajaOut.model_validate({
        **{c.name: getattr(baja, c.name) for c in baja.__table__.columns},
        "items": [
            {c.name: getattr(it, c.name) for c in it.__table__.columns}
            for it in items
        ],
    })


@router.get("", response_model=BajaListResponse)
def listar_bajas(
    db: Session = Depends(get_db),
    estado: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
):
    q = db.query(ComunicacionBaja)
    if estado:
        q = q.filter(ComunicacionBaja.estado == estado)

    total = q.count()
    bajas = (q.order_by(ComunicacionBaja.fecha_comunicacion.desc(),
                        ComunicacionBaja.id.desc())
              .limit(limit).offset(offset).all())
    out = []
    for b in bajas:
        items = (db.query(ComunicacionBajaItem)
                  .filter(ComunicacionBajaItem.baja_id == b.id).all())
        out.append(_serializar_baja(b, items))
    return BajaListResponse(items=out, total=total, limit=limit, offset=offset)


@router.post("", response_model=ComunicacionBajaOut, status_code=201)
def crear_baja(payload: ComunicacionBajaIn, db: Session = Depends(get_db)):
    """Crea la comunicación de baja, registra ítems y la envía a SUNAT."""
    # Resolver comprobantes y validar
    comp_ids = [it.comprobante_id for it in payload.items]
    comprobantes = (
        db.query(Comprobante).filter(Comprobante.id.in_(comp_ids)).all()
    )
    encontrados = {c.id: c for c in comprobantes}
    faltantes = [cid for cid in comp_ids if cid not in encontrados]
    if faltantes:
        raise HTTPException(
            status_code=404,
            detail=f"Comprobantes no encontrados: {faltantes}",
        )

    # Sólo facturas/NC/ND aceptadas se anulan vía RA (boletas van por RC)
    para_baja = []
    for cid in comp_ids:
        c = encontrados[cid]
        if c.tipo_documento == "03":
            raise HTTPException(
                status_code=422,
                detail=f"Boleta {c.numero_completo} debe anularse vía Resumen Diario, no Comunicación de Baja",
            )
        if c.estado not in ("A", "P", "R"):
            raise HTTPException(
                status_code=422,
                detail=f"{c.numero_completo} está en estado {c.estado} y no puede anularse",
            )
        para_baja.append(c)

    # Bug B4/G5: evitar registrar el mismo comprobante en dos bajas activas (P o A)
    ya_en_baja_rows = (
        db.query(ComunicacionBajaItem.comprobante_id)
          .join(ComunicacionBaja, ComunicacionBaja.id == ComunicacionBajaItem.baja_id)
          .filter(ComunicacionBajaItem.comprobante_id.in_(comp_ids))
          .filter(ComunicacionBaja.estado.in_(["P", "A"]))
          .all()
    )
    ya_set = {row[0] for row in ya_en_baja_rows}
    if ya_set:
        # Mapear ids a numero_completo para mensaje claro
        nombres = sorted(
            (encontrados[cid].numero_completo for cid in ya_set if cid in encontrados)
        )
        raise HTTPException(
            status_code=409,
            detail=f"Estos comprobantes ya tienen una baja activa: {', '.join(nombres) or sorted(ya_set)}",
        )

    # Fechas
    fecha_docs = payload.fecha_documentos or min(c.fecha_emision for c in para_baja)
    fecha_com = payload.fecha_comunicacion or _date.today()

    # Correlativo del día
    last = (db.query(func.max(ComunicacionBaja.correlativo))
              .filter(ComunicacionBaja.fecha_comunicacion == fecha_com).scalar())
    correlativo = (last or 0) + 1

    # Resolver RUC para nombre archivo
    from ..models.empresa import Empresa
    empresa = db.query(Empresa).first()
    ruc = empresa.ruc if empresa else "00000000000"
    nombre_archivo = f"{ruc}-RA-{fecha_com.strftime('%Y%m%d')}-{correlativo:03d}"

    baja = ComunicacionBaja(
        fecha_documentos=fecha_docs,
        fecha_comunicacion=fecha_com,
        correlativo=correlativo,
        nombre_archivo=nombre_archivo,
        motivo=payload.motivo,
        estado="P",
    )
    db.add(baja)
    db.flush()

    for it in payload.items:
        db.add(ComunicacionBajaItem(
            baja_id=baja.id,
            comprobante_id=it.comprobante_id,
            motivo=it.motivo or payload.motivo,
        ))
    db.commit()
    db.refresh(baja)

    # Llamar al servicio del Agente A
    svc = cargar_servicio_baja()
    if svc and hasattr(svc, "emitir_y_enviar"):
        try:
            svc.emitir_y_enviar(db, baja.id)
            db.refresh(baja)
        except Exception as e:  # noqa: BLE001
            logger.exception("Error envío baja SUNAT")
            raise http_502_sunat(str(e))
    else:
        logger.warning("Servicio de baja no disponible — quedó en estado P")

    items = (db.query(ComunicacionBajaItem)
              .filter(ComunicacionBajaItem.baja_id == baja.id).all())
    return _serializar_baja(baja, items)


@router.get("/{baja_id}", response_model=ComunicacionBajaOut)
def obtener_baja(baja_id: int, db: Session = Depends(get_db)):
    baja = db.get(ComunicacionBaja, baja_id)
    if baja is None:
        raise HTTPException(status_code=404, detail="Comunicación de baja no encontrada")
    items = (db.query(ComunicacionBajaItem)
              .filter(ComunicacionBajaItem.baja_id == baja_id).all())
    return _serializar_baja(baja, items)


@router.get("/{baja_id}/consultar-ticket", response_model=TicketConsultaOut)
def consultar_ticket(baja_id: int, db: Session = Depends(get_db)):
    """Re-consulta el ticket SUNAT por si quedó pendiente."""
    baja = db.get(ComunicacionBaja, baja_id)
    if baja is None:
        raise HTTPException(status_code=404, detail="Comunicación de baja no encontrada")
    if not baja.ticket:
        raise HTTPException(status_code=409, detail="Esta baja aún no tiene ticket SUNAT")

    svc = cargar_servicio_baja()
    if svc is None or not hasattr(svc, "consultar_ticket"):
        raise HTTPException(status_code=503, detail="Servicio de consulta SUNAT no disponible")

    try:
        result = svc.consultar_ticket(db, baja.id)
    except Exception as e:  # noqa: BLE001
        logger.exception("Error consultando ticket")
        raise http_502_sunat(str(e))

    db.refresh(baja)
    return TicketConsultaOut(
        ticket=baja.ticket,
        estado=baja.estado,
        cdr_codigo=baja.cdr_codigo,
        cdr_descripcion=baja.cdr_descripcion,
        cdr_path=baja.cdr_path,
    )


# ---------- Aliases para el frontend (Agente C) ----------
@router.get("/{baja_id}/consultar", response_model=TicketConsultaOut)
def consultar_alias(baja_id: int, db: Session = Depends(get_db)):
    """Alias de /{baja_id}/consultar-ticket."""
    return consultar_ticket(baja_id, db)


@router.get("/{baja_id}/cdr")
def descargar_cdr(baja_id: int, db: Session = Depends(get_db)):
    baja = db.get(ComunicacionBaja, baja_id)
    if baja is None:
        raise HTTPException(status_code=404, detail="Comunicación de baja no encontrada")
    if not baja.cdr_path:
        raise HTTPException(status_code=404, detail="Aún no hay CDR para esta baja")
    p = Path(baja.cdr_path)
    if not p.exists():
        raise HTTPException(status_code=404, detail=f"Archivo no encontrado: {p.name}")
    return FileResponse(path=str(p),
                         filename=p.name,
                         media_type="application/zip")
