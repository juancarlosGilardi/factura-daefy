"""Writers de escritura sobre .mdb (SIAP legacy). Sprint 2 Factura-mdb.

Escribe directamente sobre las tablas de SIAP (EF2CLIENTES, TBVENTA_CAB,
TBVENTA_DET) y sobre una tabla auxiliar `FMDB_PRODUCTOS` para mantener
el catálogo propio de productos sin contaminar el flujo SIAP.

Reglas clave:
- F2CODCLI es VARCHAR(4): generamos siguiente como str(MAX+1).
- F4NUMDOC es VARCHAR(7) con padding "0001234". Usamos `f"{n:07d}"`.
- F4TIPMON: 'S' (PEN) | 'D' (USD).
- F2TIPDOC: 'J' (jurídico/RUC) | 'N' (natural/DNI/CE/Pas).
- Estado inicial de comprobante: F4ESTNUL=False, F4ESTEMI=False,
  F4ENVIADO=False (borrador, sin enviar a SUNAT).
- Generación de F4NUMDOC: MAX+1 dentro de la misma transacción
  (BEGIN/COMMIT). NO garantiza atomicidad bajo concurrencia con SIAP
  ejecutándose en paralelo (limitación Access JET sin nivel de
  aislamiento serializable). Sprint 3 añadirá lock de aplicación.
"""
from __future__ import annotations

import logging
from datetime import date as _date, datetime
from typing import Optional, Any

from .mdb_lock import write_cursor, MDBLockTimeout

logger = logging.getLogger("factura_mdb.mdb.writer")


# ---------------------------------------------------------------------------
# Mapeos Factura-mdb ↔ SIAP
# ---------------------------------------------------------------------------
def _factura_mdb_tipodoc_to_siap(tipo: str) -> str:
    """Factura-mdb cat 06 SUNAT → F2TIPDOC SIAP.

    - '6' (RUC) → 'J' (jurídico)
    - '1' (DNI), '4' (CE), '7' (Pas), '0' (S/D), 'A','B','C' → 'N' (natural)
    """
    return "J" if tipo == "6" else "N"


def _factura_mdb_moneda_to_siap(moneda: str) -> str:
    """Factura-mdb → F4TIPMON. PEN→'S', USD→'D', EUR→'D' (no soporta) ."""
    if moneda == "USD":
        return "D"
    return "S"  # default a soles incluso EUR


def _factura_mdb_forma_pago_to_siap(forma: str) -> str:
    """Factura-mdb → F4FORPAG. 'Contado'→'001', 'Credito'→'003'."""
    return "001" if (forma or "").lower() == "contado" else "003"


