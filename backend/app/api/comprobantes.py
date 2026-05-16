"""Endpoints de comprobantes electrónicos (factura, boleta, NC, ND)."""
import logging
import random
import time
from datetime import date as _date
from pathlib import Path
from typing import Optional, List
from fastapi import APIRouter, Depends, HTTPException, Query, BackgroundTasks
from fastapi.responses import FileResponse, Response
from sqlalchemy import or_, and_, func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..services.excel_export import (
    dict_list_to_xlsx_bytes, xlsx_response_headers, XLSX_MEDIA_TYPE,
)

from ..core.database import get_db
from ..core.db_adapter import is_dbf_mode, is_mdb_mode
from ..models.empresa import Configuracion
from ..models.comprobante import Comprobante, ComprobanteDetalle
from ..schemas.comprobante import (
    ComprobanteIn, ComprobanteOut, ComprobanteListItem, ComprobanteListResponse,
    ProximoCorrelativoOut, AnularIn,
)
from ..schemas.common import MessageResponse
from ._deps import (
    cargar_servicio_comprobante, cargar_servicio_pdf, http_502_sunat,
    escape_like,
)
from ._comprobante_calc import calcular_totales

logger = logging.getLogger("factura_mdb.api.comprobantes")

router = APIRouter(prefix="/api/comprobantes", tags=["comprobantes"])

# Mensaje para endpoints SUNAT (envío) que NO se implementan en MDB Sprint 2.
_MDB_SUNAT_NOT_IMPL = (
    "Envío a SUNAT desde modo MDB no implementado (Sprint 3). "
    "El comprobante se creó localmente en el .mdb pero no se firmó ni envió."
)


def _numero_completo(serie: str, correlativo: int) -> str:
    return f"{serie}-{correlativo:08d}"


def _proximo_correlativo(db: Session, serie: str, tipo_documento: Optional[str] = None) -> int:
    q = db.query(func.max(Comprobante.correlativo)).filter(Comprobante.serie == serie)
    if tipo_documento:
        q = q.filter(Comprobante.tipo_documento == tipo_documento)
    last = q.scalar()
    return (last or 0) + 1


@router.get("", response_model=ComprobanteListResponse)
def listar_comprobantes(
    db: Session = Depends(get_db),
    tipo_documento: Optional[str] = Query(None),
    tipo: Optional[str] = Query(None, description="Alias de tipo_documento (frontend)"),
    serie: Optional[str] = Query(None),
    estado: Optional[str] = Query(None),
    fecha_desde: Optional[_date] = Query(None),
    desde: Optional[_date] = Query(None, description="Alias de fecha_desde (frontend)"),
    fecha_hasta: Optional[_date] = Query(None),
    hasta: Optional[_date] = Query(None, description="Alias de fecha_hasta (frontend)"),
    cliente_id: Optional[int] = Query(None),
    search: Optional[str] = Query(None),
    q_search: Optional[str] = Query(None, alias="q", description="Alias de search (frontend)"),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
):
    # Aliases del frontend
    tipo_documento = tipo_documento or tipo
    fecha_desde = fecha_desde or desde
    fecha_hasta = fecha_hasta or hasta
    search = search or q_search

    if is_dbf_mode():
        from ..core.db_adapter.dbf_repo import ComprobanteRepoDBF
        items, total = ComprobanteRepoDBF.listar(
            filtros={
                "tipo_documento": tipo_documento,
                "serie": serie,
                "estado": estado,
                "fecha_desde": fecha_desde,
                "fecha_hasta": fecha_hasta,
                "cliente_id": cliente_id,
                "q": (search or "").strip() or None,
            },
            limit=limit, offset=offset,
        )
        return ComprobanteListResponse(
            items=items, total=total, limit=limit, offset=offset,
        )

    if is_mdb_mode():
        from ..core.db_adapter.repo import ComprobanteRepoMDB
        items, total = ComprobanteRepoMDB.listar(
            filtros={
                "tipo_documento": tipo_documento,
                "serie": serie,
                "estado": estado,
                "fecha_desde": fecha_desde,
                "fecha_hasta": fecha_hasta,
                "cliente_id": cliente_id,
                "q": (search or "").strip() or None,
            },
            limit=limit, offset=offset,
        )
        return ComprobanteListResponse(
            items=items, total=total, limit=limit, offset=offset,
        )

    q = db.query(Comprobante)
    if tipo_documento:
        q = q.filter(Comprobante.tipo_documento == tipo_documento)
    if serie:
        q = q.filter(Comprobante.serie == serie.upper())
    if estado:
        q = q.filter(Comprobante.estado == estado)
    if fecha_desde:
        q = q.filter(Comprobante.fecha_emision >= fecha_desde)
    if fecha_hasta:
        q = q.filter(Comprobante.fecha_emision <= fecha_hasta)
    if cliente_id:
        q = q.filter(Comprobante.cliente_id == cliente_id)
    search_clean = (search or "").strip()
    if search_clean:
        # Bug E1: escapar wildcards LIKE
        safe = escape_like(search_clean)
        like = f"%{safe}%"
        q = q.filter(or_(
            Comprobante.numero_completo.like(like, escape="\\"),
            Comprobante.cliente_numero_doc.like(like, escape="\\"),
            Comprobante.cliente_razon_social.like(like, escape="\\"),
        ))

    total = q.count()
    items = (q.order_by(Comprobante.fecha_emision.desc(), Comprobante.id.desc())
              .limit(limit).offset(offset).all())
    return ComprobanteListResponse(items=items, total=total, limit=limit, offset=offset)


