"""Telegram Bot API ``sendMessage`` adapter.

No polling, no webhook — the bot only sends outbound messages, so all we
need is an HTTPS POST with the bot token in the URL and the chat id in
the payload. Token is read from the env var named in
``profile.notifications.telegram.bot_token_env`` (default
``TELEGRAM_BOT_TOKEN``), chat id from ``profile.notifications.telegram.chat_id``.
"""

from __future__ import annotations

import logging
import os

import httpx

from flatpilot.profile import Profile

logger = logging.getLogger(__name__)


API_BASE = "https://api.telegram.org"
_TIMEOUT = 10.0


class TelegramError(RuntimeError):
    pass


def send(
    profile: Profile,
    text: str,
    *,
    parse_mode: str = "HTML",
    disable_web_page_preview: bool = False,
    bot_token_env: str | None = None,
    chat_id: str | None = None,
) -> None:
    tg = profile.notifications.telegram
    if not tg.enabled:
        logger.debug("Telegram notifications disabled in profile; skipping send")
        return

    resolved_token_env = bot_token_env if bot_token_env is not None else tg.bot_token_env
    resolved_chat_id = chat_id if chat_id is not None else tg.chat_id

    token = os.environ.get(resolved_token_env)
    if not token:
        raise TelegramError(f"bot token not found in env var {resolved_token_env!r}")
    if not resolved_chat_id:
        raise TelegramError("chat_id not configured (override or base both empty)")

    url = f"{API_BASE}/bot{token}/sendMessage"
    payload = {
        "chat_id": resolved_chat_id,
        "text": text,
        "parse_mode": parse_mode,
        "disable_web_page_preview": disable_web_page_preview,
    }

    try:
        response = httpx.post(url, json=payload, timeout=_TIMEOUT)
        response.raise_for_status()
    except httpx.HTTPError as exc:
        raise TelegramError(f"Telegram send failed: {exc}") from exc

    body = response.json()
    if not body.get("ok"):
        raise TelegramError(
            f"Telegram API returned ok=false: {body.get('description', 'unknown error')}"
        )

    logger.info("Telegram message delivered to chat %s", resolved_chat_id)
