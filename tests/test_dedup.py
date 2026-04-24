"""Tests for flatpilot.matcher.dedup."""

from __future__ import annotations

import pytest

from flatpilot.matcher.dedup import normalize_address


@pytest.mark.parametrize(
    "raw, expected",
    [
        # Straße family → "str"
        ("Greifswalder Straße 42", "greifswalder str 42"),
        ("greifswalder strasse 42", "greifswalder str 42"),
        ("Greifswalder Str. 42", "greifswalder str 42"),
        ("  Greifswalder  Strasse, 42 ", "greifswalder str 42"),
        # Postcode + city prefix (Kleinanzeigen flavour)
        ("10435 Berlin, Greifswalder Str. 42", "greifswalder str 42"),
        ("Greifswalder Str. 42, Berlin", "greifswalder str 42"),
        # House-number suffix collapsed when written with a space
        ("Greifswalder Str. 42 a", "greifswalder str 42a"),
        ("Greifswalder Str. 42A", "greifswalder str 42a"),
        # Preserved distinction: "42" and "42a" must stay different
        ("Greifswalder Str. 42", "greifswalder str 42"),
        # Empty / whitespace / None → None
        (None, None),
        ("", None),
        ("   ", None),
    ],
)
def test_normalize_address(raw, expected):
    assert normalize_address(raw) == expected


def test_normalize_preserves_distinct_house_numbers():
    """42 vs 42a must produce different outputs — different buildings."""
    assert normalize_address("Greifswalder Str. 42") != normalize_address(
        "Greifswalder Str. 42a"
    )


def test_normalize_preserves_umlauts():
    """Non-Straße umlauts stay put."""
    assert normalize_address("Schöneberger Ufer 1") == "schöneberger ufer 1"
