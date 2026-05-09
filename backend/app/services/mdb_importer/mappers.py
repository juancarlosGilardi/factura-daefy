"""Transformación de filas MDB → dicts listos para los modelos Factura-mdb.

Cada función toma una "fila" pyodbc (que es accesible por nombre de columna
y/o índice) y devuelve un diccionario serializable que puede pasarse
directamente al constructor del modelo SQLAlchemy correspondiente.

Las funciones son **defensivas**: no lanzan excepciones por datos sucios;
en lugar de eso, devuelven `None` para registros irrecuperables. Esto
permite que el orquestador (`importer.py`) las maneje y reporte errores
sin abortar la importación entera.
"""
from __future__ import annotations

import logging
from typing import Any, Optional

from .validators import (
    inferir_tipo_documento,
    normalizar_codigo,
    normalizar_descripcion,
    normalizar_email,
    normalizar_moneda,
    normalizar_telefono,
    normalizar_tipo_comprobante,
    normalizar_tipo_documento_mdb,
    validar_fecha,
    validar_monto,
)

logger = logging.getLogger("factura_mdb.mdb")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _get(row: Any, attr: str, default: Any = None) -> Any:
    """Lee un atributo de una fila pyodbc tolerando ausencia.

    Si la columna no existe en este MDB devuelve `default`. Esto permite
    que distintas versiones de SIAP (con columnas faltantes) no rompan
    el mapper.
    """
    try:
        return getattr(row, attr)
    except (AttributeError, IndexError):
        return default


# ---------------------------------------------------------------------------
# Clientes — EF2CLIENTES → models.cliente.Cliente
# ---------------------------------------------------------------------------
def mdb_cliente_to_dict(row: Any) -> Optional[dict]:
    """Mapea una fila de `EF2CLIENTES` al constructor de `Cliente`.

    Reglas:
        * Documento: prioriza `F2NEWRUC`; si no, `F2DOCCLI`.
        * Si no hay documento o no hay nombre, devuelve None (saltar).
        * `tipo_documento` se determina con `normalizar_tipo_documento_mdb`,
          que considera el campo F2TIPDOC pero infiere por longitud
          si está ausente o trae basura.
    """
    documento_raw = _get(row, "F2NEWRUC") or _get(row, "F2DOCCLI") or ""
    documento = "".join(c for c in str(documento_raw) if c.isalnum())[:20]
    razon = normalizar_descripcion(_get(row, "F2NOMCLI"), 200)

    if not documento or not razon:
        return None

    tipo = normalizar_tipo_documento_mdb(_get(row, "F2TIPDOC"), documento)

    direccion = normalizar_descripcion(_get(row, "F2DIRCLI"), 300)
    # SIAP guarda "-" cuando no hay dirección.
    if direccion in ("-", ".", "S/D", "SD", "N/A"):
        direccion = ""

    telefono = normalizar_telefono(_get(row, "F2TELCLI"))
    email = normalizar_email(_get(row, "F2EMAIL"))
    ubigeo_raw = _get(row, "F2UBIGEO")
    ubigeo = "".join(c for c in str(ubigeo_raw or "") if c.isdigit())[:6] or None

    return {
        "tipo_documento": tipo,
        "numero_documento": documento,
        "razon_social": razon,
        "direccion": direccion or None,
        "ubigeo": ubigeo,
        "email": email,
        "telefono": telefono or None,
        "activo": True,
    }


