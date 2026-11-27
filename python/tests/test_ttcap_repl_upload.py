# SPDX-License-Identifier: Apache-2.0
"""`RawRepl.upload()` of bytes, checked by length and crc32.

The board is `fake_repl.FakeBinaryBoard`, whose `open()` reaches an
in-memory filesystem (`board.files`) rather than the host's, so the tests
see exactly what landed on the "flash" and can corrupt a write on its way
there.
"""

from __future__ import annotations

import base64
import random
import re
import sys
import zlib

import pytest

from fake_repl import FakeBinaryBoard
from ttcap.repl import RawRepl, UploadError

HELD = "/_vgacap/held.bin"

#: About the size of the FPGA bitstream 4d loads, and not a multiple of
#: 2048, so the last slice is short.
PAYLOAD_LEN = 104_000

SLICE = re.compile(r"a2b_base64\(b'([A-Za-z0-9+/=]*)'\)")


def _payload(seed: int = 4, n: int = PAYLOAD_LEN) -> bytes:
    return random.Random(seed).randbytes(n)


def _slices(board: FakeBinaryBoard) -> list[bytes]:
    """The payload bytes each slice command carried, in order."""
    out = []
    for code in board.commands:
        m = SLICE.search(code)
        if m:
            out.append(base64.b64decode(m.group(1)))
    return out


def _repl(board: FakeBinaryBoard) -> RawRepl:
    r = RawRepl(board)
    r.enter()
    return r


def test_the_fake_board_has_its_own_filesystem():
    board = FakeBinaryBoard()
    repl = _repl(board)

    _out, err = repl.exec("open('/nowhere.bin','rb')")
    assert "OSError" in err

    repl.exec("f=open('/a.bin','wb')\nf.write(b'xy')\nf.write(b'z')\nf.close()")
    assert board.files == {"/a.bin": b"xyz"}


def test_a_104_kb_payload_round_trips_in_2_kb_slices():
    payload = _payload()
    board = FakeBinaryBoard()

    wrote = _repl(board).upload(HELD, payload)

    assert wrote is True
    assert board.files[HELD] == payload
    sizes = [len(s) for s in _slices(board)]
    assert sizes == [2048] * (PAYLOAD_LEN // 2048) + [PAYLOAD_LEN % 2048]
    assert b"".join(_slices(board)) == payload


def test_a_str_is_still_uploaded_as_its_utf8():
    board = FakeBinaryBoard()

    _repl(board).upload("/s.py", "print('café')\n")

    assert board.files["/s.py"] == "print('café')\n".encode("utf-8")


def test_an_empty_payload_writes_an_empty_file():
    board = FakeBinaryBoard()

    assert _repl(board).upload("/e.bin", b"") is True
    assert board.files["/e.bin"] == b""
    assert _slices(board) == []


def test_a_matching_file_is_not_sent_again():
    payload = _payload()
    board = FakeBinaryBoard(files={HELD: payload})

    wrote = _repl(board).upload(HELD, payload)

    assert wrote is False
    assert _slices(board) == []
    assert not any("open(%r,'wb')" % HELD in c for c in board.commands)
    assert board.files[HELD] == payload


def test_a_file_of_another_length_is_replaced():
    payload = _payload()
    board = FakeBinaryBoard(files={HELD: payload[:-1]})

    assert _repl(board).upload(HELD, payload) is True
    assert board.files[HELD] == payload
    assert len(_slices(board)) == PAYLOAD_LEN // 2048 + 1


def test_a_file_of_the_same_length_and_other_bytes_is_replaced():
    payload = _payload()
    stale = bytearray(payload)
    stale[50_000] ^= 0x01
    board = FakeBinaryBoard(files={HELD: bytes(stale)})

    assert _repl(board).upload(HELD, payload) is True
    assert board.files[HELD] == payload


def _flip_a_byte_of_slice(index: int):
    def hook(path, n, data):
        if n != index:
            return data
        bad = bytearray(data)
        bad[7] ^= 0x40
        return bytes(bad)

    return hook


def test_a_slice_corrupted_on_the_board_raises_with_both_values():
    payload = _payload()
    board = FakeBinaryBoard(on_file_write=_flip_a_byte_of_slice(17))

    with pytest.raises(UploadError) as raised:
        _repl(board).upload(HELD, payload)

    bad = board.files[HELD]
    msg = str(raised.value)
    assert f"host len={PAYLOAD_LEN} crc32=0x{zlib.crc32(payload):08x}" in msg
    assert f"board len={len(bad)} crc32=0x{zlib.crc32(bad):08x}" in msg
    assert HELD in msg
    # Left in place: the next run's check decides.
    assert bad != payload and len(bad) == PAYLOAD_LEN


def test_a_slice_lost_on_the_board_raises_with_both_lengths():
    payload = _payload()
    board = FakeBinaryBoard(
        on_file_write=lambda path, n, data: b"" if n == 3 else data,
    )

    with pytest.raises(UploadError) as raised:
        _repl(board).upload(HELD, payload)

    msg = str(raised.value)
    assert f"host len={PAYLOAD_LEN} " in msg
    assert f"board len={PAYLOAD_LEN - 2048} " in msg


def test_the_next_upload_repairs_a_corrupted_file():
    payload = _payload()
    board = FakeBinaryBoard(on_file_write=_flip_a_byte_of_slice(0))
    repl = _repl(board)
    with pytest.raises(UploadError):
        repl.upload(HELD, payload)

    board.on_file_write = None
    assert repl.upload(HELD, payload) is True
    assert board.files[HELD] == payload


def test_the_board_reads_the_file_back_in_pieces_not_whole():
    # 104 KB in one read() would need that much contiguous heap on the
    # board, which is what writing the bitstream to a file avoids.
    payload = _payload()
    board = FakeBinaryBoard(files={HELD: payload})

    _repl(board).upload(HELD, payload)

    check = [c for c in board.commands if "crc32" in c]
    assert check, board.commands
    assert all(".read()" not in c for c in check)
    assert all("readinto" in c for c in check)


def test_a_board_without_crc32_fails_loudly(monkeypatch):
    board = FakeBinaryBoard()
    monkeypatch.delattr(sys.modules["ubinascii"], "crc32")

    with pytest.raises(RuntimeError, match="crc32"):
        _repl(board).upload(HELD, b"abc")
