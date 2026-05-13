"""Endpoints de Cotizaciones y Notas de Pedido (NO SUNAT).

Estos documentos son INTERNOS — no se firman, no se envían a SUNAT, NO se
escriben en ventas.dbf. Se guardan como metadata simple en
`data/pedidos.json` (un solo archivo para cotizaciones y pedidos, con
campo `tipo_pedido = 'COTIZACION' | 'PEDIDO'`) y los PDFs en
`storage/pedidos/`.

Frontend ya espera:
    GET    /api/pedidos/                 -> lista
    POST   /api/pedidos                  -> crea
    GET    /api/pedidos/{id}             -> detalle
    PUT    /api/pedidos/{id}             -> actualiza
    PATCH  /api/pedidos/{id}/estado      -> cambia estado
    POST   /api/pedidos/{id}/facturar    -> convierte en comprobante
    DELETE /api/pedidos/{id}             -> elimina
    GET    /api/pedidos/{id}/pdf         -> descarga PDF

Persistencia: archivo JSON con lock simple (escritura atómica via .tmp).
"""
from __future__ import annotations

import json
import logging
import threading
import uuid
from datetime import date as _date, datetime
from pathlib import Path
from typing import Optional, Any

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import Response
from pydantic import BaseModel, Field

from ..core.config import settings, storage_dir

logger = logging.getLogger("factura_mdb.api.pedidos")

router = APIRouter(prefix="/api/pedidos", tags=["pedidos"])

# ---------------------------------------------------------------------------
# Storage
# ---------------------------------------------------------------------------
_DATA_DIR = settings.PROJECT_ROOT / "data"
_DATA_DIR.mkdir(parents=True, exist_ok=True)
_PEDIDOS_JSON = _DATA_DIR / "pedidos.json"
_PDF_DIR = storage_dir() / "pedidos"
_PDF_DIR.mkdir(parents=True, exist_ok=True)
_LOCK = threading.RLock()


def _load_all() -> list[dict]:
    if not _PEDIDOS_JSON.exists():
        return []
    try:
        raw = _PEDIDOS_JSON.read_text(encoding="utf-8")
        if not raw.strip():
            return []
        data = json.loads(raw)
        return data if isinstance(data, list) else []
    except Exception as exc:  # noqa: BLE001
        logger.warning("pedidos.json corrupto: %s", exc)
        return []


def _save_all(items: list[dict]) -> None:
    tmp = _PEDIDOS_JSON.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(items, ensure_ascii=False, indent=2,
                                default=_json_default),
                    encoding="utf-8")
    tmp.replace(_PEDIDOS_JSON)


def _json_default(o: Any) -> Any:
    if isinstance(o, (datetime, _date)):
        return o.isoformat()
    raise TypeError(f"Not serializable: {type(o)}")


def _next_correlativo(tipo_pedido: str, items: list[dict]) -> int:
    prefix = "PED" if tipo_pedido == "PEDIDO" else "COT"
    nums = []
    for it in items:
        if (it.get("tipo_pedido") or "COTIZACION") != tipo_pedido:
            continue
        nc = it.get("numero_completo") or ""
        if nc.startswith(prefix + "-"):
            try:
                nums.append(int(nc.split("-")[1]))
            except (ValueError, IndexError):
                pass
    return (max(nums) if nums else 0) + 1


def _serie(tipo_pedido: str) -> str:
    return "PED" if tipo_pedido == "PEDIDO" else "COT"


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------
class DetalleIn(BaseModel):
    producto_id: Optional[int] = None
    codigo: Optional[str] = ""
    descripcion: str
    unidad_medida: Optional[str] = "NIU"
    cantidad: float = 1.0
    precio_unitario: float = 0.0
    descuento_pct: float = 0.0
    tipo_afectacion: Optional[str] = "10"
    tiene_isc: bool = False
    isc_tasa: float = 0.0
    tiene_icbper: bool = False
    es_gratuito: bool = False
    peso_kg: float = 0.0


