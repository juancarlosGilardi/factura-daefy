"""Mappers GECOPE DBF → dicts compatibles con Factura-mdb.

Análogo a `mdb_importer/mappers.py` pero para los esquemas de GECOPE/VFP9
(empresa DAEFY). Convenciones clave de GECOPE:

* `cliente.CODIGO` (VARCHAR(20)) es el documento (RUC/DNI/CE/Pas).
  Para DNI/CE/Pas viene con padding 0 hasta 11 chars (ej. "10000699654"
  significa DNI=00699654 con prefijo "1" + ceros).
* `cliente.COD_TIPO_D` (CHAR(1)) es el código SUNAT directo: '1' DNI,
  '6' RUC, '4' CE, '7' Pas, '0' S/D, 'A','B','C','D','E'.
* `articulo.UNIDAD_MED` (CHAR(3)) es el código SUNAT (NIU, ZZ, KGM, …),
  ya sin mapeo legacy (a diferencia de SIAP).
* `ventas.DOCUMENTO` (VARCHAR(6)) es código interno GECOPE:
    '000001' = Factura  → SUNAT '01'
    '000002' = Boleta   → SUNAT '03'
    '000003' = NC       → SUNAT '07'
    '000004' = ND       → SUNAT '08'
* `ventas.MONEDA` (texto): "Soles" | "Dolares" | "Euros".
* `ventas.NUM_DOCUME` (VARCHAR(8)) viene con padding (ej. "00000001").
* `ventas_detalle.CODIGO` (N 8) es FK a `ventas.CODIGO` (no es el item
  number — éste se infiere por orden de aparición o queda None).
"""
from __future__ import annotations

import logging
from datetime import date as _date, datetime
from typing import Any, Optional

from ..mdb_importer.validators import (
    inferir_tipo_documento,
    normalizar_codigo,
    normalizar_descripcion,
    normalizar_email,
    normalizar_telefono,
    validar_fecha,
    validar_monto,
)

logger = logging.getLogger("factura_mdb.dbf.mappers")


# ---------------------------------------------------------------------------
# Diccionarios de mapeo GECOPE ↔ SUNAT/Factura-mdb
# ---------------------------------------------------------------------------

# DOCUMENTO interno GECOPE → cat. 01 SUNAT
GECOPE_DOC_A_SUNAT: dict[str, str] = {
    "000001": "01",  # Factura
    "000002": "03",  # Boleta
    "000003": "07",  # Nota de Crédito
    "000004": "08",  # Nota de Débito
}
SUNAT_A_GECOPE_DOC: dict[str, str] = {v: k for k, v in GECOPE_DOC_A_SUNAT.items()}
SUNAT_A_GECOPE_NOMBRE: dict[str, str] = {
    "01": "Factura",
    "03": "Boleta de Venta",
    "07": "Nota de Crédito",
    "08": "Nota de Débito",
}

# GECOPE MONEDA texto → ISO 4217
GECOPE_MONEDA: dict[str, str] = {
    "Soles": "PEN",
    "Dolares": "USD",
    "Dólares": "USD",
    "Euros": "EUR",
    "PEN": "PEN",
    "USD": "USD",
    "EUR": "EUR",
    "S": "PEN",
    "D": "USD",
}
SUNAT_MONEDA_A_GECOPE: dict[str, str] = {
    "PEN": "Soles",
    "USD": "Dolares",
    "EUR": "Euros",
}


def _g(row: Any, key: str, default: Any = None) -> Any:
    """Acceso defensivo a un campo de la fila DBF.

    `dbfread` devuelve `OrderedDict` con keys en uppercase. Toleramos
    también acceso por atributo (algunos tests envuelven con DictNS).
    """
    if isinstance(row, dict):
        return row.get(key, default)
    return getattr(row, key, default)


def _safe_str(v: Any, max_len: Optional[int] = None) -> str:
    """Strip + truncate, tolerando None y bytes."""
    if v is None:
        return ""
    if isinstance(v, bytes):
        try:
            v = v.decode("cp1252", errors="replace")
        except Exception:  # noqa: BLE001
            v = ""
    s = str(v).strip()
    if max_len is not None:
        s = s[:max_len]
    return s


