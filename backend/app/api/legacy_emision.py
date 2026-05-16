"""Endpoints alias para el frontend React legacy (FacturaPE-MultiEmpresa).

El frontend antiguo asume el shape de ese sistema:
    - POST /api/comprobantes/emitir   (payload distinto al de Factura-mdb)
    - GET  /api/comprobantes/listar   ({items, total})
    - GET  /api/empresa/              (datos basicos de la empresa)
    - POST /api/auth/login            (dummy, sin auth real)
    - GET  /api/clientes/{id}/direcciones
    - GET  /api/listas-precios/{id}/precios

Este router NO toca los endpoints existentes de Factura-mdb (que usan
schemas Pydantic rigidos): provee una capa de compatibilidad encima.

En modo DBF escribe via `ComprobanteWriterDBF`. En modo MDB y SQLite
delega o devuelve 501 (los flujos originales se mantienen en
`api/comprobantes.py`).
"""
from __future__ import annotations

import logging
from datetime import date as _date
from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Query

from ..core.db_adapter import is_dbf_mode

logger = logging.getLogger("factura_mdb.api.legacy_emision")

router = APIRouter(tags=["legacy-emision"])


# ---------------------------------------------------------------------------
# Helpers de calculo (independientes de schemas Pydantic rigidos)
# ---------------------------------------------------------------------------

GRAVADAS = {"10", "11", "12", "13", "14", "15", "16", "17"}
EXONERADAS = {"20", "21"}
INAFECTAS = {"30", "31", "32", "33", "34", "35", "36"}
EXPORTACION = {"40"}


def _r(v: float, n: int = 2) -> float:
    return round(float(v) + 1e-9, n)


def _calcular_linea_simple(item: dict, igv_rate: float = 18.0) -> dict:
    """Calcula montos de una linea a partir del payload del frontend antiguo.

    Acepta dos shapes:
      - Frontend React antiguo: ``precio_unitario`` viene CON IGV (precio final).
      - OpenAPI / shape SUNAT: ``valor_unitario`` viene SIN IGV (valor de venta).

    Si llega ``valor_unitario`` lo trata como sin-IGV y deriva ``precio_unit``;
    si llega ``precio_unitario`` lo trata como con-IGV (comportamiento legacy).
    """
    cantidad = float(item.get("cantidad") or 0)
    afect = str(
        item.get("tipo_afectacion")
        or item.get("tipoAfectacion")
        or item.get("tipo_afectacion_igv")
        or "10"
    )

    # Detectar si vino valor_unitario (sin IGV) o precio_unitario (con IGV).
    raw_valor = item.get("valor_unitario") or item.get("valorUnitario")
    raw_precio = item.get("precio_unitario") or item.get("precioUnitario")

    if raw_valor is not None and float(raw_valor or 0) > 0:
        # Shape SUNAT/OpenAPI: el precio sin IGV viene explicito.
        valor_unit = float(raw_valor)
        if afect in GRAVADAS:
            precio_unit = valor_unit * (1 + igv_rate / 100.0)
        else:
            precio_unit = valor_unit
    else:
        # Shape legacy: precio_unitario incluye IGV.
        precio_unit = float(raw_precio or 0)
        if afect in GRAVADAS and precio_unit > 0:
            valor_unit = precio_unit / (1 + igv_rate / 100.0)
        else:
            valor_unit = precio_unit

    bruto = cantidad * valor_unit
    desc_pct = float(
        item.get("descuento_pct") or item.get("descuentoPct") or 0
    ) + float(
        item.get("descuento2_pct") or item.get("descuento2Pct") or 0
    ) + float(
        item.get("descuento3_pct") or item.get("descuento3Pct") or 0
    )
    desc_monto = bruto * (desc_pct / 100.0) if desc_pct > 0 else 0.0
    valor_venta = max(0.0, bruto - desc_monto)

    if afect in GRAVADAS and not item.get("es_gratuito"):
        igv_monto = valor_venta * (igv_rate / 100.0)
    else:
        igv_monto = 0.0

    total_linea = _r(valor_venta + igv_monto)

    return {
        "producto_id": item.get("producto_id") or item.get("productoId"),
        "codigo": item.get("codigo") or "",
        "descripcion": item.get("descripcion") or "ITEM",
        "unidad_medida": item.get("unidad_medida") or item.get("unidadMedida") or "NIU",
        "cantidad": _r(cantidad, 4),
        "valor_unitario": _r(valor_unit, 6),
        "precio_unitario": _r(precio_unit, 6),
        "descuento_pct": _r(desc_pct, 2),
        "descuento_monto": _r(desc_monto, 2),
        "valor_venta": _r(valor_venta, 2),
        "igv_pct": igv_rate,
        "igv_monto": _r(igv_monto, 2),
        "tipo_afectacion": afect,
        "tipo_afectacion_igv": afect,
        "isc_pct": float(item.get("isc_tasa") or 0),
        "isc_monto": 0.0,
        "icbper_monto": 0.50 if item.get("tiene_icbper") else 0.0,
        "total_linea": total_linea,
        "es_gratuito": bool(item.get("es_gratuito")),
        "tiene_icbper": bool(item.get("tiene_icbper")),
        "peso_kg": float(item.get("peso_kg") or 0),
    }