# ---------------------------------------------------------------------------
# Productos — registros únicos en TBVENTA_DET
# ---------------------------------------------------------------------------
def mdb_producto_unico_to_dict(
    codigo: Any,
    descripcion: Any,
    unidad_medida: Any,
    valor_unitario: Any,
    afecto: Any,
    moneda: Any = None,
) -> Optional[dict]:
    """Construye un dict para `Producto` a partir de columnas únicas
    detectadas en `TBVENTA_DET` (ya que muchos SIAP no tienen tabla
    explícita de productos).

    `unidad_medida` se mapea con tolerancia: SIAP usa códigos legacy
    como "101" (NIU), "102" (KGM), etc.
    """
    cod = normalizar_codigo(codigo, 40)
    desc = normalizar_descripcion(descripcion, 300)
    if not cod or not desc:
        return None

    um_map = {
        "101": "NIU", "102": "KGM", "103": "LTR",
        "104": "MTR", "201": "ZZ",  # 201 servicios M&C
        "ZZ": "ZZ", "NIU": "NIU", "KGM": "KGM", "LTR": "LTR",
        "MTR": "MTR", "GLN": "GLN", "BX": "BX",
    }
    um_raw = (str(unidad_medida or "").strip().upper() or "NIU")
    um = um_map.get(um_raw, um_raw[:8] if um_raw else "NIU")

    valor = validar_monto(valor_unitario, 0.0)
    afect = "10" if afecto else "30"  # gravado / inafecto
    moneda_norm = normalizar_moneda(moneda) if moneda is not None else "PEN"

    return {
        "codigo": cod,
        "descripcion": desc,
        "unidad_medida": um[:8],
        "valor_unitario": valor,
        "moneda": moneda_norm,
        "tipo_afectacion_igv": afect,
        "incluye_igv": False,
        "activo": True,
    }


# ---------------------------------------------------------------------------
# Comprobantes — TBVENTA_CAB → models.comprobante.Comprobante
# ---------------------------------------------------------------------------
def _decidir_estado(row: Any) -> str:
    """Calcula el estado Factura-mdb (P/A/R/B) a partir de las flags MDB.

    Heurística derivada de los scripts de inspección SIAP:
        * F4ESTNUL truthy → 'B' (baja)
        * F4CDR contiene 'aceptado' o F4ESTEMI truthy → 'A'
        * F4CDR contiene 'rechazado' → 'R'
        * else → 'P' (pendiente)
    """
    estnul = _get(row, "F4ESTNUL")
    if estnul is True or (estnul and str(estnul).strip() in ("*", "A", "1", "S", "B")):
        return "B"
    cdr = (str(_get(row, "F4CDR") or "")).lower()
    if "aceptado" in cdr or _get(row, "F4ESTEMI") is True:
        return "A"
    if "rechazado" in cdr:
        return "R"
    return "P"


def _construir_numero_completo(serie: str, correlativo: int) -> str:
    """`F001-00000123` (8 dígitos)."""
    return f"{serie}-{correlativo:08d}"