# ---------------------------------------------------------------------------
# Cliente — cliente.dbf
# ---------------------------------------------------------------------------

def dbf_cliente_to_dict(row: Any) -> Optional[dict]:
    """Mapea una fila de `cliente.dbf` a dict ClienteOut.

    Reglas:
        - Sin CODIGO o sin NOMBRE → None (skip).
        - tipo_documento = COD_TIPO_D (código SUNAT directo).
        - El CODIGO se almacena tal cual (con padding GECOPE). Para
          el dict Factura-mdb removemos espacios y dejamos el documento
          puro.
    """
    from ...core.db_adapter.mdb_repo import _synth_id

    codigo = _safe_str(_g(row, "CODIGO"), 20)
    nombre = _safe_str(_g(row, "NOMBRE"), 200)
    if not codigo or not nombre:
        return None

    tipo_raw = _safe_str(_g(row, "COD_TIPO_D"), 1) or "0"
    # Normalizamos: cualquier código no estándar → '0' (S/D)
    if tipo_raw not in ("0", "1", "4", "6", "7", "A", "B", "C", "D", "E"):
        # Si parece RUC (11 dígitos empieza por 10/15/16/17/20) inferimos
        if codigo.isdigit() and len(codigo) == 11 and codigo[:2] in (
            "10", "15", "16", "17", "20"
        ):
            tipo_raw = "6"
        else:
            tipo_raw = inferir_tipo_documento(codigo) or "0"

    direccion = _safe_str(_g(row, "DIRECCION"), 300)
    if direccion in ("-", ".", "S/D", "SD", "N/A"):
        direccion = ""

    telefono = normalizar_telefono(_g(row, "TELEFONO")) or _safe_str(
        _g(row, "CELULAR"), 30
    ) or None
    email = normalizar_email(_g(row, "COR_ELECTR"))
    ubigeo = _safe_str(_g(row, "CODIGO_UBI"), 6) or None
    distrito = _safe_str(_g(row, "DISTRITO"), 35) or None
    provincia = _safe_str(_g(row, "PROVINCIA"), 35) or None
    departamento = _safe_str(_g(row, "DEPARTAMEN"), 20) or None

    anulado = bool(_g(row, "ANULADO"))

    return {
        "id": _synth_id("cli_dbf", codigo),
        "tipo_documento": tipo_raw,
        "numero_documento": codigo,
        "razon_social": nombre,
        "direccion": direccion or None,
        "telefono": telefono,
        "email": email,
        "ubigeo": ubigeo,
        "distrito": distrito,
        "provincia": provincia,
        "departamento": departamento,
        "activo": not anulado,
        "created_at": None,
        "updated_at": None,
    }


# ---------------------------------------------------------------------------
# Producto — articulo.dbf
# ---------------------------------------------------------------------------

def dbf_producto_to_dict(row: Any) -> Optional[dict]:
    """Mapea una fila de `articulo.dbf` a dict ProductoOut.

    Notas:
        - UNIDAD_MED es código SUNAT directo (NIU, ZZ, KGM, …).
        - EXONERADO=True → tipo_afectacion '20'.
        - INAFECTO no existe en articulo (sí en ventas_detalle); si no
          hay flag, default a '10' (gravado).
        - El producto se considera siempre PEN (GECOPE no guarda moneda
          a nivel de artículo en articulo.dbf — usa COSTO en moneda
          base de la empresa).
    """
    from ...core.db_adapter.mdb_repo import _synth_id

    codigo = _safe_str(_g(row, "CODIGO"), 40)
    nombre = _safe_str(_g(row, "NOMBRE"), 300)
    if not codigo or not nombre:
        return None

    unidad = _safe_str(_g(row, "UNIDAD_MED"), 8) or "NIU"
    if unidad:
        unidad = unidad.upper()
    costo = validar_monto(_g(row, "COSTO"), 0.0)

    exonerado = bool(_g(row, "EXONERADO"))
    anulado = bool(_g(row, "ANULADO"))

    # Afectación: EXONERADO toma prioridad. Sin más flags, default '10'.
    afect = "20" if exonerado else "10"

    return {
        "id": _synth_id("prod_dbf", codigo),
        "codigo": codigo,
        # Alias para compatibilidad con frontend React (espera 'nombre' y 'precio')
        "nombre": nombre,
        "descripcion": nombre,
        "unidad_medida": unidad,
        "precio": costo,
        "valor_unitario": costo,
        "moneda": "PEN",
        "categoria": "General",
        "tipo_afectacion_igv": afect,
        "incluye_igv": False,
        "tiene_isc": False,
        "isc_tasa": 0.0,
        "tiene_icbper": False,
        "peso_kg": 0.0,
        "activo": not anulado,
        "notas": None,
        "created_at": None,
        "updated_at": None,
    }


