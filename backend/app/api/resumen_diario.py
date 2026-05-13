"""Endpoints de Resumen Diario (RC)."""
import logging
from datetime import date as _date
from typing import Optional, List
from pathlib import Path
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse
from sqlalchemy import func, and_, or_, not_, exists
from sqlalchemy.orm import Session

from ..core.database import get_db
from ..core.db_adapter import is_dbf_mode
from ..models.comprobante import Comprobante
from ..models.resumen import (
    ResumenDiario, ResumenDiarioItem, ComunicacionBaja, ComunicacionBajaItem,
)
from ..schemas.resumen import (
    ResumenDiarioIn, ResumenDiarioOut, ResumenListResponse,
    ComprobantePendienteResumen,
)
from ..schemas.common import TicketConsultaOut
from ._deps import cargar_servicio_resumen, http_502_sunat

logger = logging.getLogger("factura_mdb.api.resumen")

router = APIRouter(prefix="/api/resumen-diario", tags=["resumen-diario"])


def _serializar_resumen(r: ResumenDiario, items: list) -> ResumenDiarioOut:
    return ResumenDiarioOut.model_validate({
        **{c.name: getattr(r, c.name) for c in r.__table__.columns},
        "items": [
            {c.name: getattr(it, c.name) for c in it.__table__.columns}
            for it in items
        ],
    })


@router.get("", response_model=ResumenListResponse)
def listar_resumenes(
    db: Session = Depends(get_db),
    estado: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
):
    q = db.query(ResumenDiario)
    if estado:
        q = q.filter(ResumenDiario.estado == estado)

    total = q.count()
    resumenes = (q.order_by(ResumenDiario.fecha_comunicacion.desc(),
                            ResumenDiario.id.desc())
                  .limit(limit).offset(offset).all())
    out = []
    for r in resumenes:
        items = (db.query(ResumenDiarioItem)
                  .filter(ResumenDiarioItem.resumen_id == r.id).all())
        out.append(_serializar_resumen(r, items))
    return ResumenListResponse(items=out, total=total, limit=limit, offset=offset)


@router.get("/comprobantes-pendientes", response_model=List[ComprobantePendienteResumen])
def comprobantes_pendientes(
    fecha: _date = Query(..., description="Fecha de emisión a consultar"),
    db: Session = Depends(get_db),
):
    """Boletas (03) emitidas en `fecha` que aún no están en un resumen ni en una baja."""
    incluidas_en_resumen = db.query(ResumenDiarioItem.comprobante_id).subquery()
    incluidas_en_baja = db.query(ComunicacionBajaItem.comprobante_id).subquery()

    items = (
        db.query(Comprobante)
        .filter(Comprobante.tipo_documento == "03")
        .filter(Comprobante.fecha_emision == fecha)
        .filter(~Comprobante.id.in_(incluidas_en_resumen))
        .filter(~Comprobante.id.in_(incluidas_en_baja))
        .order_by(Comprobante.serie, Comprobante.correlativo)
        .all()
    )
    return [
        ComprobantePendienteResumen(
            id=c.id,
            numero_completo=c.numero_completo,
            fecha_emision=c.fecha_emision,
            cliente_numero_doc=c.cliente_numero_doc,
            cliente_razon_social=c.cliente_razon_social,
            moneda=c.moneda,
            total_venta=c.total_venta,
            estado=c.estado,
        )
        for c in items
    ]