def mdb_comprobante_to_dict(row: Any) -> Optional[dict]:
    """Mapea una fila de `TBVENTA_CAB` al constructor de `Comprobante`.

    No incluye los detalles — esos se mapean por separado y se asocian
    en el orquestador. Devuelve `None` si la fila no es importable
    (sin tipo válido, serie vacía, número no numérico, etc.).
    """
    tipo = normalizar_tipo_comprobante(_get(row, "F4TIPODOCU"))
    if not tipo:
        return None
    serie = (str(_get(row, "F4SERDOC") or "").strip())[:4]
    if not serie:
        return None
    num_raw = str(_get(row, "F4NUMDOC") or "0").strip()
    try:
        correlativo = int(num_raw)
    except (TypeError, ValueError):
        return None
    if correlativo <= 0:
        return None

    fecha = validar_fecha(_get(row, "F4FECEMI"))
    if not fecha:
        return None

    moneda = normalizar_moneda(_get(row, "F4TIPMON"))
    tc = validar_monto(_get(row, "F4TIPCAM"), 1.0)
    if tc <= 0:
        tc = 1.0

    # Cliente "snapshot" (lo que estaba en el comprobante al emitirlo)
    cli_doc_raw = (str(_get(row, "F2RUCCLI") or "").strip())
    cli_doc = "".join(c for c in cli_doc_raw if c.isalnum())[:20]
    if not cli_doc or cli_doc == "-":
        cli_doc = "00000000"  # consumidor final placeholder
    cli_razon = normalizar_descripcion(_get(row, "F2NOMCLI"), 200) or "VARIOS"
    cli_dir = normalizar_descripcion(_get(row, "F2DIRCLI"), 300)
    if cli_dir in ("-", ".", "S/D", "SD", "N/A"):
        cli_dir = ""
    td_cli = inferir_tipo_documento(cli_doc)

    # Totales
    sub = validar_monto(_get(row, "F4SUBTOT"), 0.0)
    igv = validar_monto(_get(row, "F4TOTIGV"), 0.0)
    inaf = validar_monto(_get(row, "F4SUBFACINAF"), 0.0)
    exo = validar_monto(_get(row, "F4MONTOEXONERADO"), 0.0)
    total = validar_monto(_get(row, "F4TOTFAC"), sub + igv + inaf + exo)
    total_pen = round(total * tc, 2) if moneda == "USD" else total

    estado = _decidir_estado(row)
    cdr = _get(row, "F4CDR")
    cdr_descripcion = normalizar_descripcion(cdr, 500) if cdr else None
    cdr_codigo = "0" if estado == "A" else None

    forma_pago_raw = (str(_get(row, "F4FORPAG") or "").strip())
    forma_pago = "Contado" if forma_pago_raw in ("001", "1", "C", "") else "Credito"
    if tipo == "07":  # NC: SUNAT no exige forma de pago
        forma_pago = "Contado"

    # Detracción (catálogo 54). SIAP solo guarda monto/porcentaje,
    # asumimos código 037 (servicios genéricos) si aplica y el doc es factura.
    det_aplica = bool(_get(row, "F4DETRACCIONAPLICA"))
    if det_aplica and tipo == "01":
        det_codigo = "037"
        det_pct = validar_monto(_get(row, "F4DETRACCIONPORC"), 0.0)
        det_monto = validar_monto(_get(row, "F4DETRACCIONMONTO"), 0.0)
    else:
        det_codigo = None
        det_pct = None
        det_monto = None

    # Doc referencia para NC (07)
    doc_ref_tipo = None
    doc_ref_serie = None
    doc_ref_motivo = None
    if tipo == "07":
        ref_tipo = normalizar_tipo_comprobante(_get(row, "F4TIPREF"))
        ref_serie = (str(_get(row, "F4SERGUI") or "").strip())[:4]
        ref_num_raw = str(_get(row, "F4NUMGUI") or "0").strip()
        try:
            ref_num = int(ref_num_raw)
        except (TypeError, ValueError):
            ref_num = 0
        if ref_tipo and ref_serie and ref_num > 0:
            doc_ref_tipo = ref_tipo
            doc_ref_serie = _construir_numero_completo(ref_serie, ref_num)
            doc_ref_motivo = "01"  # anulación de la operación (default)

    # Tipo operación: 0101 venta interna, 0401 exportación.
    # Si el cliente tiene RUC con prefijo 11/12/etc (no domiciliado) → exportación.
    tipo_operacion = "0101"
    if (
        td_cli == "6"
        and len(cli_doc) == 11
        and cli_doc.isdigit()
        and cli_doc[:2] not in ("10", "15", "16", "17", "20")
    ):
        tipo_operacion = "0401"

    return {
        "tipo_documento": tipo,
        "serie": serie,
        "correlativo": correlativo,
        "numero_completo": _construir_numero_completo(serie, correlativo),
        "fecha_emision": fecha,
        "moneda": moneda,
        "tipo_cambio": tc,
        "tipo_operacion": tipo_operacion,
        "cliente_tipo_doc": td_cli,
        "cliente_numero_doc": cli_doc,
        "cliente_razon_social": cli_razon,
        "cliente_direccion": cli_dir or None,
        "total_gravado": sub,
        "total_exonerado": exo,
        "total_inafecto": inaf,
        "total_exportacion": 0.0,
        "total_gratuito": 0.0,
        "total_descuento": 0.0,
        "subtotal": sub,
        "total_igv": igv,
        "total_isc": 0.0,
        "total_icbper": 0.0,
        "total_venta": total,
        "total_pen": total_pen,
        "detraccion_codigo": det_codigo,
        "detraccion_tasa": det_pct,
        "detraccion_monto": det_monto,
        "forma_pago": forma_pago,
        "doc_referencia_tipo": doc_ref_tipo,
        "doc_referencia_serie": doc_ref_serie,
        "doc_referencia_motivo": doc_ref_motivo,
        "estado": estado,
        "cdr_codigo": cdr_codigo,
        "cdr_descripcion": cdr_descripcion,
        "observaciones": "Importado desde MDB SIAP legacy",
    }


