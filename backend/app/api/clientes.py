"""CRUD de clientes."""
import logging
from datetime import date as _date
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from sqlalchemy import or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..core.database import get_db
from ..core.db_adapter import is_mdb_mode
from ..models.cliente import Cliente
from ..schemas.cliente import (
    ClienteIn, ClienteOut, ClienteUpdate, ClienteListResponse,
)
from ..schemas.common import MessageResponse
from ..services.excel_export import (
    dict_list_to_xlsx_bytes, xlsx_response_headers, XLSX_MEDIA_TYPE,
)
from ._deps import escape_like

logger = logging.getLogger("factura_mdb.api.clientes")

router = APIRouter(prefix="/api/clientes", tags=["clientes"])


@router.get("", response_model=ClienteListResponse)
def listar_clientes(
    db: Session = Depends(get_db),
    search: Optional[str] = Query(None, description="Búsqueda por doc/razón social"),
    q_search: Optional[str] = Query(None, alias="q", description="Alias de search (frontend)"),
    tipo_doc: Optional[str] = Query(None),
    tipo_documento: Optional[str] = Query(None, description="Alias de tipo_doc (frontend)"),
    activo: Optional[bool] = Query(None),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
):
    # Aliases del frontend
    search = search or q_search
    tipo_doc = tipo_doc or tipo_documento

    # Modo MDB lab: leer directo del .mdb del cliente
    if is_mdb_mode():
        from ..core.db_adapter.mdb_repo import ClienteRepoMDB
        items, total = ClienteRepoMDB.listar(
            q=search, tipo_documento=tipo_doc, activo=activo,
            limit=limit, offset=offset,
        )
        return ClienteListResponse(
            items=items, total=total, limit=limit, offset=offset,
        )

    q = db.query(Cliente)
    # Bug E2: si search trae solo espacios, no debemos devolver toda la tabla
    search_clean = (search or "").strip()
    if search_clean:
        # Bug E1: escapar wildcards LIKE
        safe = escape_like(search_clean)
        like = f"%{safe}%"
        q = q.filter(or_(
            Cliente.numero_documento.like(like, escape="\\"),
            Cliente.razon_social.like(like, escape="\\"),
        ))
    elif search is not None and not search_clean:
        # search vino con solo espacios: tratar como filtro vacío explícito
        return ClienteListResponse(items=[], total=0, limit=limit, offset=offset)
    if tipo_doc:
        q = q.filter(Cliente.tipo_documento == tipo_doc)
    if activo is not None:
        q = q.filter(Cliente.activo == activo)

    total = q.count()
    items = (q.order_by(Cliente.razon_social.asc())
              .limit(limit).offset(offset).all())
    return ClienteListResponse(items=items, total=total, limit=limit, offset=offset)


@router.get("/buscar", response_model=list[ClienteOut])
def buscar_clientes(
    q: str = Query(..., min_length=1, description="Texto a buscar"),
    db: Session = Depends(get_db),
):
    """Autocomplete: top 20 resultados por número doc o razón social."""
    # Bug E2: q con solo espacios → no devolver toda la tabla
    q_clean = (q or "").strip()
    if not q_clean:
        return []

    if is_mdb_mode():
        from ..core.db_adapter.mdb_repo import ClienteRepoMDB
        return ClienteRepoMDB.buscar(q=q_clean, limit=20)

    # Bug E1: escapar wildcards LIKE
    safe = escape_like(q_clean)
    like = f"%{safe}%"
    items = (
        db.query(Cliente)
        .filter(Cliente.activo == True)  # noqa: E712
        .filter(or_(
            Cliente.numero_documento.like(like, escape="\\"),
            Cliente.razon_social.like(like, escape="\\"),
        ))
        .order_by(Cliente.razon_social.asc())
        .limit(20)
        .all()
    )
    return items