@router.post("", response_model=ResumenDiarioOut, status_code=201)
def crear_resumen(payload: ResumenDiarioIn, db: Session = Depends(get_db)):
    """Crea y envía un resumen diario."""
    comp_ids = [it.comprobante_id for it in payload.items]
    comprobantes = db.query(Comprobante).filter(Comprobante.id.in_(comp_ids)).all()
    encontrados = {c.id: c for c in comprobantes}
    faltantes = [cid for cid in comp_ids if cid not in encontrados]
    if faltantes:
        raise HTTPException(
            status_code=404,
            detail=f"Comprobantes no encontrados: {faltantes}",
        )

    fecha_com = payload.fecha_comunicacion or _date.today()

    # Validar que los comprobantes coincidan con la fecha_referencia
    fuera = [
        encontrados[cid].numero_completo
        for cid in comp_ids
        if encontrados[cid].fecha_emision != payload.fecha_referencia
    ]
    if fuera:
        raise HTTPException(
            status_code=422,
            detail=f"Estos comprobantes no son del {payload.fecha_referencia}: {fuera}",
        )

    # Bug D6: evitar incluir comprobantes que ya están en otro resumen activo.
    ya_incluidos = (
        db.query(ResumenDiarioItem.comprobante_id)
        .join(ResumenDiario, ResumenDiario.id == ResumenDiarioItem.resumen_id)
        .filter(ResumenDiarioItem.comprobante_id.in_(comp_ids))
        .filter(ResumenDiario.estado != "B")
        .all()
    )
    ya_set = {row[0] for row in ya_incluidos}
    if ya_set:
        raise HTTPException(
            status_code=409,
            detail=f"Comprobantes ya están en otro resumen: {sorted(ya_set)}",
        )

    last = (db.query(func.max(ResumenDiario.correlativo))
              .filter(ResumenDiario.fecha_comunicacion == fecha_com).scalar())
    correlativo = (last or 0) + 1

    from ..models.empresa import Empresa
    empresa = db.query(Empresa).first()
    ruc = empresa.ruc if empresa else "00000000000"
    nombre_archivo = f"{ruc}-RC-{fecha_com.strftime('%Y%m%d')}-{correlativo:03d}"

    # Bug C7/F9: tolerar None en columnas para no explotar con TypeError
    total_gravado = sum((c.total_gravado or 0) for c in comprobantes)
    total_igv = sum((c.total_igv or 0) for c in comprobantes)
    total = sum((c.total_venta or 0) for c in comprobantes)

    resumen = ResumenDiario(
        fecha_referencia=payload.fecha_referencia,
        fecha_comunicacion=fecha_com,
        correlativo=correlativo,
        nombre_archivo=nombre_archivo,
        tipo_resumen="RC",
        estado="P",
        total_documentos=len(comp_ids),
        total_gravado=total_gravado,
        total_igv=total_igv,
        total=total,
    )
    db.add(resumen)
    db.flush()

    for it in payload.items:
        db.add(ResumenDiarioItem(
            resumen_id=resumen.id,
            comprobante_id=it.comprobante_id,
            condicion=it.condicion,
        ))
    db.commit()
    db.refresh(resumen)

    svc = cargar_servicio_resumen()
    if svc and hasattr(svc, "emitir_y_enviar"):
        try:
            svc.emitir_y_enviar(db, resumen.id)
            db.refresh(resumen)
        except Exception as e:  # noqa: BLE001
            logger.exception("Error envío resumen SUNAT")
            raise http_502_sunat(str(e))
    else:
        logger.warning("Servicio de resumen no disponible — quedó en P")

    items = (db.query(ResumenDiarioItem)
              .filter(ResumenDiarioItem.resumen_id == resumen.id).all())
    return _serializar_resumen(resumen, items)


@router.get("/{resumen_id}", response_model=ResumenDiarioOut)
def obtener_resumen(resumen_id: int, db: Session = Depends(get_db)):
    r = db.get(ResumenDiario, resumen_id)
    if r is None:
        raise HTTPException(status_code=404, detail="Resumen no encontrado")
    items = (db.query(ResumenDiarioItem)
              .filter(ResumenDiarioItem.resumen_id == resumen_id).all())
    return _serializar_resumen(r, items)


@router.get("/{resumen_id}/consultar-ticket", response_model=TicketConsultaOut)
def consultar_ticket_resumen(resumen_id: int, db: Session = Depends(get_db)):
    r = db.get(ResumenDiario, resumen_id)
    if r is None:
        raise HTTPException(status_code=404, detail="Resumen no encontrado")
    if not r.ticket:
        raise HTTPException(status_code=409, detail="Este resumen aún no tiene ticket SUNAT")

    svc = cargar_servicio_resumen()
    if svc is None or not hasattr(svc, "consultar_ticket"):
        raise HTTPException(status_code=503, detail="Servicio de consulta SUNAT no disponible")

    try:
        svc.consultar_ticket(db, r.id)
    except Exception as e:  # noqa: BLE001
        logger.exception("Error consultando ticket resumen")
        raise http_502_sunat(str(e))

    db.refresh(r)
    return TicketConsultaOut(
        ticket=r.ticket,
        estado=r.estado,
        cdr_codigo=r.cdr_codigo,
        cdr_descripcion=r.cdr_descripcion,
        cdr_path=r.cdr_path,
    )


# ---------- Aliases para el frontend (Agente C) ----------
@router.get("/{resumen_id}/consultar", response_model=TicketConsultaOut)
def consultar_alias_resumen(resumen_id: int, db: Session = Depends(get_db)):
    return consultar_ticket_resumen(resumen_id, db)


@router.get("/{resumen_id}/cdr")
def descargar_cdr_resumen(resumen_id: int, db: Session = Depends(get_db)):
    r = db.get(ResumenDiario, resumen_id)
    if r is None:
        raise HTTPException(status_code=404, detail="Resumen no encontrado")
    if not r.cdr_path:
        raise HTTPException(status_code=404, detail="Aún no hay CDR para este resumen")
    p = Path(r.cdr_path)
    if not p.exists():
        raise HTTPException(status_code=404, detail=f"Archivo no encontrado: {p.name}")
    return FileResponse(path=str(p),
                         filename=p.name,
                         media_type="application/zip")
