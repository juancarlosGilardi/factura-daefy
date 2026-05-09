"""Endpoints de reportes para el dashboard."""
import logging
from datetime import date as _date, timedelta
from typing import Optional, List, Dict, Any
from fastapi import APIRouter, Depends, Query
from fastapi.responses import Response
from sqlalchemy import func, and_, case
from sqlalchemy.orm import Session

from ..core.database import get_db
from ..models.comprobante import Comprobante
from ..models.cliente import Cliente
from ..models.producto import Producto
from ..services.excel_export import (
    dict_list_to_xlsx_bytes, xlsx_response_headers, XLSX_MEDIA_TYPE,
)

logger = logging.getLogger("factura_mdb.api.reportes")

router = APIRouter(prefix="/api/reportes", tags=["reportes"])


def _xlsx_response(rows, headers, sheet_name: str, filename: str) -> Response:
    data = dict_list_to_xlsx_bytes(rows, headers=headers, sheet_name=sheet_name)
    return Response(
        content=data,
        media_type=XLSX_MEDIA_TYPE,
        headers=xlsx_response_headers(filename),
    )


@router.get("/ventas-mes")
def ventas_mes(
    anio: int = Query(..., ge=2000, le=2100),
    mes: int = Query(..., ge=1, le=12),
    db: Session = Depends(get_db),
):
    """Total ventas del mes desglosado por moneda. Excluye anuladas (B) y rechazadas (R)."""
    desde = _date(anio, mes, 1)
    hasta = _date(anio + (1 if mes == 12 else 0), 1 if mes == 12 else mes + 1, 1)

    rows = (
        db.query(
            Comprobante.moneda,
            func.count(Comprobante.id).label("cantidad"),
            func.coalesce(func.sum(Comprobante.total_venta), 0.0).label("total"),
            func.coalesce(func.sum(Comprobante.total_pen), 0.0).label("total_pen"),
        )
        .filter(Comprobante.fecha_emision >= desde)
        .filter(Comprobante.fecha_emision < hasta)
        .filter(Comprobante.estado.notin_(["B", "R"]))
        .filter(Comprobante.tipo_documento.in_(["01", "03", "07", "08"]))
        .group_by(Comprobante.moneda)
        .all()
    )
    return {
        "anio": anio,
        "mes": mes,
        "por_moneda": [
            {"moneda": r.moneda, "cantidad": r.cantidad,
             "total": float(r.total), "total_pen": float(r.total_pen)}
            for r in rows
        ],
        "total_pen": float(sum(r.total_pen for r in rows)),
        "total_comprobantes": int(sum(r.cantidad for r in rows)),
    }


@router.get("/pendientes-sunat")
def pendientes_sunat(db: Session = Depends(get_db)):
    """Comprobantes en estado P o R."""
    rows = (
        db.query(
            Comprobante.estado,
            func.count(Comprobante.id).label("cantidad"),
            func.coalesce(func.sum(Comprobante.total_pen), 0.0).label("total_pen"),
        )
        .filter(Comprobante.estado.in_(["P", "R"]))
        .group_by(Comprobante.estado)
        .all()
    )
    detalle = {r.estado: {"cantidad": int(r.cantidad), "total_pen": float(r.total_pen)} for r in rows}
    return {
        "pendientes": detalle.get("P", {"cantidad": 0, "total_pen": 0.0}),
        "rechazados": detalle.get("R", {"cantidad": 0, "total_pen": 0.0}),
        "total_cantidad": sum(d["cantidad"] for d in detalle.values()),
        "total_pen": sum(d["total_pen"] for d in detalle.values()),
    }