@router.get("/proximo-correlativo", response_model=ProximoCorrelativoOut)
def proximo_correlativo(
    serie: str = Query(..., min_length=4, max_length=4),
    tipo_documento: Optional[str] = Query(None),
    db: Session = Depends(get_db),
):
    serie = serie.upper()
    if is_mdb_mode():
        from ..core.db_adapter.repo import ComprobanteRepoMDB
        d = ComprobanteRepoMDB.proximo_correlativo(serie, tipo_documento)
        return ProximoCorrelativoOut(**d)
    last = (db.query(func.max(Comprobante.correlativo))
              .filter(Comprobante.serie == serie)
              .filter(Comprobante.tipo_documento == tipo_documento) if tipo_documento
            else db.query(func.max(Comprobante.correlativo))
              .filter(Comprobante.serie == serie)).scalar()
    return ProximoCorrelativoOut(
        serie=serie,
        proximo_correlativo=(last or 0) + 1,
        ultimo_emitido=last,
    )


@router.get("/export.xlsx")
def export_comprobantes_xlsx(
    db: Session = Depends(get_db),
    tipo_documento: Optional[str] = Query(None),
    tipo: Optional[str] = Query(None),
    serie: Optional[str] = Query(None),
    estado: Optional[str] = Query(None),
    fecha_desde: Optional[_date] = Query(None),
    desde: Optional[_date] = Query(None),
    fecha_hasta: Optional[_date] = Query(None),
    hasta: Optional[_date] = Query(None),
    cliente_id: Optional[int] = Query(None),
    search: Optional[str] = Query(None),
    q_search: Optional[str] = Query(None, alias="q"),
):
    """Exporta a .xlsx la lista de comprobantes con los mismos filtros del listado.

    Sin paginación: cap a 50,000 registros por seguridad.
    """
    tipo_documento = tipo_documento or tipo
    fecha_desde = fecha_desde or desde
    fecha_hasta = fecha_hasta or hasta
    search = search or q_search

    _tipo_label = {"01": "Factura", "03": "Boleta", "07": "Nota Crédito",
                    "08": "Nota Débito"}
    _estado_label = {
        "E": "Emitido (no enviado)",
        "A": "Aceptado SUNAT",
        "T": "Timeout (reintenta)",
        "R": "Rechazado SUNAT",
        "B": "Anulado/Baja",
        "P": "Pendiente",
        "X": "Comunic. baja",
    }

    if is_mdb_mode():
        from ..core.db_adapter.repo import ComprobanteRepoMDB
        items_dict, _ = ComprobanteRepoMDB.listar(
            filtros={
                "tipo_documento": tipo_documento,
                "serie": serie,
                "estado": estado,
                "fecha_desde": fecha_desde,
                "fecha_hasta": fecha_hasta,
                "cliente_id": cliente_id,
                "q": (search or "").strip() or None,
            },
            limit=50_000, offset=0,
        )
        rows = []
        for idx, c in enumerate(items_dict, start=1):
            rows.append({
                "n": idx,
                "fecha_emision": (c["fecha_emision"].isoformat()
                                   if c.get("fecha_emision") else ""),
                "tipo": _tipo_label.get(c.get("tipo_documento", ""),
                                         c.get("tipo_documento", "")),
                "serie": c.get("serie") or "",
                "correlativo": c.get("correlativo") or 0,
                "cliente_numero_doc": c.get("cliente_numero_doc") or "",
                "cliente_razon_social": c.get("cliente_razon_social") or "",
                "moneda": c.get("moneda") or "",
                "total_venta": float(c.get("total_venta") or 0.0),
                "estado": _estado_label.get(c.get("estado", ""),
                                             c.get("estado", "")),
            })
        headers = [
            ("n", "#"),
            ("fecha_emision", "Fecha"),
            ("tipo", "Tipo"),
            ("serie", "Serie"),
            ("correlativo", "Correlativo"),
            ("cliente_numero_doc", "Cliente Doc"),
            ("cliente_razon_social", "Cliente Razón Social"),
            ("moneda", "Moneda"),
            ("total_venta", "Total"),
            ("estado", "Estado SUNAT"),
        ]
        data = dict_list_to_xlsx_bytes(rows, headers=headers,
                                        sheet_name="Comprobantes")
        fname = f"comprobantes_{_date.today().isoformat()}.xlsx"
        return Response(content=data, media_type=XLSX_MEDIA_TYPE,
                         headers=xlsx_response_headers(fname))

    q = db.query(Comprobante)
    if tipo_documento:
        q = q.filter(Comprobante.tipo_documento == tipo_documento)
    if serie:
        q = q.filter(Comprobante.serie == serie.upper())
    if estado:
        q = q.filter(Comprobante.estado == estado)
    if fecha_desde:
        q = q.filter(Comprobante.fecha_emision >= fecha_desde)
    if fecha_hasta:
        q = q.filter(Comprobante.fecha_emision <= fecha_hasta)
    if cliente_id:
        q = q.filter(Comprobante.cliente_id == cliente_id)
    search_clean = (search or "").strip()
    if search_clean:
        # Bug E1: escapar wildcards LIKE
        safe = escape_like(search_clean)
        like = f"%{safe}%"
        q = q.filter(or_(
            Comprobante.numero_completo.like(like, escape="\\"),
            Comprobante.cliente_numero_doc.like(like, escape="\\"),
            Comprobante.cliente_razon_social.like(like, escape="\\"),
        ))

    items = (q.order_by(Comprobante.fecha_emision.desc(), Comprobante.id.desc())
              .limit(50_000).all())

    rows = []
    for idx, c in enumerate(items, start=1):
        rows.append({
            "n": idx,
            "fecha_emision": c.fecha_emision.isoformat() if c.fecha_emision else "",
            "tipo": _tipo_label.get(c.tipo_documento, c.tipo_documento),
            "serie": c.serie,
            "correlativo": c.correlativo,
            "cliente_numero_doc": c.cliente_numero_doc,
            "cliente_razon_social": c.cliente_razon_social,
            "moneda": c.moneda,
            "total_venta": float(c.total_venta or 0.0),
            "estado": _estado_label.get(c.estado, c.estado),
        })

    headers = [
        ("n", "#"),
        ("fecha_emision", "Fecha"),
        ("tipo", "Tipo"),
        ("serie", "Serie"),
        ("correlativo", "Correlativo"),
        ("cliente_numero_doc", "Cliente Doc"),
        ("cliente_razon_social", "Cliente Razón Social"),
        ("moneda", "Moneda"),
        ("total_venta", "Total"),
        ("estado", "Estado SUNAT"),
    ]
    data = dict_list_to_xlsx_bytes(rows, headers=headers,
                                    sheet_name="Comprobantes")
    fname = f"comprobantes_{_date.today().isoformat()}.xlsx"
    return Response(content=data, media_type=XLSX_MEDIA_TYPE,
                     headers=xlsx_response_headers(fname))