@router.get("/export.xlsx")
def export_clientes_xlsx(
    db: Session = Depends(get_db),
    search: Optional[str] = Query(None),
    q_search: Optional[str] = Query(None, alias="q"),
    tipo_doc: Optional[str] = Query(None),
    tipo_documento: Optional[str] = Query(None),
    activo: Optional[bool] = Query(None),
):
    """Exporta clientes a .xlsx con los mismos filtros del listado."""
    search = search or q_search
    tipo_doc = tipo_doc or tipo_documento

    _doc_label = {"1": "DNI", "6": "RUC", "4": "CE", "7": "PAS", "0": "S/D"}

    if is_mdb_mode():
        from ..core.db_adapter.mdb_repo import ClienteRepoMDB
        items_dict, _ = ClienteRepoMDB.listar(
            q=search, tipo_documento=tipo_doc, activo=activo,
            limit=50_000, offset=0,
        )
        rows = [
            {
                "tipo_doc": _doc_label.get(c.get("tipo_documento", ""),
                                            c.get("tipo_documento", "")),
                "numero_documento": c.get("numero_documento") or "",
                "razon_social": c.get("razon_social") or "",
                "direccion": c.get("direccion") or "",
                "distrito": c.get("distrito") or "",
                "email": c.get("email") or "",
                "telefono": c.get("telefono") or "",
                "activo": "Sí" if c.get("activo") else "No",
            }
            for c in items_dict
        ]
        headers = [
            ("tipo_doc", "Tipo Doc"),
            ("numero_documento", "Número"),
            ("razon_social", "Razón Social"),
            ("direccion", "Dirección"),
            ("distrito", "Distrito"),
            ("email", "Email"),
            ("telefono", "Teléfono"),
            ("activo", "Activo"),
        ]
        data = dict_list_to_xlsx_bytes(rows, headers=headers, sheet_name="Clientes")
        fname = f"clientes_{_date.today().isoformat()}.xlsx"
        return Response(content=data, media_type=XLSX_MEDIA_TYPE,
                         headers=xlsx_response_headers(fname))

    q = db.query(Cliente)
    search_clean = (search or "").strip()
    if search_clean:
        # Bug E1: escapar wildcards LIKE
        safe = escape_like(search_clean)
        like = f"%{safe}%"
        q = q.filter(or_(
            Cliente.numero_documento.like(like, escape="\\"),
            Cliente.razon_social.like(like, escape="\\"),
        ))
    if tipo_doc:
        q = q.filter(Cliente.tipo_documento == tipo_doc)
    if activo is not None:
        q = q.filter(Cliente.activo == activo)

    items = q.order_by(Cliente.razon_social.asc()).limit(50_000).all()

    rows = [
        {
            "tipo_doc": _doc_label.get(c.tipo_documento, c.tipo_documento),
            "numero_documento": c.numero_documento,
            "razon_social": c.razon_social,
            "direccion": c.direccion or "",
            "distrito": c.distrito or "",
            "email": c.email or "",
            "telefono": c.telefono or "",
            "activo": "Sí" if c.activo else "No",
        }
        for c in items
    ]
    headers = [
        ("tipo_doc", "Tipo Doc"),
        ("numero_documento", "Número"),
        ("razon_social", "Razón Social"),
        ("direccion", "Dirección"),
        ("distrito", "Distrito"),
        ("email", "Email"),
        ("telefono", "Teléfono"),
        ("activo", "Activo"),
    ]
    data = dict_list_to_xlsx_bytes(rows, headers=headers, sheet_name="Clientes")
    fname = f"clientes_{_date.today().isoformat()}.xlsx"
    return Response(content=data, media_type=XLSX_MEDIA_TYPE,
                     headers=xlsx_response_headers(fname))


@router.get("/{cliente_id}", response_model=ClienteOut)
def obtener_cliente(cliente_id: int, db: Session = Depends(get_db)):
    if is_mdb_mode():
        from ..core.db_adapter.mdb_repo import ClienteRepoMDB
        d = ClienteRepoMDB.obtener(cliente_id)
        if d is None:
            raise HTTPException(status_code=404, detail="Cliente no encontrado")
        return d
    cliente = db.get(Cliente, cliente_id)
    if cliente is None:
        raise HTTPException(status_code=404, detail="Cliente no encontrado")
    return cliente