# ---------------------------------------------------------------------------
# Comprobante — ventas.dbf
# ---------------------------------------------------------------------------

def _decidir_estado_gecope(row: Any) -> str:
    """Calcula el estado Factura-mdb (E/A/T/R/B) a partir de los flags GECOPE.

    Heurística refinada para el flujo de emisión en 2 pasos:
        - REGISTRO_A / DATA_BAJA truthy → 'B' (anulado / dado de baja).
        - RPTA=True + CODIGO_HAS no vacío → 'A' (SUNAT aceptó CDR=0).
        - RPTA=True + MOTIVO_BAJ con prefijo "[R]" o "rechaz" → 'R'.
        - DATA=True + FIRMA=True + RPTA=False (con MOTIVO_BAJ "[T]" o sin él):
            → 'T' (timeout/error transitorio, robot lo reintenta).
        - DATA=False y FIRMA=False y RPTA=False → 'E' (emitido local,
          aun no se envio nada a SUNAT).
        - Fallback (RPTA=True sin CODIGO_HAS y sin MOTIVO) → 'R'.
    """
    if bool(_g(row, "REGISTRO_A")) or bool(_g(row, "DATA_BAJA")):
        return "B"

    rpta = bool(_g(row, "RPTA"))
    data = bool(_g(row, "DATA"))
    firma = bool(_g(row, "FIRMA"))
    code_hash = _safe_str(_g(row, "CODIGO_HAS"))
    motivo = _safe_str(_g(row, "MOTIVO_BAJ"))
    motivo_low = motivo.lower()

    if rpta and code_hash:
        return "A"
    if rpta and (motivo.startswith("[R]") or "rechaz" in motivo_low):
        return "R"
    if rpta and not code_hash:
        # SUNAT respondio pero sin hash: lo tratamos como rechazo
        return "R"
    if (data or firma) and not rpta:
        # Se intentó enviar pero no se obtuvo respuesta valida
        return "T"
    # Sin DATA/FIRMA ni RPTA → recien creado en modo emitir-local
    return "E"


