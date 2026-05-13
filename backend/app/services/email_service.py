"""Servicio de envío de emails con adjuntos PDF.

Lee la configuración SMTP desde `config.json -> smtp` (con fallback a
defaults para Gmail con app password). Permite enviar comprobantes
electrónicos, cotizaciones y pedidos al cliente final.

Configuración esperada en config.json:
    {
      "smtp": {
        "host": "smtp.gmail.com",
        "port": 587,
        "user": "remitente@gmail.com",
        "password": "app-password",
        "from_name": "Mi Empresa",
        "use_tls": true
      }
    }
"""
from __future__ import annotations

import logging
import re
import smtplib
from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formataddr
from typing import Optional

from ..core.config import settings


logger = logging.getLogger("factura_mdb.email_service")


# Defaults de fallback (HANDOFF). Si el cliente no configura nada en
# config.json, se usa esta cuenta de pruebas.
_DEFAULT_SMTP = {
    "host": "smtp.gmail.com",
    "port": 587,
    "user": "python.peru.gratis@gmail.com",
    "password": "tpxezmbfvlxddpop",
    "from_name": "Factura-mdb",
    "use_tls": True,
}

# Tope de seguridad para el adjunto (5 MB).
MAX_ADJUNTO_BYTES = 5 * 1024 * 1024

# Regex simple para validar email (no es RFC perfecto, pero filtra typos).
_EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}$")


class EmailError(Exception):
    """Error genérico al enviar email (SMTP/conexión/auth)."""


class EmailValidacionError(ValueError):
    """Email destino inválido o adjunto demasiado grande."""


def email_valido(direccion: str) -> bool:
    """True si la dirección parece un email válido."""
    if not direccion or not isinstance(direccion, str):
        return False
    return bool(_EMAIL_RE.match(direccion.strip()))


def _smtp_config() -> dict:
    """Devuelve la config SMTP efectiva (config.json + defaults)."""
    raw = {}
    try:
        raw = (settings._config.get("smtp") or {}) if hasattr(settings, "_config") else {}
    except Exception:  # noqa: BLE001
        raw = {}
    return {
        "host": raw.get("host") or _DEFAULT_SMTP["host"],
        "port": int(raw.get("port") or _DEFAULT_SMTP["port"]),
        "user": raw.get("user") or _DEFAULT_SMTP["user"],
        "password": raw.get("password") or _DEFAULT_SMTP["password"],
        "from_name": raw.get("from_name") or _DEFAULT_SMTP["from_name"],
        "use_tls": bool(raw.get("use_tls", _DEFAULT_SMTP["use_tls"])),
    }


