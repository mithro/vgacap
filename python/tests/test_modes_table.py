# SPDX-License-Identifier: Apache-2.0
"""The Python mode table must equal the C table, field by field."""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from vgacap import modes

BIN = Path(__file__).resolve().parents[2] / "build" / "test_modes"

pytestmark = pytest.mark.skipif(not BIN.exists(), reason="needs build/test_modes (cmake --build build)")

FIELDS = ("name", "h_active", "h_front", "h_sync", "h_back", "v_active", "v_front", "v_sync", "v_back",
          "h_sync_positive", "v_sync_positive", "extended")


def c_rows() -> list[tuple]:
    out = subprocess.run([str(BIN), "--dump"], check=True, capture_output=True, text=True).stdout
    rows = []
    for line in out.splitlines():
        parts = line.split(",")
        rows.append((parts[0], *(int(p) for p in parts[1:])))
    return rows


def test_python_table_equals_c_table():
    rows = c_rows()
    assert len(rows) == len(modes.TABLE) == 12
    for row, mode in zip(rows, modes.TABLE):
        for field, c_value in zip(FIELDS, row):
            assert int(getattr(mode, field)) == c_value if field != "name" else mode.name == c_value, (mode.name, field)


def test_match_mirrors_c():
    assert modes.match(1040, 666).name == "800x600@72"
    assert modes.match(801, 525) is None


def test_match_cpl_skips_extended():
    assert modes.match_cpl(1056).name == "800x600@60"
    assert modes.match_cpl(1040) is None
    assert modes.match_cpl(800).name == "640x480@60"
