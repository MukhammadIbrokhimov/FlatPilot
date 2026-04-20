"""SMTP email adapter.

Credentials come from the environment: ``SMTP_HOST``, ``SMTP_PORT``,
``SMTP_USER``, ``SMTP_PASSWORD``, ``SMTP_FROM``. Port 465 triggers
``SMTP_SSL``; any other port opens a plain connection and upgrades via
STARTTLS. Messages are multipart (plain text + optional HTML alternative).

Note: this module lives at ``flatpilot.notifications.email`` — the stdlib
``email`` package is still imported correctly below thanks to Python 3's
absolute imports.
"""

from __future__ import annotations

import logging
import os
import smtplib
from email.message import EmailMessage


logger = logging.getLogger(__name__)


_REQUIRED_ENV = ("SMTP_HOST", "SMTP_PORT", "SMTP_USER", "SMTP_PASSWORD", "SMTP_FROM")
_TIMEOUT = 30.0


class EmailError(RuntimeError):
    pass


def send(to: str, subject: str, plain: str, html: str | None = None) -> None:
    values = {name: os.environ.get(name) for name in _REQUIRED_ENV}
    missing = [name for name, value in values.items() if not value]
    if missing:
        raise EmailError(f"SMTP config missing: {', '.join(missing)}")

    try:
        port = int(values["SMTP_PORT"])  # type: ignore[arg-type]
    except ValueError as exc:
        raise EmailError(f"SMTP_PORT must be an integer, got {values['SMTP_PORT']!r}") from exc

    msg = EmailMessage()
    msg["From"] = values["SMTP_FROM"]
    msg["To"] = to
    msg["Subject"] = subject
    msg.set_content(plain)
    if html:
        msg.add_alternative(html, subtype="html")

    host = values["SMTP_HOST"]
    user = values["SMTP_USER"]
    password = values["SMTP_PASSWORD"]

    try:
        if port == 465:
            with smtplib.SMTP_SSL(host, port, timeout=_TIMEOUT) as smtp:
                smtp.login(user, password)
                smtp.send_message(msg)
        else:
            with smtplib.SMTP(host, port, timeout=_TIMEOUT) as smtp:
                smtp.starttls()
                smtp.login(user, password)
                smtp.send_message(msg)
    except (smtplib.SMTPException, OSError) as exc:
        raise EmailError(f"Email send failed: {exc}") from exc

    logger.info("Email delivered to %s via %s:%d", to, host, port)
