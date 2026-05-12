"""CRUD de productos / servicios."""
import logging
from datetime import date as _date
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from sqlalchemy import or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..core.database import get_db
from ..core.db_adapter import is_dbf_mode, is_mdb_mode
from ..models.producto import Producto
from ..schemas.producto import (
    ProductoIn, ProductoOut, ProductoUpdate, ProductoListResponse,
)
from ..schemas.common import MessageResponse
from ..services.excel_export import (
    dict_list_to_xlsx_bytes, xlsx_response_headers, XLSX_MEDIA_TYPE,
)
from ._deps import escape_like

logger = logging.getLogger("factura_mdb.api.productos")

router = APIRouter(prefix="/api/productos", tags=["productos"])


@router.get("", response_model=ProductoListResponse)
def listar_productos(
    db: Session = Depends(get_db),
    search: Optional[str] = Query(None),
    q_search: Optional[str] = Query(None, alias="q", description="Alias de search (frontend)"),
    activo: Optional[bool] = Query(None),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
):
    # Aliases del frontend
    search = search or q_search

    if is_dbf_mode():
        from ..core.db_adapter.dbf_repo import ProductoRepoDBF
        items, total = ProductoRepoDBF.listar(
            q=search, activo=activo, limit=limit, offset=offset,
        )
        return ProductoListResponse(
            items=items, total=total, limit=limit, offset=offset,
        )

    if is_mdb_mode():
        from ..core.db_adapter.mdb_repo import ProductoRepoMDB
        items, total = ProductoRepoMDB.listar(
            q=search, activo=activo, limit=limit, offset=offset,
        )
        return ProductoListResponse(
            items=items, total=total, limit=limit, offset=offset,
        )

    q = db.query(Producto)
    # Bug E2: search con solo espacios → no devolver toda la tabla
    search_clean = (search or "").strip()
    if search_clean:
        # Bug E1: escapar wildcards
        safe = escape_like(search_clean)
        like = f"%{safe}%"
        q = q.filter(or_(
            Producto.codigo.like(like, escape="\\"),
            Producto.descripcion.like(like, escape="\\"),
        ))
    elif search is not None and not search_clean:
        return ProductoListResponse(items=[], total=0, limit=limit, offset=offset)
    if activo is not None:
        q = q.filter(Producto.activo == activo)

    total = q.count()
    items = (q.order_by(Producto.descripcion.asc())
              .limit(limit).offset(offset).all())
    return ProductoListResponse(items=items, total=total, limit=limit, offset=offset)


@router.get("/buscar", response_model=list[ProductoOut])
def buscar_productos(
    q: str = Query(..., min_length=1),
    db: Session = Depends(get_db),
):
    """Autocomplete: top 20."""
    # Bug E2: q con solo espacios → vacío
    q_clean = (q or "").strip()
    if not q_clean:
        return []
    if is_mdb_mode():
        from ..core.db_adapter.mdb_repo import ProductoRepoMDB
        return ProductoRepoMDB.buscar(q=q_clean, limit=20)

    # Bug E1: escapar wildcards
    safe = escape_like(q_clean)
    like = f"%{safe}%"
    items = (
        db.query(Producto)
        .filter(Producto.activo == True)  # noqa: E712
        .filter(or_(
            Producto.codigo.like(like, escape="\\"),
            Producto.descripcion.like(like, escape="\\"),
        ))
        .order_by(Producto.descripcion.asc())
        .limit(20)
        .all()
    )
    return items


@router.get("/export.xlsx")
def export_productos_xlsx(
    db: Session = Depends(get_db),
    search: Optional[str] = Query(None),
    q_search: Optional[str] = Query(None, alias="q"),
    activo: Optional[bool] = Query(None),
):
    """Exporta productos a .xlsx con los mismos filtros del listado."""
    search = search or q_search

    _afect_label = {
        "10": "Gravado", "20": "Exonerado",
        "30": "Inafecto", "40": "Exportación",
    }

    if is_mdb_mode():
        from ..core.db_adapter.mdb_repo import ProductoRepoMDB
        items_dict, _ = ProductoRepoMDB.listar(
            q=search, activo=activo, limit=50_000, offset=0,
        )
        rows = [
            {
                "codigo": p.get("codigo") or "",
                "descripcion": p.get("descripcion") or "",
                "unidad_medida": p.get("unidad_medida") or "",
                "valor_unitario": float(p.get("valor_unitario") or 0.0),
                "tipo_igv": _afect_label.get(p.get("tipo_afectacion_igv", ""),
                                              p.get("tipo_afectacion_igv", "")),
                "activo": "Sí" if p.get("activo") else "No",
            }
            for p in items_dict
        ]
        headers = [
            ("codigo", "Código"),
            ("descripcion", "Descripción"),
            ("unidad_medida", "UM"),
            ("valor_unitario", "Valor Unitario"),
            ("tipo_igv", "Tipo IGV"),
            ("activo", "Activo"),
        ]
        data = dict_list_to_xlsx_bytes(rows, headers=headers, sheet_name="Productos")
        fname = f"productos_{_date.today().isoformat()}.xlsx"
        return Response(content=data, media_type=XLSX_MEDIA_TYPE,
                         headers=xlsx_response_headers(fname))

    q = db.query(Producto)
    search_clean = (search or "").strip()
    if search_clean:
        # Bug E1: escapar wildcards
        safe = escape_like(search_clean)
        like = f"%{safe}%"
        q = q.filter(or_(
            Producto.codigo.like(like, escape="\\"),
            Producto.descripcion.like(like, escape="\\"),
        ))
    if activo is not None:
        q = q.filter(Producto.activo == activo)

    items = q.order_by(Producto.descripcion.asc()).limit(50_000).all()

    rows = [
        {
            "codigo": p.codigo,
            "descripcion": p.descripcion,
            "unidad_medida": p.unidad_medida,
            "valor_unitario": float(p.valor_unitario or 0.0),
            "tipo_igv": _afect_label.get(p.tipo_afectacion_igv,
                                          p.tipo_afectacion_igv),
            "activo": "Sí" if p.activo else "No",
        }
        for p in items
    ]
    headers = [
        ("codigo", "Código"),
        ("descripcion", "Descripción"),
        ("unidad_medida", "UM"),
        ("valor_unitario", "Valor Unitario"),
        ("tipo_igv", "Tipo IGV"),
        ("activo", "Activo"),
    ]
    data = dict_list_to_xlsx_bytes(rows, headers=headers, sheet_name="Productos")
    fname = f"productos_{_date.today().isoformat()}.xlsx"
    return Response(content=data, media_type=XLSX_MEDIA_TYPE,
                     headers=xlsx_response_headers(fname))