@router.get("/{comp_id}", response_model=ComprobanteOut)
def obtener_comprobante(comp_id: int, db: Session = Depends(get_db)):
    if is_mdb_mode():
        from ..core.db_adapter.repo import ComprobanteRepoMDB
        d = ComprobanteRepoMDB.obtener(comp_id)
        if d is None:
            raise HTTPException(status_code=404, detail="Comprobante no encontrado")
        return ComprobanteOut.model_validate(d)
    comp = db.get(Comprobante, comp_id)
    if comp is None:
        raise HTTPException(status_code=404, detail="Comprobante no encontrado")
    detalles = (db.query(ComprobanteDetalle)
                 .filter(ComprobanteDetalle.comprobante_id == comp_id)
                 .order_by(ComprobanteDetalle.orden).all())
    return ComprobanteOut.model_validate({
        **{c.name: getattr(comp, c.name) for c in comp.__table__.columns},
        "detalles": [
            {c.name: getattr(d, c.name) for c in d.__table__.columns}
            for d in detalles
        ],
    })


@router.post("/emitir", response_model=ComprobanteOut, status_code=201)
def emitir_comprobante(payload: ComprobanteIn, db: Session = Depends(get_db)):
    """Crea un comprobante. Si auto_envio_sunat=True, lo envía sincrónicamente.

    Modo MDB (Sprint 2): solo crea localmente en TBVENTA_CAB/DET, sin
    enviar a SUNAT. Devuelve estado 'P'.
    """
    if is_mdb_mode():
        from ..core.db_adapter.repo import ComprobanteWriterMDB
        from ..core.db_adapter.mdb_lock import MDBLockTimeout
        # IGV default 18% (no leemos config del MDB porque el .mdb no la tiene)
        igv_rate = 18.0
        calc = calcular_totales(payload, igv_rate)
        try:
            result = ComprobanteWriterMDB.crear(
                payload.model_dump(),
                items=calc["lineas"],
                totales=calc,
            )
        except MDBLockTimeout as e:
            raise HTTPException(status_code=503, detail=str(e))
        except Exception as e:  # noqa: BLE001
            logger.exception("Error creando comprobante en MDB")
            raise HTTPException(status_code=500,
                                 detail=f"Error escribiendo al MDB: {e}")
        # Hidratar detalles (lo que devolvió el writer + las líneas calculadas)
        detalles_out = []
        for idx, linea in enumerate(calc["lineas"], start=1):
            det = {**linea}
            det["id"] = idx  # id sintético interno (no real PK)
            det.setdefault("comprobante_id", result["id"])
            det.setdefault("producto_id", None)
            detalles_out.append(det)
        result["detalles"] = detalles_out
        return ComprobanteOut.model_validate(result)

    config = db.query(Configuracion).first()
    igv_rate = config.igv_rate if config else 18.0
    auto_envio = bool(config and config.auto_envio_sunat)

    # Calcular totales y líneas
    calc = calcular_totales(payload, igv_rate)

    # Bug A2/F2/G1: race condition en correlativo. Retry loop sobre IntegrityError UNIQUE.
    MAX_RETRIES = 5
    comp = None
    for intento in range(MAX_RETRIES):
        # Asignar correlativo si no vino (recalcular en cada retry)
        if payload.correlativo:
            correlativo = payload.correlativo
        else:
            correlativo = _proximo_correlativo(
                db, payload.serie, payload.tipo_documento
            )
        numero_completo = _numero_completo(payload.serie, correlativo)

        comp = Comprobante(
            tipo_documento=payload.tipo_documento,
            serie=payload.serie,
            correlativo=correlativo,
            numero_completo=numero_completo,
            fecha_emision=payload.fecha_emision,
            fecha_vencimiento=payload.fecha_vencimiento,
            hora_emision=payload.hora_emision,
            moneda=payload.moneda,
            tipo_cambio=payload.tipo_cambio,
            tipo_operacion=payload.tipo_operacion,
            cliente_id=payload.cliente_id,
            cliente_tipo_doc=payload.cliente_tipo_doc,
            cliente_numero_doc=payload.cliente_numero_doc,
            cliente_razon_social=payload.cliente_razon_social,
            cliente_direccion=payload.cliente_direccion,
            forma_pago=payload.forma_pago,
            forma_pago_json=payload.forma_pago_json,
            detraccion_codigo=payload.detraccion_codigo,
            detraccion_tasa=payload.detraccion_tasa,
            detraccion_monto=payload.detraccion_monto,
            detraccion_cta_bn=payload.detraccion_cta_bn,
            percepcion_pct=payload.percepcion_pct,
            percepcion_monto=payload.percepcion_monto,
            doc_referencia_tipo=payload.documento_referencia_tipo,
            doc_referencia_serie=payload.documento_referencia_serie,
            motivo_nc_codigo=payload.motivo_codigo,
            motivo_nc_descripcion=payload.motivo_descripcion,
            comprobante_ref_id=payload.comprobante_ref_id,
            observaciones=payload.observaciones,
            total_gravado=calc["total_gravado"],
            total_exonerado=calc["total_exonerado"],
            total_inafecto=calc["total_inafecto"],
            total_exportacion=calc["total_exportacion"],
            total_gratuito=calc["total_gratuito"],
            total_descuento=calc["total_descuento"],
            subtotal=calc["subtotal"],
            total_igv=calc["total_igv"],
            total_isc=calc["total_isc"],
            total_icbper=calc["total_icbper"],
            total_venta=calc["total_venta"],
            total_pen=calc["total_pen"],
            estado="P",
        )
        db.add(comp)
        try:
            db.flush()
            for linea in calc["lineas"]:
                det = ComprobanteDetalle(comprobante_id=comp.id, **linea)
                db.add(det)
            db.commit()
            db.refresh(comp)
            break
        except IntegrityError as e:
            db.rollback()
            msg = str(e).upper()
            # Bug D2: distinguir FK-violation (referencia inválida) de UNIQUE.
            if "FOREIGN KEY" in msg or "FOREIGN" in msg:
                raise HTTPException(
                    status_code=422,
                    detail=f"Referencia inválida: {str(e.orig)[:200]}",
                )
            # Si el correlativo vino fijo, no podemos reintentar
            if payload.correlativo:
                raise HTTPException(
                    status_code=409,
                    detail=f"Ya existe el comprobante {numero_completo}",
                )
            # UNIQUE collision por correlativo concurrente: reintentar
            if intento == MAX_RETRIES - 1:
                logger.warning("Conflicto de correlativo persistente serie=%s tras %d intentos",
                               payload.serie, MAX_RETRIES)
                raise HTTPException(
                    status_code=503,
                    detail="Conflicto de correlativo. Reintenta.",
                )
            time.sleep(0.05 + random.random() * 0.1)
            continue

    # Auto-envío SUNAT
    if auto_envio:
        svc = cargar_servicio_comprobante()
        if svc and hasattr(svc, "emitir_y_enviar"):
            try:
                svc.emitir_y_enviar(db, comp.id)
                db.refresh(comp)
            except Exception as e:  # noqa: BLE001
                logger.exception("Error en auto-envío SUNAT")
                # No fallar el emitir; queda en P y el usuario puede reenviar
                comp.observaciones = (
                    (comp.observaciones or "") +
                    f"\n[auto-envío falló] {e}"
                )
                db.commit()
                db.refresh(comp)

    detalles = (db.query(ComprobanteDetalle)
                 .filter(ComprobanteDetalle.comprobante_id == comp.id)
                 .order_by(ComprobanteDetalle.orden).all())
    return ComprobanteOut.model_validate({
        **{c.name: getattr(comp, c.name) for c in comp.__table__.columns},
        "detalles": [
            {c.name: getattr(d, c.name) for c in d.__table__.columns}
            for d in detalles
        ],
    })


