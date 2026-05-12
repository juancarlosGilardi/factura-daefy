"""Prueba envio SUNAT BETA directo sobre el ultimo comprobante de ventas.dbf.

Bypassa el endpoint HTTP (que tiene un bug con FPT de campos G via dbfread)
y va directo a emitir_y_enviar_directo() con dicts.

Flujo:
  1. Lee ventas.dbf con librería `dbf` (no `dbfread`).
  2. Toma el último comprobante FMDB (los que emitimos anoche).
  3. Lee detalles de ventas_detalle.dbf.
  4. Llama emitir_y_enviar_directo() → firma + envío SUNAT BETA.
  5. Si funciona, llama ComprobanteWriterDBF.actualizar_cdr() para persistir.
  6. Reporta resultado.
"""
from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "backend"))

import dbf as dbflib  # noqa: E402
from app.core.config import settings  # noqa: E402
from app.core.db_adapter import get_dbf_path  # noqa: E402
from app.services.dbf_importer import mappers as dbf_mappers  # noqa: E402
from app.services.comprobante_service import emitir_y_enviar_directo  # noqa: E402


OK = "[ OK ]"
ERR = "[FAIL]"
WARN = "[WARN]"


def _open_dbf(name: str):
    path = get_dbf_path() / name
    t = dbflib.Table(str(path), codepage="cp1252")
    t.open(mode=dbflib.READ_ONLY)
    return t


def _row_to_dict(row, fields):
    """Convierte un Record de la libreria `dbf` a dict (los campos memo G/P
    suelen venir vacios o como bytes; los ignoramos)."""
    out = {}
    for f in fields:
        try:
            val = row[f]
            # Saltar bytes / objetos no serializables (memo G picture)
            if isinstance(val, (bytes, bytearray)):
                val = None
            out[f.upper()] = val
        except Exception:  # noqa: BLE001
            out[f.upper()] = None
    return out


def buscar_ultimo_fmdb(t) -> dict | None:
    """Devuelve el ultimo comprobante FMDB de ventas.dbf.

    Daefy no tiene campo F4USEGRA — los nuestros son los ULTIMOS escritos
    (correlativos 354+, USER='FMDB' o el ultimo registro fisico).
    """
    fields = list(t.field_names)
    # Daefy ventas.dbf: el ultimo registro fisico es el ultimo emitido
    todos = list(t)
    if not todos:
        return None
    # Filtrar a F001-00000354+ (nuestros) o tomar el ultimo
    ultimo = todos[-1]
    return _row_to_dict(ultimo, fields)


def buscar_detalles(t_det, cab_codigo) -> list[dict]:
    fields = list(t_det.field_names)
    out = []
    for rec in t_det:
        if rec["CODIGO"] == cab_codigo:
            d = _row_to_dict(rec, fields)
            out.append(d)
    return out


