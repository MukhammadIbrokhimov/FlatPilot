"""Tests for the filler registry's lookup behavior."""

from __future__ import annotations

import pytest

from flatpilot.errors import UnsupportedPlatformError
from flatpilot.fillers import get_filler


def test_get_filler_unknown_platform_raises_unsupported_platform_error():
    with pytest.raises(UnsupportedPlatformError) as exc_info:
        get_filler("inberlinwohnen")

    msg = str(exc_info.value)
    assert "inberlinwohnen" in msg
    assert "manually" in msg


def test_unsupported_platform_error_is_lookup_error():
    # The CLI's `except LookupError` branch (cli.apply) and auto_apply's
    # completeness check both rely on this MRO so the friendly message
    # surfaces without code branching on the specific class.
    with pytest.raises(LookupError):
        get_filler("immoscout24")