def dbf_comprobante_to_dict(row: Any) -> Optional[dict]:
    """Mapea una fila de `ventas.dbf` a dict ComprobanteListItem/Out.

    No incluye detalles. El llamador asocia detalles aparte por la
    FK CODIGO ↔ ventas_detalle.CODIGO.
    """
    from ...core.db_adapter.mdb_repo import _synth_id

    documento_raw = _safe_str(_g(row, "DOCUMENTO"), 6)
    tipo = GECOPE_DOC_A_SUNAT.get(documento_raw)
    if not tipo:
        return None

    serie = _safe_str(_g(row, "SER_DOCUME"), 4)
    if not serie:
        return None

    num_raw = _safe_str(_g(row, "NUM_DOCUME"), 8) or "0"
    try:
        correlativo = int(num_raw)
    except (TypeError, ValueError):
        return None
    if correlativo <= 0:
        return None

    fecha = validar_fecha(_g(row, "FECHA_EMIS"))
    if not fecha:
        return None

    moneda_raw = _safe_str(_g(row, "MONEDA"), 7) or "Soles"
    moneda = GECOPE_MONEDA.get(moneda_raw, "PEN")
    tc = validar_monto(_g(row, "TIPO_CAMBI") or _g(row, "TIPO_CAMB"), 1.0)
    if tc <= 0:
        tc = 1.0

    # Cliente snapshot
    cli_doc = _safe_str(_g(row, "CLIENTE"), 20) or "00000000"
    cli_razon = _safe_str(_g(row, "NOMBRE_CLI"), 200) or "VARIOS"
    cli_dir = _safe_str(_g(row, "DIRECCION"), 300)
    if cli_dir in ("-", ".", "S/D", "SD", "N/A"):
        cli_dir = ""
    td_cli = inferir_tipo_documento(cli_doc)

    # Totales
    sub = validar_monto(_g(row, "BASE_IMPON"), 0.0)
    igv = validar_monto(_g(row, "IGV"), 0.0)
    total = validar_monto(_g(row, "TOTAL"), sub + igv)
    inaf = validar_monto(_g(row, "VENTAS_INA"), 0.0)
    exo = validar_monto(_g(row, "VENTAS_EXO"), 0.0)
    grat = validar_monto(_g(row, "VENTAS_GRA"), 0.0)
    expo = validar_monto(_g(row, "VENTAS_EXP"), 0.0)
    total_pen = round(total * tc, 2) if moneda == "USD" else total

    estado = _decidir_estado_gecope(row)
    cdr_raw = _safe_str(_g(row, "MOTIVO_BAJ")) if estado in ("R", "B", "T") else ""
    cdr_descripcion = cdr_raw[:500] if cdr_raw else None
    if estado == "A":
        cdr_codigo = "0"
    elif estado == "R":
        # Extraer codigo del prefijo "[R][<codigo>]"
        cdr_codigo = None
        if cdr_raw.startswith("[R][") and "]" in cdr_raw[4:]:
            try:
                cdr_codigo = cdr_raw[4:].split("]", 1)[0]
            except Exception:
                cdr_codigo = None
    else:
        cdr_codigo = None

    forma_pago_raw = _safe_str(_g(row, "FORMA_PAGO"), 7)
    forma_pago = "Credito" if forma_pago_raw.lower().startswith("cr") else "Contado"
    if tipo == "07":
        forma_pago = "Contado"

    # Detracción (catálogo 54): GECOPE guarda DETRACCION (CHAR(3))
    det_codigo_raw = _safe_str(_g(row, "DETRACCION"), 3) or None
    det_monto_raw = validar_monto(_g(row, "MONTO_DETR"), 0.0)
    if det_codigo_raw and det_monto_raw > 0:
        det_codigo = det_codigo_raw
        det_monto = det_monto_raw
        # Porcentaje: si el campo numérico DETRACCIO3 existe, úsalo
        det_pct = validar_monto(_g(row, "DETRACCIO3"), 0.0) or None
    else:
        det_codigo = None
        det_pct = None
        det_monto = None

    # Doc referencia para NC/ND
    doc_ref_tipo = None
    doc_ref_serie = None
    doc_ref_motivo = None
    if tipo in ("07", "08"):
        ref_serie = _safe_str(_g(row, "SER_REFERE"), 4)
        ref_num_raw = _safe_str(_g(row, "NUM_REFERE"), 8) or "0"
        try:
            ref_num = int(ref_num_raw)
        except (TypeError, ValueError):
            ref_num = 0
        # Tipo del doc referenciado: GECOPE guarda el código interno en
        # REFERENCIA (texto) o se infiere por la serie (F* → '01', B* → '03').
        ref_tipo = None
        if ref_serie:
            if ref_serie.upper().startswith("F"):
                ref_tipo = "01"
            elif ref_serie.upper().startswith("B"):
                ref_tipo = "03"
        if ref_tipo and ref_serie and ref_num > 0:
            doc_ref_tipo = ref_tipo
            doc_ref_serie = f"{ref_serie}-{ref_num:08d}"
            doc_ref_motivo = _safe_str(_g(row, "TIPO_NOTA_"), 2) or "01"

    tipo_operacion = _safe_str(_g(row, "TIPO_OPERA"), 4) or "0101"

    return {
        "id": _synth_id("comp_dbf", tipo, serie, correlativo),
        "tipo_documento": tipo,
        "serie": serie,
        "correlativo": correlativo,
        "numero_completo": f"{serie}-{correlativo:08d}",
        "fecha_emision": fecha,
        "moneda": moneda,
        "tipo_cambio": tc,
        "tipo_operacion": tipo_operacion,
        "cliente_id": None,  # se asigna externamente si se conoce
        "cliente_tipo_doc": td_cli,
        "cliente_numero_doc": cli_doc,
        "cliente_razon_social": cli_razon,
        "cliente_direccion": cli_dir or None,
        "total_gravado": sub,
        "total_exonerado": exo,
        "total_inafecto": inaf,
        "total_exportacion": expo,
        "total_gratuito": grat,
        "total_descuento": 0.0,
        "subtotal": sub,
        "total_igv": igv,
        "total_isc": 0.0,
        "total_icbper": validar_monto(_g(row, "ICBPER"), 0.0),
        "total_venta": total,
        "total_pen": total_pen,
        "detraccion_codigo": det_codigo,
        "detraccion_tasa": det_pct,
        "detraccion_monto": det_monto,
        "detraccion_cta_bn": None,
        "percepcion_pct": None,
        "percepcion_monto": None,
        "forma_pago": forma_pago,
        "forma_pago_json": None,
        "doc_referencia_tipo": doc_ref_tipo,
        "doc_referencia_serie": doc_ref_serie,
        "doc_referencia_motivo": doc_ref_motivo,
        "motivo_nc_codigo": _safe_str(_g(row, "TIPO_NOTA_"), 2) or None,
        "motivo_nc_descripcion": None,
        "estado": estado,
        "cdr_codigo": cdr_codigo,
        "cdr_descripcion": cdr_descripcion,
        "xml_path": None,
        "cdr_path": None,
        "pdf_path": None,
        "qr_data": _safe_str(_g(row, "QR_DATO"), 254) or None,
        "monto_letras": _safe_str(_g(row, "LETRAS"), 100) or None,
        "observaciones": _safe_str(_g(row, "CONCEPTO"), 100) or None,
        "fecha_vencimiento": validar_fecha(_g(row, "FECHA_VENC")),
        "hora_emision": None,
        "created_at": None,
        "updated_at": None,
        "_gecope_codigo": _g(row, "CODIGO"),  # int FK para detalles
    }