class PedidoIn(BaseModel):
    tipo_pedido: str = "COTIZACION"  # COTIZACION | PEDIDO
    fecha_emision: Optional[str] = None
    moneda: str = "PEN"
    tipo_operacion: str = "0101"
    cliente_id: Optional[int] = None
    descuento_global_pct: float = 0.0
    validez_dias: int = 15
    condicion_pago: Optional[str] = None
    vendedor: Optional[str] = None
    observaciones: Optional[str] = None
    detalles: list[DetalleIn] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Cálculo de totales (mínimo, sin reusar comprobante_calc para no acoplar)
# ---------------------------------------------------------------------------
GRAVADAS = {"10", "11", "12", "13", "14", "15", "16", "17"}
EXONERADAS = {"20", "21"}
INAFECTAS = {"30", "31", "32", "33", "34", "35", "36"}
EXPORTACION = {"40"}
IGV_DEFAULT = 18.0


def _calcular(payload: PedidoIn) -> dict:
    """Calcula totales y devuelve líneas + agregados."""
    lineas: list[dict] = []
    total_gravado = 0.0
    total_exonerado = 0.0
    total_inafecto = 0.0
    total_exportacion = 0.0
    total_descuento = 0.0
    total_igv = 0.0
    total_isc = 0.0

    desc_global_pct = float(payload.descuento_global_pct or 0.0)

    for idx, d in enumerate(payload.detalles, start=1):
        afect = d.tipo_afectacion or "10"
        cant = float(d.cantidad or 0)
        pu = float(d.precio_unitario or 0)
        desc_pct = float(d.descuento_pct or 0)

        # Si la afectación es gravada, el precio unitario incluye IGV → derivar valor unit.
        if afect in GRAVADAS:
            valor_unit = pu / (1 + IGV_DEFAULT / 100.0)
        else:
            valor_unit = pu

        bruto = cant * valor_unit
        desc_monto = bruto * (desc_pct / 100.0) if desc_pct else 0.0
        valor_venta = max(0.0, bruto - desc_monto)
        # descuento global aplicado sobre el valor de venta
        if desc_global_pct:
            valor_venta = valor_venta * (1 - desc_global_pct / 100.0)

        igv_monto = valor_venta * (IGV_DEFAULT / 100.0) if afect in GRAVADAS else 0.0
        isc_pct = float(d.isc_tasa or 0) if d.tiene_isc else 0.0
        isc_monto = valor_venta * (isc_pct / 100.0) if isc_pct else 0.0

        total_descuento += desc_monto
        total_igv += igv_monto
        total_isc += isc_monto
        if afect in GRAVADAS:
            total_gravado += valor_venta
        elif afect in EXONERADAS:
            total_exonerado += valor_venta
        elif afect in INAFECTAS:
            total_inafecto += valor_venta
        elif afect in EXPORTACION:
            total_exportacion += valor_venta

        lineas.append({
            "orden": idx,
            "producto_id": d.producto_id,
            "codigo": d.codigo or "",
            "descripcion": d.descripcion,
            "unidad_medida": d.unidad_medida or "NIU",
            "cantidad": round(cant, 4),
            "precio_unitario": round(pu, 6),
            "valor_unitario": round(valor_unit, 6),
            "descuento_pct": round(desc_pct, 2),
            "descuento_monto": round(desc_monto, 2),
            "valor_venta": round(valor_venta, 2),
            "igv_pct": IGV_DEFAULT if afect in GRAVADAS else 0.0,
            "igv_monto": round(igv_monto, 2),
            "tipo_afectacion_igv": afect,
            "isc_pct": round(isc_pct, 2),
            "isc_monto": round(isc_monto, 2),
            "total_linea": round(valor_venta + igv_monto + isc_monto, 2),
        })

    subtotal = total_gravado + total_exonerado + total_inafecto + total_exportacion
    total_venta = subtotal + total_igv + total_isc

    return {
        "lineas": lineas,
        "total_gravado": round(total_gravado, 2),
        "total_exonerado": round(total_exonerado, 2),
        "total_inafecto": round(total_inafecto, 2),
        "total_exportacion": round(total_exportacion, 2),
        "total_descuento": round(total_descuento, 2),
        "subtotal": round(subtotal, 2),
        "total_igv": round(total_igv, 2),
        "total_isc": round(total_isc, 2),
        "total_venta": round(total_venta, 2),
    }


