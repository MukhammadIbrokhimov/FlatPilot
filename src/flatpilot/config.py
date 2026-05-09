"""Application paths and environment loading.

All user state lives under ``APP_DIR`` (default ``~/.flatpilot``). Override with
the ``FLATPILOT_DIR`` environment variable — useful for test isolation or for
pointing the container runtime at a non-default host directory.
"""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv


def _resolve_app_dir() -> Path:
    override = os.environ.get("FLATPILOT_DIR")
    if override:
        return Path(override).expanduser().resolve()
    return Path.home() / ".flatpilot"


APP_DIR: Path = _resolve_app_dir()
DB_PATH: Path = APP_DIR / "flatpilot.db"
PROFILE_PATH: Path = APP_DIR / "profile.json"
SESSIONS_DIR: Path = APP_DIR / "sessions"
GEOCODE_CACHE_PATH: Path = APP_DIR / "geocode_cache.json"
LOG_DIR: Path = APP_DIR / "logs"
ENV_PATH: Path = APP_DIR / ".env"
# User drops SCHUFA, Gehaltsnachweise, ID scans etc. here; the apply
# command looks up which of them to attach for a given platform from
# profile.attachments.
ATTACHMENTS_DIR: Path = APP_DIR / "attachments"
# One Markdown Anschreiben per rental platform, named ``<platform>.md``.
# The L2 composer (flatpilot.compose) reads from here; L4 apply wires
# it into the outgoing contact form.
TEMPLATES_DIR: Path = APP_DIR / "templates"
# Per-platform debug screenshots written when a filler raises
# SubmitVerificationError. FlatPilot-8kt — best-effort post-mortem aid
# so silent submit rejections become diagnosable after the fact.
FAILURE_SCREENSHOTS_DIR: Path = APP_DIR / "screenshots"


def ensure_dirs() -> None:
    APP_DIR.mkdir(parents=True, exist_ok=True)
    SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    ATTACHMENTS_DIR.mkdir(parents=True, exist_ok=True)
    TEMPLATES_DIR.mkdir(parents=True, exist_ok=True)
    FAILURE_SCREENSHOTS_DIR.mkdir(parents=True, exist_ok=True)


def load_env() -> None:
    if ENV_PATH.exists():
        load_dotenv(ENV_PATH)
        return
    cwd_env = Path.cwd() / ".env"
    if cwd_env.exists():
        load_dotenv(cwd_env)