@router.get("/{producto_id}", response_model=ProductoOut)
def obtener_producto(producto_id: int, db: Session = Depends(get_db)):
    if is_mdb_mode():
        from ..core.db_adapter.mdb_repo import ProductoRepoMDB
        d = ProductoRepoMDB.obtener(producto_id)
        if d is None:
            raise HTTPException(status_code=404, detail="Producto no encontrado")
        return d
    p = db.get(Producto, producto_id)
    if p is None:
        raise HTTPException(status_code=404, detail="Producto no encontrado")
    return p


@router.post("", response_model=ProductoOut, status_code=201)
def crear_producto(payload: ProductoIn, db: Session = Depends(get_db)):
    if is_mdb_mode():
        from ..core.db_adapter.mdb_writer import ProductoWriterMDB
        from ..core.db_adapter.mdb_lock import MDBLockTimeout
        from ..core.db_adapter.mdb_repo import ProductoRepoMDB
        # Verificar que no exista
        existing = ProductoRepoMDB.obtener_por_codigo(payload.codigo)
        if existing is not None and existing.get("activo"):
            raise HTTPException(
                status_code=409,
                detail=f"Ya existe un producto con código {payload.codigo}",
            )
        try:
            codigo = ProductoWriterMDB.crear(payload.model_dump())
        except MDBLockTimeout as e:
            raise HTTPException(status_code=503, detail=str(e))
        except Exception as e:  # noqa: BLE001
            logger.exception("Error creando producto en MDB")
            raise HTTPException(status_code=500,
                                 detail=f"Error escribiendo al MDB: {e}")
        prod = ProductoRepoMDB.obtener_por_codigo(codigo)
        if prod is None:
            raise HTTPException(status_code=500,
                                 detail="Producto creado pero no se puede releer del MDB")
        return prod

    p = Producto(**payload.model_dump())
    db.add(p)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail=f"Ya existe un producto con código {payload.codigo}")
    db.refresh(p)
    return p


@router.put("/{producto_id}", response_model=ProductoOut)
def actualizar_producto(producto_id: int, payload: ProductoUpdate, db: Session = Depends(get_db)):
    if is_mdb_mode():
        from ..core.db_adapter.mdb_writer import ProductoWriterMDB
        from ..core.db_adapter.mdb_lock import MDBLockTimeout
        from ..core.db_adapter.mdb_repo import ProductoRepoMDB
        existing = ProductoRepoMDB.obtener(producto_id)
        if existing is None:
            raise HTTPException(status_code=404, detail="Producto no encontrado")
        codigo = existing["codigo"]
        try:
            ProductoWriterMDB.actualizar(codigo, payload.model_dump(exclude_unset=True))
        except MDBLockTimeout as e:
            raise HTTPException(status_code=503, detail=str(e))
        except Exception as e:  # noqa: BLE001
            logger.exception("Error actualizando producto en MDB")
            raise HTTPException(status_code=500,
                                 detail=f"Error escribiendo al MDB: {e}")
        prod = ProductoRepoMDB.obtener_por_codigo(codigo)
        if prod is None:
            raise HTTPException(status_code=500,
                                 detail="Producto actualizado pero no se puede releer")
        return prod

    p = db.get(Producto, producto_id)
    if p is None:
        raise HTTPException(status_code=404, detail="Producto no encontrado")
    data = payload.model_dump(exclude_unset=True)
    for k, v in data.items():
        if hasattr(p, k):
            setattr(p, k, v)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="Código ya registrado en otro producto")
    db.refresh(p)
    return p


@router.delete("/{producto_id}", response_model=MessageResponse)
def eliminar_producto(producto_id: int, db: Session = Depends(get_db)):
    if is_mdb_mode():
        from ..core.db_adapter.mdb_writer import ProductoWriterMDB
        from ..core.db_adapter.mdb_lock import MDBLockTimeout
        from ..core.db_adapter.mdb_repo import ProductoRepoMDB
        existing = ProductoRepoMDB.obtener(producto_id)
        if existing is None:
            raise HTTPException(status_code=404, detail="Producto no encontrado")
        try:
            ProductoWriterMDB.borrar(existing["codigo"])
        except MDBLockTimeout as e:
            raise HTTPException(status_code=503, detail=str(e))
        except Exception as e:  # noqa: BLE001
            logger.exception("Error desactivando producto en MDB")
            raise HTTPException(status_code=500,
                                 detail=f"Error escribiendo al MDB: {e}")
        return MessageResponse(ok=True, mensaje="Producto desactivado")

    p = db.get(Producto, producto_id)
    if p is None:
        raise HTTPException(status_code=404, detail="Producto no encontrado")
    p.activo = False
    db.commit()
    return MessageResponse(ok=True, mensaje="Producto desactivado")