# ---------------------------------------------------------------------------
# Cliente lookup helper
# ---------------------------------------------------------------------------
def _resolver_cliente(cliente_id: Optional[int]) -> dict:
    if not cliente_id:
        return {}
    try:
        from ..core.db_adapter import is_dbf_mode, is_mdb_mode
        if is_dbf_mode():
            from ..core.db_adapter.dbf_repo import ClienteRepoDBF
            return ClienteRepoDBF.obtener(cliente_id) or {}
        if is_mdb_mode():
            from ..core.db_adapter.mdb_repo import ClienteRepoMDB
            return ClienteRepoMDB.obtener(cliente_id) or {}
    except Exception as exc:  # noqa: BLE001
        logger.debug("No se pudo resolver cliente %s: %s", cliente_id, exc)
    return {}


def _resolver_empresa() -> dict:
    try:
        from ..core.db_adapter import is_dbf_mode, is_mdb_mode
        if is_dbf_mode():
            from ..core.db_adapter.dbf_repo import EmpresaRepoDBF
            return EmpresaRepoDBF.obtener() or {}
        if is_mdb_mode():
            from ..core.db_adapter.mdb_repo import EmpresaRepoMDB
            return EmpresaRepoMDB.obtener() or {}
    except Exception:  # noqa: BLE001
        pass
    # Fallback al fake del main
    try:
        from ..main import _FAKE_EMPRESA_MDB
        return {
            "ruc": _FAKE_EMPRESA_MDB.ruc,
            "razon_social": _FAKE_EMPRESA_MDB.razon_social,
            "direccion": _FAKE_EMPRESA_MDB.direccion,
            "telefono": _FAKE_EMPRESA_MDB.telefono,
            "email": _FAKE_EMPRESA_MDB.email,
            "logo_path": _FAKE_EMPRESA_MDB.logo_path,
        }
    except Exception:  # noqa: BLE001
        return {}


# ---------------------------------------------------------------------------
# PDF generation helper
# ---------------------------------------------------------------------------
def _generar_pdf_para_pedido(pedido: dict) -> bytes:
    """Genera PDF reutilizando el generador existente."""
    from ..services._dict_adapter import DictNS
    from ..services.pdf_generator import generar_pdf_cotizacion, generar_pdf_pedido

    empresa_dict = _resolver_empresa()

    # Construir un "comprobante-like" con lo que el template espera
    comp_like = {
        "id": pedido.get("id"),
        "numero_completo": pedido.get("numero_completo"),
        "fecha_emision": pedido.get("fecha_emision"),
        "fecha_vencimiento": pedido.get("fecha_vencimiento"),
        "moneda": pedido.get("moneda", "PEN"),
        "cliente_tipo_doc": pedido.get("cliente_tipo_doc", ""),
        "cliente_numero_doc": pedido.get("cliente_numero_doc", ""),
        "cliente_razon_social": pedido.get("cliente_razon_social", ""),
        "cliente_direccion": pedido.get("cliente_direccion", ""),
        "total_gravado": pedido.get("total_gravado", 0),
        "total_exonerado": pedido.get("total_exonerado", 0),
        "total_inafecto": pedido.get("total_inafecto", 0),
        "total_igv": pedido.get("total_igv", 0),
        "total_venta": pedido.get("total_venta", 0),
        "descuento_global_pct": pedido.get("descuento_global_pct", 0),
        "descuento_global_monto": pedido.get("descuento_global_monto", 0),
        "forma_pago": pedido.get("condicion_pago", ""),
        "codigo_trabajador": pedido.get("vendedor", ""),
        "observaciones": pedido.get("observaciones", ""),
    }
    detalles_ns = [DictNS(d) for d in (pedido.get("detalles") or [])]

    if (pedido.get("tipo_pedido") or "COTIZACION") == "PEDIDO":
        return generar_pdf_pedido(
            DictNS(comp_like), detalles_ns, DictNS(empresa_dict),
            condicion_pago=pedido.get("condicion_pago") or "",
            vendedor=pedido.get("vendedor") or "",
            observaciones=pedido.get("observaciones") or "",
        )
    return generar_pdf_cotizacion(
        DictNS(comp_like), detalles_ns, DictNS(empresa_dict),
        validez_dias=int(pedido.get("validez_dias") or 15),
        condicion_pago=pedido.get("condicion_pago") or "",
        vendedor=pedido.get("vendedor") or "",
        observaciones=pedido.get("observaciones") or "",
    )


