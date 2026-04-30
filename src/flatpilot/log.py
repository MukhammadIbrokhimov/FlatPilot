"""Rotating file + console logging setup.

Writes daily-rotated logs to ``~/.flatpilot/logs/flatpilot.log`` and mirrors
them to stderr at INFO. Called once from the typer callback in
:mod:`flatpilot.cli` so every CLI invocation has logging available.

The module is named ``log`` instead of ``logging`` to keep ``import logging``
inside flatpilot unambiguous — Python 3 uses absolute imports by default,
but ``logging`` is a stdlib name worth not shadowing.
"""

from __future__ import annotations

import logging
import logging.handlers
import sys

from flatpilot.config import LOG_DIR, ensure_dirs

_LOG_FILE = "flatpilot.log"
_BACKUP_DAYS = 14
_FORMAT = "%(asctime)s %(levelname)-8s %(name)s: %(message)s"
_DATEFMT = "%Y-%m-%d %H:%M:%S"

_CONFIGURED = False


def setup_logging(level: int = logging.INFO) -> None:
    global _CONFIGURED
    if _CONFIGURED:
        return

    ensure_dirs()
    formatter = logging.Formatter(_FORMAT, datefmt=_DATEFMT)

    file_handler = logging.handlers.TimedRotatingFileHandler(
        LOG_DIR / _LOG_FILE,
        when="midnight",
        interval=1,
        backupCount=_BACKUP_DAYS,
        encoding="utf-8",
    )
    file_handler.setFormatter(formatter)
    file_handler.setLevel(level)

    console_handler = logging.StreamHandler(sys.stderr)
    console_handler.setFormatter(formatter)
    console_handler.setLevel(level)

    root = logging.getLogger()
    root.setLevel(level)
    root.addHandler(file_handler)
    root.addHandler(console_handler)

    _CONFIGURED = True