# ---------------------------------------------------------------------------
# Detalle — ventas_detalle.dbf
# ---------------------------------------------------------------------------

def dbf_detalle_to_dict(row: Any, orden: int = 1) -> Optional[dict]:
    """Mapea una fila de `ventas_detalle.dbf` a dict ComprobanteDetalleOut.

    En GECOPE:
        - PRECIO es precio unitario (al parecer SIN IGV — verificado en
          datos: PRECIO * CANTIDAD ≈ IMPORTE post descuento).
        - IMPORTE es total línea (puede incluir IGV; GECOPE no separa
          consistentemente). Tratamos PRECIO como `valor_unitario` y
          derivamos lo demás con el flag CODIGO_TRI.
        - CODIGO_TRI ('10','20','30',...) es el tipo de afectación IGV.
        - DESCUENTO1/2/3 son porcentajes.
    """
    cantidad = validar_monto(_g(row, "CANTIDAD"), 1.0)
    if cantidad <= 0:
        cantidad = 1.0

    valor_unitario = validar_monto(_g(row, "PRECIO"), 0.0)
    importe_total = validar_monto(_g(row, "IMPORTE"), 0.0)

    desc_pct = (
        validar_monto(_g(row, "DESCUENTO1"), 0.0)
        + validar_monto(_g(row, "DESCUENTO2"), 0.0)
        + validar_monto(_g(row, "DESCUENTO3"), 0.0)
    )

    afect_raw = _safe_str(_g(row, "CODIGO_TRI"), 2)
    if not afect_raw or afect_raw not in (
        "10", "11", "12", "13", "14", "15", "16", "17",
        "20", "21", "30", "31", "32", "33", "34", "35", "36",
        "40",
    ):
        # Fallback: INAFECTO=True → '30', sino '10'
        afect_raw = "30" if bool(_g(row, "INAFECTO")) else "10"
    afecto_grav = afect_raw.startswith("1")

    igv_pct = 18.0 if afecto_grav else 0.0

    # Valor venta y IGV: si afecto y total > 0, descomponemos.
    if afecto_grav:
        # PRECIO en GECOPE incluye IGV en algunas instalaciones. Para
        # ser conservadores y consistentes con la suma del cabecero:
        # tratamos `importe_total` como total línea (lo que aporta a
        # F4TOTFAC) y derivamos valor_venta = total / 1.18.
        if importe_total > 0:
            valor_venta = round(importe_total / 1.18, 6)
            igv_monto = round(importe_total - valor_venta, 6)
        else:
            valor_venta = round(valor_unitario * cantidad, 6)
            igv_monto = round(valor_venta * 0.18, 6)
        total_linea = importe_total or round(valor_venta + igv_monto, 2)
    else:
        valor_venta = importe_total or round(valor_unitario * cantidad, 6)
        igv_monto = 0.0
        total_linea = valor_venta

    # Precio unitario (con IGV) para el detalle UBL
    precio_unitario = (
        round(total_linea / cantidad, 6) if cantidad > 0 else valor_unitario
    )

    descripcion = _safe_str(_g(row, "NOMBRE_ART"), 500) or "ITEM"
    codigo = _safe_str(_g(row, "ARTICULO"), 40) or None

    # UNIDAD: en ventas_detalle es PRESENTACI (texto corto)
    unidad = _safe_str(_g(row, "PRESENTACI"), 8) or "NIU"
    if unidad:
        unidad = unidad.upper()

    return {
        "orden": orden,
        "codigo": codigo,
        "descripcion": descripcion,
        "unidad_medida": unidad,
        "cantidad": cantidad,
        "valor_unitario": valor_unitario,
        "precio_unitario": precio_unitario,
        "descuento_pct": desc_pct,
        "descuento_monto": 0.0,
        "valor_venta": valor_venta,
        "igv_pct": igv_pct,
        "igv_monto": igv_monto,
        "tipo_afectacion_igv": afect_raw,
        "isc_pct": 0.0,
        "isc_monto": validar_monto(_g(row, "ISC"), 0.0),
        "icbper_monto": 0.0,
        "total_linea": total_linea,
        "_gecope_codigo": _g(row, "CODIGO"),  # FK para agrupar por cabecera
    }