def _calcular_totales_simple(items: list[dict], descuento_global_pct: float = 0.0,
                              igv_rate: float = 18.0) -> dict:
    """Totales agregados a partir de una lista de lineas calculadas."""
    total_gravado = 0.0
    total_exonerado = 0.0
    total_inafecto = 0.0
    total_exportacion = 0.0
    total_gratuito = 0.0
    total_descuento = 0.0
    total_igv = 0.0
    total_isc = 0.0
    total_icbper = 0.0

    for d in items:
        afect = d["tipo_afectacion"]
        vv = d["valor_venta"]
        if afect in GRAVADAS:
            total_gravado += vv
        elif afect in EXONERADAS:
            total_exonerado += vv
        elif afect in INAFECTAS:
            total_inafecto += vv
        elif afect in EXPORTACION:
            total_exportacion += vv
        if d.get("es_gratuito"):
            total_gratuito += vv
        total_descuento += d["descuento_monto"]
        total_igv += d["igv_monto"]
        total_isc += d.get("isc_monto", 0)
        total_icbper += d.get("icbper_monto", 0)

    # Descuento global
    desc_global_monto = 0.0
    if descuento_global_pct > 0:
        base = total_gravado + total_exonerado + total_inafecto
        desc_global_monto = base * (descuento_global_pct / 100.0)
        # Reducir proporcionalmente del gravado para recalcular IGV
        if total_gravado > 0:
            factor_g = total_gravado / base if base > 0 else 0
            reduccion_grav = desc_global_monto * factor_g
            total_gravado -= reduccion_grav
            total_igv = total_gravado * (igv_rate / 100.0)
        total_descuento += desc_global_monto

    subtotal = total_gravado + total_exonerado + total_inafecto + total_exportacion
    total_venta = subtotal + total_igv + total_isc + total_icbper

    return {
        "total_gravado": _r(total_gravado),
        "total_exonerado": _r(total_exonerado),
        "total_inafecto": _r(total_inafecto),
        "total_exportacion": _r(total_exportacion),
        "total_gratuito": _r(total_gratuito),
        "total_descuento": _r(total_descuento),
        "descuento_global_monto": _r(desc_global_monto),
        "subtotal": _r(subtotal),
        "total_igv": _r(total_igv),
        "total_isc": _r(total_isc),
        "total_icbper": _r(total_icbper),
        "total_venta": _r(total_venta),
        "total_pen": _r(total_venta),
    }


def _resolver_serie_default(tipo_documento: str) -> str:
    """Devuelve serie por defecto segun tipo SUNAT."""
    return {"01": "F001", "03": "B001", "07": "FC01", "08": "FD01"}.get(
        tipo_documento, "F001"
    )


def _resolver_cliente_dbf(cliente_id: Optional[int],
                           cliente_numero_doc: Optional[str]) -> dict:
    """Busca cliente en DBF por id sintetico o numero documento."""
    if not is_dbf_mode():
        return {}
    from ..core.db_adapter.dbf_repo import ClienteRepoDBF
    if cliente_id:
        c = ClienteRepoDBF.obtener(int(cliente_id))
        if c:
            return c
    if cliente_numero_doc:
        c = ClienteRepoDBF.obtener_por_codigo(str(cliente_numero_doc))
        if c:
            return c
    return {}