def enviar_email_con_adjunto(
    destinatario: str,
    asunto: str,
    body_html: str,
    adjunto_bytes: bytes,
    adjunto_nombre: str,
    body_text: Optional[str] = None,
    remitente_nombre: Optional[str] = None,
) -> dict:
    """Envía un email con un adjunto PDF.

    Args:
        destinatario: dirección del receptor.
        asunto: subject del mensaje.
        body_html: cuerpo HTML del email.
        adjunto_bytes: bytes del PDF a adjuntar.
        adjunto_nombre: nombre de archivo del adjunto (ej: "factura_F001-356.pdf").
        body_text: alternativa text/plain (opcional, se deriva del HTML si None).
        remitente_nombre: override del display name del remitente.

    Returns:
        dict con {"ok": True, "mensaje": "Enviado a ..."}.

    Raises:
        EmailValidacionError: email inválido o adjunto > 5 MB.
        EmailError: error SMTP (conexión, auth, etc.).
    """
    destinatario = (destinatario or "").strip()
    if not email_valido(destinatario):
        raise EmailValidacionError(f"Email inválido: {destinatario!r}")

    if adjunto_bytes is None:
        raise EmailValidacionError("Adjunto vacío")
    if len(adjunto_bytes) > MAX_ADJUNTO_BYTES:
        raise EmailValidacionError(
            f"Adjunto excede el límite de {MAX_ADJUNTO_BYTES // (1024*1024)} MB "
            f"(tamaño actual: {len(adjunto_bytes) // 1024} KB)"
        )

    cfg = _smtp_config()
    from_name = remitente_nombre or cfg["from_name"]
    from_addr = cfg["user"]

    msg = MIMEMultipart("mixed")
    msg["From"] = formataddr((from_name, from_addr))
    msg["To"] = destinatario
    msg["Subject"] = asunto or "(sin asunto)"

    # Cuerpo: si se proporcionó texto plano lo agrego, si no derivo uno.
    alt = MIMEMultipart("alternative")
    if body_text is None:
        body_text = re.sub(r"<[^>]+>", "", body_html or "").strip()
    alt.attach(MIMEText(body_text or "(sin contenido)", "plain", "utf-8"))
    alt.attach(MIMEText(body_html or "", "html", "utf-8"))
    msg.attach(alt)

    # Adjunto PDF
    parte = MIMEApplication(adjunto_bytes, _subtype="pdf")
    parte.add_header(
        "Content-Disposition",
        "attachment",
        filename=adjunto_nombre,
    )
    msg.attach(parte)

    logger.info(
        "Enviando email a %s (asunto=%r, adjunto=%s, %d bytes) via %s:%d",
        destinatario, asunto, adjunto_nombre, len(adjunto_bytes),
        cfg["host"], cfg["port"],
    )

    try:
        with smtplib.SMTP(cfg["host"], cfg["port"], timeout=30) as smtp:
            smtp.ehlo()
            if cfg["use_tls"]:
                smtp.starttls()
                smtp.ehlo()
            smtp.login(cfg["user"], cfg["password"])
            smtp.sendmail(from_addr, [destinatario], msg.as_string())
    except smtplib.SMTPAuthenticationError as exc:
        logger.exception("SMTP auth falló")
        raise EmailError(
            f"Autenticación SMTP rechazada: {exc.smtp_error.decode(errors='replace') if exc.smtp_error else exc}"
        ) from exc
    except smtplib.SMTPException as exc:
        logger.exception("SMTP error")
        raise EmailError(f"Error SMTP: {exc}") from exc
    except OSError as exc:
        logger.exception("No se pudo conectar al SMTP")
        raise EmailError(
            f"No se pudo conectar a {cfg['host']}:{cfg['port']} - {exc}"
        ) from exc

    return {
        "ok": True,
        "mensaje": f"Enviado a {destinatario}",
        "destinatario": destinatario,
        "asunto": asunto,
        "adjunto": adjunto_nombre,
    }


def cuerpo_html_default(
    tipo_nombre: str,
    numero_completo: str,
    empresa_razon_social: str,
    empresa_ruc: str = "",
    monto: Optional[str] = None,
    mensaje_extra: str = "",
) -> str:
    """Construye un cuerpo HTML genérico para los envíos por defecto."""
    extra_html = ""
    if mensaje_extra:
        extra_html = (
            f"<p style='margin:14px 0;color:#374151;'>"
            f"{mensaje_extra}</p>"
        )
    monto_html = ""
    if monto:
        monto_html = (
            f"<p style='margin:6px 0;color:#374151;'>"
            f"<strong>Importe total:</strong> {monto}</p>"
        )
    return f"""\
<!doctype html>
<html><body style="font-family:Arial,sans-serif;color:#1f2937;
       background:#f9fafb;padding:20px;">
  <div style="max-width:560px;margin:0 auto;background:#ffffff;
              border:1px solid #e5e7eb;border-radius:8px;padding:24px;">
    <h2 style="margin:0 0 12px 0;color:#1a3f5c;">Estimado cliente,</h2>
    <p style="margin:0 0 12px 0;">
      Adjuntamos el comprobante <strong>{tipo_nombre} {numero_completo}</strong>
      emitido por <strong>{empresa_razon_social}</strong>
      {f'(RUC {empresa_ruc})' if empresa_ruc else ''}.
    </p>
    {monto_html}
    {extra_html}
    <p style="margin:16px 0 0 0;color:#374151;">
      Por favor revisa el archivo PDF adjunto. Si tienes alguna consulta,
      no dudes en responder a este correo.
    </p>
    <hr style="border:none;border-top:1px solid #e5e7eb;margin:20px 0;">
    <p style="font-size:11px;color:#6b7280;margin:0;">
      Mensaje enviado automáticamente desde el sistema de facturación
      electrónica de {empresa_razon_social}.
    </p>
  </div>
</body></html>"""
