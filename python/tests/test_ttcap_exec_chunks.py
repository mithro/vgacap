# SPDX-License-Identifier: Apache-2.0
"""Tests for `RawRepl.exec_chunks()`: length-driven binary chunk framing.

The raw REPL does not escape the 0x04 it uses to end a script's stdout, and
sample data contains 0x04 like any other byte -- so the host must read each
chunk's declared length rather than scanning for a marker. These tests drive
`FakeChunkBoard`, which hands the client canned bytes without running
anything, so the exact byte sequence under test is the one the board sends.
"""

from __future__ import annotations

import struct

import pytest
from fake_repl import FakeChunkBoard

from ttcap.repl import RawRepl, ReplFramingError

#: Stands in for a real capture script: `FakeChunkBoard` streams its canned
#: chunks for the command carrying the `CFG = ` prefix `ttcap.mp.with_cfg()`
#: prepends, and answers anything else the way a plain `exec()` is answered.
SCRIPT = "CFG = {}\nrun()"


def chunk(tag: bytes, payload: bytes) -> bytes:
    return tag + struct.pack("<I", len(payload)) + payload


def connect(board: FakeChunkBoard) -> RawRepl:
    repl = RawRepl(board)
    repl.enter()
    return repl


def test_yields_a_payload_containing_the_eot_byte_intact():
    # The whole point: 0x04 inside sample data must not end the stream.
    payload = struct.pack("<I", 4) + bytes([0x04, 0x00, 0x04, 0xFF])
    board = FakeChunkBoard([chunk(b"RAW ", payload)])
    repl = connect(board)

    assert list(repl.exec_chunks(SCRIPT)) == [(b"RAW ", payload)]
    assert repl.last_stderr == ""


def test_leaves_the_repl_usable_for_a_following_exec():
    payload = struct.pack("<I", 2) + b"\x04\x04"
    board = FakeChunkBoard([chunk(b"RAW ", payload)], replies={"print(1)": "1\r\n"})
    repl = connect(board)

    list(repl.exec_chunks(SCRIPT))

    assert repl.exec("print(1)") == ("1\r\n", "")


def test_yields_several_chunks_split_across_reads():
    a = chunk(b"RAW ", struct.pack("<I", 2) + b"\xde\xad\xbe\xef")
    b = chunk(b"TIME", struct.pack("<QIIH", 0, 0, 7, 4) + b"done")
    # Hand the two chunks out in three reads that do not align with either
    # chunk boundary, so the client has to reassemble.
    both = a + b
    board = FakeChunkBoard([both[:5], both[5:17], both[17:]])
    repl = connect(board)

    assert list(repl.exec_chunks(SCRIPT)) == [(b"RAW ", a[8:]), (b"TIME", b[8:])]


def test_captures_stderr_after_the_terminator():
    board = FakeChunkBoard([], stderr="Traceback:\r\n  MemoryError\r\n")
    repl = connect(board)

    assert list(repl.exec_chunks(SCRIPT)) == []
    assert "MemoryError" in repl.last_stderr


def test_rejects_an_unknown_tag():
    board = FakeChunkBoard([chunk(b"JUNK", b"payload!")])
    repl = connect(board)

    with pytest.raises(ReplFramingError, match="JUNK"):
        list(repl.exec_chunks(SCRIPT))


def test_truncated_payload_times_out():
    # A header promising 100 bytes with only 5 behind it, and no terminator.
    board = FakeChunkBoard([b"RAW " + struct.pack("<I", 100) + b"short"], terminate=False)
    repl = connect(board)

    with pytest.raises(TimeoutError, match="100 bytes"):
        list(repl.exec_chunks(SCRIPT, timeout=0.2))


def test_read_exact_returns_exactly_n_bytes():
    board = FakeChunkBoard([])
    repl = RawRepl(board)
    board.write(b"\x01")  # make the board emit its banner
    assert repl.read_exact(4, 1.0) == b"raw "


def test_read_exact_discards_the_partial_buffer_on_timeout():
    board = FakeChunkBoard([])
    repl = RawRepl(board)
    board.write(b"\x01")
    with pytest.raises(TimeoutError):
        repl.read_exact(1000, 0.2)
    # Nothing of the banner is left to desynchronise a later read.
    with pytest.raises(TimeoutError):
        repl.read_exact(1, 0.1)