def _path_pdf(pedido: dict) -> Path:
    pid = pedido.get("id") or "unknown"
    nc = (pedido.get("numero_completo") or "").replace("/", "-")
    return _PDF_DIR / f"{nc or pid}.pdf"


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------
@router.get("")
@router.get("/")
def listar_pedidos(
    busqueda: Optional[str] = Query(None),
    estado: Optional[str] = Query(None),
    tipo_pedido: Optional[str] = Query(None),
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=200),
):
    """Lista cotizaciones/pedidos paginada (orden desc por fecha)."""
    with _LOCK:
        items = _load_all()

    # Filtros
    if estado:
        items = [it for it in items if (it.get("estado") or "") == estado]
    if tipo_pedido:
        items = [it for it in items
                 if (it.get("tipo_pedido") or "COTIZACION") == tipo_pedido]
    if busqueda:
        q = busqueda.lower()
        items = [it for it in items
                 if q in (it.get("numero_completo") or "").lower()
                 or q in (it.get("cliente_razon_social") or "").lower()
                 or q in (it.get("cliente_numero_doc") or "").lower()]

    # Orden desc por fecha + id
    items.sort(key=lambda x: (x.get("fecha_emision") or "", x.get("id") or ""),
               reverse=True)
    total = len(items)
    start = (page - 1) * per_page
    end = start + per_page
    return {"items": items[start:end], "total": total, "page": page,
            "per_page": per_page}


@router.get("/{pedido_id}")
def obtener_pedido(pedido_id: str):
    with _LOCK:
        items = _load_all()
    for it in items:
        if str(it.get("id")) == str(pedido_id):
            return it
    raise HTTPException(status_code=404, detail="Pedido/cotización no encontrado")


@router.post("", status_code=201)
def crear_pedido(payload: PedidoIn):
    """Crea cotización o pedido. NO toca ventas.dbf."""
    if not payload.detalles:
        raise HTTPException(status_code=422, detail="Debe incluir al menos un ítem")

    tipo_pedido = (payload.tipo_pedido or "COTIZACION").upper()
    if tipo_pedido not in ("COTIZACION", "PEDIDO"):
        raise HTTPException(status_code=422, detail="tipo_pedido debe ser COTIZACION o PEDIDO")

    cliente = _resolver_cliente(payload.cliente_id)
    calc = _calcular(payload)

    with _LOCK:
        items = _load_all()
        correlativo = _next_correlativo(tipo_pedido, items)
        serie = _serie(tipo_pedido)
        numero_completo = f"{serie}-{correlativo:08d}"

        pid = uuid.uuid4().hex[:12]
        fecha_emision = payload.fecha_emision or _date.today().isoformat()
        validez_dias = int(payload.validez_dias or 15)
        try:
            fecha_dt = _date.fromisoformat(fecha_emision)
            fecha_vencimiento = (fecha_dt.toordinal() + validez_dias)
            from datetime import date as _d
            fecha_vencimiento = _d.fromordinal(fecha_vencimiento).isoformat()
        except Exception:  # noqa: BLE001
            fecha_vencimiento = None

        nuevo = {
            "id": pid,
            "tipo_pedido": tipo_pedido,
            "serie": serie,
            "correlativo": correlativo,
            "numero_completo": numero_completo,
            "fecha_emision": fecha_emision,
            "fecha_vencimiento": fecha_vencimiento,
            "validez_dias": validez_dias,
            "moneda": payload.moneda,
            "tipo_operacion": payload.tipo_operacion,
            "cliente_id": payload.cliente_id,
            "cliente_tipo_doc": cliente.get("tipo_documento") or cliente.get("cliente_tipo_doc") or "",
            "cliente_numero_doc": cliente.get("numero_documento") or cliente.get("cliente_numero_doc") or "",
            "cliente_razon_social": cliente.get("razon_social") or cliente.get("cliente_razon_social") or "",
            "cliente_direccion": cliente.get("direccion") or cliente.get("cliente_direccion") or "",
            "cliente_email": cliente.get("email") or "",
            "descuento_global_pct": float(payload.descuento_global_pct or 0),
            "condicion_pago": payload.condicion_pago,
            "vendedor": payload.vendedor,
            "observaciones": payload.observaciones,
            "estado": "BORRADOR",
            "detalles": calc["lineas"],
            "total_gravado": calc["total_gravado"],
            "total_exonerado": calc["total_exonerado"],
            "total_inafecto": calc["total_inafecto"],
            "total_exportacion": calc["total_exportacion"],
            "total_descuento": calc["total_descuento"],
            "subtotal": calc["subtotal"],
            "total_igv": calc["total_igv"],
            "total_isc": calc["total_isc"],
            "total_venta": calc["total_venta"],
            "comprobante_id": None,
            "pdf_path": None,
            "created_at": datetime.now().isoformat(),
            "updated_at": datetime.now().isoformat(),
        }

        # Generar PDF al vuelo (best-effort)
        try:
            pdf_bytes = _generar_pdf_para_pedido(nuevo)
            pdf_path = _path_pdf(nuevo)
            pdf_path.write_bytes(pdf_bytes)
            nuevo["pdf_path"] = str(pdf_path)
        except Exception as exc:  # noqa: BLE001
            logger.exception("PDF inicial falló (se generará a demanda): %s", exc)

        items.append(nuevo)
        _save_all(items)
        return nuevo