@router.get("/top-clientes")
def top_clientes(
    desde: Optional[_date] = Query(None),
    hasta: Optional[_date] = Query(None),
    limit: int = Query(10, ge=1, le=100),
    db: Session = Depends(get_db),
):
    """Top clientes por monto facturado en el período (en PEN)."""
    q = (
        db.query(
            Comprobante.cliente_numero_doc.label("doc"),
            Comprobante.cliente_razon_social.label("razon"),
            func.count(Comprobante.id).label("cantidad"),
            func.coalesce(func.sum(Comprobante.total_pen), 0.0).label("total_pen"),
        )
        .filter(Comprobante.estado.notin_(["B", "R"]))
        .filter(Comprobante.tipo_documento.in_(["01", "03"]))
    )
    if desde:
        q = q.filter(Comprobante.fecha_emision >= desde)
    if hasta:
        q = q.filter(Comprobante.fecha_emision <= hasta)

    rows = (q.group_by(Comprobante.cliente_numero_doc, Comprobante.cliente_razon_social)
             .order_by(func.sum(Comprobante.total_pen).desc())
             .limit(limit).all())

    return [
        {
            "numero_documento": r.doc,
            "razon_social": r.razon,
            "cantidad": int(r.cantidad),
            "total_pen": float(r.total_pen),
        }
        for r in rows
    ]


# ---------- Aliases para el frontend (Agente C) ----------
@router.get("/ventas")
def ventas_periodo(
    desde: Optional[_date] = Query(None),
    hasta: Optional[_date] = Query(None),
    db: Session = Depends(get_db),
):
    """Ventas en un período con desglose por día. Shape esperado por el frontend."""
    if not desde:
        hoy = _date.today()
        desde = _date(hoy.year, hoy.month, 1)
    if not hasta:
        hasta = _date.today()

    base_q = (
        db.query(Comprobante)
        .filter(Comprobante.fecha_emision >= desde)
        .filter(Comprobante.fecha_emision <= hasta)
        .filter(Comprobante.tipo_documento.in_(["01", "03"]))
    )

    activos = base_q.filter(Comprobante.estado.notin_(["B", "R"]))
    total = activos.with_entities(
        func.coalesce(func.sum(Comprobante.total_pen), 0.0)
    ).scalar() or 0.0
    cantidad = activos.count()
    aceptados = base_q.filter(Comprobante.estado == "A").count()
    pendientes = base_q.filter(Comprobante.estado.in_(["P", "R"])).count()

    rows = (
        activos.with_entities(
            Comprobante.fecha_emision.label("fecha"),
            func.coalesce(func.sum(Comprobante.total_pen), 0.0).label("total"),
        )
        .group_by(Comprobante.fecha_emision)
        .order_by(Comprobante.fecha_emision.asc())
        .all()
    )
    dias = [{"fecha": r.fecha.isoformat(), "total": float(r.total)} for r in rows]

    return {
        "desde": desde.isoformat(),
        "hasta": hasta.isoformat(),
        "total": float(total),
        "cantidad": int(cantidad),
        "aceptados": int(aceptados),
        "pendientes": int(pendientes),
        "dias": dias,
    }


@router.get("/top-productos")
def top_productos_periodo(
    desde: Optional[_date] = Query(None),
    hasta: Optional[_date] = Query(None),
    limit: int = Query(10, ge=1, le=100),
    db: Session = Depends(get_db),
):
    """Top productos por monto facturado en el período (en PEN)."""
    from ..models.comprobante import ComprobanteDetalle  # local import (alias)

    q = (
        db.query(
            ComprobanteDetalle.descripcion.label("producto"),
            func.coalesce(func.sum(ComprobanteDetalle.cantidad), 0.0).label("cantidad"),
            func.coalesce(
                func.sum(ComprobanteDetalle.precio_unitario * ComprobanteDetalle.cantidad),
                0.0,
            ).label("total"),
        )
        .join(Comprobante, Comprobante.id == ComprobanteDetalle.comprobante_id)
        .filter(Comprobante.estado.notin_(["B", "R"]))
        .filter(Comprobante.tipo_documento.in_(["01", "03"]))
    )
    if desde:
        q = q.filter(Comprobante.fecha_emision >= desde)
    if hasta:
        q = q.filter(Comprobante.fecha_emision <= hasta)

    rows = (
        q.group_by(ComprobanteDetalle.descripcion)
         .order_by(func.sum(
             ComprobanteDetalle.precio_unitario * ComprobanteDetalle.cantidad
         ).desc())
         .limit(limit).all()
    )
    return {
        "items": [
            {"producto": r.producto, "cantidad": float(r.cantidad),
             "total": float(r.total)}
            for r in rows
        ]
    }