# ---------------------------------------------------------------------------
# Empresa — datos_compañia.dbf
# ---------------------------------------------------------------------------

def dbf_empresa_to_dict(row: Any) -> dict:
    """Mapea la fila única de `datos_compañia.dbf` a dict EmpresaOut.

    El campo `LOGO` es OLE binario; lo ignoramos y el caller debe
    sustituirlo por `empresa.logo_path` desde config.json.
    """
    ruc = _safe_str(_g(row, "CODIGO"), 11)
    razon = _safe_str(_g(row, "NOMBRE"), 200)
    direccion = _safe_str(_g(row, "DIRECCION"), 300) or None
    distrito = _safe_str(_g(row, "DISTRITO"), 35) or None
    provincia = _safe_str(_g(row, "PROVINCIA"), 35) or None
    departamento = _safe_str(_g(row, "DEPARTAMEN"), 20) or None
    ubigeo = _safe_str(_g(row, "CODIGO_UBI"), 6) or None
    telefono = normalizar_telefono(_g(row, "TELEFONO")) or None
    email = normalizar_email(_g(row, "COR_ELECTR"))
    sitio_web = _safe_str(_g(row, "SITIO_WEB"), 200) or None

    return {
        "ruc": ruc,
        "razon_social": razon,
        "nombre_comercial": razon,
        "direccion": direccion,
        "distrito": distrito,
        "provincia": provincia,
        "departamento": departamento,
        "ubigeo": ubigeo,
        "telefono": telefono,
        "email": email,
        "sitio_web": sitio_web,
    }
