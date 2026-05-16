"""Writer DBF — escritura de comprobantes en ventas.dbf + ventas_detalle.dbf.

Usa la libreria `dbf` (Ethan Furman) que es mas permisiva con FPTs
placeholder y campos memo G corruptos. Es simetrico a `dbf_repo.py`.

Convenciones GECOPE clave (del sistema VFP9 DAEFY):
    - ventas.CODIGO: numerico int autoincremental (no es PK declarada,
      pero usado como FK desde ventas_detalle.CODIGO).
    - ventas.NUM_DOCUME: VARCHAR(8) con padding "00000001".
    - ventas.SER_DOCUME: VARCHAR(4) "F001", "B001", etc.
    - ventas.DOCUMENTO: codigo interno '000001'=Factura, '000002'=Boleta,
      '000003'=NC, '000004'=ND.
    - ventas.MONEDA: texto "Soles", "Dolares", "Euros".
    - ventas.FORMA_PAGO: "Contado" | "Credito".

Tras INSERT se invoca `pack()`+`reindex()` via CodeBase si esta disponible
para regenerar CDX (indice). Si no, las lecturas siguientes funcionan
igual ya que el reader usa la libreria `dbf` que soporta tablas sin CDX.
"""
from __future__ import annotations

import logging
from datetime import date as _date, datetime
from typing import Any, Optional

import dbf as _dbflib

from . import get_dbf_path
from ...services.dbf_importer.mappers import (
    SUNAT_A_GECOPE_DOC, SUNAT_A_GECOPE_NOMBRE, SUNAT_MONEDA_A_GECOPE,
)

logger = logging.getLogger("factura_mdb.dbf.writer")

DBF_ENC = "cp1252"


def _open_table(name: str, mode=None):
    """Abre una tabla DBF en modo escritura."""
    path = get_dbf_path() / name
    if not path.exists():
        for child in path.parent.iterdir():
            if child.name.lower() == name.lower():
                path = child
                break
    if not path.exists():
        raise RuntimeError(f"DBF no encontrado: {path}")
    t = _dbflib.Table(str(path), codepage=DBF_ENC)
    t.open(mode=mode or _dbflib.READ_WRITE)
    return t


def _next_codigo_ventas() -> int:
    """Calcula el proximo CODIGO (autoincremental) en ventas.dbf."""
    t = _open_table("ventas.dbf", mode=_dbflib.READ_ONLY)
    try:
        max_cod = 0
        for r in t:
            try:
                c = int(r["CODIGO"] or 0)
                if c > max_cod:
                    max_cod = c
            except Exception:
                continue
        return max_cod + 1
    finally:
        t.close()


def _next_correlativo(serie: str, tipo_documento: str) -> int:
    """Calcula el proximo NUM_DOCUME para (SER_DOCUME, DOCUMENTO).

    Reusa la logica del reader pero la duplicamos para no acoplar.
    """
    serie_n = (serie or "").upper()[:4]
    gecope_doc = SUNAT_A_GECOPE_DOC.get(tipo_documento, "000001")
    t = _open_table("ventas.dbf", mode=_dbflib.READ_ONLY)
    try:
        ultimo = 0
        for r in t:
            try:
                if (r["SER_DOCUME"] or "").strip() != serie_n:
                    continue
                if (r["DOCUMENTO"] or "").strip() != gecope_doc:
                    continue
                num_raw = (r["NUM_DOCUME"] or "0").strip()
                n = int(num_raw)
                if n > ultimo:
                    ultimo = n
            except Exception:
                continue
        return ultimo + 1
    finally:
        t.close()


def _f_or_default(t, name: str, value: Any, default: Any = None) -> Any:
    """Devuelve value si el campo existe en la tabla; sino None.

    Util para tolerar diferencias de schema entre instalaciones GECOPE.
    """
    try:
        if name in t.field_names:
            return value
    except Exception:
        pass
    return default