# ---------------------------------------------------------------------------
# POST /api/comprobantes/emitir  (shape FacturaPE-MultiEmpresa)
# ---------------------------------------------------------------------------

@router.post("/api/comprobantes/emitir")
def emitir_legacy(payload: dict) -> dict:
    """Emite un comprobante en 2 pasos — PASO 1: persiste local con estado E.

    NUNCA envia a SUNAT desde aqui. El cliente debe llamar despues a
    POST /api/comprobantes/{id}/enviar-sunat (PASO 2) para firmar +
    enviar el XML. Cualquier flag `auto_envio_sunat` del payload se
    ignora deliberadamente — el flujo en 2 pasos es la unica via
    soportada en DAEFY.
    """
    # Limpieza defensiva: descartar cualquier flag legacy de auto-envio.
    if isinstance(payload, dict):
        payload.pop("auto_envio_sunat", None)
        payload.pop("auto_envio", None)
        payload.pop("enviar_sunat", None)
    if not is_dbf_mode():
        raise HTTPException(
            status_code=501,
            detail="Endpoint legacy solo soportado en modo DBF (cliente DAEFY).",
        )

    from ..core.db_adapter.dbf_writer import ComprobanteWriterDBF

    tipo_doc = str(payload.get("tipo_documento") or "01")
    # Aceptar tanto 'detalles' (shape pydantic interno) como 'items'
    # (shape OpenAPI / frontend nuevo). Lo que llegue primero gana.
    detalles = payload.get("detalles") or payload.get("items") or []
    if not detalles:
        raise HTTPException(
            status_code=422,
            detail="Debe enviar al menos un item en 'items' o 'detalles'",
        )

    # Resolver cliente para snapshot en cabecera. Acepta el sub-objeto
    # 'cliente' del shape OpenAPI o los campos planos legacy.
    cliente_obj = payload.get("cliente") or {}
    cliente_id = payload.get("cliente_id") or cliente_obj.get("id")
    cliente_doc = (
        payload.get("cliente_numero_doc")
        or cliente_obj.get("numero_doc")
        or cliente_obj.get("numero_documento")
    )
    cli = _resolver_cliente_dbf(cliente_id, cliente_doc)
    # Si vino el sub-objeto 'cliente' completo, usarlo como fallback de los
    # snapshots que el writer espera planos.
    if cliente_obj:
        if not cli.get("razon_social") and cliente_obj.get("razon_social"):
            cli = dict(cli) if cli else {}
            cli["razon_social"] = cliente_obj.get("razon_social")
        if not cli.get("direccion") and cliente_obj.get("direccion"):
            cli = dict(cli) if cli else {}
            cli["direccion"] = cliente_obj.get("direccion")
        if not cli.get("tipo_documento") and cliente_obj.get("tipo_doc"):
            cli = dict(cli) if cli else {}
            cli["tipo_documento"] = cliente_obj.get("tipo_doc")
        if not cli.get("numero_documento") and cliente_doc:
            cli = dict(cli) if cli else {}
            cli["numero_documento"] = cliente_doc

    # Resolver serie
    serie = payload.get("serie") or _resolver_serie_default(tipo_doc)

    # Calcular lineas + totales
    items_calc = [_calcular_linea_simple(d, igv_rate=18.0) for d in detalles]
    desc_global = float(payload.get("descuento_global_pct") or 0)
    totales = _calcular_totales_simple(items_calc,
                                         descuento_global_pct=desc_global)

    # Enriquecer payload con datos del cliente resuelto
    enriched = dict(payload)
    enriched.setdefault("serie", serie)
    enriched.setdefault("cliente_numero_doc", cli.get("numero_documento") or cliente_doc or "")
    enriched.setdefault("cliente_razon_social", cli.get("razon_social") or "VARIOS")
    enriched.setdefault("cliente_direccion", cli.get("direccion") or "")
    enriched.setdefault("cliente_tipo_doc", cli.get("tipo_documento") or "6")
    enriched.setdefault("cliente_email", cli.get("email"))

    try:
        result = ComprobanteWriterDBF.crear(enriched, items=items_calc, totales=totales)
    except Exception as e:  # noqa: BLE001
        logger.exception("Error escribiendo comprobante en DBF")
        raise HTTPException(status_code=500,
                             detail=f"Error escribiendo en DBF: {e}")

    # Asegurar shape compatible con frontend (estado E = emitido local).
    result["estado"] = "E"
    result["debe_enviar_sunat"] = True  # hint al frontend
    return result