@router.put("/{pedido_id}")
def actualizar_pedido(pedido_id: str, payload: PedidoIn):
    """Actualiza una cotización/pedido (regenera totales y PDF)."""
    with _LOCK:
        items = _load_all()
        idx = next((i for i, it in enumerate(items)
                    if str(it.get("id")) == str(pedido_id)), -1)
        if idx == -1:
            raise HTTPException(status_code=404, detail="Pedido no encontrado")

        existing = items[idx]
        if existing.get("estado") == "FACTURADO":
            raise HTTPException(status_code=409,
                                 detail="No se puede modificar un pedido ya facturado")

        cliente = _resolver_cliente(payload.cliente_id)
        calc = _calcular(payload)

        existing.update({
            "tipo_pedido": (payload.tipo_pedido or existing.get("tipo_pedido") or "COTIZACION").upper(),
            "fecha_emision": payload.fecha_emision or existing.get("fecha_emision"),
            "moneda": payload.moneda or existing.get("moneda"),
            "tipo_operacion": payload.tipo_operacion or existing.get("tipo_operacion"),
            "cliente_id": payload.cliente_id,
            "cliente_tipo_doc": cliente.get("tipo_documento") or "",
            "cliente_numero_doc": cliente.get("numero_documento") or "",
            "cliente_razon_social": cliente.get("razon_social") or "",
            "cliente_direccion": cliente.get("direccion") or "",
            "descuento_global_pct": float(payload.descuento_global_pct or 0),
            "validez_dias": int(payload.validez_dias or 15),
            "condicion_pago": payload.condicion_pago,
            "vendedor": payload.vendedor,
            "observaciones": payload.observaciones,
            "detalles": calc["lineas"],
            "total_gravado": calc["total_gravado"],
            "total_exonerado": calc["total_exonerado"],
            "total_inafecto": calc["total_inafecto"],
            "total_exportacion": calc["total_exportacion"],
            "total_descuento": calc["total_descuento"],
            "subtotal": calc["subtotal"],
            "total_igv": calc["total_igv"],
            "total_isc": calc["total_isc"],
            "total_venta": calc["total_venta"],
            "updated_at": datetime.now().isoformat(),
        })

        # Regenerar PDF
        try:
            pdf_bytes = _generar_pdf_para_pedido(existing)
            pdf_path = _path_pdf(existing)
            pdf_path.write_bytes(pdf_bytes)
            existing["pdf_path"] = str(pdf_path)
        except Exception as exc:  # noqa: BLE001
            logger.exception("Regenerar PDF falló: %s", exc)

        items[idx] = existing
        _save_all(items)
        return existing