@router.post("/{comp_id}/enviar-sunat", response_model=ComprobanteOut)
def enviar_sunat(comp_id: int, db: Session = Depends(get_db)):
    """Fuerza envío a SUNAT (re-intento o primer envío manual)."""
    if is_mdb_mode():
        # Sprint 3 — firma + envío directo sobre dicts (MDB/SQLite).
        from ..core.db_adapter.repo import (
            ComprobanteRepoMDB, EmpresaRepoMDB, ComprobanteWriterMDB,
        )
        from ..core.db_adapter.mdb_lock import MDBLockTimeout
        from ..services.comprobante_service import emitir_y_enviar_directo

        comp = ComprobanteRepoMDB.obtener(comp_id)
        if comp is None:
            raise HTTPException(status_code=404,
                                 detail="Comprobante no encontrado")
        if comp.get("estado") == "A":
            raise HTTPException(
                status_code=409,
                detail="El comprobante ya está aceptado por SUNAT",
            )
        if comp.get("estado") == "B":
            raise HTTPException(
                status_code=409,
                detail="El comprobante está anulado, no puede reenviarse",
            )

        empresa = EmpresaRepoMDB.obtener()
        detalles = comp.get("detalles") or []
        if not detalles:
            raise HTTPException(
                status_code=422,
                detail="El comprobante no tiene detalles que firmar",
            )

        try:
            resultado = emitir_y_enviar_directo(
                comp, empresa, detalles, generar_pdf=True,
            )
        except Exception as e:  # noqa: BLE001
            logger.exception("Error envío SUNAT (modo directo)")
            raise http_502_sunat(str(e))

        # Persistir CDR/estado (incluso si falló, para registrar rechazo)
        try:
            ComprobanteWriterMDB.actualizar_cdr(
                comp_id,
                tipo=comp.get("tipo_documento"),
                serie=comp.get("serie"),
                correlativo=comp.get("correlativo"),
                cdr_codigo=str(resultado.get("codigo") or ""),
                cdr_descripcion=str(resultado.get("descripcion") or ""),
                cdr_hash=str(resultado.get("cdr_hash") or ""),
                estado=resultado.get("estado") or "R",
                xml_path=resultado.get("xml_path"),
                cdr_path=resultado.get("cdr_path"),
                pdf_path=resultado.get("pdf_path"),
            )
        except MDBLockTimeout as e:
            raise HTTPException(status_code=503, detail=str(e))
        except Exception as e:  # noqa: BLE001
            logger.exception("Error persistiendo CDR (continúo)")

        # Si SUNAT rechazó, devolver 502 con detalle
        if not resultado.get("success"):
            err = resultado.get("error") or resultado.get("descripcion") or "SUNAT rechazó el comprobante"
            codigo = resultado.get("codigo")
            detalle = f"[{codigo}] {err}" if codigo else err
            raise http_502_sunat(detalle)

        # Releer y devolver
        nuevo = ComprobanteRepoMDB.obtener(comp_id)
        if nuevo is None:
            raise HTTPException(status_code=500,
                                 detail="No se pudo releer el comprobante")
        # Inyectar paths del resultado (en MDB no se persisten en columnas
        # estándar; F4CDR guarda solo la descripción)
        nuevo["xml_path"] = resultado.get("xml_path")
        nuevo["cdr_path"] = resultado.get("cdr_path")
        nuevo["pdf_path"] = resultado.get("pdf_path")
        return ComprobanteOut.model_validate(nuevo)

    comp = db.get(Comprobante, comp_id)
    if comp is None:
        raise HTTPException(status_code=404, detail="Comprobante no encontrado")
    if comp.estado == "A":
        raise HTTPException(status_code=409, detail="El comprobante ya está aceptado por SUNAT")

    svc = cargar_servicio_comprobante()
    if svc is None or not hasattr(svc, "emitir_y_enviar"):
        raise HTTPException(
            status_code=503,
            detail="Servicio de envío SUNAT no disponible aún.",
        )

    try:
        result = svc.emitir_y_enviar(db, comp.id)
    except Exception as e:  # noqa: BLE001
        logger.exception("Error envío SUNAT")
        raise http_502_sunat(str(e))

    # Bug C17/G8: el servicio puede retornar success=False sin lanzar excepción
    if isinstance(result, dict) and not result.get("success"):
        err = result.get("error") or result.get("descripcion") or "SUNAT rechazó el comprobante"
        codigo = result.get("codigo")
        detalle = f"[{codigo}] {err}" if codigo else err
        raise http_502_sunat(detalle)

    db.refresh(comp)
    detalles = (db.query(ComprobanteDetalle)
                 .filter(ComprobanteDetalle.comprobante_id == comp.id)
                 .order_by(ComprobanteDetalle.orden).all())
    return ComprobanteOut.model_validate({
        **{c.name: getattr(comp, c.name) for c in comp.__table__.columns},
        "detalles": [
            {c.name: getattr(d, c.name) for c in d.__table__.columns}
            for d in detalles
        ],
    })


