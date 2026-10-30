from __future__ import annotations

import logging
import smtplib
from dataclasses import dataclass
from email.message import EmailMessage

from codecare.config import Settings, get_settings

log = logging.getLogger("codecare.email")
OUTBOX: list[dict] = []


@dataclass
class OutboundEmail:
    to: str
    subject: str
    text: str


def reset_outbox() -> None:
    OUTBOX.clear()


def delivery_mode(settings: Settings | None = None) -> str:
    settings = settings or get_settings()
    if settings.email_configured:
        return "smtp"
    if settings.app_env == "production":
        return "unavailable"
    return "console"


def delivery_message(mode: str) -> str:
    if mode == "smtp":
        return "If an account exists for that address, a message was sent."
    if mode == "console":
        return "Email delivery is in console mode. If an account exists, the message was written to the server log."
    if mode == "failed":
        return "Email delivery failed, so the message was not sent."
    if mode == "unavailable":
        return "Email delivery is not configured, so no message was sent."
    raise AssertionError(f"Unknown email delivery mode: {mode}")


def _send_smtp(settings: Settings, message: OutboundEmail) -> None:
    payload = EmailMessage()
    payload["From"] = settings.smtp_from
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
    if mode == "smtp":
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
        log.warning("Email not sent to %s because SMTP is not configured", message.to)
    OUTBOX.append(record)
    return record["delivery"]