def main() -> int:
    print("=" * 70)
    print("PRUEBA SUNAT BETA — envio directo desde DBF de Daefy")
    print("=" * 70)
    print()

    # 1) Verificar configuracion
    print(f"Modo BD:    {settings.DB_MODE}")
    print(f"DBF path:   {settings.DBF_PATH}")
    print(f"SUNAT env:  {settings.SUNAT_ENV}")
    print(f"Cert:       {settings.CERT_PATH}")
    print()
    if settings.DB_MODE != "dbf":
        print(f"{ERR} settings.DB_MODE no es 'dbf' — revisa config.json")
        return 1

    # 2) Abrir ventas.dbf con librería `dbf`
    print("Abriendo ventas.dbf...")
    t_cab = _open_dbf("ventas.dbf")
    print(f"  Total registros: {len(t_cab)}")

    # 3) Buscar último comprobante nuestro
    cab_dict = buscar_ultimo_fmdb(t_cab)
    if not cab_dict:
        print(f"{ERR} No hay comprobantes FMDB en ventas.dbf")
        t_cab.close()
        return 1

    print(f"{OK} Último FMDB encontrado")
    cab_codigo = cab_dict.get("CODIGO")
    print(f"  CODIGO interno: {cab_codigo}")
    print(f"  DOCUMENTO: {cab_dict.get('DOCUMENTO')}")
    print(f"  SER_DOCUME: {cab_dict.get('SER_DOCUME')}")
    print(f"  NUM_DOCUME: {cab_dict.get('NUM_DOCUME')}")
    print(f"  TOTAL:      {cab_dict.get('TOTAL')}")
    print(f"  CLIENTE:    {cab_dict.get('NOMBRE_CLI')}")
    print(f"  DATA flag (enviado SUNAT?): {cab_dict.get('DATA')}")
    print(f"  RPTA flag (recibio CDR?):   {cab_dict.get('RPTA')}")
    t_cab.close()

    # 4) Buscar detalles
    print("\nLeyendo detalles...")
    t_det = _open_dbf("ventas_detalle.dbf")
    det_rows = buscar_detalles(t_det, cab_codigo)
    t_det.close()
    print(f"  Items encontrados: {len(det_rows)}")
    for d in det_rows:
        print(f"    {d.get('CANTIDAD')} x {d.get('ARTICULO')} = {d.get('SUBTOTAL')}")

    # 5) Mapear DBF → dict usando los mappers existentes
    print("\nMapeando a estructura interna...")
    comp_dict = dbf_mappers.dbf_comprobante_to_dict(cab_dict)
    if not comp_dict:
        print(f"{ERR} dbf_comprobante_to_dict devolvio None — mapping incompleto")
        return 1

    detalles_dicts = [
        dbf_mappers.dbf_detalle_to_dict(d, orden=i + 1)
        for i, d in enumerate(det_rows)
    ]
    detalles_dicts = [d for d in detalles_dicts if d]
    if not detalles_dicts:
        print(f"{ERR} Detalles vacios despues del mapping")
        return 1

    print(f"{OK} Mapping OK: {comp_dict.get('numero_completo')}")
    print(f"  Total venta:   {comp_dict.get('total_venta')}")
    print(f"  Total gravado: {comp_dict.get('total_gravado')}")
    print(f"  Total IGV:     {comp_dict.get('total_igv')}")
    print(f"  Estado:        {comp_dict.get('estado')}")

    # 6) Empresa desde config.json + _FAKE_EMPRESA_MDB
    print("\nLeyendo Empresa...")
    from app.core.db_adapter.repo import EmpresaRepoMDB
    empresa_dict = EmpresaRepoMDB.obtener()
    if not empresa_dict:
        print(f"{ERR} EmpresaRepoMDB devolvio None")
        return 1
    print(f"{OK} Empresa: {empresa_dict.get('razon_social')} RUC {empresa_dict.get('ruc')}")
    print(f"  Cert:      {empresa_dict.get('certificado_path')}")
    print(f"  SUNAT env: {empresa_dict.get('sunat_env')}")

    # 7) ENVIAR a SUNAT BETA
    print()
    print("-" * 70)
    print("ENVIANDO A SUNAT BETA...")
    print("-" * 70)

    resultado = emitir_y_enviar_directo(
        comp_dict, empresa_dict, detalles_dicts,
        generar_pdf=False,  # PDF al final si todo OK
    )

    print()
    print("-" * 70)
    print("RESULTADO")
    print("-" * 70)
    print(f"  success:     {resultado.get('success')}")
    print(f"  codigo:      {resultado.get('codigo')}")
    print(f"  descripcion: {resultado.get('descripcion')}")
    print(f"  estado:      {resultado.get('estado')}")
    print(f"  xml_path:    {resultado.get('xml_path')}")
    print(f"  cdr_path:    {resultado.get('cdr_path')}")
    if resultado.get("error"):
        print(f"  ERROR:       {resultado.get('error')}")
        print(f"  STAGE:       {resultado.get('stage')}")

    # 8) Persistir CDR si exito (o tambien si rechazo, para registrar)
    if resultado.get("codigo"):
        print()
        print("Persistiendo CDR en ventas.dbf...")
        from app.core.db_adapter.repo import ComprobanteWriterMDB
        cid = comp_dict["id"]
        ok = ComprobanteWriterMDB.actualizar_cdr(
            cid,
            tipo=comp_dict.get("tipo_documento"),
            serie=comp_dict.get("serie"),
            correlativo=comp_dict.get("correlativo"),
            cdr_codigo=str(resultado.get("codigo") or ""),
            cdr_descripcion=str(resultado.get("descripcion") or ""),
            cdr_hash=str(resultado.get("cdr_hash") or ""),
            estado=resultado.get("estado") or "R",
            xml_path=resultado.get("xml_path"),
            cdr_path=resultado.get("cdr_path"),
            pdf_path=resultado.get("pdf_path"),
        )
        print(f"  actualizar_cdr -> {ok}")

    print()
    if resultado.get("success"):
        print(f"{OK} SUNAT BETA acepto el comprobante")
        return 0
    print(f"{ERR} SUNAT BETA rechazo o hubo error tecnico")
    return 1


if __name__ == "__main__":
    sys.exit(main())