# ---------------------------------------------------------------------------
# ClienteWriterMDB
# ---------------------------------------------------------------------------
class ClienteWriterMDB:
    """CRUD sobre EF2CLIENTES."""

    @staticmethod
    def crear(payload: dict) -> int:
        """INSERT en EF2CLIENTES. Devuelve el F2CODCLI generado como int.

        F2CODCLI es VARCHAR(4) sin padding (vimos '1', '1160' en datos).
        Generamos MAX(VAL(F2CODCLI))+1 y lo guardamos como str(n).
        """
        tipo = str(payload.get("tipo_documento", "6"))
        num = str(payload.get("numero_documento", "")).strip()
        razon = str(payload.get("razon_social", "")).strip()[:120]
        if not num or not razon:
            raise ValueError("numero_documento y razon_social son obligatorios")

        with write_cursor() as cur:
            # 1. Próximo F2CODCLI (numérico, sin padding observado)
            cur.execute("SELECT MAX(VAL(F2CODCLI)) FROM EF2CLIENTES")
            row = cur.fetchone()
            try:
                next_id = int(row[0] or 0) + 1
            except (TypeError, ValueError):
                next_id = 1
            f2codcli_str = str(next_id)

            # 2. Mapear campos Factura-mdb → SIAP
            f2tipdoc = _factura_mdb_tipodoc_to_siap(tipo)
            f2newruc = num if tipo == "6" else None  # solo RUC
            f2doccli = num if tipo != "6" else None  # otros docs (DNI/CE/Pas)
            direccion = (payload.get("direccion") or "")[:120] or None
            telefono = (payload.get("telefono") or "")[:30] or None
            email = (payload.get("email") or "")[:150] or None

            # ESTADO bool default True; F4ENVIADO bool default False;
            # FECREG/FECING fechas opcionales
            now = datetime.now()
            cur.execute(
                """
                INSERT INTO EF2CLIENTES (
                    F2CODCLI, F2NEWRUC, F2DOCCLI, F2NOMCLI, F2DIRCLI,
                    F2TELCLI, F2TIPDOC, F2EMAIL, ESTADO, F4ENVIADO,
                    FECING, FECREG
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    f2codcli_str,
                    f2newruc,
                    f2doccli,
                    razon,
                    direccion,
                    telefono,
                    f2tipdoc,
                    email,
                    True,
                    False,
                    now,
                    now,
                ],
            )
            logger.info("Cliente creado en EF2CLIENTES: F2CODCLI=%s razon=%s",
                         f2codcli_str, razon[:50])
            return next_id

    @staticmethod
    def actualizar(id_cliente: int, payload: dict) -> bool:
        """UPDATE EF2CLIENTES por F2CODCLI. Devuelve True si actualizó.

        Solo actualiza campos presentes en payload (semantics de PUT
        parcial / PATCH).
        """
        # Mapear campos del payload a columnas SIAP
        sets: list[str] = []
        vals: list[Any] = []

        if "razon_social" in payload and payload["razon_social"] is not None:
            sets.append("F2NOMCLI = ?")
            vals.append(str(payload["razon_social"])[:120])
        if "direccion" in payload:
            sets.append("F2DIRCLI = ?")
            v = payload["direccion"]
            vals.append(str(v)[:120] if v else None)
        if "telefono" in payload:
            sets.append("F2TELCLI = ?")
            v = payload["telefono"]
            vals.append(str(v)[:30] if v else None)
        if "email" in payload:
            sets.append("F2EMAIL = ?")
            v = payload["email"]
            vals.append(str(v)[:150] if v else None)
        if "tipo_documento" in payload and payload["tipo_documento"]:
            sets.append("F2TIPDOC = ?")
            vals.append(_factura_mdb_tipodoc_to_siap(str(payload["tipo_documento"])))
        if "numero_documento" in payload and payload["numero_documento"]:
            tipo = str(payload.get("tipo_documento") or "")
            num = str(payload["numero_documento"]).strip()
            if tipo == "6":
                sets.append("F2NEWRUC = ?")
                vals.append(num)
                sets.append("F2DOCCLI = ?")
                vals.append(None)
            else:
                sets.append("F2DOCCLI = ?")
                vals.append(num)
                sets.append("F2NEWRUC = ?")
                vals.append(None)
        if "activo" in payload and payload["activo"] is not None:
            sets.append("ESTADO = ?")
            vals.append(bool(payload["activo"]))

        if not sets:
            return False

        sets.append("FecMod = ?")
        vals.append(datetime.now())

        sql = f"UPDATE EF2CLIENTES SET {', '.join(sets)} WHERE VAL(F2CODCLI) = ?"
        vals.append(int(id_cliente))

        with write_cursor() as cur:
            cur.execute(sql, vals)
            updated = cur.rowcount
        logger.info("Cliente F2CODCLI=%d actualizado (rowcount=%d)",
                     id_cliente, updated)
        return updated > 0

    @staticmethod
    def borrar(id_cliente: int) -> bool:
        """Soft-delete: marca ESTADO=False (mismo behavior que SQLite mode)."""
        with write_cursor() as cur:
            cur.execute(
                "UPDATE EF2CLIENTES SET ESTADO = ?, FecMod = ? "
                "WHERE VAL(F2CODCLI) = ?",
                [False, datetime.now(), int(id_cliente)],
            )
            updated = cur.rowcount
        logger.info("Cliente F2CODCLI=%d desactivado (rowcount=%d)",
                     id_cliente, updated)
        return updated > 0


# ---------------------------------------------------------------------------
# ProductoWriterMDB
# ---------------------------------------------------------------------------
class ProductoWriterMDB:
    """CRUD sobre tabla auxiliar FMDB_PRODUCTOS (no contamina SIAP).

    SIAP no tiene tabla maestra de productos — sus "productos" se
    deducen del histórico de TBVENTA_DET. Para que Factura-mdb pueda crear
    productos sin afectar lecturas SIAP, mantenemos `FMDB_PRODUCTOS`
    como tabla propia dentro del .mdb (compartido).

    El listado en MDB combina ambas fuentes: ver `ProductoRepoMDB`
    extendido. Sprint 2 deja la creación funcionando contra la tabla
    auxiliar; en `ProductoRepoMDB.listar()` los productos creados aquí
    aparecen también.
    """

    _TABLE = "FMDB_PRODUCTOS"

    @staticmethod
    def asegurar_tabla() -> None:
        """CREATE TABLE IF NOT EXISTS — Access no soporta IF NOT EXISTS,
        manejamos la excepción si ya existe.
        """
        try:
            with write_cursor() as cur:
                cur.execute(
                    f"""
                    CREATE TABLE {ProductoWriterMDB._TABLE} (
                        codigo VARCHAR(40) PRIMARY KEY,
                        descripcion VARCHAR(250),
                        unidad_medida VARCHAR(10),
                        valor_unitario DOUBLE,
                        moneda VARCHAR(3),
                        tipo_afectacion_igv VARCHAR(2),
                        incluye_igv BIT,
                        notas VARCHAR(255),
                        activo BIT,
                        created_at DATETIME,
                        updated_at DATETIME
                    )
                    """
                )
                logger.info("Tabla FMDB_PRODUCTOS creada")
        except Exception as e:  # noqa: BLE001
            msg = str(e).lower()
            if (
                "ya existe" in msg
                or "already exists" in msg
                or "existe en la base" in msg
                or "exists" in msg
            ):
                logger.debug("FMDB_PRODUCTOS ya existe (ok)")
            else:
                raise

    @staticmethod
    def crear(payload: dict) -> str:
        """INSERT en FMDB_PRODUCTOS. Devuelve el código."""
        ProductoWriterMDB.asegurar_tabla()

        codigo = str(payload["codigo"]).strip()[:40]
        descripcion = str(payload["descripcion"]).strip()[:250]
        if not codigo or not descripcion:
            raise ValueError("codigo y descripcion son obligatorios")

        unidad = str(payload.get("unidad_medida") or "NIU")[:10]
        valor = float(payload.get("valor_unitario") or 0.0)
        moneda = str(payload.get("moneda") or "PEN")[:3]
        afect = str(payload.get("tipo_afectacion_igv") or "10")[:2]
        incluye = bool(payload.get("incluye_igv", False))
        notas = (str(payload.get("notas") or "") or None)
        if notas:
            notas = notas[:255]
        activo = bool(payload.get("activo", True))

        now = datetime.now()
        with write_cursor() as cur:
            cur.execute(
                f"""
                INSERT INTO {ProductoWriterMDB._TABLE} (
                    codigo, descripcion, unidad_medida, valor_unitario,
                    moneda, tipo_afectacion_igv, incluye_igv, notas,
                    activo, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    codigo, descripcion, unidad, valor, moneda, afect,
                    incluye, notas, activo, now, now,
                ],
            )
        logger.info("Producto creado en FMDB_PRODUCTOS: codigo=%s", codigo)
        return codigo

    @staticmethod
    def actualizar(codigo: str, payload: dict) -> bool:
        """UPDATE FMDB_PRODUCTOS por codigo."""
        ProductoWriterMDB.asegurar_tabla()
        sets: list[str] = []
        vals: list[Any] = []

        if "descripcion" in payload and payload["descripcion"] is not None:
            sets.append("descripcion = ?")
            vals.append(str(payload["descripcion"])[:250])
        if "unidad_medida" in payload and payload["unidad_medida"] is not None:
            sets.append("unidad_medida = ?")
            vals.append(str(payload["unidad_medida"])[:10])
        if "valor_unitario" in payload and payload["valor_unitario"] is not None:
            sets.append("valor_unitario = ?")
            vals.append(float(payload["valor_unitario"]))
        if "moneda" in payload and payload["moneda"] is not None:
            sets.append("moneda = ?")
            vals.append(str(payload["moneda"])[:3])
        if "tipo_afectacion_igv" in payload and payload["tipo_afectacion_igv"] is not None:
            sets.append("tipo_afectacion_igv = ?")
            vals.append(str(payload["tipo_afectacion_igv"])[:2])
        if "incluye_igv" in payload and payload["incluye_igv"] is not None:
            sets.append("incluye_igv = ?")
            vals.append(bool(payload["incluye_igv"]))
        if "notas" in payload:
            sets.append("notas = ?")
            v = payload["notas"]
            vals.append(str(v)[:255] if v else None)
        if "activo" in payload and payload["activo"] is not None:
            sets.append("activo = ?")
            vals.append(bool(payload["activo"]))

        if not sets:
            return False
        sets.append("updated_at = ?")
        vals.append(datetime.now())

        sql = (
            f"UPDATE {ProductoWriterMDB._TABLE} SET {', '.join(sets)} "
            "WHERE codigo = ?"
        )
        vals.append(codigo)

        with write_cursor() as cur:
            cur.execute(sql, vals)
            updated = cur.rowcount
        return updated > 0

    @staticmethod
    def borrar(codigo: str) -> bool:
        """Soft-delete."""
        ProductoWriterMDB.asegurar_tabla()
        with write_cursor() as cur:
            cur.execute(
                f"UPDATE {ProductoWriterMDB._TABLE} SET activo = ?, "
                "updated_at = ? WHERE codigo = ?",
                [False, datetime.now(), codigo],
            )
            updated = cur.rowcount
        return updated > 0


