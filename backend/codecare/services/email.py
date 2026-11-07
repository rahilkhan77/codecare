from __future__ import annotations

import logging
import smtplib
from dataclasses import dataclass
from email.message import EmailMessage

import httpx

from codecare.config import Settings, get_settings

log = logging.getLogger("codecare.email")
OUTBOX: list[dict] = []
RESEND_API = "https://api.resend.com"


@dataclass
class OutboundEmail:
    to: str
    subject: str
    text: str


def reset_outbox() -> None:
    OUTBOX.clear()


def delivery_mode(settings: Settings | None = None) -> str:
    settings = settings or get_settings()
    if settings.resend_api_key.strip():
        return "resend"
    if settings.smtp_host.strip():
        return "smtp"
    if settings.app_env == "production":
        return "unavailable"
    return "console"


def delivery_message(mode: str) -> str:
    if mode == "resend":
        return "If an account exists for that address, a message was sent."
    if mode == "smtp":
        return "If an account exists for that address, a message was sent."
    if mode == "console":
        return "Email delivery is in console mode. If an account exists, the message was written to the server log."
    if mode == "failed":
        return "Email delivery failed, so the message was not sent."
    if mode == "unavailable":
        return "Email delivery is not configured, so no message was sent."
    raise AssertionError(f"Unknown email delivery mode: {mode}")


def _from_address(settings: Settings) -> str:
    return settings.email_from or settings.smtp_from or "CodeCare <noreply@codecare.si>"


def _send_resend(settings: Settings, message: OutboundEmail) -> None:
    response = httpx.post(
        f"{RESEND_API}/emails",
        headers={"Authorization": f"Bearer {settings.resend_api_key}", "Content-Type": "application/json"},
        json={
            "from": _from_address(settings),
            "to": [message.to],
            "subject": message.subject,
            "text": message.text,
        },
        timeout=20,
    )
    if response.status_code >= 400:
        raise RuntimeError(f"Resend returned HTTP {response.status_code}")


def check_resend_key(api_key: str) -> int:
    """Validate a Resend key with a read-only request. This does not send email."""
    response = httpx.get(
        f"{RESEND_API}/domains",
        headers={"Authorization": f"Bearer {api_key}"},
        timeout=20,
    )
    return response.status_code


def _send_smtp(settings: Settings, message: OutboundEmail) -> None:
    payload = EmailMessage()
    payload["From"] = settings.smtp_from or _from_address(settings)
    payload["To"] = message.to
    payload["Subject"] = message.subject
    payload.set_content(message.text)
    with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=20) as smtp:
        if settings.smtp_tls:
            smtp.starttls()
        if settings.smtp_username:
            smtp.login(settings.smtp_username, settings.smtp_password)
        smtp.send_message(payload)


def deliver(message: OutboundEmail, settings: Settings | None = None) -> str:
    settings = settings or get_settings()
    mode = delivery_mode(settings)
    record = {"to": message.to, "subject": message.subject, "text": message.text, "delivery": mode}
    if mode == "resend":
        try:
            _send_resend(settings, message)
        except Exception:
            log.exception("Resend delivery failed")
            record["delivery"] = "failed"
            OUTBOX.append(record)
            return "failed"
    elif mode == "smtp":
        try:
            _send_smtp(settings, message)
        except Exception:
            log.exception("SMTP delivery failed")
            record["delivery"] = "failed"
            OUTBOX.append(record)
            return "failed"
    elif mode == "console":
        log.info("Dev email to %s — %s\n%s", message.to, message.subject, message.text)
    else:
        log.warning("Email not sent to %s because neither Resend nor SMTP is configured", message.to)
    OUTBOX.append(record)
    return record["delivery"]
