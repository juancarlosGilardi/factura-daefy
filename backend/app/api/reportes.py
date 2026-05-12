"""Endpoints de reportes para el dashboard.

Incluye reportes de ventas mes/dia, top clientes/productos, dashboard
y exports a Excel (incluye reporte de ventas del día imprimible A4).
"""
import logging
from datetime import date as _date, datetime, timedelta
from typing import Optional, List, Dict, Any
from fastapi import APIRouter, Depends, Query
from fastapi.responses import Response
from sqlalchemy import func, and_, case
from sqlalchemy.orm import Session

from ..core.config import settings
from ..core.database import get_db
from ..core.db_adapter import is_dbf_mode
from ..models.comprobante import Comprobante
from ..models.cliente import Cliente
from ..models.producto import Producto
from ..services.excel_export import (
    dict_list_to_xlsx_bytes, xlsx_response_headers, XLSX_MEDIA_TYPE,
)
from ..services.excel_reportes import build_ventas_dia_xlsx

logger = logging.getLogger("factura_mdb.api.reportes")

router = APIRouter(prefix="/api/reportes", tags=["reportes"])


# ---------------------------------------------------------------------------
# Helpers DBF — agregan sobre ventas.dbf cuando el modo activo es DBF.
# Evitan crashes porque la sesión SQLAlchemy en este proyecto es un stub.
# ---------------------------------------------------------------------------

def _dbf_ventas_periodo(desde: _date, hasta: _date) -> dict:
    """Calcula ventas en PEN agregadas por día para el período [desde, hasta].

    Excluye comprobantes anulados (REGISTRO_A/DATA_BAJA) y filtra a los
    tipos 01 (factura) y 03 (boleta).
    """
    from ..core.db_adapter.dbf_repo import _dbf  # type: ignore

    dias_acum: dict[str, float] = {}
    cantidad = 0
    aceptados = 0
    pendientes = 0
    total = 0.0

    for row in _dbf("ventas.dbf"):
        # Tipo de documento — los DBF de GECOPE usan TIPO_DOCUM con códigos
        # SUNAT ("01", "03", etc.). Si no está, lo deducimos por la serie.
        tipo = (row.get("TIPO_DOCUM") or "").strip()
        if not tipo:
            ser = (row.get("SER_DOCUME") or "").strip().upper()
            if ser.startswith("F"):
                tipo = "01"
            elif ser.startswith("B"):
                tipo = "03"
        if tipo not in ("01", "03"):
            continue

        fe = row.get("FECHA_EMIS")
        if fe is None:
            continue
        try:
            fe_str = fe.isoformat() if hasattr(fe, "isoformat") else str(fe)[:10]
        except Exception:  # noqa: BLE001
            continue
        if fe_str < desde.isoformat() or fe_str > hasta.isoformat():
            continue

        anulado = bool(row.get("REGISTRO_A")) or bool(row.get("DATA_BAJA"))

        try:
            monto = float(row.get("TOTAL") or row.get("IMPORTE_TO") or 0.0)
        except (TypeError, ValueError):
            monto = 0.0

        if not anulado:
            dias_acum[fe_str] = dias_acum.get(fe_str, 0.0) + monto
            total += monto
            cantidad += 1
            estado = (row.get("ESTADO_SUN") or row.get("ESTADO") or "").strip().upper()
            if estado == "A":
                aceptados += 1
            elif estado in ("P", "R", ""):
                pendientes += 1

    dias = [{"fecha": d, "total": round(v, 2)} for d, v in sorted(dias_acum.items())]
    return {
        "desde": desde.isoformat(),
        "hasta": hasta.isoformat(),
        "total": round(total, 2),
        "cantidad": cantidad,
        "aceptados": aceptados,
        "pendientes": pendientes,
        "dias": dias,
    }