# ---------------------------------------------------------------------------
# GET /api/comprobantes/listar  (alias del listar existente)
# ---------------------------------------------------------------------------

@router.get("/api/comprobantes/listar")
def listar_legacy(
    tipo: Optional[str] = Query(None),
    estado: Optional[str] = Query(None),
    fecha_desde: Optional[_date] = Query(None),
    fecha_hasta: Optional[_date] = Query(None),
    busqueda: Optional[str] = Query(None),
    busqueda2: Optional[str] = Query(None),
    col_busqueda: Optional[str] = Query(None),
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100),
) -> dict:
    """Listar comprobantes con shape {items, total, page, per_page}."""
    if not is_dbf_mode():
        # Fallback: delegar al endpoint nativo
        from .comprobantes import listar_comprobantes
        from ..core.database import SessionLocal
        db = SessionLocal()
        try:
            res = listar_comprobantes(
                db=db, tipo_documento=tipo, tipo=tipo, serie=None,
                estado=estado, fecha_desde=fecha_desde, desde=None,
                fecha_hasta=fecha_hasta, hasta=None, cliente_id=None,
                search=busqueda, q_search=None,
                limit=per_page, offset=(page - 1) * per_page,
            )
            return {
                "items": res.items if hasattr(res, "items") else res.get("items", []),
                "total": res.total if hasattr(res, "total") else res.get("total", 0),
                "page": page, "per_page": per_page,
            }
        finally:
            db.close()

    from ..core.db_adapter.dbf_repo import ComprobanteRepoDBF
    items, total = ComprobanteRepoDBF.listar(
        filtros={
            "tipo_documento": tipo,
            "estado": estado,
            "fecha_desde": fecha_desde,
            "fecha_hasta": fecha_hasta,
            "q": (busqueda or "").strip() or None,
        },
        limit=per_page, offset=(page - 1) * per_page,
    )
    return {"items": items, "total": total, "page": page, "per_page": per_page}


# ---------------------------------------------------------------------------
# GET /api/empresa/   (alias con barra final)
# ---------------------------------------------------------------------------

@router.get("/api/empresa/")
def empresa_legacy() -> dict:
    """Datos basicos de la empresa para el frontend."""
    try:
        if is_dbf_mode():
            from ..core.db_adapter.dbf_repo import EmpresaRepoDBF
            return EmpresaRepoDBF.obtener()
        from ..main import _FAKE_EMPRESA_MDB
        return {
            c: getattr(_FAKE_EMPRESA_MDB, c, None)
            for c in (
                "id", "ruc", "razon_social", "nombre_comercial", "direccion",
                "ubigeo", "departamento", "provincia", "distrito", "telefono",
                "email", "sitio_web", "logo_path", "sunat_env",
            )
        }
    except Exception as exc:  # noqa: BLE001
        logger.warning("empresa_legacy fallback: %s", exc)
        return {
            "id": 1, "ruc": "20615413071",
            "razon_social": "DAEFY", "nombre_comercial": "DAEFY",
            "direccion": "", "telefono": "", "email": "",
            "sunat_env": "beta",
        }


# ---------------------------------------------------------------------------
# POST /api/auth/login  (dummy)
# ---------------------------------------------------------------------------

@router.post("/api/auth/login")
def auth_login_dummy(payload: Optional[dict] = None) -> dict:
    """Login dummy: el sistema es local, sin auth real."""
    return {
        "token": "fake-local-token",
        "user": {
            "id": 1,
            "ruc": "20615413071",
            "rol": "admin",
            "username": "local",
            "email": "local@daefy.local",
            "empresa_id": 1,
        },
        "empresa": {
            "id": 1,
            "ruc": "20615413071",
            "razon_social": "DAEFY",
        },
    }


@router.get("/api/auth/me")
def auth_me_dummy() -> dict:
    """Usuario actual dummy."""
    return {
        "id": 1, "ruc": "20615413071", "rol": "admin",
        "username": "local", "email": "local@daefy.local",
        "empresa_id": 1,
        "permisos": ["emitir", "emitir_nc_nd", "anular", "ver_reportes"],
    }