@router.post("/{comp_id}/anular", response_model=MessageResponse)
def anular_local(comp_id: int, payload: AnularIn, db: Session = Depends(get_db)):
    """Anula localmente (estado=B). Sólo permitido si nunca fue enviado a SUNAT.

    Para anular en SUNAT usa /api/comunicacion-baja.
    """
    if is_mdb_mode():
        from ..core.db_adapter.repo import ComprobanteWriterMDB
        from ..core.db_adapter.mdb_lock import MDBLockTimeout
        from ..core.db_adapter.repo import ComprobanteRepoMDB
        existing = ComprobanteRepoMDB.obtener(comp_id)
        if existing is None:
            raise HTTPException(status_code=404, detail="Comprobante no encontrado")
        if existing.get("estado") == "A":
            raise HTTPException(
                status_code=409,
                detail="Ya fue aceptado por SUNAT. Usa Comunicación de Baja.",
            )
        if existing.get("estado") == "B":
            raise HTTPException(
                status_code=409,
                detail="El comprobante ya está anulado",
            )
        try:
            ok = ComprobanteWriterMDB.anular(comp_id, payload.motivo)
        except MDBLockTimeout as e:
            raise HTTPException(status_code=503, detail=str(e))
        except Exception as e:  # noqa: BLE001
            logger.exception("Error anulando comprobante en MDB")
            raise HTTPException(status_code=500,
                                 detail=f"Error escribiendo al MDB: {e}")
        if not ok:
            raise HTTPException(status_code=404,
                                 detail="Comprobante no encontrado en MDB")
        return MessageResponse(ok=True, mensaje="Comprobante anulado localmente")

    comp = db.get(Comprobante, comp_id)
    if comp is None:
        raise HTTPException(status_code=404, detail="Comprobante no encontrado")
    if comp.estado == "A":
        raise HTTPException(
            status_code=409,
            detail="Ya fue aceptado por SUNAT. Usa Comunicación de Baja.",
        )
    # Bug D10: evitar anular dos veces (estado idempotente)
    if comp.estado == "B":
        raise HTTPException(
            status_code=409,
            detail="El comprobante ya está anulado",
        )
    comp.estado = "B"
    comp.observaciones = (comp.observaciones or "") + f"\n[anulado local] {payload.motivo}"
    db.commit()
    return MessageResponse(ok=True, mensaje="Comprobante anulado localmente")