def _dbf_resumen_dashboard() -> dict:
    """Versión DBF del /resumen-dashboard."""
    hoy = _date.today()
    inicio_mes = _date(hoy.year, hoy.month, 1)

    hoy_data = _dbf_ventas_periodo(hoy, hoy)
    mes_data = _dbf_ventas_periodo(inicio_mes, hoy)

    # Conteos absolutos de catálogo (clientes/productos) y pendientes SUNAT.
    try:
        from ..core.db_adapter.dbf_repo import (  # type: ignore
            ClienteRepoDBF, ProductoRepoDBF,
        )
        total_clientes = len(ClienteRepoDBF._iter_all())
        total_productos = len(ProductoRepoDBF._cargar_todos())
    except Exception as exc:  # noqa: BLE001
        logger.warning("conteo catálogo DBF falló: %s", exc)
        total_clientes = 0
        total_productos = 0

    return {
        "fecha": hoy.isoformat(),
        "ventas_hoy": {
            "total_pen": hoy_data["total"],
            "cantidad": hoy_data["cantidad"],
        },
        "ventas_mes": {
            "total_pen": mes_data["total"],
            "cantidad": mes_data["cantidad"],
        },
        "pendientes_sunat": mes_data["pendientes"],
        "total_clientes": total_clientes,
        "total_productos": total_productos,
    }


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

    if is_dbf_mode():
        return _dbf_ventas_periodo(desde, hasta)

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
    if is_dbf_mode():
        try:
            from ..core.db_adapter.dbf_repo import ComprobanteRepoDBF
            items = ComprobanteRepoDBF.top_productos(
                desde=desde, hasta=hasta, limit=limit,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("top_productos DBF falló: %s", exc)
            items = []
        return {"items": items}

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
    if is_dbf_mode():
        # No tenemos un agregado por cliente en el repo DBF — devolvemos vacío
        # para que el dashboard renderice "sin datos" en vez de crashear.
        return {"items": []}
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
    if is_dbf_mode():
        return _dbf_resumen_dashboard()

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


# =====================================================================
# Reporte de ventas del día — JSON + XLSX A4
# =====================================================================

_TIPO_LABEL = {
    "01": "Factura",
    "03": "Boleta",
    "07": "N. Crédito",
    "08": "N. Débito",
}

_ESTADO_LABEL = {
    "A": "Aceptado",
    "P": "Pendiente",
    "R": "Rechazado",
    "B": "Anulado",
    "": "Pendiente",
}


def _fmt_hora(v: Any) -> str:
    """Devuelve HH:MM si v es datetime; cadena vacía en caso contrario."""
    if v is None:
        return ""
    if isinstance(v, datetime):
        return v.strftime("%H:%M")
    return ""


def _ventas_dia_dbf(fecha: _date) -> tuple[list[dict], dict]:
    """Lee ventas.dbf y devuelve (items_ordenados, resumen) para la fecha dada.

    - Filtra tipo 01/03 (factura/boleta) por defecto, pero también permite
      07/08 si están presentes (NC/ND).
    - Ordena por (serie ASC, correlativo DESC) — todos del mismo día.
    - Excluye anulados (REGISTRO_A/DATA_BAJA) del resumen, pero los marca
      como tales en la tabla.
    """
    from ..core.db_adapter.dbf_repo import _dbf  # type: ignore
    from ..services.dbf_importer import mappers as dbf_mappers

    items: list[dict] = []
    cant_facturas = 0
    cant_boletas = 0
    total_facturas = 0.0
    total_boletas = 0.0
    total_igv = 0.0

    fecha_iso = fecha.isoformat()

    for row in _dbf("ventas.dbf"):
        d = dbf_mappers.dbf_comprobante_to_dict(row)
        if not d:
            continue
        fe = d.get("fecha_emision")
        fe_iso = fe.isoformat() if hasattr(fe, "isoformat") else str(fe)[:10]
        if fe_iso != fecha_iso:
            continue

        tipo = d.get("tipo_documento") or ""
        if tipo not in ("01", "03", "07", "08"):
            continue

        estado_raw = d.get("estado") or ""
        anulado = estado_raw == "B"

        # Hora de emisión: GECOPE no la guarda, pero USER_FECHA suele ser
        # un datetime cercano al momento real de creación.
        hora = _fmt_hora(row.get("USER_FECHA"))

        moneda = d.get("moneda") or "PEN"
        subtotal = float(d.get("subtotal") or 0.0)
        igv = float(d.get("total_igv") or 0.0)
        total = float(d.get("total_venta") or 0.0)
        total_pen = float(d.get("total_pen") or total)

        items.append({
            "tipo_doc": _TIPO_LABEL.get(tipo, tipo),
            "tipo_doc_codigo": tipo,
            "serie_numero": d.get("numero_completo") or "",
            "serie": d.get("serie") or "",
            "correlativo": int(d.get("correlativo") or 0),
            "hora": hora,
            "cliente": d.get("cliente_razon_social") or "",
            "ruc_dni": d.get("cliente_numero_doc") or "",
            "moneda": moneda,
            "subtotal": round(subtotal, 2),
            "igv": round(igv, 2),
            "total": round(total, 2),
            "total_pen": round(total_pen, 2),
            "estado": _ESTADO_LABEL.get(estado_raw, estado_raw or "Pendiente"),
            "anulado": anulado,
        })

        if anulado:
            continue

        if tipo == "01":
            cant_facturas += 1
            total_facturas += total_pen
        elif tipo == "03":
            cant_boletas += 1
            total_boletas += total_pen
        # NC/ND no entran al resumen "ventas".

        total_igv += igv  # IGV en moneda original (PEN si moneda PEN).

    # Orden: serie ASC, correlativo DESC.
    items.sort(key=lambda it: (it["serie"], -it["correlativo"]))

    resumen = {
        "cantidad_facturas": cant_facturas,
        "cantidad_boletas": cant_boletas,
        "total_facturas_pen": round(total_facturas, 2),
        "total_boletas_pen": round(total_boletas, 2),
        "total_general_pen": round(total_facturas + total_boletas, 2),
        "total_igv_pen": round(total_igv, 2),
    }
    return items, resumen


def _ventas_dia_sql(fecha: _date, db: Session) -> tuple[list[dict], dict]:
    """Lee comprobantes desde SQL y devuelve (items, resumen) para la fecha."""
    rows = (
        db.query(Comprobante)
        .filter(Comprobante.fecha_emision == fecha)
        .filter(Comprobante.tipo_documento.in_(["01", "03", "07", "08"]))
        .order_by(Comprobante.serie.asc(), Comprobante.correlativo.desc())
        .all()
    )

    items: list[dict] = []
    cant_facturas = 0
    cant_boletas = 0
    total_facturas = 0.0
    total_boletas = 0.0
    total_igv = 0.0

    for c in rows:
        anulado = (c.estado == "B")
        tipo = c.tipo_documento or ""
        total_pen = float(c.total_pen or c.total_venta or 0.0)
        total_v = float(c.total_venta or 0.0)
        igv = float(c.total_igv or 0.0)
        subtotal = float(getattr(c, "subtotal", None) or getattr(c, "total_gravado", None) or 0.0)

        hora = _fmt_hora(getattr(c, "hora_emision", None)) or _fmt_hora(getattr(c, "created_at", None))

        items.append({
            "tipo_doc": _TIPO_LABEL.get(tipo, tipo),
            "tipo_doc_codigo": tipo,
            "serie_numero": c.numero_completo,
            "serie": c.serie,
            "correlativo": int(c.correlativo or 0),
            "hora": hora,
            "cliente": c.cliente_razon_social or "",
            "ruc_dni": c.cliente_numero_doc or "",
            "moneda": c.moneda or "PEN",
            "subtotal": round(subtotal, 2),
            "igv": round(igv, 2),
            "total": round(total_v, 2),
            "total_pen": round(total_pen, 2),
            "estado": _ESTADO_LABEL.get(c.estado, c.estado or "Pendiente"),
            "anulado": anulado,
        })

        if anulado:
            continue
        if tipo == "01":
            cant_facturas += 1
            total_facturas += total_pen
        elif tipo == "03":
            cant_boletas += 1
            total_boletas += total_pen
        total_igv += igv

    resumen = {
        "cantidad_facturas": cant_facturas,
        "cantidad_boletas": cant_boletas,
        "total_facturas_pen": round(total_facturas, 2),
        "total_boletas_pen": round(total_boletas, 2),
        "total_general_pen": round(total_facturas + total_boletas, 2),
        "total_igv_pen": round(total_igv, 2),
    }
    return items, resumen


@router.get("/ventas-dia")
def ventas_dia(
    fecha: Optional[_date] = Query(None, description="Fecha del reporte (YYYY-MM-DD)"),
    db: Session = Depends(get_db),
):
    """Reporte de ventas del día (JSON).

    Si `fecha` no se envía, usa la fecha actual del servidor.
    Devuelve `{fecha, resumen, items}` con todos los comprobantes 01/03/07/08
    emitidos ese día, ordenados por serie ASC y correlativo DESC.
    """
    f = fecha or _date.today()

    if is_dbf_mode():
        items, resumen = _ventas_dia_dbf(f)
    else:
        items, resumen = _ventas_dia_sql(f, db)

    return {
        "fecha": f.isoformat(),
        "resumen": resumen,
        "items": items,
    }


@router.get("/ventas-dia.xlsx")
def export_ventas_dia(
    fecha: Optional[_date] = Query(None),
    db: Session = Depends(get_db),
):
    """Reporte de ventas del día en .xlsx formateado para impresión A4.

    Hoja vertical, márgenes 1cm, headers de tabla repetidos en cada
    página, y bloque de resumen al final.
    """
    f = fecha or _date.today()

    if is_dbf_mode():
        items, resumen = _ventas_dia_dbf(f)
    else:
        items, resumen = _ventas_dia_sql(f, db)

    empresa = settings.EMPRESA or {}
    empresa_nombre = empresa.get("razon_social") or empresa.get("nombre_comercial") or "EMPRESA"
    empresa_ruc = empresa.get("ruc") or settings.RUC or ""

    # Logo: resolver path absoluto si está configurado
    logo_path: Optional[str] = None
    raw_logo = empresa.get("logo_path")
    if raw_logo:
        from pathlib import Path as _P
        p = _P(raw_logo)
        if not p.is_absolute():
            p = _P(getattr(settings, "PROJECT_ROOT", ".")) / raw_logo
        if p.exists():
            logo_path = str(p)

    data = build_ventas_dia_xlsx(
        fecha=f,
        items=items,
        resumen=resumen,
        empresa_nombre=empresa_nombre,
        empresa_ruc=empresa_ruc,
        logo_path=logo_path,
    )

    fname = f"reporte_ventas_dia_{f.isoformat()}.xlsx"
    return Response(
        content=data,
        media_type=XLSX_MEDIA_TYPE,
        headers=xlsx_response_headers(fname),
    )
