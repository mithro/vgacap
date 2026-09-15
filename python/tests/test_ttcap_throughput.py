# SPDX-License-Identifier: Apache-2.0
"""Tests for the USB-CDC throughput measurement (`ttcap throughput`).

`FakeBinaryBoard` runs the *real* `ttcap/mp/throughput.py` source on the
host with a stubbed `sys`, so these tests cover the MicroPython script text
that gets shipped to the board as well as the host-side measurement.
"""

from __future__ import annotations

import pytest
from fake_repl import FakeBinaryBoard

import ttcap.mp as mp
from ttcap.repl import RawRepl
from ttcap.throughput import PATTERN, ThroughputResult, measure_throughput, pattern_slice


@pytest.fixture
def board():
    return FakeBinaryBoard()


def _repl(board: FakeBinaryBoard) -> RawRepl:
    repl = RawRepl(board)
    repl.enter()
    return repl


# -- the script package ---------------------------------------------------


def test_load_returns_the_script_source():
    source = mp.load("throughput.py")
    assert "SPDX-License-Identifier: Apache-2.0" in source
    assert "CFG" in source


def test_load_rejects_a_path_like_name():
    with pytest.raises(ValueError):
        mp.load("../boards.py")


def test_with_cfg_prepends_a_cfg_literal():
    combined = mp.with_cfg("print(CFG)\n", {"total": 7, "block": 3})
    assert combined.startswith("CFG = ")
    assert combined.endswith("print(CFG)\n")
    namespace: dict = {}
    exec(compile(combined, "<throughput>", "exec"), namespace)  # noqa: S102
    assert namespace["CFG"] == {"total": 7, "block": 3}


def test_script_compiles_after_cfg_substitution():
    source = mp.with_cfg(mp.load("throughput.py"), {"total": 1024, "block": 256})
    compile(source, "throughput.py", "exec")


# -- the pattern ----------------------------------------------------------


def test_pattern_contains_no_raw_repl_terminator():
    # 0x04 ends stdout in the raw REPL, so the pattern must not contain it.
    assert 0x04 not in PATTERN
    assert len(PATTERN) == 255
    assert set(PATTERN) == set(range(256)) - {0x04}


def test_pattern_slice_repeats_with_the_pattern_period():
    assert pattern_slice(0, 255) == PATTERN
    assert pattern_slice(255, 255) == PATTERN
    assert pattern_slice(10, 300) == (PATTERN + PATTERN)[10:310]


# -- measurement ----------------------------------------------------------


def test_measure_throughput_counts_every_byte(board):
    result = measure_throughput(_repl(board), total=100_000, block=4096)

    assert isinstance(result, ThroughputResult)
    assert result.requested == 100_000
    assert result.received == 100_000
    assert result.first_mismatch is None
    assert not result.corrupt
    assert not result.short
    assert result.seconds >= 0.0
    assert "corrupt=no" in result.format()


def test_measure_throughput_sends_cfg_to_the_board(board):
    measure_throughput(_repl(board), total=8192, block=1024)

    assert board.commands[-1].startswith("CFG = {'total': 8192, 'block': 1024}")


def test_measure_throughput_writes_in_block_sized_chunks(board):
    # The board-side script must issue whole-block writes, not one huge one.
    writes: list[int] = []
    board.record_write_sizes(writes)

    measure_throughput(_repl(board), total=10_000, block=4096)

    assert writes == [4096, 4096, 1808]


def test_measure_throughput_detects_a_corrupted_byte():
    def flip(sink: bytearray) -> None:
        sink[5000] ^= 0xFF

    board = FakeBinaryBoard(corrupt=flip)
    result = measure_throughput(_repl(board), total=20_000, block=4096)

    assert result.received == 20_000
    assert result.corrupt
    assert result.first_mismatch == 5000
    assert "corrupt=yes" in result.format()


def test_measure_throughput_detects_a_short_read():
    def truncate(sink: bytearray) -> None:
        del sink[9_000:]

    board = FakeBinaryBoard(corrupt=truncate)
    result = measure_throughput(_repl(board), total=20_000, block=4096)

    assert result.received == 9_000
    assert result.short
    assert not result.corrupt


def test_throughput_result_formats_the_expected_fields():
    result = ThroughputResult(requested=1024, received=1024, seconds=2.0, first_mismatch=None)

    assert result.kbytes_per_s == pytest.approx(0.5)
    assert result.format() == "bytes=1024 seconds=2.000 kbytes_per_s=0.5 corrupt=no"


def test_throughput_result_kbytes_per_s_is_zero_for_zero_elapsed():
    result = ThroughputResult(requested=1024, received=1024, seconds=0.0, first_mismatch=None)

    assert result.kbytes_per_s == 0.0


# -- cli ------------------------------------------------------------------


def test_cli_throughput_returns_1_on_a_short_read(monkeypatch, capsys):
    from ttcap import cli

    def truncate(sink: bytearray) -> None:
        del sink[100:]

    board = FakeBinaryBoard(corrupt=truncate)
    monkeypatch.setattr(cli, "link_from_url", lambda url: board)

    assert cli.main(["throughput", "serial:/dev/null", "--bytes", "4096", "--block", "1024"]) == 1
    assert "bytes=100" in capsys.readouterr().out
    assert board.closed


def test_cli_throughput_returns_0_when_clean(monkeypatch, capsys):
    from ttcap import cli

    board = FakeBinaryBoard()
    monkeypatch.setattr(cli, "link_from_url", lambda url: board)

    assert cli.main(["throughput", "serial:/dev/null", "--bytes", "4096"]) == 0
    assert "corrupt=no" in capsys.readouterr().out