# ---------- Descargas ----------

def _file_response(path_str: Optional[str], filename: str, media_type: str):
    if not path_str:
        raise HTTPException(status_code=404, detail="Archivo no disponible")
    p = Path(path_str)
    if not p.exists():
        raise HTTPException(status_code=404, detail=f"Archivo no encontrado: {p.name}")
    return FileResponse(path=str(p), filename=filename, media_type=media_type)


@router.get("/{comp_id}/descargar/xml")
def descargar_xml(comp_id: int, db: Session = Depends(get_db)):
    comp = db.get(Comprobante, comp_id)
    if comp is None:
        raise HTTPException(status_code=404, detail="Comprobante no encontrado")
    return _file_response(
        comp.xml_path,
        filename=f"{comp.numero_completo}.xml",
        media_type="application/xml",
    )


@router.get("/{comp_id}/descargar/cdr")
def descargar_cdr(comp_id: int, db: Session = Depends(get_db)):
    comp = db.get(Comprobante, comp_id)
    if comp is None:
        raise HTTPException(status_code=404, detail="Comprobante no encontrado")
    return _file_response(
        comp.cdr_path,
        filename=f"R-{comp.numero_completo}.zip",
        media_type="application/zip",
    )


@router.get("/{comp_id}/descargar/pdf")
def descargar_pdf(comp_id: int, db: Session = Depends(get_db)):
    comp = db.get(Comprobante, comp_id)
    if comp is None:
        raise HTTPException(status_code=404, detail="Comprobante no encontrado")

    # Si no existe, intentar generar al vuelo via servicio
    if not comp.pdf_path or not Path(comp.pdf_path).exists():
        svc = cargar_servicio_pdf()
        if svc is None or not hasattr(svc, "generar_pdf"):
            raise HTTPException(
                status_code=503,
                detail="Generador de PDF no disponible aún",
            )
        try:
            ruta = svc.generar_pdf(db, comp.id)
            comp.pdf_path = str(ruta)
            db.commit()
        except Exception as e:  # noqa: BLE001
            logger.exception("Error generando PDF")
            raise HTTPException(status_code=500, detail=f"No se pudo generar PDF: {e}")

    return _file_response(
        comp.pdf_path,
        filename=f"{comp.numero_completo}.pdf",
        media_type="application/pdf",
    )


# ---------- Aliases para el frontend (Agente C) ----------
@router.post("/{comp_id}/reintentar", response_model=ComprobanteOut)
def reintentar_envio(comp_id: int, db: Session = Depends(get_db)):
    """Alias de POST /{comp_id}/enviar-sunat."""
    return enviar_sunat(comp_id, db)


@router.get("/{comp_id}/xml")
def descargar_xml_alias(comp_id: int, db: Session = Depends(get_db)):
    return descargar_xml(comp_id, db)


@router.get("/{comp_id}/cdr")
def descargar_cdr_alias(comp_id: int, db: Session = Depends(get_db)):
    return descargar_cdr(comp_id, db)


