# SPDX-License-Identifier: Apache-2.0
"""`RawRepl.upload()` of bytes, checked by length and crc32.

The board is `fake_repl.FakeBinaryBoard`, whose `open()` reaches an
in-memory filesystem (`board.files`) rather than the host's, so the tests
see exactly what landed on the "flash" and can corrupt a write on its way
there.
"""

from __future__ import annotations

import base64
import errno
import random
import re
import sys
import zlib

import pytest

from fake_repl import FakeBinaryBoard
from ttcap.repl import LinkClosed, RawRepl, UploadError

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


def test_a_garbled_check_reply_is_refused_not_read_as_absent():
    # A corrupted reply must not pass for "absent" (which would quietly
    # rewrite the file) or for a length and crc32 the board never sent.
    def garble(sink):
        sink[:] = sink.replace(b"len=", b"len=?")

    board = FakeBinaryBoard(files={HELD: b"abc"}, corrupt=garble)

    with pytest.raises(RuntimeError, match="unexpected reply to the upload check"):
        _repl(board).upload(HELD, b"abc")
    assert not any("open(%r,'wb')" % HELD in c for c in board.commands)


def _four_bytes_giving_crc32(prefix: bytes, target: int) -> bytes:
    """The 4 bytes that, appended to `prefix`, make its crc32 `target`.

    With the prefix fixed, the crc32 is affine over GF(2) in the 32 bits
    appended, so its 32 columns are measured and the system solved.
    """
    base = zlib.crc32(prefix)

    def crc(bits: int) -> int:
        return zlib.crc32(bits.to_bytes(4, "little"), base)

    zero = crc(0)
    # (column of the linear map, the unknown bits it stands for)
    rows = [(crc(1 << i) ^ zero, 1 << i) for i in range(32)]
    want = target ^ zero
    x = 0
    for bit in range(32):
        pivot = next(i for i, (col, _) in enumerate(rows) if col >> bit & 1)
        pcol, pvar = rows.pop(pivot)
        rows = [(c ^ pcol, v ^ pvar) if c >> bit & 1 else (c, v) for c, v in rows]
        if want >> bit & 1:
            want ^= pcol
            x ^= pvar
    forged = x.to_bytes(4, "little")
    assert zlib.crc32(prefix + forged) == target
    return forged


def test_a_longer_file_with_the_same_crc32_is_replaced():
    # Not a realistic failure, but it is what makes the check length AND
    # crc32 rather than crc32 alone.
    payload = _payload()
    stale = payload + _four_bytes_giving_crc32(payload, zlib.crc32(payload))
    board = FakeBinaryBoard(files={HELD: stale})

    assert _repl(board).upload(HELD, payload) is True
    assert board.files[HELD] == payload


# -- fix round 1 ---------------------------------------------------------------


def _fail_slice(index: int):
    """A write hook whose `index`-th write fails, as a heap or link failure
    would, without the handle itself erring."""

    def hook(path, n, data):
        if n == index:
            raise MemoryError("memory allocation failed, allocating 2048 bytes")
        return data

    return hook


def test_every_handle_is_closed():
    # On littlefs the data and size reach the directory only at close
    # (the fake models that), so a missing close is a short file there.
    # The check's read handles are closed too, rather than left to a GC.
    payload = _payload()
    board = FakeBinaryBoard(files={HELD: b"old"})

    _repl(board).upload(HELD, payload)

    assert len(board.readers) == 2 and len(board.writers) == 1
    assert board.unclosed_handles() == []
    assert board.files[HELD] == payload


def test_a_failed_slice_closes_its_handle_before_the_retry():
    payload = _payload()
    board = FakeBinaryBoard(on_file_write=_fail_slice(10))
    repl = _repl(board)
    with pytest.raises(RuntimeError, match="MemoryError"):
        repl.upload(HELD, payload)
    assert board.unclosed_writers() == []

    board.on_file_write = None
    assert repl.upload(HELD, payload) is True
    # A handle left open by the failure, finalised by a GC after the retry,
    # would commit its 10 stale slices over the retry's file.
    board.collect()
    assert board.files[HELD] == payload


def test_a_handle_left_by_a_lost_host_is_closed_before_the_check():
    # The host died mid-upload, so nobody sent the close: the board still
    # holds the write handle under upload()'s documented name.
    payload = _payload()
    board = FakeBinaryBoard()
    repl = _repl(board)
    _out, err = repl.exec(
        f"_ttcap_up_f=open({HELD!r},'wb')\n_ttcap_up_f.write(b'stale')"
    )
    assert err == ""

    assert repl.upload(HELD, payload) is True
    board.collect()
    assert board.files[HELD] == payload


def test_a_name_with_a_quote_and_a_backslash_lands_under_that_name():
    name = "/_vgacap/it's\\x.bin"
    board = FakeBinaryBoard()
    repl = _repl(board)

    assert repl.upload(name, b"held") is True
    assert board.files == {name: b"held"}
    assert repl.upload(name, b"held") is False