# ---------------------------------------------------------------------------
# ComprobanteWriterMDB
# ---------------------------------------------------------------------------
class ComprobanteWriterMDB:
    """CRUD sobre TBVENTA_CAB + TBVENTA_DET."""

    @staticmethod
    def crear(payload: dict, items: list[dict], totales: dict) -> dict:
        """INSERT TBVENTA_CAB + TBVENTA_DET en una transacción.

        Args:
            payload: dict con campos del comprobante (serializado de
                ComprobanteIn). Acepta keys: tipo_documento, serie,
                fecha_emision, moneda, tipo_cambio, cliente_*, etc.
            items: lista de dicts ya calculados (output de calcular_linea).
            totales: output de calcular_totales (subtotal, total_igv, etc.).

        Returns:
            dict con id sintético, numero_completo, totales y
            metadata útil para responder al frontend.
        """
        tipo = str(payload["tipo_documento"])
        serie = str(payload["serie"]).upper()[:4]
        fecha_emision = payload["fecha_emision"]
        if isinstance(fecha_emision, str):
            fecha_emision = _date.fromisoformat(fecha_emision)
        # F4FECEMI es DATETIME en TBVENTA_CAB
        if isinstance(fecha_emision, _date) and not isinstance(fecha_emision, datetime):
            fecha_emision_dt = datetime.combine(fecha_emision, datetime.min.time())
        else:
            fecha_emision_dt = fecha_emision

        moneda = str(payload.get("moneda") or "PEN")
        tipo_cambio = float(payload.get("tipo_cambio") or 1.0)
        cliente_tipo_doc = str(payload.get("cliente_tipo_doc") or "6")
        cliente_numero_doc = str(payload.get("cliente_numero_doc") or "")[:11]
        cliente_razon = str(payload.get("cliente_razon_social") or "")[:100]
        cliente_dir = (str(payload.get("cliente_direccion") or "") or "")[:120]
        forma_pago = str(payload.get("forma_pago") or "Contado")
        forma_pago_siap = _factura_mdb_forma_pago_to_siap(forma_pago)

        f4tipmon = _factura_mdb_moneda_to_siap(moneda)

        # Detracción
        det_aplica = bool(payload.get("detraccion_codigo"))
        det_pct = float(payload.get("detraccion_tasa") or 0.0) if det_aplica else None
        det_monto = float(payload.get("detraccion_monto") or 0.0) if det_aplica else None

        # Totales (input de calcular_totales)
        sub_gravado = float(totales.get("total_gravado") or 0.0)
        total_igv = float(totales.get("total_igv") or 0.0)
        sub_inafecto = float(totales.get("total_inafecto") or 0.0)
        total_exonerado = float(totales.get("total_exonerado") or 0.0)
        total_venta = float(totales.get("total_venta") or 0.0)
        subtotal = float(totales.get("subtotal") or 0.0)

        # Calcular cliente_id (F2CODCLI) si vino o por lookup por número doc
        f2codcli = None
        cli_id = payload.get("cliente_id")
        if cli_id:
            f2codcli = str(int(cli_id))

        with write_cursor() as cur:
            # 1. Próximo correlativo: MAX(VAL(F4NUMDOC))+1 para
            #    (F4SERDOC, F4TIPODOCU). Se hace dentro de la misma
            #    transacción pero Access JET no garantiza serialización
            #    contra otros procesos (limitación documentada).
            cur.execute(
                "SELECT MAX(VAL(F4NUMDOC)) FROM TBVENTA_CAB "
                "WHERE F4SERDOC = ? AND F4TIPODOCU = ?",
                [serie, tipo],
            )
            row = cur.fetchone()
            try:
                correlativo = int(row[0] or 0) + 1
            except (TypeError, ValueError):
                correlativo = 1

            # F4NUMDOC se almacena con padding 7 dígitos en SIAP
            f4numdoc = f"{correlativo:07d}"

            # Buscar F2CODCLI por documento si no vino
            if not f2codcli and cliente_tipo_doc == "6":
                cur.execute(
                    "SELECT TOP 1 F2CODCLI FROM EF2CLIENTES "
                    "WHERE F2NEWRUC = ?",
                    [cliente_numero_doc],
                )
                r = cur.fetchone()
                if r:
                    f2codcli = str(r[0]).strip()

            now = datetime.now()
            # 2. INSERT TBVENTA_CAB
            cur.execute(
                """
                INSERT INTO TBVENTA_CAB (
                    F4TIPODOCU, F4SERDOC, F4NUMDOC, F4FECEMI, F4TIPMON,
                    F4TIPCAM, F2RUCCLI, F2NOMCLI, F2DIRCLI, F2CODCLI,
                    F4SUBTOT, F4TOTIGV, F4SUBFACINAF, F4MONTOEXONERADO,
                    F4TOTFAC, F4BASIMP,
                    F4ESTNUL, F4ESTEMI, F4ENVIADO, F4ESTFAC, F4ESTVAL,
                    F4CONTABLE, F4CHECK, F4LOCAL, F4DETRACCIONAPLICA,
                    F4DETRACCIONPORC, F4DETRACCIONMONTO,
                    F4FORPAG, F4FECGRA, F4USEGRA, F4DRAWBACK,
                    F4ESTRECHAZO, F4PERCEPCION, F4VB
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                          ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    tipo, serie, f4numdoc, fecha_emision_dt, f4tipmon,
                    tipo_cambio,
                    cliente_numero_doc if cliente_tipo_doc == "6" else None,
                    cliente_razon, cliente_dir or None, f2codcli,
                    sub_gravado, total_igv, sub_inafecto, total_exonerado,
                    total_venta, sub_gravado,
                    False, False, False, False, False,
                    False, False, False, det_aplica,
                    det_pct, det_monto,
                    forma_pago_siap, now, "FMDB", False,
                    False, bool(payload.get("percepcion_pct")), False,
                ],
            )

            # 3. INSERT TBVENTA_DET por cada item
            for idx, item in enumerate(items, start=1):
                cantidad = float(item.get("cantidad") or 0)
                valor_unit = float(item.get("valor_unitario") or 0)
                valor_venta = float(item.get("valor_venta") or 0)
                igv_monto = float(item.get("igv_monto") or 0)
                total_linea = float(item.get("total_linea") or 0)
                precio_unit = float(item.get("precio_unitario") or valor_unit)
                desc = str(item.get("descripcion") or "ITEM")[:255]
                cod = (str(item.get("codigo") or "") or None)
                if cod:
                    cod = cod[:10]
                um = str(item.get("unidad_medida") or "NIU")[:3]
                # Mapeo inverso de UM Factura-mdb → SIAP códigos legacy:
                # NIU→101, KGM→102, LTR→103, MTR→104, ZZ→201
                um_siap_map = {
                    "NIU": "101", "KGM": "102", "LTR": "103",
                    "MTR": "104", "ZZ": "201",
                }
                f7codmed = um_siap_map.get(um.upper(), um[:3] or "101")
                afecto = item.get("tipo_afectacion_igv", "10") in (
                    "10", "11", "12", "13", "14", "15", "16", "17"
                )
                cur.execute(
                    """
                    INSERT INTO TBVENTA_DET (
                        F4TIPODOCU, F4SERDOC, F4NUMDOC, F5CODPRO, F5NOMPRO,
                        F7CODMED, F3CANPRO, F3VALVTAUNIT, F3PREUNI,
                        F3VALVTA, F3IGV, F3PREVTA, F3VALBRUTO, F3PREBRU,
                        F3AFECTO, F3ITEM, F4FECEMI, F4TIPMON
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    [
                        tipo, serie, f4numdoc, cod, desc,
                        f7codmed, cantidad, valor_unit, precio_unit,
                        valor_venta, igv_monto, total_linea,
                        valor_venta, precio_unit,
                        afecto, idx, fecha_emision_dt, f4tipmon,
                    ],
                )

            logger.info(
                "Comprobante creado en MDB: %s-%s items=%d total=%.2f",
                serie, f4numdoc, len(items), total_venta,
            )

        # ID sintético (mismo crc32 que ComprobanteRepoMDB._row_to_comprobante_dict)
        from .mdb_repo import _synth_id
        sid = _synth_id("comp", tipo, serie, correlativo)
        return {
            "id": sid,
            "tipo_documento": tipo,
            "serie": serie,
            "correlativo": correlativo,
            "numero_completo": f"{serie}-{correlativo:08d}",
            "fecha_emision": fecha_emision if isinstance(fecha_emision, _date)
                              else fecha_emision_dt.date(),
            "moneda": moneda,
            "tipo_cambio": tipo_cambio,
            "tipo_operacion": payload.get("tipo_operacion", "0101"),
            "cliente_id": int(f2codcli) if f2codcli and str(f2codcli).isdigit() else None,
            "cliente_tipo_doc": cliente_tipo_doc,
            "cliente_numero_doc": cliente_numero_doc,
            "cliente_razon_social": cliente_razon,
            "cliente_direccion": cliente_dir or None,
            "forma_pago": forma_pago,
            "forma_pago_json": payload.get("forma_pago_json"),
            "total_gravado": sub_gravado,
            "total_exonerado": total_exonerado,
            "total_inafecto": sub_inafecto,
            "total_exportacion": float(totales.get("total_exportacion") or 0),
            "total_gratuito": float(totales.get("total_gratuito") or 0),
            "total_descuento": float(totales.get("total_descuento") or 0),
            "subtotal": subtotal,
            "total_igv": total_igv,
            "total_isc": float(totales.get("total_isc") or 0),
            "total_icbper": float(totales.get("total_icbper") or 0),
            "total_venta": total_venta,
            "total_pen": float(totales.get("total_pen") or total_venta),
            "detraccion_codigo": payload.get("detraccion_codigo"),
            "detraccion_tasa": det_pct,
            "detraccion_monto": det_monto,
            "detraccion_cta_bn": payload.get("detraccion_cta_bn"),
            "percepcion_pct": payload.get("percepcion_pct"),
            "percepcion_monto": payload.get("percepcion_monto"),
            "doc_referencia_tipo": payload.get("documento_referencia_tipo"),
            "doc_referencia_serie": payload.get("documento_referencia_serie"),
            "motivo_nc_codigo": payload.get("motivo_codigo"),
            "motivo_nc_descripcion": payload.get("motivo_descripcion"),
            "estado": "P",  # estado Factura-mdb pendiente (sin enviar a SUNAT)
            "cdr_codigo": None,
            "cdr_descripcion": None,
            "xml_path": None,
            "cdr_path": None,
            "pdf_path": None,
            "qr_data": None,
            "monto_letras": None,
            "observaciones": payload.get("observaciones"),
            "fecha_vencimiento": payload.get("fecha_vencimiento"),
            "hora_emision": payload.get("hora_emision"),
            "created_at": None,
            "updated_at": None,
            "detalles": [],  # el caller debe re-leer si necesita los detalles
        }

    @staticmethod
    def anular(comprobante_id: int, motivo: str) -> bool:
        """Marca F4ESTNUL=True. Busca por id sintético recorriendo CAB.

        Como no hay PK numérica, debemos identificar la tupla
        (F4TIPODOCU, F4SERDOC, F4NUMDOC) que corresponda al id.
        """
        from .mdb_repo import _synth_id
        # Necesitamos el id sintético = crc32("comp|{tipo}|{serie}|{correlativo}")
        # Como no es invertible, recorremos TBVENTA_CAB hasta encontrar match.
        with write_cursor() as cur:
            cur.execute(
                "SELECT F4TIPODOCU, F4SERDOC, F4NUMDOC FROM TBVENTA_CAB "
                "WHERE (F4ESTNUL = FALSE OR F4ESTNUL IS NULL)"
            )
            rows = cur.fetchall()
            target = None
            for r in rows:
                tipo = (r[0] or "").strip()
                serie = (r[1] or "").strip()[:4]
                num_raw = str(r[2] or "0").strip()
                try:
                    correlativo = int(num_raw)
                except (TypeError, ValueError):
                    continue
                sid = _synth_id("comp", tipo, serie, correlativo)
                if sid == int(comprobante_id):
                    target = (tipo, r[1], r[2])
                    break

            if target is None:
                logger.warning("anular: comprobante id=%d no encontrado",
                                comprobante_id)
                return False

            tipo_v, serie_v, numero_v = target
            motivo_clean = (motivo or "").strip()[:200]
            cur.execute(
                "UPDATE TBVENTA_CAB SET F4ESTNUL = ?, F4OBSERVA = ?, "
                "F4FECMOD = ?, F4USEMOD = ? "
                "WHERE F4TIPODOCU = ? AND F4SERDOC = ? AND F4NUMDOC = ?",
                [
                    True,
                    f"[Anulado Factura-mdb] {motivo_clean}",
                    datetime.now(),
                    "FMDB",
                    tipo_v, serie_v, numero_v,
                ],
            )
            updated = cur.rowcount
        logger.info("Comprobante %s-%s anulado (rowcount=%d)",
                     serie_v, numero_v, updated)
        return updated > 0
