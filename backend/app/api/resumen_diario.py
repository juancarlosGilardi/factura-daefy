"""Endpoints de Resumen Diario (RC).

Soporta dos modos:
  - SQLAlchemy (legacy): tabla `resumenes_diarios` + comprobantes ORM.
  - MDB/SQLite (Factura-mdb productivo): FMDB_RESUMENES + TBVENTA_CAB via
    ResumenStore + ComprobanteRepoMDB. El dispatch se hace con `is_mdb_mode()`.
"""
import logging
from datetime import date as _date
from typing import Optional, List
from pathlib import Path
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse
from sqlalchemy import func
from sqlalchemy.orm import Session

from ..core.database import get_db
from ..core.db_adapter import is_mdb_mode
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


# =====================================================================
# GET / — listar
# =====================================================================
@router.get("", response_model=ResumenListResponse)
def listar_resumenes(
    db: Session = Depends(get_db),
    estado: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
):
    if is_mdb_mode():
        from ..core.db_adapter.resumen_store import ResumenStore
        items_dict, total = ResumenStore.listar(
            estado=estado, limit=limit, offset=offset,
        )
        out = [ResumenDiarioOut.model_validate(r) for r in items_dict]
        return ResumenListResponse(items=out, total=total, limit=limit, offset=offset)

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


# =====================================================================
# GET /comprobantes-pendientes
# =====================================================================
@router.get("/comprobantes-pendientes", response_model=List[ComprobantePendienteResumen])
def comprobantes_pendientes(
    fecha: _date = Query(..., description="Fecha de emisión a consultar"),
    db: Session = Depends(get_db),
):
    """Boletas (03) y NCs/NDs de boleta (07/08) emitidas en `fecha`
    que aún no están en un resumen ni en una baja."""
    if is_mdb_mode():
        from ..core.db_adapter.repo import ComprobanteRepoMDB
        from ..core.db_adapter.resumen_store import ResumenStore
        from ..core.db_adapter.baja_store import BajaStore

        # Usar listar con cap amplio y filtrar por fecha exacta + tipo
        # Nota: no usamos fecha_hasta porque F4FECEMI puede venir con hora
        # ("2026-05-06 00:00:00") y la comparacion lexicografica TEXT > date
        # excluye filas validas. Filtramos por fecha exacta en Python.
        candidatos: list[dict] = []
        for tipo in ("03", "07", "08"):
            items, _ = ComprobanteRepoMDB.listar(
                filtros={"tipo_documento": tipo, "fecha_desde": fecha},
                limit=500, offset=0,
            )
            for c in items:
                if c.get("fecha_emision") == fecha:
                    candidatos.append(c)

        # Excluir los ya incluidos en un resumen activo o en una baja
        ya_resumen = ResumenStore.comprobante_ids_en_resumen_activo()
        ya_baja: set[int] = set()
        try:
            bajas, _ = BajaStore.listar(limit=500, offset=0)
            for b in bajas:
                if b.get("estado") in ("B",):
                    continue
                for it in b.get("_items_raw") or []:
                    cid = it.get("comprobante_id")
                    if cid:
                        try:
                            ya_baja.add(int(cid))
                        except (TypeError, ValueError):
                            pass
        except Exception as e:  # noqa: BLE001
            logger.debug("No se pudo cargar bajas para excluir: %s", e)

        out: list[ComprobantePendienteResumen] = []
        for c in candidatos:
            cid = int(c.get("id") or 0)
            if cid in ya_resumen or cid in ya_baja:
                continue
            out.append(ComprobantePendienteResumen(
                id=cid,
                numero_completo=c.get("numero_completo") or "",
                fecha_emision=c.get("fecha_emision"),
                cliente_numero_doc=c.get("cliente_numero_doc") or "",
                cliente_razon_social=c.get("cliente_razon_social") or "",
                moneda=c.get("moneda") or "PEN",
                total_venta=float(c.get("total_venta") or 0),
                estado=c.get("estado") or "P",
            ))
        out.sort(key=lambda x: (x.numero_completo or ""))
        return out

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