@router.get("/top-clientes-resumen")
def top_clientes_resumen(
    desde: Optional[_date] = Query(None),
    hasta: Optional[_date] = Query(None),
    limit: int = Query(10, ge=1, le=100),
    db: Session = Depends(get_db),
):
    """Wrapper de /top-clientes con shape `{items: [...]}` esperado por el frontend."""
    base = top_clientes(desde=desde, hasta=hasta, limit=limit, db=db)
    return {
        "items": [
            {"cliente": r.get("razon_social") or r.get("numero_documento"),
             "cantidad": r.get("cantidad", 0),
             "total": r.get("total_pen", 0.0)}
            for r in base
        ]
    }


@router.get("/resumen-dashboard")
def resumen_dashboard(db: Session = Depends(get_db)):
    """Combo para el dashboard: hoy, mes, pendientes, totales."""
    hoy = _date.today()
    inicio_mes = _date(hoy.year, hoy.month, 1)

    # Ventas hoy (PEN)
    ventas_hoy = (
        db.query(func.coalesce(func.sum(Comprobante.total_pen), 0.0))
        .filter(Comprobante.fecha_emision == hoy)
        .filter(Comprobante.estado.notin_(["B", "R"]))
        .filter(Comprobante.tipo_documento.in_(["01", "03"]))
        .scalar() or 0.0
    )
    cant_hoy = (
        db.query(func.count(Comprobante.id))
        .filter(Comprobante.fecha_emision == hoy)
        .filter(Comprobante.estado.notin_(["B", "R"]))
        .filter(Comprobante.tipo_documento.in_(["01", "03"]))
        .scalar() or 0
    )

    # Ventas mes
    ventas_mes_v = (
        db.query(func.coalesce(func.sum(Comprobante.total_pen), 0.0))
        .filter(Comprobante.fecha_emision >= inicio_mes)
        .filter(Comprobante.fecha_emision <= hoy)
        .filter(Comprobante.estado.notin_(["B", "R"]))
        .filter(Comprobante.tipo_documento.in_(["01", "03"]))
        .scalar() or 0.0
    )
    cant_mes = (
        db.query(func.count(Comprobante.id))
        .filter(Comprobante.fecha_emision >= inicio_mes)
        .filter(Comprobante.fecha_emision <= hoy)
        .filter(Comprobante.estado.notin_(["B", "R"]))
        .filter(Comprobante.tipo_documento.in_(["01", "03"]))
        .scalar() or 0
    )

    # Pendientes
    cant_pendientes = (
        db.query(func.count(Comprobante.id))
        .filter(Comprobante.estado.in_(["P", "R"]))
        .scalar() or 0
    )

    total_clientes = (
        db.query(func.count(Cliente.id))
        .filter(Cliente.activo == True)  # noqa: E712
        .scalar() or 0
    )
    total_productos = (
        db.query(func.count(Producto.id))
        .filter(Producto.activo == True)  # noqa: E712
        .scalar() or 0
    )

    return {
        "fecha": hoy.isoformat(),
        "ventas_hoy": {"total_pen": float(ventas_hoy), "cantidad": int(cant_hoy)},
        "ventas_mes": {"total_pen": float(ventas_mes_v), "cantidad": int(cant_mes)},
        "pendientes_sunat": int(cant_pendientes),
        "total_clientes": int(total_clientes),
        "total_productos": int(total_productos),
    }


# =====================================================================
# Exports a Excel (.xlsx) — Feature 1
# =====================================================================

@router.get("/ventas-mes/export.xlsx")
def export_ventas_mes(
    anio: int = Query(..., ge=2000, le=2100),
    mes: int = Query(..., ge=1, le=12),
    db: Session = Depends(get_db),
):
    """Exporta el detalle de ventas del mes (por moneda) a .xlsx."""
    data = ventas_mes(anio=anio, mes=mes, db=db)
    rows = data.get("por_moneda") or []
    # Añadir fila resumen al final
    rows = list(rows) + [{
        "moneda": "TOTAL (PEN)",
        "cantidad": data.get("total_comprobantes", 0),
        "total": "",
        "total_pen": data.get("total_pen", 0.0),
    }]
    headers = [
        ("moneda", "Moneda"),
        ("cantidad", "Cantidad"),
        ("total", "Total (moneda original)"),
        ("total_pen", "Total (PEN)"),
    ]
    fname = f"reporte_ventas_mes_{anio:04d}-{mes:02d}.xlsx"
    return _xlsx_response(rows, headers, sheet_name=f"Ventas {anio}-{mes:02d}",
                          filename=fname)