@router.post("", response_model=ClienteOut, status_code=201)
def crear_cliente(payload: ClienteIn, db: Session = Depends(get_db)):
    if is_mdb_mode():
        from ..core.db_adapter.mdb_writer import ClienteWriterMDB
        from ..core.db_adapter.mdb_lock import MDBLockTimeout
        from ..core.db_adapter.mdb_repo import ClienteRepoMDB
        try:
            new_id = ClienteWriterMDB.crear(payload.model_dump())
        except MDBLockTimeout as e:
            raise HTTPException(status_code=503, detail=str(e))
        except Exception as e:  # noqa: BLE001
            logger.exception("Error creando cliente en MDB")
            raise HTTPException(status_code=500,
                                 detail=f"Error escribiendo al MDB: {e}")
        cliente_dict = ClienteRepoMDB.obtener(new_id)
        if cliente_dict is None:
            # No debería pasar — INSERT exitoso pero lookup falla
            raise HTTPException(status_code=500,
                                 detail="Cliente creado pero no se puede releer del MDB")
        return cliente_dict

    cliente = Cliente(**payload.model_dump())
    db.add(cliente)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status_code=409,
            detail=f"Ya existe un cliente con tipo_doc={payload.tipo_documento} numero_doc={payload.numero_documento}",
        )
    db.refresh(cliente)
    return cliente


@router.put("/{cliente_id}", response_model=ClienteOut)
def actualizar_cliente(cliente_id: int, payload: ClienteUpdate, db: Session = Depends(get_db)):
    if is_mdb_mode():
        from ..core.db_adapter.mdb_writer import ClienteWriterMDB
        from ..core.db_adapter.mdb_lock import MDBLockTimeout
        from ..core.db_adapter.mdb_repo import ClienteRepoMDB
        # Verificar que existe primero
        existing = ClienteRepoMDB.obtener(cliente_id)
        if existing is None:
            raise HTTPException(status_code=404, detail="Cliente no encontrado")
        data = payload.model_dump(exclude_unset=True)
        try:
            ClienteWriterMDB.actualizar(cliente_id, data)
        except MDBLockTimeout as e:
            raise HTTPException(status_code=503, detail=str(e))
        except Exception as e:  # noqa: BLE001
            logger.exception("Error actualizando cliente en MDB")
            raise HTTPException(status_code=500,
                                 detail=f"Error escribiendo al MDB: {e}")
        cliente_dict = ClienteRepoMDB.obtener(cliente_id)
        if cliente_dict is None:
            raise HTTPException(status_code=500,
                                 detail="Cliente actualizado pero no se puede releer")
        return cliente_dict

    cliente = db.get(Cliente, cliente_id)
    if cliente is None:
        raise HTTPException(status_code=404, detail="Cliente no encontrado")
    data = payload.model_dump(exclude_unset=True)
    for k, v in data.items():
        if hasattr(cliente, k):
            setattr(cliente, k, v)
    # Bug E3: revalidar la combinación tipo+numero post-merge para que un PUT
    # parcial no deje el cliente en estado inconsistente
    from ..schemas.common import validar_documento_identidad
    try:
        validar_documento_identidad(cliente.tipo_documento, cliente.numero_documento)
    except ValueError as e:
        db.rollback()
        raise HTTPException(status_code=422, detail=str(e))
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="Documento ya registrado en otro cliente")
    db.refresh(cliente)
    return cliente


@router.delete("/{cliente_id}", response_model=MessageResponse)
def eliminar_cliente(cliente_id: int, db: Session = Depends(get_db)):
    """Soft-delete: marca activo=False."""
    if is_mdb_mode():
        from ..core.db_adapter.mdb_writer import ClienteWriterMDB
        from ..core.db_adapter.mdb_lock import MDBLockTimeout
        from ..core.db_adapter.mdb_repo import ClienteRepoMDB
        if ClienteRepoMDB.obtener(cliente_id) is None:
            raise HTTPException(status_code=404, detail="Cliente no encontrado")
        try:
            ClienteWriterMDB.borrar(cliente_id)
        except MDBLockTimeout as e:
            raise HTTPException(status_code=503, detail=str(e))
        except Exception as e:  # noqa: BLE001
            logger.exception("Error desactivando cliente en MDB")
            raise HTTPException(status_code=500,
                                 detail=f"Error escribiendo al MDB: {e}")
        return MessageResponse(ok=True, mensaje="Cliente desactivado")

    cliente = db.get(Cliente, cliente_id)
    if cliente is None:
        raise HTTPException(status_code=404, detail="Cliente no encontrado")
    cliente.activo = False
    db.commit()
    return MessageResponse(ok=True, mensaje="Cliente desactivado")
