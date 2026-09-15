# SPDX-License-Identifier: Apache-2.0
"""Tests for the `ttcap probe` subcommand."""

from __future__ import annotations

import sys
import types

import pytest
from fake_repl import FakeBinaryBoard

from ttcap import cli


@pytest.fixture
def fake_ttboard(monkeypatch):
    """Put a stub `ttboard.pins.gpio_map.GPIOMap` on the import path."""
    gpio_map = types.SimpleNamespace(all=staticmethod(lambda: {"rp_projclk": 0, "uo_out0": 5}))
    module = types.ModuleType("ttboard.pins.gpio_map")
    module.GPIOMap = gpio_map
    pins = types.ModuleType("ttboard.pins")
    pins.gpio_map = module
    ttboard = types.ModuleType("ttboard")
    ttboard.pins = pins
    monkeypatch.setitem(sys.modules, "ttboard", ttboard)
    monkeypatch.setitem(sys.modules, "ttboard.pins", pins)
    monkeypatch.setitem(sys.modules, "ttboard.pins.gpio_map", module)
    return gpio_map


def test_gpio_map_code_imports_gpiomap():
    # `GPIOMap` is not in the demo board's REPL globals; probing without the
    # import raises NameError on the board.
    assert "from ttboard.pins.gpio_map import GPIOMap" in cli.GPIO_MAP_CODE
    assert "GPIOMap.all()" in cli.GPIO_MAP_CODE


def test_probe_sends_the_gpiomap_import_to_the_board(monkeypatch, capsys, fake_ttboard):
    board = FakeBinaryBoard()
    monkeypatch.setattr(cli, "link_from_url", lambda url: board)

    report = cli.probe("serial:/dev/null")

    assert any("from ttboard.pins.gpio_map import GPIOMap" in c for c in board.commands)
    assert "rp_projclk" in report
    assert "rp_projclk" in capsys.readouterr().out
    assert board.closed


def test_probe_reports_a_board_error(monkeypatch):
    board = FakeBinaryBoard()  # no ttboard stub installed -> ImportError on the board
    monkeypatch.setattr(cli, "link_from_url", lambda url: board)

    with pytest.raises(RuntimeError, match="GPIOMap.all"):
        cli.probe("serial:/dev/null")
