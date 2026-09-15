# SPDX-License-Identifier: Apache-2.0
"""Tests for the `ttcap probe` subcommand."""

from __future__ import annotations

import sys
import types

import pytest
from fake_repl import FakeBinaryBoard

from ttcap import cli
from ttcap.capture import CaptureError


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

    with pytest.raises(CaptureError, match="GPIOMap.all"):
        cli.probe("serial:/dev/null")


def test_probe_exits_one_without_a_traceback(monkeypatch, capsys):
    # `ttcap probe serial:/dev/nope` is the first command anyone runs, and
    # the exit-code contract promises 1 and a message, not a traceback.
    def _explode(url):
        raise OSError(2, "no such device")

    monkeypatch.setattr(cli, "link_from_url", _explode)

    assert cli.main(["probe", "serial:/dev/nope"]) == 1
    assert "probe failed: FileNotFoundError" in capsys.readouterr().err


def test_probe_exits_one_on_a_board_traceback(monkeypatch, capsys):
    board = FakeBinaryBoard()  # no ttboard stub -> ImportError on the board
    monkeypatch.setattr(cli, "link_from_url", lambda url: board)

    assert cli.main(["probe", "serial:/dev/null"]) == 1
    assert "probe failed: CaptureError" in capsys.readouterr().err


def test_probe_exits_one_when_the_board_replies_with_junk(monkeypatch, capsys):
    # A firmware banner or a leftover print() makes `ast.literal_eval()`
    # raise SyntaxError -- neither an OSError nor a ValueError, so it used
    # to escape the CLI's error contract entirely.
    board = FakeBinaryBoard()
    monkeypatch.setattr(cli, "link_from_url", lambda url: board)
    monkeypatch.setattr(
        cli.RawRepl, "exec", lambda self, code, **kw: ("MicroPython v1.24\r\n", "")
    )

    assert cli.main(["probe", "serial:/dev/null"]) == 1
    err = capsys.readouterr().err
    assert "probe failed: CaptureError" in err
    assert "--profile rp2040" in err


def test_syntax_error_is_one_of_the_named_failures():
    # `ast.literal_eval()` raises it, and it is not a subclass of anything
    # else in the tuple.
    assert SyntaxError in cli.CAPTURE_FAILURES


def test_throughput_exits_one_without_a_traceback(monkeypatch, capsys):
    def _explode(url):
        raise OSError(2, "no such device")

    monkeypatch.setattr(cli, "link_from_url", _explode)

    assert cli.main(["throughput", "serial:/dev/nope"]) == 1
    assert "throughput failed: FileNotFoundError" in capsys.readouterr().err


def test_throughput_exits_one_when_the_board_goes_quiet(monkeypatch, capsys):
    monkeypatch.setattr(cli, "link_from_url", lambda url: FakeBinaryBoard())

    def _timeout(repl, total, block):
        raise TimeoutError("timed out waiting for b'OK'")

    monkeypatch.setattr(cli, "measure_throughput", _timeout)

    assert cli.main(["throughput", "serial:/dev/null"]) == 1
    assert "throughput failed: TimeoutError" in capsys.readouterr().err
