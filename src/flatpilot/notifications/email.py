"""SMTP email adapter.

Credentials come from the environment using a configurable prefix.
The default prefix is ``SMTP`` (so ``SMTP_HOST``, ``SMTP_PORT``,
``SMTP_USER``, ``SMTP_PASSWORD``, ``SMTP_FROM``). Saved-search-scoped
overrides may pass a different prefix (e.g. ``ROOMMATE_SMTP``) to route
notifications through a separate SMTP account.

Port 465 triggers ``SMTP_SSL``; any other port opens a plain connection
and upgrades via STARTTLS.

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


_REQUIRED_SUFFIXES = ("HOST", "PORT", "USER", "PASSWORD", "FROM")
_TIMEOUT = 30.0


class EmailError(RuntimeError):
    pass


def _env_names(prefix: str) -> tuple[str, ...]:
    return tuple(f"{prefix}_{suffix}" for suffix in _REQUIRED_SUFFIXES)


def send(
    to: str,
    subject: str,
    plain: str,
    html: str | None = None,
    *,
    smtp_env: str | None = None,
) -> None:
    prefix = smtp_env if smtp_env is not None else "SMTP"
    names = _env_names(prefix)
    values = {name: os.environ.get(name) for name in names}
    missing = [name for name, value in values.items() if not value]
    if missing:
        raise EmailError(f"SMTP config missing: {', '.join(missing)}")

    host_key, port_key, user_key, password_key, from_key = names
    try:
        port = int(values[port_key])  # type: ignore[arg-type]
    except ValueError as exc:
        raise EmailError(f"{port_key} must be an integer, got {values[port_key]!r}") from exc

    msg = EmailMessage()
    msg["From"] = values[from_key]
    msg["To"] = to
    msg["Subject"] = subject
    msg.set_content(plain)
    if html:
        msg.add_alternative(html, subtype="html")

    host: str = values[host_key]  # type: ignore[assignment]
    user: str = values[user_key]  # type: ignore[assignment]
    password: str = values[password_key]  # type: ignore[assignment]

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