@router.get("/{comp_id}/pdf")
def descargar_pdf_alias(comp_id: int, inline: int = 0,
                         force: int = 0,
                         db: Session = Depends(get_db)):
    """Alias de /descargar/pdf con soporte ?inline=1 para iframe.

    `force=1`: regenera el PDF aunque exista cacheado en storage/pdf/.
    """
    if is_dbf_mode():
        from ..core.db_adapter.dbf_repo import ComprobanteRepoDBF, EmpresaRepoDBF
        from ..core.config import storage_dir
        from ..services._dict_adapter import DictNS

        comp_dict = ComprobanteRepoDBF.obtener(comp_id)
        if comp_dict is None:
            raise HTTPException(status_code=404, detail="Comprobante no encontrado")
        empresa = EmpresaRepoDBF.obtener()

        num_parts = (comp_dict.get("numero_completo") or "").split("-")
        correl = num_parts[1] if len(num_parts) == 2 else f"{int(comp_dict.get('correlativo') or 0):08d}"
        filename = f"{empresa.get('ruc')}-{comp_dict.get('tipo_documento')}-{comp_dict.get('serie')}-{correl}"
        pdf_path = storage_dir() / "pdf" / f"{filename}.pdf"

        if force or not pdf_path.exists():
            try:
                from ..services.pdf_generator import generar_pdf_comprobante
                comp_ns = DictNS(comp_dict)
                emp_ns = DictNS(empresa)
                det_list = [DictNS(d) for d in (comp_dict.get("detalles") or [])]
                pdf_bytes = generar_pdf_comprobante(comp_ns, det_list, emp_ns, None)
                pdf_path.parent.mkdir(parents=True, exist_ok=True)
                pdf_path.write_bytes(pdf_bytes)
            except Exception as e:  # noqa: BLE001
                logger.exception("Error generando PDF modo DBF")
                raise HTTPException(status_code=500, detail=f"No se pudo generar PDF: {e}")

        headers = {}
        if inline:
            headers["Content-Disposition"] = f'inline; filename="{comp_dict.get("numero_completo")}.pdf"'
        return FileResponse(path=str(pdf_path),
                             filename=f"{comp_dict.get('numero_completo')}.pdf",
                             media_type="application/pdf",
                             headers=headers)

    if is_mdb_mode():
        from ..core.db_adapter.repo import ComprobanteRepoMDB, EmpresaRepoMDB
        from ..core.config import storage_dir
        from ..services._dict_adapter import DictNS

        comp_dict = ComprobanteRepoMDB.obtener(comp_id)
        if comp_dict is None:
            raise HTTPException(status_code=404, detail="Comprobante no encontrado")
        empresa = EmpresaRepoMDB.obtener()

        # Convención SUNAT: {RUC}-{TIPO}-{SERIE}-{CORRELATIVO_PADDED}.pdf
        num_parts = (comp_dict.get("numero_completo") or "").split("-")
        correl = num_parts[1] if len(num_parts) == 2 else f"{int(comp_dict.get('correlativo') or 0):08d}"
        filename = f"{empresa.get('ruc')}-{comp_dict.get('tipo_documento')}-{comp_dict.get('serie')}-{correl}"
        pdf_path = storage_dir() / "pdf" / f"{filename}.pdf"

        # Regenerar si no existe (o si force=1)
        if force or not pdf_path.exists():
            try:
                from ..services.pdf_generator import generar_pdf_comprobante
                comp_ns = DictNS(comp_dict)
                emp_ns = DictNS(empresa)
                det_list = [DictNS(d) for d in (comp_dict.get("detalles") or [])]
                pdf_bytes = generar_pdf_comprobante(comp_ns, det_list, emp_ns, None)
                pdf_path.parent.mkdir(parents=True, exist_ok=True)
                pdf_path.write_bytes(pdf_bytes)
            except Exception as e:  # noqa: BLE001
                logger.exception("Error generando PDF modo MDB")
                raise HTTPException(status_code=500, detail=f"No se pudo generar PDF: {e}")

        headers = {}
        if inline:
            headers["Content-Disposition"] = f'inline; filename="{comp_dict.get("numero_completo")}.pdf"'
        return FileResponse(path=str(pdf_path),
                             filename=f"{comp_dict.get('numero_completo')}.pdf",
                             media_type="application/pdf",
                             headers=headers)

    # Flujo SQLAlchemy original
    comp = db.get(Comprobante, comp_id)
    if comp is None:
        raise HTTPException(status_code=404, detail="Comprobante no encontrado")
    if not comp.pdf_path or not Path(comp.pdf_path).exists():
        svc = cargar_servicio_pdf()
        if svc is None or not hasattr(svc, "generar_pdf"):
            raise HTTPException(status_code=503,
                                detail="Generador de PDF no disponible aún")
        try:
            ruta = svc.generar_pdf(db, comp.id)
            comp.pdf_path = str(ruta)
            db.commit()
        except Exception as e:  # noqa: BLE001
            logger.exception("Error generando PDF")
            raise HTTPException(status_code=500, detail=f"No se pudo generar PDF: {e}")

    p = Path(comp.pdf_path)
    if not p.exists():
        raise HTTPException(status_code=404, detail="PDF no encontrado")
    headers = {}
    if inline:
        headers["Content-Disposition"] = f'inline; filename="{comp.numero_completo}.pdf"'
    return FileResponse(path=str(p),
                         filename=f"{comp.numero_completo}.pdf",
                         media_type="application/pdf",
                         headers=headers)


# ─────────────────────────────────────────────────────────────────────────
# Cotización y Pedido — PDFs internos generados desde un comprobante
# existente. NO son documentos SUNAT, no se envían a SUNAT, no se persisten.
# ─────────────────────────────────────────────────────────────────────────