@router.patch("/{pedido_id}/estado")
def cambiar_estado(pedido_id: str, estado: str = Query(...)):
    """Cambia el estado: BORRADOR | ENVIADO | APROBADO | FACTURADO | CANCELADO."""
    estado = estado.upper()
    if estado not in ("BORRADOR", "ENVIADO", "APROBADO", "FACTURADO", "CANCELADO"):
        raise HTTPException(status_code=422, detail="Estado inválido")
    with _LOCK:
        items = _load_all()
        for it in items:
            if str(it.get("id")) == str(pedido_id):
                if it.get("estado") == "FACTURADO" and estado != "FACTURADO":
                    raise HTTPException(status_code=409,
                                        detail="No se puede revertir un pedido facturado")
                it["estado"] = estado
                it["updated_at"] = datetime.now().isoformat()
                _save_all(items)
                return {"ok": True, "estado": estado, "id": pedido_id}
        raise HTTPException(status_code=404, detail="Pedido no encontrado")


@router.post("/{pedido_id}/facturar")
def facturar_pedido(pedido_id: str, payload: dict | None = None):
    """Convierte el pedido en comprobante real (escribe en ventas.dbf via
    el flujo de comprobantes existente).

    payload: {"tipo_documento": "01" | "03" | None}
    """
    payload = payload or {}
    tipo_documento = (payload.get("tipo_documento") or "").strip()

    with _LOCK:
        items = _load_all()
        idx = next((i for i, it in enumerate(items)
                    if str(it.get("id")) == str(pedido_id)), -1)
        if idx == -1:
            raise HTTPException(status_code=404, detail="Pedido no encontrado")
        pedido = items[idx]
        if pedido.get("estado") == "FACTURADO":
            raise HTTPException(status_code=409,
                                 detail="El pedido ya fue facturado")

    # Auto-detectar tipo: RUC (6) -> Factura (01), otro -> Boleta (03)
    if not tipo_documento:
        tipo_documento = "01" if pedido.get("cliente_tipo_doc") == "6" else "03"
    if tipo_documento not in ("01", "03"):
        raise HTTPException(status_code=422,
                            detail="tipo_documento debe ser '01' o '03'")

    # Construir payload de comprobante y llamar al endpoint correspondiente
    # delegando a la lógica existente. Importamos el shape esperado.
    serie_default = "F001" if tipo_documento == "01" else "B001"

    detalles_comp = []
    for d in pedido.get("detalles") or []:
        detalles_comp.append({
            "producto_id": d.get("producto_id"),
            "codigo": d.get("codigo") or "",
            "descripcion": d.get("descripcion"),
            "unidad_medida": d.get("unidad_medida") or "NIU",
            "cantidad": d.get("cantidad"),
            "valor_unitario": d.get("valor_unitario"),
            "precio_unitario": d.get("precio_unitario"),
            "descuento_pct": d.get("descuento_pct") or 0,
            "tipo_afectacion_igv": d.get("tipo_afectacion_igv") or "10",
            "igv_pct": d.get("igv_pct") or 18.0,
            "isc_pct": d.get("isc_pct") or 0,
        })

    comp_payload = {
        "tipo_documento": tipo_documento,
        "serie": serie_default,
        "fecha_emision": _date.today().isoformat(),
        "moneda": pedido.get("moneda", "PEN"),
        "tipo_operacion": pedido.get("tipo_operacion", "0101"),
        "cliente_id": pedido.get("cliente_id"),
        "cliente_tipo_doc": pedido.get("cliente_tipo_doc"),
        "cliente_numero_doc": pedido.get("cliente_numero_doc"),
        "cliente_razon_social": pedido.get("cliente_razon_social"),
        "cliente_direccion": pedido.get("cliente_direccion"),
        "observaciones": (
            f"Origen: {pedido.get('numero_completo')} ({pedido.get('tipo_pedido')})"
            + (f"\n{pedido.get('observaciones')}" if pedido.get("observaciones") else "")
        ),
        "items": detalles_comp,
    }

    # Intentamos vía servicio: usa el flujo legacy_emision si está, si no
    # devolvemos un error claro para que el cliente lo emita manualmente.
    try:
        from ..api.legacy_emision import emitir_legacy_endpoint  # type: ignore
    except ImportError:
        emitir_legacy_endpoint = None  # type: ignore

    resultado = None
    try:
        if emitir_legacy_endpoint:
            # Llamada directa (no http)
            from ..schemas.comprobante import ComprobanteIn
            comp_in = ComprobanteIn.model_validate(comp_payload)
            # Re-uso del endpoint /api/comprobantes/emitir vía función directa
            from ..api.comprobantes import emitir_comprobante
            from ..core.database import get_db
            db_gen = get_db()
            db = next(db_gen)
            try:
                resultado = emitir_comprobante(comp_in, db)
            finally:
                try:
                    db_gen.close()
                except Exception:  # noqa: BLE001
                    pass
        else:
            from ..api.comprobantes import emitir_comprobante
            from ..schemas.comprobante import ComprobanteIn
            from ..core.database import get_db
            comp_in = ComprobanteIn.model_validate(comp_payload)
            db_gen = get_db()
            db = next(db_gen)
            try:
                resultado = emitir_comprobante(comp_in, db)
            finally:
                try:
                    db_gen.close()
                except Exception:  # noqa: BLE001
                    pass
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        logger.exception("Error facturando pedido %s", pedido_id)
        raise HTTPException(
            status_code=500,
            detail=f"No se pudo emitir el comprobante: {exc}",
        )

    if resultado is None:
        raise HTTPException(status_code=500, detail="Sin resultado de emisión")

    # Marcar el pedido como FACTURADO con referencia
    res_dict = resultado.model_dump() if hasattr(resultado, "model_dump") else dict(resultado)
    with _LOCK:
        items = _load_all()
        for it in items:
            if str(it.get("id")) == str(pedido_id):
                it["estado"] = "FACTURADO"
                it["comprobante_id"] = res_dict.get("id")
                it["comprobante_numero"] = res_dict.get("numero_completo")
                it["updated_at"] = datetime.now().isoformat()
                break
        _save_all(items)

    return res_dict