@router.get("/ventas/export.xlsx")
def export_ventas_periodo(
    desde: Optional[_date] = Query(None),
    hasta: Optional[_date] = Query(None),
    db: Session = Depends(get_db),
):
    """Exporta el detalle día-por-día del período."""
    data = ventas_periodo(desde=desde, hasta=hasta, db=db)
    rows = data.get("dias") or []
    headers = [
        ("fecha", "Fecha"),
        ("total", "Total (PEN)"),
    ]
    d, h = data.get("desde"), data.get("hasta")
    fname = f"reporte_ventas_{d}_a_{h}.xlsx"
    return _xlsx_response(rows, headers, sheet_name="Ventas por dia",
                          filename=fname)


@router.get("/top-clientes/export.xlsx")
def export_top_clientes(
    desde: Optional[_date] = Query(None),
    hasta: Optional[_date] = Query(None),
    limit: int = Query(50, ge=1, le=1000),
    db: Session = Depends(get_db),
):
    rows = top_clientes(desde=desde, hasta=hasta, limit=limit, db=db)
    headers = [
        ("numero_documento", "N° Documento"),
        ("razon_social", "Razón Social"),
        ("cantidad", "Comprobantes"),
        ("total_pen", "Total (PEN)"),
    ]
    suffix = ""
    if desde or hasta:
        suffix = f"_{desde or ''}_a_{hasta or ''}"
    fname = f"reporte_top_clientes{suffix}.xlsx"
    return _xlsx_response(rows, headers, sheet_name="Top clientes",
                          filename=fname)


@router.get("/top-productos/export.xlsx")
def export_top_productos(
    desde: Optional[_date] = Query(None),
    hasta: Optional[_date] = Query(None),
    limit: int = Query(50, ge=1, le=1000),
    db: Session = Depends(get_db),
):
    data = top_productos_periodo(desde=desde, hasta=hasta, limit=limit, db=db)
    rows = data.get("items") or []
    headers = [
        ("producto", "Producto"),
        ("cantidad", "Cantidad"),
        ("total", "Total (PEN)"),
    ]
    suffix = ""
    if desde or hasta:
        suffix = f"_{desde or ''}_a_{hasta or ''}"
    fname = f"reporte_top_productos{suffix}.xlsx"
    return _xlsx_response(rows, headers, sheet_name="Top productos",
                          filename=fname)


@router.get("/pendientes-sunat/export.xlsx")
def export_pendientes_sunat(db: Session = Depends(get_db)):
    """Exporta el detalle de comprobantes pendientes/rechazados."""
    items = (
        db.query(Comprobante)
        .filter(Comprobante.estado.in_(["P", "R"]))
        .order_by(Comprobante.fecha_emision.desc(), Comprobante.id.desc())
        .all()
    )
    rows = [
        {
            "numero_completo": c.numero_completo,
            "fecha_emision": c.fecha_emision.isoformat() if c.fecha_emision else "",
            "tipo_documento": c.tipo_documento,
            "cliente_numero_doc": c.cliente_numero_doc,
            "cliente_razon_social": c.cliente_razon_social,
            "moneda": c.moneda,
            "total_venta": float(c.total_venta or 0.0),
            "total_pen": float(c.total_pen or 0.0),
            "estado": c.estado,
            "cdr_descripcion": c.cdr_descripcion or "",
        }
        for c in items
    ]
    headers = [
        ("numero_completo", "Número"),
        ("fecha_emision", "Fecha"),
        ("tipo_documento", "Tipo"),
        ("cliente_numero_doc", "Cliente Doc"),
        ("cliente_razon_social", "Cliente Razón Social"),
        ("moneda", "Moneda"),
        ("total_venta", "Total"),
        ("total_pen", "Total (PEN)"),
        ("estado", "Estado"),
        ("cdr_descripcion", "Detalle SUNAT"),
    ]
    fname = f"reporte_pendientes_sunat_{_date.today().isoformat()}.xlsx"
    return _xlsx_response(rows, headers, sheet_name="Pendientes SUNAT",
                          filename=fname)