# ---------------------------------------------------------------------------
# Stubs para evitar crashes del frontend (devuelven listas/objetos vacios)
# ---------------------------------------------------------------------------

@router.get("/api/clientes/{cliente_id}/direcciones")
def clientes_direcciones_stub(cliente_id: int) -> dict:
    """Stub: devuelve direcciones vacias (los clientes DBF no tienen sucursales)."""
    return {"items": [], "total": 0}


@router.get("/api/listas-precios/{lista_id}/precios")
def listas_precios_stub(lista_id: int) -> dict:
    return {"items": [], "total": 0}


@router.get("/api/establecimientos")
def establecimientos_stub() -> dict:
    return {
        "items": [
            {
                "id": 1, "codigo": "0000",
                "descripcion": "Establecimiento Principal",
                "serie_factura": "F001", "serie_boleta": "B001",
                "serie_nc": "FC01", "serie_nd": "FD01",
                "activo": True,
            }
        ],
        "total": 1,
    }


@router.get("/api/anticipos")
def anticipos_stub() -> dict:
    return {"items": [], "total": 0}


@router.get("/api/cuentas-cobrar")
def cuentas_cobrar_stub() -> dict:
    return {"items": [], "total": 0}


@router.get("/api/configuracion-empresa")
def config_empresa_stub() -> dict:
    """Flags de configuracion de la empresa (solo_contado, etc.)."""
    return {
        "solo_contado": False,
        "controla_stock": False,
        "aplica_detraccion": True,
        "aplica_percepcion": False,
        "aplica_retencion": False,
        "aplica_isc": False,
        "aplica_icbper": True,
        "permite_desc_item": True,
        "permite_desc_global": True,
        "igv_rate": 18.0,
        "icbper_monto": 0.50,
        "moneda_default": "PEN",
        "auto_envio_sunat": False,
    }


@router.get("/api/tipo-cambio")
def tipo_cambio_stub(moneda: str = "USD") -> dict:
    """Tipo de cambio del dia. Stub con valor estatico."""
    return {"moneda": moneda, "compra": 3.75, "venta": 3.78,
             "fecha": _date.today().isoformat()}


# ---------------------------------------------------------------------------
# Aliases con barra final (el frontend React antiguo los usa con slash)
# ---------------------------------------------------------------------------

@router.get("/api/clientes/")
def clientes_slash_alias(
    q: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
):
    """Alias /api/clientes/ con barra final."""
    from .clientes import listar_clientes
    return listar_clientes(
        search=None, q_search=q, tipo_doc=None, tipo_documento=None,
        activo=None, limit=limit, offset=offset, db=None,
    )


@router.get("/api/productos/")
def productos_slash_alias(
    q: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
):
    """Alias /api/productos/ con barra final."""
    from .productos import listar_productos
    return listar_productos(
        db=None, search=None, q_search=q, activo=None,
        limit=limit, offset=offset,
    )


# ---------------------------------------------------------------------------
# Stubs adicionales para evitar 404/500 en el dashboard y configuración
# ---------------------------------------------------------------------------

# Dashboard.jsx llama /api/tipos-cambio/hoy (plural). Mantenemos el alias
# singular existente y agregamos el plural para que ambos formatos sirvan.
@router.get("/api/tipos-cambio/hoy")
def tipos_cambio_hoy_stub(moneda: str = "USD") -> dict:
    """Tipo de cambio del día (alias plural). Stub con valor estático."""
    return {
        "moneda": moneda,
        "compra": 3.75,
        "venta": 3.78,
        "fecha": _date.today().isoformat(),
    }


# Dashboard.jsx llama /api/envios/email/resumen para mostrar el contador
# de correos enviados. No tenemos servicio real aún; devolvemos vacío.
@router.get("/api/envios/email/resumen")
def envios_email_resumen_stub() -> dict:
    """Resumen de envíos por email (stub). Sin envíos automáticos hoy."""
    return {
        "hoy": {"enviados": 0, "fallidos": 0},
        "mes": {"enviados": 0, "fallidos": 0},
        "ultimos": [],
    }