@router.delete("/{pedido_id}")
def eliminar_pedido(pedido_id: str):
    with _LOCK:
        items = _load_all()
        idx = next((i for i, it in enumerate(items)
                    if str(it.get("id")) == str(pedido_id)), -1)
        if idx == -1:
            raise HTTPException(status_code=404, detail="Pedido no encontrado")
        if items[idx].get("estado") == "FACTURADO":
            raise HTTPException(status_code=409,
                                 detail="No se puede eliminar un pedido facturado")
        # Borrar PDF si existe
        pdf_p = items[idx].get("pdf_path")
        if pdf_p:
            try:
                Path(pdf_p).unlink(missing_ok=True)
            except Exception:  # noqa: BLE001
                pass
        items.pop(idx)
        _save_all(items)
    return {"ok": True}


@router.get("/{pedido_id}/pdf")
def descargar_pdf(pedido_id: str, inline: int = 0, force: int = 0):
    """Devuelve el PDF guardado (regenera si no existe o ?force=1)."""
    with _LOCK:
        items = _load_all()
    pedido = next((it for it in items if str(it.get("id")) == str(pedido_id)), None)
    if pedido is None:
        raise HTTPException(status_code=404, detail="Pedido no encontrado")

    pdf_p = _path_pdf(pedido)
    needs_regen = force or not pdf_p.exists()
    if needs_regen:
        try:
            pdf_bytes = _generar_pdf_para_pedido(pedido)
            pdf_p.parent.mkdir(parents=True, exist_ok=True)
            pdf_p.write_bytes(pdf_bytes)
            # Persistir path
            with _LOCK:
                items_w = _load_all()
                for it in items_w:
                    if str(it.get("id")) == str(pedido_id):
                        it["pdf_path"] = str(pdf_p)
                        break
                _save_all(items_w)
        except Exception as exc:  # noqa: BLE001
            logger.exception("Error generando PDF de pedido %s", pedido_id)
            raise HTTPException(status_code=500,
                                 detail=f"No se pudo generar PDF: {exc}")

    fname = f"{pedido.get('tipo_pedido', 'COT')}-{pedido.get('numero_completo', pedido_id)}.pdf"
    disp = "inline" if inline else "attachment"
    return Response(
        content=pdf_p.read_bytes(),
        media_type="application/pdf",
        headers={"Content-Disposition": f'{disp}; filename="{fname}"'},
    )