@router.get("/{comp_id}/cotizacion-pdf", summary="PDF de cotización (no SUNAT)")
def descargar_cotizacion_pdf(
    comp_id: int,
    validez_dias: int = 15,
    condicion_pago: str = "",
    vendedor: str = "",
    observaciones: str = "",
    inline: int = 0,
    db: Session = Depends(get_db),
):
    """Genera PDF de COTIZACION reutilizando los items de un comprobante.
    Útil para enviar propuesta al cliente antes de emitir factura.
    """
    from ..core.db_adapter.repo import ComprobanteRepoMDB, EmpresaRepoMDB
    from ..services._dict_adapter import DictNS
    from ..services.pdf_generator import generar_pdf_cotizacion
    from fastapi.responses import Response

    if not is_mdb_mode():
        raise HTTPException(status_code=501, detail="Endpoint solo disponible en modo MDB/DBF/SQLite")
    comp_dict = ComprobanteRepoMDB.obtener(comp_id)
    if not comp_dict:
        raise HTTPException(status_code=404, detail="Comprobante no encontrado")
    empresa = EmpresaRepoMDB.obtener()
    detalles = [DictNS(d) for d in (comp_dict.get("detalles") or [])]
    try:
        pdf = generar_pdf_cotizacion(
            DictNS(comp_dict), detalles, DictNS(empresa),
            validez_dias=validez_dias,
            condicion_pago=condicion_pago,
            vendedor=vendedor,
            observaciones=observaciones,
        )
    except Exception as e:  # noqa: BLE001
        logger.exception("Error generando PDF cotización")
        raise HTTPException(status_code=500, detail=f"No se pudo generar PDF: {e}")
    fname = f"COTIZACION-{comp_dict.get('numero_completo')}.pdf"
    headers = {"Content-Disposition": f'{"inline" if inline else "attachment"}; filename="{fname}"'}
    return Response(content=pdf, media_type="application/pdf", headers=headers)


@router.get("/{comp_id}/pedido-pdf", summary="PDF de nota de pedido (no SUNAT)")
def descargar_pedido_pdf(
    comp_id: int,
    condicion_pago: str = "",
    vendedor: str = "",
    observaciones: str = "",
    inline: int = 0,
    db: Session = Depends(get_db),
):
    """Genera PDF de NOTA DE PEDIDO. Confirma un pedido del cliente antes
    de emitir la factura. Mismo dataset que /cotizacion-pdf, distinto layout.
    """
    from ..core.db_adapter.repo import ComprobanteRepoMDB, EmpresaRepoMDB
    from ..services._dict_adapter import DictNS
    from ..services.pdf_generator import generar_pdf_pedido
    from fastapi.responses import Response

    if not is_mdb_mode():
        raise HTTPException(status_code=501, detail="Endpoint solo disponible en modo MDB/DBF/SQLite")
    comp_dict = ComprobanteRepoMDB.obtener(comp_id)
    if not comp_dict:
        raise HTTPException(status_code=404, detail="Comprobante no encontrado")
    empresa = EmpresaRepoMDB.obtener()
    detalles = [DictNS(d) for d in (comp_dict.get("detalles") or [])]
    try:
        pdf = generar_pdf_pedido(
            DictNS(comp_dict), detalles, DictNS(empresa),
            condicion_pago=condicion_pago,
            vendedor=vendedor,
            observaciones=observaciones,
        )
    except Exception as e:  # noqa: BLE001
        logger.exception("Error generando PDF pedido")
        raise HTTPException(status_code=500, detail=f"No se pudo generar PDF: {e}")
    fname = f"PEDIDO-{comp_dict.get('numero_completo')}.pdf"
    headers = {"Content-Disposition": f'{"inline" if inline else "attachment"}; filename="{fname}"'}
    return Response(content=pdf, media_type="application/pdf", headers=headers)


# ---------- Catálogo de series (correlativos) ----------
correlativos_router = APIRouter(prefix="/api/correlativos", tags=["correlativos"])

# Series default si no hay comprobantes emitidos aún
_SERIES_DEFAULT = ["F001", "B001", "FC01", "BC01", "FD01", "BD01"]


@correlativos_router.get("")
def listar_series(db: Session = Depends(get_db)):
    """Devuelve series existentes en la BD + defaults para arrancar."""
    if is_mdb_mode():
        from ..core.db_adapter.repo import ComprobanteRepoMDB
        items = ComprobanteRepoMDB.listar_series()
        existentes = {it["serie"] for it in items}
        for s in _SERIES_DEFAULT:
            if s not in existentes:
                items.append({"serie": s, "ultimo": 0})
        items.sort(key=lambda x: x["serie"])
        return {"items": items}
    rows = (
        db.query(Comprobante.serie, func.max(Comprobante.correlativo).label("ultimo"))
          .group_by(Comprobante.serie)
          .all()
    )
    existentes = {r.serie: r.ultimo for r in rows if r.serie}
    items = [{"serie": s, "ultimo": existentes[s]} for s in existentes]
    for s in _SERIES_DEFAULT:
        if s not in existentes:
            items.append({"serie": s, "ultimo": 0})
    items.sort(key=lambda x: x["serie"])
    return {"items": items}