def test_a_file_gone_after_the_write_is_refused():
    board = FakeBinaryBoard(on_file_close=lambda path, data: None)

    with pytest.raises(UploadError) as raised:
        _repl(board).upload(HELD, b"abc")

    assert f"host len=3 crc32=0x{zlib.crc32(b'abc'):08x}" in str(raised.value)
    assert "board absent" in str(raised.value)


def test_a_directory_as_the_target_fails_loudly_and_writes_nothing():
    board = FakeBinaryBoard(dirs={HELD})

    with pytest.raises(RuntimeError, match="OSError"):
        _repl(board).upload(HELD, b"abc")

    assert HELD not in board.files
    assert not any("'wb'" in c for c in board.commands)


def test_an_existing_empty_file_is_skipped_for_empty_data():
    board = FakeBinaryBoard(files={"/e.bin": b""})

    assert _repl(board).upload("/e.bin", b"") is False
    assert board.writers == []


def test_a_failed_slice_reports_a_short_head_of_the_command():
    payload = _payload()
    board = FakeBinaryBoard(on_file_write=_fail_slice(0))

    with pytest.raises(RuntimeError) as raised:
        _repl(board).upload(HELD, payload)

    msg = str(raised.value)
    assert "MemoryError" in msg
    slice_b64 = base64.b64encode(payload[:2048]).decode("ascii")
    assert slice_b64[:200] not in msg
    assert "chars" in msg


def test_upload_leaves_no_names_of_its_own_in_the_repl_globals():
    board = FakeBinaryBoard()
    repl = _repl(board)
    before = set(board._globals) | {"ubinascii"}

    repl.upload(HELD, _payload())
    assert set(board._globals) <= before

    board.on_file_write = _fail_slice(2)
    with pytest.raises(RuntimeError):
        repl.upload(HELD, _payload(seed=5))
    assert set(board._globals) <= before


# -- round 2 -------------------------------------------------------------------


class _StallingLink:
    """`board` behind a link on which slice `stall_at` (from 0) gets no reply.

    The slice reaches the board, which is still running it: every read
    after that raises `error`, as a timed-out read, a closed link or a
    Ctrl-C on the host would end it. `after_stall` records whatever the
    host writes after that, which the board would take as its next input.
    """

    def __init__(self, board: FakeBinaryBoard, stall_at: int, error: BaseException):
        self.board = board
        self.stall_at = stall_at
        self.error = error
        self.slices = 0
        self.stalled = False
        self.after_stall: list[bytes] = []

    def write(self, data: bytes) -> None:
        if self.stalled:
            self.after_stall.append(bytes(data))
            return
        self.board.write(data)
        if b"a2b_base64" in data:
            if self.slices == self.stall_at:
                self.stalled = True
            self.slices += 1

    def read(self, timeout: float) -> bytes:
        if self.stalled:
            raise self.error
        return self.board.read(timeout)

    def close(self) -> None:
        self.board.close()


@pytest.mark.parametrize(
    "error",
    [TimeoutError("timed out waiting for b'OK'"), LinkClosed("bridge gone"),
     KeyboardInterrupt()],
    ids=["timeout", "link-closed", "ctrl-c"],
)
def test_a_slice_with_no_reply_ends_the_upload_with_nothing_more_sent(error):
    # The board may still be running the slice: a close sent now would take
    # the slice's late reply as its own and leave its own for the next
    # command. The next upload's first close is the backstop.
    link = _StallingLink(FakeBinaryBoard(), stall_at=5, error=error)
    repl = RawRepl(link)
    repl.enter()

    with pytest.raises(type(error)):
        repl.upload(HELD, _payload())

    assert link.after_stall == []


def test_a_directory_target_reports_its_oserror_not_a_nameerror():
    board = FakeBinaryBoard(dirs={HELD})

    with pytest.raises(RuntimeError) as raised:
        _repl(board).upload(HELD, b"abc")

    # The board prints "OSError: 21"; CPython, running the fake, spells
    # the same error IsADirectoryError.
    last = [ln for ln in str(raised.value).splitlines() if ln.strip()][-1]
    assert re.match(r"(OSError|IsADirectoryError)\b.*\b21\b", last), last
    assert "NameError" not in str(raised.value)
    assert "UnboundLocalError" not in str(raised.value)


def test_a_read_error_in_the_check_still_closes_the_read_handle():
    def fail_second_read(path, n):
        if n == 1:
            raise OSError(errno.EIO, "EIO")

    board = FakeBinaryBoard(files={HELD: _payload()}, on_file_read=fail_second_read)

    with pytest.raises(RuntimeError, match="OSError"):
        _repl(board).upload(HELD, _payload())

    assert len(board.readers) == 1
    assert board.unclosed_handles() == []


def test_a_failing_close_after_a_failed_slice_does_not_hide_the_slice_error():
    def close_fails(path, data):
        raise OSError(errno.ENOSPC, "ENOSPC")

    board = FakeBinaryBoard(on_file_write=_fail_slice(3), on_file_close=close_fails)

    with pytest.raises(RuntimeError) as raised:
        _repl(board).upload(HELD, _payload())

    assert "MemoryError" in str(raised.value)
    assert "ENOSPC" not in str(raised.value)
