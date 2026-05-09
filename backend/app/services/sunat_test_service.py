"""Servicio de prueba de conexión a SUNAT.

Hace una consulta `getStatus` con un ticket dummy. Si SUNAT acepta la
autenticación, responderá un código de error 'ticket inexistente' (que
PARA NOSOTROS significa éxito de auth). Si las credenciales SOL son malas,
responderá un Fault con código 0102/0103 ("Usuario o clave incorrectos").
"""
from __future__ import annotations

import asyncio
import logging
from typing import Optional

from .sunat_client import SUNATClient

logger = logging.getLogger("factura_mdb.sunat_test")

# Ticket dummy: 23 dígitos válidos sintácticamente pero inexistente
_TICKET_DUMMY = "12345678901234567890123"


async def _probar_async(ruc: str, sol_user: str, sol_pass: str,
                         sunat_env: str) -> dict:
    client = SUNATClient(ruc=ruc, sol_user=sol_user, sol_pass=sol_pass,
                          ambiente=sunat_env)
    try:
        cod, desc, _ = await client.consultar_ticket(_TICKET_DUMMY)
    except Exception as e:  # noqa: BLE001
        msg = str(e).lower()
        if "0102" in msg or "0103" in msg or "usuario" in msg or "clave" in msg:
            return {"ok": False, "mensaje": "Credenciales SOL incorrectas",
                    "detalle": str(e)}
        return {"ok": False, "mensaje": "Error de comunicación con SUNAT",
                "detalle": str(e)}

    # Códigos típicos de "ticket inexistente" — significan que la auth pasó
    if cod and cod.startswith(("01", "98", "99")) and "ticket" in (desc or "").lower():
        return {"ok": True,
                "mensaje": f"Conexión a SUNAT {sunat_env} exitosa",
                "detalle": f"{cod} {desc}"}

    # Si llegó respuesta cualquiera, asumimos auth ok (los faults explícitos
    # de credenciales se capturan en el except de arriba)
    return {"ok": True,
            "mensaje": f"Conexión a SUNAT {sunat_env} exitosa",
            "detalle": f"{cod or ''} {desc or ''}".strip()}


def probar_conexion(*, sol_user: str, sol_pass: str, sunat_env: str,
                     empresa: Optional[object] = None) -> dict:
    """Prueba sincronizada (envuelve la versión async)."""
    ruc = getattr(empresa, "ruc", None) if empresa else None
    if not ruc:
        return {"ok": False, "mensaje": "Falta RUC en empresa",
                "detalle": "Configura primero los datos de empresa"}
    try:
        return asyncio.run(_probar_async(ruc, sol_user, sol_pass, sunat_env))
    except Exception as e:  # noqa: BLE001
        logger.exception("Error probando SUNAT")
        return {"ok": False, "mensaje": "Error inesperado",
                "detalle": str(e)}