# =====================================================================
# POST / — crear y enviar
# =====================================================================
@router.post("", response_model=ResumenDiarioOut, status_code=201)
def crear_resumen(payload: ResumenDiarioIn, db: Session = Depends(get_db)):
    """Crea y envía un resumen diario."""
    if is_mdb_mode():
        from ..core.db_adapter.repo import ComprobanteRepoMDB, EmpresaRepoMDB
        from ..core.db_adapter.resumen_store import ResumenStore
        from ..services.resumen_service import enviar_resumen_directo

        # Resolver y validar comprobantes
        comp_ids = [it.comprobante_id for it in payload.items]
        condicion_por_id = {it.comprobante_id: it.condicion for it in payload.items}
        encontrados: dict[int, dict] = {}
        for cid in comp_ids:
            comp = ComprobanteRepoMDB.obtener(cid)
            if comp is None:
                raise HTTPException(
                    status_code=404,
                    detail=f"Comprobante id={cid} no encontrado",
                )
            encontrados[cid] = comp

        # fecha_referencia debe coincidir con fecha_emision
        fuera = [
            encontrados[cid].get("numero_completo")
            for cid in comp_ids
            if encontrados[cid].get("fecha_emision") != payload.fecha_referencia
        ]
        if fuera:
            raise HTTPException(
                status_code=422,
                detail=f"Estos comprobantes no son del {payload.fecha_referencia}: {fuera}",
            )

        # Evitar duplicados en otro resumen activo
        ya_set = ResumenStore.comprobante_ids_en_resumen_activo()
        en_otro = [cid for cid in comp_ids if cid in ya_set]
        if en_otro:
            raise HTTPException(
                status_code=409,
                detail=f"Comprobantes ya están en otro resumen: {sorted(en_otro)}",
            )

        fecha_com = payload.fecha_comunicacion or _date.today()
        correlativo = ResumenStore.proximo_correlativo(fecha_com)

        empresa = EmpresaRepoMDB.obtener()
        ruc = empresa.get("ruc") or "00000000000"
        nombre_archivo = f"{ruc}-RC-{fecha_com.strftime('%Y%m%d')}-{correlativo:03d}"

        total_gravado = sum(float(encontrados[cid].get("total_gravado") or 0) for cid in comp_ids)
        total_igv = sum(float(encontrados[cid].get("total_igv") or 0) for cid in comp_ids)
        total = sum(float(encontrados[cid].get("total_venta") or 0) for cid in comp_ids)

        # items_json compactos: lo necesario para reconstruir y para getStatus
        items_persist: list[dict] = []
        for cid in comp_ids:
            c = encontrados[cid]
            items_persist.append({
                "comprobante_id": cid,
                "condicion": condicion_por_id.get(cid, "1"),
                "tipo_documento": c.get("tipo_documento"),
                "serie": c.get("serie"),
                "correlativo": c.get("correlativo"),
                "numero_completo": c.get("numero_completo"),
                "total_venta": float(c.get("total_venta") or 0),
            })

        resumen_id = ResumenStore.crear(
            fecha_referencia=payload.fecha_referencia,
            fecha_comunicacion=fecha_com,
            correlativo=correlativo,
            nombre_archivo=nombre_archivo,
            items=items_persist,
            total_documentos=len(comp_ids),
            total_gravado=total_gravado,
            total_igv=total_igv,
            total=total,
        )

        # Construir comprobantes-dict para mapper (con condicion inyectada)
        comps_para_mapper: list[dict] = []
        for cid in comp_ids:
            c = dict(encontrados[cid])
            c["__condicion"] = condicion_por_id.get(cid, "1")
            comps_para_mapper.append(c)

        resumen_dict = {
            "correlativo": correlativo,
            "fecha_referencia": payload.fecha_referencia,
            "fecha_comunicacion": fecha_com,
            "nombre_archivo": nombre_archivo,
        }

        try:
            resultado = enviar_resumen_directo(resumen_dict, empresa, comps_para_mapper)
        except Exception as e:  # noqa: BLE001
            logger.exception("Error envío resumen SUNAT (directo)")
            raise http_502_sunat(str(e))

        ResumenStore.actualizar(
            resumen_id,
            estado=resultado.get("estado") or ("P" if resultado.get("success") else "R"),
            ticket=resultado.get("ticket"),
            xml_path=resultado.get("xml_path"),
            nombre_archivo=resultado.get("nombre_archivo") or nombre_archivo,
            cdr_descripcion=(resultado.get("error") if not resultado.get("success") else None),
        )

        if not resultado.get("success"):
            err = resultado.get("error") or "SUNAT rechazó el resumen"
            raise http_502_sunat(err)

        out = ResumenStore.obtener(resumen_id)
        return ResumenDiarioOut.model_validate(out)

    # ----- Fallback SQLAlchemy (legacy) -----
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


