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


def ensure_dirs() -> None:
    APP_DIR.mkdir(parents=True, exist_ok=True)
    SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
    LOG_DIR.mkdir(parents=True, exist_ok=True)


def load_env() -> None:
    if ENV_PATH.exists():
        load_dotenv(ENV_PATH)
        return
    cwd_env = Path.cwd() / ".env"
    if cwd_env.exists():
        load_dotenv(cwd_env)