# ---------------------------------------------------------------------------
# Detalles — TBVENTA_DET → models.comprobante.ComprobanteDetalle
# ---------------------------------------------------------------------------
def mdb_detalle_to_dict(row: Any) -> Optional[dict]:
    """Mapea una fila de `TBVENTA_DET` al constructor de
    `ComprobanteDetalle`.

    El llamador es responsable de asignar `comprobante_id`. Aquí sólo
    reportamos los campos por valor.
    """
    cantidad = validar_monto(_get(row, "F3CANPRO"), 1.0)
    if cantidad <= 0:
        cantidad = 1.0
    valor_unitario = validar_monto(_get(row, "F3VALVTAUNIT"), 0.0)
    valor_venta = validar_monto(_get(row, "F3VALVTA"), valor_unitario * cantidad)
    igv_monto = validar_monto(_get(row, "F3IGV"), 0.0)
    total_linea = validar_monto(_get(row, "F3PREVTA"), valor_venta + igv_monto)

    afecto = bool(_get(row, "F3AFECTO"))
    tipo_afect = "10" if afecto else "30"
    igv_pct = 18.0 if afecto else 0.0
    precio_unitario = round(total_linea / cantidad, 6) if cantidad else valor_unitario

    descripcion = normalizar_descripcion(_get(row, "F5NOMPRO"), 500) or "ITEM"
    codigo = normalizar_codigo(_get(row, "F5CODPRO"), 40) or None

    um_map = {"101": "NIU", "102": "KGM", "103": "LTR", "104": "MTR", "201": "ZZ"}
    um_raw = (str(_get(row, "F7CODMED") or "").strip().upper() or "NIU")
    unidad_medida = um_map.get(um_raw, um_raw[:8] if um_raw else "NIU")

    orden_raw = _get(row, "F3ITEM") or 1
    try:
        orden = int(orden_raw)
        if orden <= 0:
            orden = 1
    except (TypeError, ValueError):
        orden = 1

    return {
        "orden": orden,
        "codigo": codigo,
        "descripcion": descripcion,
        "unidad_medida": unidad_medida,
        "cantidad": cantidad,
        "valor_unitario": valor_unitario,
        "precio_unitario": precio_unitario,
        "descuento_pct": 0.0,
        "descuento_monto": 0.0,
        "valor_venta": valor_venta,
        "igv_pct": igv_pct,
        "igv_monto": igv_monto,
        "tipo_afectacion_igv": tipo_afect,
        "isc_pct": 0.0,
        "isc_monto": 0.0,
        "icbper_monto": 0.0,
        "total_linea": total_linea,
    }


# ---------------------------------------------------------------------------
# Helpers para identificar el comprobante "padre" de un detalle
# ---------------------------------------------------------------------------
def clave_comprobante_de_detalle(row: Any) -> Optional[tuple[str, str, int]]:
    """Devuelve la tupla (tipo, serie, correlativo) que identifica el
    comprobante padre de una fila TBVENTA_DET. None si no es válido.
    """
    tipo = normalizar_tipo_comprobante(_get(row, "F4TIPODOCU"))
    serie = (str(_get(row, "F4SERDOC") or "").strip())[:4]
    if not tipo or not serie:
        return None
    try:
        num = int(str(_get(row, "F4NUMDOC") or "0").strip())
    except (TypeError, ValueError):
        return None
    if num <= 0:
        return None
    return tipo, serie, num