# =====================================================================
# GET /{id}
# =====================================================================
@router.get("/{resumen_id}", response_model=ResumenDiarioOut)
def obtener_resumen(resumen_id: int, db: Session = Depends(get_db)):
    if is_mdb_mode():
        from ..core.db_adapter.resumen_store import ResumenStore
        d = ResumenStore.obtener(resumen_id)
        if d is None:
            raise HTTPException(status_code=404, detail="Resumen no encontrado")
        return ResumenDiarioOut.model_validate(d)

    r = db.get(ResumenDiario, resumen_id)
    if r is None:
        raise HTTPException(status_code=404, detail="Resumen no encontrado")
    items = (db.query(ResumenDiarioItem)
              .filter(ResumenDiarioItem.resumen_id == resumen_id).all())
    return _serializar_resumen(r, items)


# =====================================================================
# GET /{id}/consultar-ticket
# =====================================================================
@router.get("/{resumen_id}/consultar-ticket", response_model=TicketConsultaOut)
def consultar_ticket_resumen(resumen_id: int, db: Session = Depends(get_db)):
    if is_mdb_mode():
        from ..core.db_adapter.resumen_store import ResumenStore
        from ..core.db_adapter.repo import EmpresaRepoMDB, ComprobanteWriterMDB
        from ..services.resumen_service import consultar_ticket_directo

        d = ResumenStore.obtener(resumen_id)
        if d is None:
            raise HTTPException(status_code=404, detail="Resumen no encontrado")
        if not d.get("ticket"):
            raise HTTPException(status_code=409,
                                 detail="Este resumen aún no tiene ticket SUNAT")

        empresa = EmpresaRepoMDB.obtener()
        try:
            res = consultar_ticket_directo(
                empresa, d["ticket"], d.get("nombre_archivo") or "",
            )
        except Exception as e:  # noqa: BLE001
            logger.exception("Error consultando ticket resumen (directo)")
            raise http_502_sunat(str(e))

        ResumenStore.actualizar(
            resumen_id,
            estado=res.get("estado") or "P",
            cdr_codigo=res.get("codigo"),
            cdr_descripcion=res.get("descripcion"),
            cdr_path=res.get("cdr_path"),
        )

        # Si aceptado, marcar comprobantes con condicion='3' como dados de baja
        if res.get("success"):
            for it in d.get("_items_raw") or []:
                if str(it.get("condicion") or "1") != "3":
                    continue
                cid = it.get("comprobante_id")
                if not cid:
                    continue
                try:
                    ComprobanteWriterMDB.anular(
                        int(cid),
                        f"[Resumen RC condicion=3] {d.get('nombre_archivo', '')}"[:200],
                    )
                except Exception as e:  # noqa: BLE001
                    logger.warning("No se pudo anular comp=%s: %s", cid, e)

        return TicketConsultaOut(
            ticket=d["ticket"],
            estado=res.get("estado") or d.get("estado"),
            cdr_codigo=res.get("codigo"),
            cdr_descripcion=res.get("descripcion"),
            cdr_path=res.get("cdr_path"),
        )

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


# ---------- Aliases para el frontend ----------
@router.get("/{resumen_id}/consultar", response_model=TicketConsultaOut)
def consultar_alias_resumen(resumen_id: int, db: Session = Depends(get_db)):
    return consultar_ticket_resumen(resumen_id, db)


@router.get("/{resumen_id}/cdr")
def descargar_cdr_resumen(resumen_id: int, db: Session = Depends(get_db)):
    if is_mdb_mode():
        from ..core.db_adapter.resumen_store import ResumenStore
        d = ResumenStore.obtener(resumen_id)
        if d is None:
            raise HTTPException(status_code=404, detail="Resumen no encontrado")
        cdr_path = d.get("cdr_path")
        if not cdr_path:
            raise HTTPException(status_code=404,
                                 detail="Aún no hay CDR para este resumen")
        p = Path(cdr_path)
        if not p.exists():
            raise HTTPException(status_code=404,
                                 detail=f"Archivo no encontrado: {p.name}")
        return FileResponse(path=str(p), filename=p.name,
                             media_type="application/zip")

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