class ComprobanteWriterDBF:
    """Escritura de comprobantes en ventas.dbf + ventas_detalle.dbf."""

    @staticmethod
    def proximo_correlativo(serie: str, tipo_documento: str = "01") -> dict:
        n = _next_correlativo(serie, tipo_documento)
        return {
            "serie": (serie or "").upper()[:4],
            "proximo_correlativo": n,
            "ultimo_emitido": (n - 1) if n > 1 else None,
        }

    @staticmethod
    def crear(payload: dict, items: list[dict], totales: dict) -> dict:
        """Inserta cabecera en ventas.dbf y detalles en ventas_detalle.dbf.

        Args:
            payload: dict con campos del comprobante (tipo_documento,
                serie, fecha_emision, moneda, cliente_*, etc.).
            items: lista de dicts ya calculados por linea (codigo,
                descripcion, cantidad, valor_unitario, igv_monto, etc.).
            totales: output de calcular_totales (subtotal, total_igv,
                total_venta, ...).

        Returns:
            dict listo para serializar como ComprobanteOut.
        """
        tipo = str(payload.get("tipo_documento") or "01")
        serie = str(payload.get("serie") or "F001").upper()[:4]
        fecha_emision = payload.get("fecha_emision") or _date.today()
        if isinstance(fecha_emision, str):
            try:
                fecha_emision = _date.fromisoformat(fecha_emision)
            except ValueError:
                fecha_emision = _date.today()
        if isinstance(fecha_emision, datetime):
            fecha_emision = fecha_emision.date()

        moneda_iso = str(payload.get("moneda") or "PEN").upper()
        moneda_gecope = SUNAT_MONEDA_A_GECOPE.get(moneda_iso, "Soles")
        gecope_doc = SUNAT_A_GECOPE_DOC.get(tipo, "000001")
        nombre_doc = SUNAT_A_GECOPE_NOMBRE.get(tipo, "Factura")

        cliente_doc = str(payload.get("cliente_numero_doc") or "")[:20]
        cliente_razon = str(payload.get("cliente_razon_social") or "")[:160]
        cliente_dir = str(payload.get("cliente_direccion") or "")[:160]
        forma_pago = str(payload.get("forma_pago") or "Contado")
        if isinstance(payload.get("forma_pago_json"), dict):
            tipo_pago = (payload["forma_pago_json"].get("tipo") or "").lower()
            forma_pago = "Credito" if tipo_pago == "credito" else "Contado"

        tipo_operacion = str(payload.get("tipo_operacion") or "0101")[:4]

        # Totales
        sub = float(totales.get("total_gravado") or 0.0)
        igv = float(totales.get("total_igv") or 0.0)
        total = float(totales.get("total_venta") or 0.0)
        inaf = float(totales.get("total_inafecto") or 0.0)
        exo = float(totales.get("total_exonerado") or 0.0)
        grat = float(totales.get("total_gratuito") or 0.0)
        expo = float(totales.get("total_exportacion") or 0.0)
        descuento = float(totales.get("total_descuento") or 0.0)
        icbper = float(totales.get("total_icbper") or 0.0)
        isc = float(totales.get("total_isc") or 0.0)

        # Detraccion
        det_codigo = payload.get("detraccion_codigo")
        det_monto = float(payload.get("detraccion_monto") or 0.0) if det_codigo else 0.0

        # Doc referencia (NC/ND)
        ref_serie = ""
        ref_num = 0
        ref_full = payload.get("doc_referencia_serie") or payload.get("documento_referencia_serie") or ""
        if ref_full and "-" in ref_full:
            try:
                _s, _n = ref_full.split("-", 1)
                ref_serie = _s.strip().upper()[:4]
                ref_num = int(_n.strip())
            except Exception:
                pass

        # 1. Calcular CODIGO autoincremental + correlativo
        codigo_ventas = _next_codigo_ventas()
        correlativo = _next_correlativo(serie, tipo)
        # Padding configurable: SUNAT estándar = 8, GECOPE/IDIVSA legacy = 7.
        # Auto-detecta el ancho de los DBFs existentes (toma el último NUM_DOCUME).
        try:
            t_check = _open_table("ventas.dbf")
            try:
                widths = {len((r["NUM_DOCUME"] or "").strip()) for r in t_check if (r["NUM_DOCUME"] or "").strip().isdigit()}
                pad = max(widths) if widths else 8
            finally:
                t_check.close()
        except Exception:
            pad = 8
        num_documento = f"{correlativo:0{pad}d}"

        # 2. INSERT en ventas.dbf
        t = _open_table("ventas.dbf")
        try:
            field_names = set(t.field_names)
            row_data: dict[str, Any] = {}

            def _put(field: str, value: Any) -> None:
                if field in field_names:
                    row_data[field] = value

            _put("CODIGO", codigo_ventas)
            _put("NO", codigo_ventas)
            _put("DOCUMENTO", gecope_doc)
            _put("NOMBRE_DOC", nombre_doc[:30])
            _put("SER_DOCUME", serie)
            _put("NUM_DOCUME", num_documento)
            _put("FECHA_EMIS", fecha_emision)
            _put("CLIENTE", cliente_doc)
            _put("NOMBRE_CLI", cliente_razon)
            _put("DIRECCION", cliente_dir)
            _put("EMPLEADO", str(payload.get("codigo_trabajador") or "")[:15])
            _put("MONEDA", moneda_gecope)
            _put("IMPORTE_BR", sub)
            _put("TOTAL_BRUT", sub + descuento)
            _put("POR_DES", 0.0)
            _put("DESCUENTO", descuento)
            _put("SUBTOTAL", sub)
            _put("ISC", isc)
            _put("BASE_IMPON", sub)
            _put("POR_IGV", 18.0)
            _put("IGV", igv)
            _put("TOTAL", total)
            _put("FECHA_VENC", payload.get("fecha_vencimiento") or fecha_emision)
            _put("SALDO", total)
            _put("CANCELADO", forma_pago.lower() == "contado")
            _put("FORMA_PAGO", forma_pago[:7])
            _put("CONCEPTO", str(payload.get("observaciones") or payload.get("detalle_adicional") or "")[:100])
            _put("LETRAS", str(payload.get("monto_letras") or "")[:100])
            _put("ZONA_CODIG", "")
            _put("LUGAR", "")
            _put("USER", "FACTURA")
            _put("USER_FECHA", datetime.now())
            _put("VENTAS_AFE", sub)
            _put("VENTAS_INA", inaf)
            _put("VENTAS_EXO", exo)
            _put("VENTAS_GRA", grat)
            _put("VENTAS_EXP", expo)
            _put("PAGA", total if forma_pago.lower() == "contado" else 0.0)
            _put("PAGA_MONED", moneda_gecope[:7])
            _put("SALDO_SOLE", 0.0)
            _put("REPAGA_SOL", 0.0)
            _put("VUELTO_SOL", 0.0)
            _put("EXPORTACIO", tipo_operacion in ("0200", "0201", "0202", "0203", "0204"))
            # Estado inicial = E (Emitido local, NO enviado).
            # Flags DBF que mapean: DATA/FIRMA/RPTA/CODIGO_HAS = false → E.
            _put("DATA", False)        # firmado XML
            _put("FIRMA", False)       # firmado
            _put("RPTA", False)        # SUNAT respondio
            _put("PDF_SUNAT", False)
            _put("PDF_GECOPE", False)
            _put("DATA_BAJA", False)
            _put("FIRMA_BAJA", False)
            _put("RPTA_BAJA", False)
            _put("REGISTRO_A", False)  # No anulado/baja
            _put("TIPO_NOTA_", str(payload.get("motivo_codigo") or payload.get("doc_referencia_motivo") or "")[:2])
            _put("TIPO_NOTA2", "")
            _put("TIPO_OPERA", tipo_operacion)
            _put("CODIGO_HAS", "")
            _put("MOTIVO_BAJ", "")
            _put("TICKET_BAJ", "")
            _put("FECHA_ENVI", fecha_emision)
            _put("ARCHIVO_BA", "")
            _put("FLETE", 0.0)
            _put("SEGURO", 0.0)
            _put("OTROS_CARG", 0.0)
            _put("OTROS_TRIB", 0.0)
            _put("ANTICIPO", 0.0)
            _put("ESTABLECIM", "0000")
            _put("QR_DATO", "")
            _put("TRIBUTO_NC", "")
            _put("ICBPER", icbper)
            _put("PAGO_ANTIC", False)
            _put("DEDUCE_ANT", False)
            _put("ANTICIPO_U", False)
            _put("ANTICIPO_D", "")
            _put("MEDIOS_PAG", "")
            _put("MONTO_DETR", det_monto)
            _put("CODIGO_PAI", "PE")
            _put("CODIGO_UBI", "")
            _put("OPCION_PAG", "")
            _put("MONTO_PEND", 0.0 if forma_pago.lower() == "contado" else total)
            _put("NRO_CUOTAS", 0)
            _put("PAGO_CREDI", "")
            _put("MONTO_RETE", 0.0)
            _put("LISTA_PREC", 0)
            _put("ACTUALIZAR", False)
            _put("REFERENCIA", ref_full[:30] if ref_full else "")
            _put("SER_REFERE", ref_serie)
            _put("NUM_REFERE", f"{ref_num:08d}" if ref_num > 0 else "")
            _put("PROFORMA", str(payload.get("numero_cotizacion") or "")[:13])
            _put("ORDEN_COMP", str(payload.get("orden_compra") or "")[:30])
            _put("SELECCION", 0)
            _put("COMENTARIO", "")
            _put("COMISION", 0.0)
            _put("FECHA_CANC", fecha_emision)
            _put("GUIA", "")

            t.append(row_data)
            logger.info(
                "Comprobante DBF insertado: %s-%s codigo=%d total=%.2f",
                serie, num_documento, codigo_ventas, total,
            )
        finally:
            t.close()

        # 3. INSERT en ventas_detalle.dbf
        td = _open_table("ventas_detalle.dbf")
        try:
            det_field_names = set(td.field_names)
            for idx, item in enumerate(items, start=1):
                cantidad = float(item.get("cantidad") or 0)
                valor_unit = float(item.get("valor_unitario") or item.get("precio_unitario") or 0)
                importe = float(item.get("total_linea") or item.get("total") or 0)
                desc1 = float(item.get("descuento_pct") or 0)
                desc2 = float(item.get("descuento2_pct") or 0)
                desc3 = float(item.get("descuento3_pct") or 0)
                isc_linea = float(item.get("isc_monto") or item.get("isc") or 0)
                cod_art = str(item.get("codigo") or "")[:20]
                desc_art = str(item.get("descripcion") or "ITEM")[:100]
                unidad = str(item.get("unidad_medida") or "NIU")[:12]
                afect = str(item.get("tipo_afectacion") or item.get("tipo_afectacion_igv") or "10")[:2]
                inafecto = afect.startswith("3")
                gratuita = bool(item.get("es_gratuito"))
                icbper_flag = bool(item.get("tiene_icbper"))

                det_row: dict[str, Any] = {}

                def _putd(field: str, value: Any) -> None:
                    if field in det_field_names:
                        det_row[field] = value

                _putd("ARTICULO", cod_art)
                _putd("NOMBRE_ART", desc_art)
                _putd("MARCA", "")
                _putd("PRESENTACI", unidad)
                _putd("EQUIVALENT", 1.0)
                _putd("CANTIDAD", cantidad)
                _putd("KILO", float(item.get("peso_kg") or 0))
                _putd("PRECIO", valor_unit)
                _putd("DESCUENTO1", desc1)
                _putd("DESCUENTO2", desc2)
                _putd("DESCUENTO3", desc3)
                _putd("IMPORTE", importe)
                _putd("INAFECTO", inafecto)
                _putd("CODIGO", codigo_ventas)
                _putd("ANULADO", False)
                _putd("COMISION", 0.0)
                _putd("SERIES", "")
                _putd("DESCRIPCIO", "")
                _putd("ISC", isc_linea)
                _putd("REGISTRO_A", False)
                _putd("NUMERO_ID", idx)
                _putd("CODIGO_TRI", afect)
                _putd("GRATUITA", gratuita)
                _putd("PROCESADO", False)
                _putd("SELECCIONA", False)
                _putd("CODIGO_TR2", "")
                _putd("CODIGO_TIP", "")
                _putd("ICBPER", icbper_flag)

                td.append(det_row)
            logger.info(
                "Detalles DBF insertados: codigo_ventas=%d items=%d",
                codigo_ventas, len(items),
            )
        finally:
            td.close()

        # 4. Reindex CDX (best-effort) — usa CodeBase si esta disponible.
        try:
            from codebasetools import cbTools  # type: ignore[import-not-found]
            for fname in ("ventas.dbf", "ventas_detalle.dbf"):
                p = str(get_dbf_path() / fname)
                cbt = cbTools()
                if cbt.use(p, exclusive=True):
                    try:
                        cbt.reindex()
                    except Exception:  # noqa: BLE001
                        pass
                    cbt.closetable()
        except Exception as exc:  # noqa: BLE001
            logger.debug("reindex CDX skipped: %s", exc)

        # 5. Invalidar cache de lectura
        try:
            from ...core.db_adapter.dbf_repo import _DbfWrapper  # noqa: F401
            # No hay cache global activa; los repos releen cada vez.
        except Exception:
            pass

        # Invalidar cache para que el listar refleje el nuevo
        try:
            from .dbf_repo import invalidate_cache
            invalidate_cache("comprobantes_cab")
            invalidate_cache("ventas_meta")
        except Exception:
            pass

        # ID sintetico para responder
        from .mdb_repo import _synth_id
        sid = _synth_id("comp_dbf", tipo, serie, correlativo)

        return {
            "id": sid,
            "tipo_documento": tipo,
            "serie": serie,
            "correlativo": correlativo,
            "numero_completo": f"{serie}-{correlativo:08d}",
            "fecha_emision": fecha_emision,
            "fecha_vencimiento": payload.get("fecha_vencimiento"),
            "hora_emision": None,
            "moneda": moneda_iso,
            "tipo_cambio": float(payload.get("tipo_cambio") or 1.0),
            "tipo_operacion": tipo_operacion,
            "cliente_id": payload.get("cliente_id"),
            "cliente_tipo_doc": str(payload.get("cliente_tipo_doc") or "6"),
            "cliente_numero_doc": cliente_doc,
            "cliente_razon_social": cliente_razon,
            "cliente_direccion": cliente_dir or None,
            "cliente_email": payload.get("cliente_email"),
            "forma_pago": forma_pago,
            "forma_pago_json": payload.get("forma_pago_json"),
            "total_gravado": sub,
            "total_exonerado": exo,
            "total_inafecto": inaf,
            "total_exportacion": expo,
            "total_gratuito": grat,
            "total_descuento": descuento,
            "subtotal": sub,
            "total_igv": igv,
            "total_isc": isc,
            "total_icbper": icbper,
            "total_venta": total,
            "total_pen": total if moneda_iso == "PEN" else total * float(payload.get("tipo_cambio") or 1.0),
            "detraccion_codigo": det_codigo,
            "detraccion_tasa": payload.get("detraccion_tasa"),
            "detraccion_monto": det_monto if det_codigo else None,
            "detraccion_cta_bn": payload.get("detraccion_cta_bn"),
            "percepcion_pct": payload.get("percepcion_pct"),
            "percepcion_monto": payload.get("percepcion_monto"),
            "doc_referencia_tipo": payload.get("doc_referencia_tipo") or payload.get("documento_referencia_tipo"),
            "doc_referencia_serie": ref_full or None,
            "doc_referencia_motivo": payload.get("doc_referencia_motivo"),
            "motivo_nc_codigo": payload.get("motivo_codigo") or payload.get("motivo_nc_codigo"),
            "motivo_nc_descripcion": payload.get("motivo_descripcion"),
            "estado": "E",  # Emitido localmente, NO enviado a SUNAT
            "cdr_codigo": None,
            "cdr_descripcion": None,
            "xml_path": None,
            "cdr_path": None,
            "pdf_path": None,
            "qr_data": None,
            "monto_letras": payload.get("monto_letras"),
            "observaciones": payload.get("observaciones") or payload.get("detalle_adicional"),
            "created_at": None,
            "updated_at": None,
            "detalles": [
                {
                    "id": idx,
                    "comprobante_id": sid,
                    "orden": idx,
                    "producto_id": item.get("producto_id"),
                    "codigo": item.get("codigo"),
                    "descripcion": item.get("descripcion"),
                    "unidad_medida": item.get("unidad_medida") or "NIU",
                    "cantidad": float(item.get("cantidad") or 0),
                    "valor_unitario": float(item.get("valor_unitario") or item.get("precio_unitario") or 0),
                    "precio_unitario": float(item.get("precio_unitario") or item.get("valor_unitario") or 0),
                    "descuento_pct": float(item.get("descuento_pct") or 0),
                    "descuento_monto": float(item.get("descuento_monto") or 0),
                    "valor_venta": float(item.get("valor_venta") or 0),
                    "igv_pct": float(item.get("igv_pct") or 18.0),
                    "igv_monto": float(item.get("igv_monto") or item.get("igv") or 0),
                    "tipo_afectacion_igv": item.get("tipo_afectacion") or item.get("tipo_afectacion_igv") or "10",
                    "isc_pct": float(item.get("isc_pct") or 0),
                    "isc_monto": float(item.get("isc_monto") or 0),
                    "icbper_monto": float(item.get("icbper_monto") or 0),
                    "total_linea": float(item.get("total_linea") or item.get("total") or 0),
                }
                for idx, item in enumerate(items, start=1)
            ],
        }

    # -----------------------------------------------------------------
    # Helpers de UPDATE de estado para el flujo en 2 pasos (E/A/T/R/B).
    # En GECOPE no existe un campo "estado" explicito; mapeamos:
    #   E (Emitido local)        → DATA=False, RPTA=False, REGISTRO_A=False
    #   A (Aceptado SUNAT)       → DATA=True, FIRMA=True, RPTA=True,
    #                              CODIGO_HAS=<cdr_hash>, MOTIVO_BAJ vacio
    #   T (Timeout SUNAT)        → DATA=True, FIRMA=True, RPTA=False,
    #                              MOTIVO_BAJ="[T] reintenta SUNAT"
    #   R (Rechazado por SUNAT)  → DATA=True, FIRMA=True, RPTA=True,
    #                              MOTIVO_BAJ="[R] <descripcion>",
    #                              CODIGO_HAS vacio
    #   B (Anulado/Baja)         → REGISTRO_A=True, MOTIVO_BAJ="<motivo>"
    # El reader (`dbf_repo._decidir_estado_gecope`) traduce esto a E/A/T/R/B.
    # -----------------------------------------------------------------

    @staticmethod
    def _find_row(serie: str, correlativo: int, tipo_documento: str) -> tuple[Any, Any]:
        """Localiza un registro en ventas.dbf por (serie, correlativo, tipo).

        Retorna (tabla_abierta_en_RW, indice_de_registro) o (None, None).
        El caller debe cerrar la tabla con t.close().
        """
        serie_n = (serie or "").upper()[:4]
        gecope_doc = SUNAT_A_GECOPE_DOC.get(tipo_documento, "000001")
        t = _open_table("ventas.dbf")
        try:
            for i, r in enumerate(t):
                try:
                    if (r["SER_DOCUME"] or "").strip() != serie_n:
                        continue
                    if (r["DOCUMENTO"] or "").strip() != gecope_doc:
                        continue
                    num_raw = (r["NUM_DOCUME"] or "0").strip()
                    if int(num_raw) == int(correlativo):
                        return t, i
                except Exception:
                    continue
        except Exception:
            t.close()
            raise
        t.close()
        return None, None

    @staticmethod
    def _update_row(serie: str, correlativo: int, tipo_documento: str,
                     updates: dict[str, Any]) -> bool:
        """Aplica un dict de updates al registro identificado.

        Retorna True si se encontro y actualizo.
        """
        serie_n = (serie or "").upper()[:4]
        gecope_doc = SUNAT_A_GECOPE_DOC.get(tipo_documento, "000001")
        t = _open_table("ventas.dbf")
        try:
            field_names = set(t.field_names)
            updated = False
            for rec in t:
                try:
                    if (rec["SER_DOCUME"] or "").strip() != serie_n:
                        continue
                    if (rec["DOCUMENTO"] or "").strip() != gecope_doc:
                        continue
                    num_raw = (rec["NUM_DOCUME"] or "0").strip()
                    if int(num_raw) != int(correlativo):
                        continue
                except Exception:
                    continue
                # Encontrado — aplicar updates
                with rec as r:
                    for k, v in updates.items():
                        if k in field_names:
                            try:
                                r[k] = v
                            except Exception as exc:  # noqa: BLE001
                                logger.warning(
                                    "No se pudo escribir campo %s=%r: %s",
                                    k, v, exc,
                                )
                updated = True
                break
            return updated
        finally:
            t.close()
            # Invalidar cache de lectura
            try:
                from .dbf_repo import invalidate_cache
                invalidate_cache("comprobantes_cab")
                invalidate_cache("ventas_meta")
            except Exception:
                pass

    @staticmethod
    def marcar_enviado_aceptado(serie: str, correlativo: int, tipo_documento: str,
                                  cdr_hash: str, cdr_descripcion: str = "Aceptado",
                                  qr_dato: Optional[str] = None) -> bool:
        """Marca el comprobante como ACEPTADO por SUNAT (estado A)."""
        updates: dict[str, Any] = {
            "DATA": True,
            "FIRMA": True,
            "RPTA": True,
            "CODIGO_HAS": (cdr_hash or "")[:40],
            "MOTIVO_BAJ": "",
            "REGISTRO_A": False,
        }
        if qr_dato:
            updates["QR_DATO"] = qr_dato[:254]
        return ComprobanteWriterDBF._update_row(
            serie, correlativo, tipo_documento, updates,
        )

    @staticmethod
    def marcar_enviado_rechazado(serie: str, correlativo: int, tipo_documento: str,
                                   codigo_cdr: str, descripcion: str) -> bool:
        """Marca como RECHAZADO por contenido (estado R)."""
        msg = f"[R][{codigo_cdr}] {descripcion or ''}"[:100]
        updates: dict[str, Any] = {
            "DATA": True,
            "FIRMA": True,
            "RPTA": True,  # SUNAT respondio, pero rechazo
            "CODIGO_HAS": "",
            "MOTIVO_BAJ": msg,
        }
        return ComprobanteWriterDBF._update_row(
            serie, correlativo, tipo_documento, updates,
        )

    @staticmethod
    def marcar_timeout_sunat(serie: str, correlativo: int, tipo_documento: str,
                              descripcion: str) -> bool:
        """Marca como TIMEOUT transitorio (estado T). Robot reintenta."""
        msg = f"[T] {descripcion or 'Timeout SUNAT'}"[:100]
        updates: dict[str, Any] = {
            "DATA": True,
            "FIRMA": True,
            "RPTA": False,  # NO respondio: pendiente reintento
            "CODIGO_HAS": "",
            "MOTIVO_BAJ": msg,
        }
        return ComprobanteWriterDBF._update_row(
            serie, correlativo, tipo_documento, updates,
        )

    @staticmethod
    def anular_local(serie: str, correlativo: int, tipo_documento: str,
                      motivo: str = "Anulado por usuario") -> bool:
        """Anula localmente (estado B). Solo si nunca fue enviado.

        Marca REGISTRO_A=True y MOTIVO_BAJ con el motivo.
        """
        updates: dict[str, Any] = {
            "REGISTRO_A": True,
            "DATA_BAJA": True,
            "MOTIVO_BAJ": (motivo or "Anulado local")[:100],
        }
        return ComprobanteWriterDBF._update_row(
            serie, correlativo, tipo_documento, updates,
        )

    @staticmethod
    def marcar_baja_sunat(serie: str, correlativo: int, tipo_documento: str,
                           ticket: str = "", motivo: str = "Dado de baja por SUNAT") -> bool:
        """Marca como dado de BAJA por SUNAT (estado B).

        Se invoca tras un getStatus exitoso de RA / Resumen condicion 3.
        """
        updates: dict[str, Any] = {
            "REGISTRO_A": True,
            "DATA_BAJA": True,
            "RPTA_BAJA": True,
            "MOTIVO_BAJ": (motivo or "Baja SUNAT")[:100],
            "TICKET_BAJ": (ticket or "")[:30],
        }
        return ComprobanteWriterDBF._update_row(
            serie, correlativo, tipo_documento, updates,
        )
