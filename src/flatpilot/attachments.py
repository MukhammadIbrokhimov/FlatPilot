"""Platform-aware attachment resolution.

Users drop SCHUFA, Gehaltsnachweise, ID scans and similar PDFs / images
into ``~/.flatpilot/attachments/`` and name which of them to attach for
each rental platform in ``profile.attachments``. The L4 apply command
and the N-epic auto-apply loop call :func:`resolve_for_platform` to turn
those names into absolute paths before handing them to the Playwright
form filler.

Design: filenames in the profile are always relative to
``ATTACHMENTS_DIR``. Absolute paths are rejected — a profile that
references ``/etc/passwd`` or ``~/Documents/...`` would be a trust
boundary leak. ``per_platform`` takes precedence over ``default``; when
a platform has an empty list we treat that as "no attachments for this
platform", not "fall back to default".
"""

from __future__ import annotations

from pathlib import Path

from flatpilot.config import ATTACHMENTS_DIR
from flatpilot.profile import Profile


class AttachmentError(RuntimeError):
    pass


def resolve_for_platform(profile: Profile, platform: str) -> list[Path]:
    """Return absolute attachment paths for ``platform``, validated on disk."""

    names = profile.attachments.per_platform.get(platform, profile.attachments.default)
    paths: list[Path] = []
    missing: list[str] = []
    for name in names:
        if Path(name).is_absolute():
            raise AttachmentError(
                f"attachment {name!r} is absolute — filenames must be relative "
                f"to {ATTACHMENTS_DIR}"
            )
        resolved = (ATTACHMENTS_DIR / name).resolve()
        try:
            resolved.relative_to(ATTACHMENTS_DIR.resolve())
        except ValueError as exc:
            # ``..`` in the filename would escape the attachments dir.
            raise AttachmentError(
                f"attachment {name!r} escapes {ATTACHMENTS_DIR}"
            ) from exc
        if not resolved.is_file():
            missing.append(name)
            continue
        paths.append(resolved)
    if missing:
        raise AttachmentError(
            f"attachments missing from {ATTACHMENTS_DIR}: {', '.join(missing)}"
        )
    return paths