# El endpoint real /api/comprobantes/{comp_id} captura "robot-status" como
# entero y devuelve 422. Definimos el estado del robot SUNAT acá ANTES de
# que se monte el router de comprobantes.
@router.get("/api/comprobantes/robot-status")
def comprobantes_robot_status_stub() -> dict:
    """Estado del robot que envía a SUNAT en background. Sin robot por ahora."""
    return {
        "activo": False,
        "ultima_corrida": None,
        "pendientes": 0,
        "mensaje": "Envío manual desde la UI (robot deshabilitado)",
    }


# Configuracion.jsx llama /api/config/ — alias plano que devuelve el
# mismo shape que /api/empresa/configuracion (frontend lo usa para
# leer flags globales).
@router.get("/api/config/")
def config_root_stub() -> dict:
    """Alias plano /api/config/ usado por Configuracion.jsx."""
    try:
        from ..main import _FAKE_CONFIG_MDB

        return {
            "id": _FAKE_CONFIG_MDB.id,
            "igv_rate": _FAKE_CONFIG_MDB.igv_rate,
            "formato_impresion": _FAKE_CONFIG_MDB.formato_impresion,
            "moneda_default": _FAKE_CONFIG_MDB.moneda_default,
            "aplica_detraccion": _FAKE_CONFIG_MDB.aplica_detraccion,
            "detraccion_porcentaje": _FAKE_CONFIG_MDB.detraccion_porcentaje,
            "auto_envio_sunat": _FAKE_CONFIG_MDB.auto_envio_sunat,
            "backup_automatico": _FAKE_CONFIG_MDB.backup_automatico,
            "pie_pagina": _FAKE_CONFIG_MDB.pie_pagina,
            "cuenta_bcp": _FAKE_CONFIG_MDB.cuenta_bcp,
            "cuenta_bcp_moneda": _FAKE_CONFIG_MDB.cuenta_bcp_moneda,
            "cta_banco_nacion": _FAKE_CONFIG_MDB.cta_banco_nacion,
        }
    except Exception as exc:  # noqa: BLE001
        logger.warning("config_root_stub fallback: %s", exc)
        return {
            "id": 1, "igv_rate": 18.0, "formato_impresion": "A4",
            "moneda_default": "PEN", "aplica_detraccion": False,
            "detraccion_porcentaje": 0.0, "auto_envio_sunat": False,
            "backup_automatico": False,
        }


# /api/correlativos cae al `db.query()` stub en modo DBF (RuntimeError 500).
# Definimos acá el handler que va a ganar (legacy_emision se incluye antes
# que comprobantes.correlativos_router).
_SERIES_DEFAULT_LEGACY = ["F001", "B001", "FC01", "BC01", "FD01", "BD01"]


@router.get("/api/correlativos")
def correlativos_dbf_safe() -> dict:
    """Devuelve series y último correlativo. Soporta modo DBF sin sesión SQL."""
    if is_dbf_mode():
        try:
            from ..core.db_adapter.dbf_repo import ComprobanteRepoDBF

            items = ComprobanteRepoDBF.listar_series()
        except Exception as exc:  # noqa: BLE001
            logger.warning("correlativos DBF fallback: %s", exc)
            items = []
        existentes = {it["serie"] for it in items}
        for s in _SERIES_DEFAULT_LEGACY:
            if s not in existentes:
                items.append({"serie": s, "ultimo": 0})
        items.sort(key=lambda x: x["serie"])
        return {"items": items}

    # Modos no-DBF: devolvemos defaults (el comprobantes.correlativos_router
    # original se intentará usar para SQLite/MDB y caerá si la BD falla).
    return {"items": [{"serie": s, "ultimo": 0} for s in _SERIES_DEFAULT_LEGACY]}


# /api/empresa (sin barra) cae al else con db.query → 500 en DBF.
# `/api/empresa/` (con barra) ya tiene handler arriba; agregamos sin barra.
@router.get("/api/empresa")
def empresa_legacy_sin_slash() -> dict:
    """Variante sin barra final — el frontend a veces la pide así."""
    return empresa_legacy()


# /api/configuracion también cae al db.query stub en DBF.
@router.get("/api/configuracion")
def configuracion_dbf_safe() -> dict:
    """Configuración global. En DBF devuelve los valores del config.json."""
    return config_root_stub()
