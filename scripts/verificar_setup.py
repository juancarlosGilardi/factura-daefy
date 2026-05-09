"""Verifica que Factura-mdb está listo para usarse.

Checks:
1. config.json existe y es JSON válido.
2. config.json tiene los campos mínimos.
3. El path del .mdb existe y es accesible.
4. pyodbc instalado y driver Access detectado.
5. El cert (.pfx) existe en disco.
6. La password del cert lo abre correctamente.
7. El subject del cert contiene el RUC declarado en config.

Salida: tabla con OK / ERROR + sugerencias accionables.
Exit code 0 si todo OK; 1 si algún check critico falla.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# Asegurar que `app` sea importable
sys.path.insert(0, str(PROJECT_ROOT / "backend"))

OK = "[ OK ]"
ERR = "[FAIL]"
WARN = "[WARN]"


def header(msg: str) -> None:
    print()
    print("=" * 70)
    print(f" {msg}")
    print("=" * 70)


def check(label: str, ok: bool, detail: str = "", suggestion: str = "") -> bool:
    tag = OK if ok else ERR
    print(f"{tag}  {label}")
    if detail:
        print(f"        -> {detail}")
    if not ok and suggestion:
        print(f"        Sugerencia: {suggestion}")
    return ok


def warn(label: str, detail: str = "") -> None:
    print(f"{WARN}  {label}")
    if detail:
        print(f"        -> {detail}")


def main() -> int:
    header("Verificación de Factura-mdb")

    failures = 0

    # 1. config.json existe
    config_path = PROJECT_ROOT / "config.json"
    if not check(
        "config.json existe en la raíz del proyecto",
        config_path.exists(),
        f"Buscado en: {config_path}",
        "Copia config.example.json a config.json y rellena los valores.",
    ):
        return 1

    # 2. config.json es JSON válido
    try:
        config = json.loads(config_path.read_text(encoding="utf-8"))
        check("config.json es JSON válido", True)
    except Exception as exc:  # noqa: BLE001
        check("config.json es JSON válido", False, str(exc),
              "Revisa la sintaxis JSON (comas, comillas).")
        return 1

    # 3. Campos mínimos
    secciones_requeridas = ("mdb", "empresa", "sunat", "certificado")
    faltantes = [s for s in secciones_requeridas if s not in config]
    check(
        f"config.json tiene secciones requeridas ({', '.join(secciones_requeridas)})",
        not faltantes,
        f"Faltan: {faltantes}" if faltantes else "",
        "Compara contra config.example.json y agrega las secciones faltantes.",
    ) or (failures := failures + 1)

    # 4. mdb.path
    header("Conectividad con .mdb")
    mdb_path_raw = (config.get("mdb") or {}).get("path", "")
    mdb_path = Path(mdb_path_raw) if mdb_path_raw else Path("")
    if not check(
        "mdb.path está configurado",
        bool(mdb_path_raw),
        suggestion=("Edita config.json -> mdb.path con el path absoluto al .mdb "
                    "del cliente (ej: C:/SIAP/db_bancos.mdb)."),
    ):
        failures += 1

    if mdb_path_raw:
        check(
            f"El archivo .mdb existe en {mdb_path}",
            mdb_path.exists() and mdb_path.is_file(),
            "" if mdb_path.exists() else "Archivo no encontrado.",
            "Verifica que el path sea correcto y el archivo exista.",
        ) or (failures := failures + 1)

    # 5. pyodbc + driver Access
    header("Driver Access (pyodbc)")
    try:
        import pyodbc  # noqa: WPS433
        check("pyodbc instalado", True, f"Versión {pyodbc.version}")
        drivers = pyodbc.drivers()
        access_drivers = [d for d in drivers if "Access" in d]
        if access_drivers:
            check(
                "Driver Access ODBC detectado",
                True,
                f"Disponibles: {', '.join(access_drivers)}",
            )
        else:
            check(
                "Driver Access ODBC detectado",
                False,
                f"Drivers disponibles: {drivers[:5]}",
                ("Instala Microsoft Access Database Engine 2016 (Redistributable). "
                 "Descarga: https://www.microsoft.com/en-us/download/details.aspx?id=54920 "
                 "Elige la arquitectura que coincide con tu Python (x64 o x86)."),
            )
            failures += 1
    except ImportError:
        check("pyodbc instalado", False,
              suggestion="pip install -r backend/requirements.txt")
        failures += 1

    # 6. Apertura real del .mdb
    if mdb_path_raw and mdb_path.exists():
        try:
            from app.services.mdb_importer.connection import conectar
            cn = conectar(mdb_path, readonly=True)
            cur = cn.cursor()
            cur.execute("SELECT TOP 1 1 FROM EF2CLIENTES")
            cur.fetchone()
            cur.close()
            cn.close()
            check("Apertura del .mdb (readonly) y query SELECT exitosa", True)
        except Exception as exc:  # noqa: BLE001
            check("Apertura del .mdb (readonly)", False, str(exc),
                  ("Si dice 'archivo no es base de datos': verifica que es .mdb. "
                   "Si dice 'no se encuentra el driver': revisa el check anterior. "
                   "Si dice 'lock/exclusivo': cierra SIAP."))
            failures += 1

    # 7. Certificado
    header("Certificado digital (.pfx)")
    cert_section = config.get("certificado") or {}
    cert_path_raw = cert_section.get("path", "")
    cert_pass = cert_section.get("password", "")

    cert_path = Path(cert_path_raw)
    if not cert_path.is_absolute():
        cert_path = (PROJECT_ROOT / cert_path).resolve()

    check(
        "certificado.path configurado en config.json",
        bool(cert_path_raw),
        suggestion="Setea certificado.path apuntando al .pfx (ej: certs/RUC.pfx).",
    ) or (failures := failures + 1)

    cert_ok = False
    if cert_path_raw:
        cert_exists = cert_path.exists() and cert_path.is_file()
        check(
            f"Archivo .pfx existe en {cert_path}",
            cert_exists,
            "" if cert_exists else "No encontrado",
            "Coloca el .pfx en la ruta indicada (recomendado: certs/RUC.pfx).",
        ) or (failures := failures + 1)

        if cert_exists:
            if not cert_pass:
                check("certificado.password configurado", False,
                      suggestion="Edita config.json y agrega la password del .pfx.")
                failures += 1
            else:
                try:
                    from cryptography.hazmat.primitives.serialization import (
                        pkcs12,
                    )
                    data = cert_path.read_bytes()
                    pkcs12.load_key_and_certificates(
                        data, cert_pass.encode("utf-8")
                    )
                    cert_ok = True
                    check("Password del .pfx correcta (cert se abre)", True)
                except Exception as exc:  # noqa: BLE001
                    check("Password del .pfx correcta", False, str(exc),
                          ("Verifica certificado.password. Si el cert venció, "
                           "renuévalo en el OSCE/proveedor."))
                    failures += 1

    # 8. Subject del cert vs RUC declarado
    if cert_ok:
        try:
            from cryptography.hazmat.primitives.serialization import pkcs12
            from cryptography.x509.oid import NameOID
            data = cert_path.read_bytes()
            _, cert_obj, _ = pkcs12.load_key_and_certificates(
                data, cert_pass.encode("utf-8")
            )
            ruc_declarado = (config.get("empresa") or {}).get("ruc", "")
            subject = cert_obj.subject
            cn_attrs = subject.get_attributes_for_oid(NameOID.COMMON_NAME)
            sn_attrs = subject.get_attributes_for_oid(NameOID.SERIAL_NUMBER)
            cn_val = cn_attrs[0].value if cn_attrs else ""
            sn_val = sn_attrs[0].value if sn_attrs else ""
            subject_str = f"CN={cn_val} | SN={sn_val}"
            ruc_match = ruc_declarado and (
                ruc_declarado in cn_val or ruc_declarado in sn_val
            )
            check(
                f"Subject del cert contiene el RUC declarado ({ruc_declarado})",
                bool(ruc_match),
                subject_str,
                ("Si el cert es de otra empresa, usa el .pfx correcto. Si es "
                 "el cert correcto pero no contiene el RUC visible, es OK "
                 "siempre que el extension SerialNumber lo declare."),
            ) or (failures := failures + 1 if ruc_declarado else failures)
        except Exception as exc:  # noqa: BLE001
            warn("No se pudo verificar subject del cert", str(exc))

    # 9. SUNAT env informativo
    header("SUNAT")
    ambiente = (config.get("sunat") or {}).get("ambiente", "beta")
    if ambiente == "produccion":
        warn("SUNAT está en MODO PRODUCCIÓN. Las emisiones llegan al SUNAT real.",
             "Asegúrate de tener autorización expresa del cliente.")
    else:
        print(f"{OK}  SUNAT en {ambiente.upper()} (default seguro).")

    sol_user = (config.get("sunat") or {}).get("sol_user", "MODDATOS")
    if ambiente == "beta" and sol_user.upper() != "MODDATOS":
        warn(
            "SOL user en BETA distinto de MODDATOS",
            "BETA acepta MODDATOS/MODDATOS. Usa esos valores en BETA.",
        )

    # ---------- Resumen ----------
    header("Resumen")
    if failures == 0:
        print(f"{OK}  Todos los checks críticos pasaron. Puedes arrancar la app:")
        print()
        print("    python scripts/arrancar_dev.py")
        print()
        print("Y abrir http://127.0.0.1:9876 en tu navegador.")
        return 0
    else:
        print(f"{ERR}  Hay {failures} check(s) fallido(s). Corrige los puntos")
        print("        marcados con [FAIL] y vuelve a ejecutar este script.")
        return 1


if __name__ == "__main__":
    sys.exit(main())
