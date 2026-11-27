# SPDX-License-Identifier: Apache-2.0
"""The fakes 4d's host side runs against: a raw REPL on the emulator.

`emu_repl.EmuRepl` is a `FakeBinaryBoard` whose commands reach an
emulator `Board`: its pins, its clock, the fake SDK (`fake_tt`) and the
fake loader. The loader reads the bitstream through the REPL's own
filesystem, the one `RawRepl.upload()` writes, as the real loader reads
the flash the upload wrote. The filesystem's `os.mkdir`, `os.remove` and
parent directories behave as littlefs's do.
"""

from __future__ import annotations

import errno

import pytest
from emu_repl import EmuRepl
from fake_repl import FakeBinaryBoard
from fake_tt import FakeLoader

from ttcap.emu import Board, ClockedDesign
from ttcap.repl import RawRepl

CLK, RST = 16, 14
UO = list(range(33, 41))
HELD = "/_vgacap/held.bin"


def _repl(link) -> RawRepl:
    r = RawRepl(link)
    r.enter()
    return r


def _board() -> Board:
    return Board(ClockedDesign(UO, clk_gpio=CLK, rst_gpio=RST), pulls={RST: 1})


# -- the fake filesystem's os -----------------------------------------------


def test_os_mkdir_makes_a_directory_on_the_fake_not_the_host():
    board = FakeBinaryBoard()
    _out, err = _repl(board).exec("import os\nos.mkdir('/_vgacap')")
    assert err == ""
    assert "/_vgacap" in board.dirs


def test_os_mkdir_of_an_existing_directory_is_eexist():
    board = FakeBinaryBoard(dirs={"/_vgacap"})
    out, _err = _repl(board).exec(
        "import os\ntry:\n os.mkdir('/_vgacap')\nexcept OSError as e:\n print(e.errno)")
    assert out.strip() == str(errno.EEXIST)


def test_os_mkdir_over_a_file_is_eexist():
    board = FakeBinaryBoard(files={"/_vgacap": b"x"})
    out, _err = _repl(board).exec(
        "import os\ntry:\n os.mkdir('/_vgacap')\nexcept OSError as e:\n print(e.errno)")
    assert out.strip() == str(errno.EEXIST)


def test_os_remove_deletes_a_file_and_a_missing_one_is_enoent():
    board = FakeBinaryBoard(files={HELD: b"x"})
    repl = _repl(board)
    _out, err = repl.exec(f"import os\nos.remove({HELD!r})")
    assert err == "" and HELD not in board.files
    out, _err = repl.exec(
        f"import os\ntry:\n os.remove({HELD!r})\nexcept OSError as e:\n print(e.errno)")
    assert out.strip() == str(errno.ENOENT)


def test_os_rmdir_removes_an_empty_directory_only():
    board = FakeBinaryBoard(files={"/a/f": b"x"}, dirs={"/a", "/b"})
    repl = _repl(board)
    code = ("import os\ntry:\n os.rmdir(%r)\nexcept OSError as e:\n print(e.errno)")
    assert repl.exec(code % "/a")[0].strip() == str(errno.ENOTEMPTY)
    assert repl.exec(code % "/c")[0].strip() == str(errno.ENOENT)
    assert repl.exec(code % "/b") == ("", "")
    assert board.dirs == {"/a"}


def test_with_parents_a_file_needs_its_directory():
    # lfs2_file_opencfg: a path whose parent does not exist is LFS_ERR_NOENT.
    board = FakeBinaryBoard(parents=True)
    repl = _repl(board)
    out, _err = repl.exec(
        f"try:\n open({HELD!r},'wb')\nexcept OSError as e:\n print(e.errno)")
    assert out.strip() == str(errno.ENOENT)
    assert HELD not in board.files
    repl.exec("import os\nos.mkdir('/_vgacap')")
    _out, err = repl.exec(f"f=open({HELD!r},'wb')\nf.write(b'ab')\nf.close()")
    assert err == "" and board.files[HELD] == b"ab"


def test_with_parents_mkdir_needs_its_parent_too():
    board = FakeBinaryBoard(parents=True)
    out, _err = _repl(board).exec(
        "import os\ntry:\n os.mkdir('/a/b')\nexcept OSError as e:\n print(e.errno)")
    assert out.strip() == str(errno.ENOENT)


def test_a_file_at_the_root_needs_no_directory():
    board = FakeBinaryBoard(parents=True)
    _out, err = _repl(board).exec("f=open('/s.bin','wb')\nf.close()\n"
                                  "f=open('t.bin','wb')\nf.close()")
    assert err == ""
    assert set(board.files) == {"/s.bin", "t.bin"}


# -- the loader reads the REPL's filesystem ------------------------------------


def test_the_loader_reads_what_was_uploaded_through_the_repl():
    # FakeLoader(files=...) copies its dict, so a file the REPL wrote after
    # it was made would never reach it; with `open=` it reads the board's.
    link = EmuRepl(_board(), dirs={"/_vgacap"})
    repl = _repl(link)
    assert repl.upload(HELD, b"\x01\x02\x03") is True
    out, err = repl.exec("from ttboard.fpga import fabricfoxv2\n"
                         f"fabricfoxv2.spi_transferPIO({HELD!r})")
    assert err == ""
    assert "Transmission complete, total bytes: 3" in out
    assert link.loader.loads == [b"\x01\x02\x03"]
    assert link.unclosed_handles() == []


def test_the_loader_sees_a_removed_file_as_missing():
    link = EmuRepl(_board(), files={HELD: b"\x01"}, dirs={"/_vgacap"})
    repl = _repl(link)
    repl.exec(f"import os\nos.remove({HELD!r})")
    out, _err = repl.exec("from ttboard.fpga import fabricfoxv2\n"
                          f"fabricfoxv2.spi_transferPIO({HELD!r})")
    assert "Error accessing file: [Errno 2] ENOENT" in out
    assert link.loader.loads == [b""]


def test_the_loader_takes_files_or_open_not_both():
    with pytest.raises(ValueError, match="files or open"):
        FakeLoader(None, {HELD: b"x"}, open=lambda path, mode: None)


# -- the REPL runs on the emulator ---------------------------------------------


def test_commands_run_on_the_boards_pins_and_clock():
    board = _board()
    link = EmuRepl(board)
    repl = _repl(link)
    start = board.now
    _out, err = repl.exec("from machine import Pin\nPin(16, Pin.OUT, value=0)\n"
                          "import time\ntime.sleep_ms(2)")
    assert err == ""
    assert board.pads[CLK] == (1, None)
    assert board.now - start >= board.sysclk_hz * 2 // 1000


def test_tt_is_the_fake_sdk_on_the_board():
    board = _board()
    link = EmuRepl(board)
    out, _err = _repl(link).exec("tt.reset_project(True)\nprint(tt.rst_n())")
    assert out.splitlines()[-1] == "0"
    assert board.pad_level(RST, board.now) == 0


def test_globals_persist_across_commands_as_on_a_board():
    link = EmuRepl(_board())
    repl = _repl(link)
    repl.exec("x = 5")
    out, _err = repl.exec("print(x)")
    assert out.strip() == "5"


def test_a_dotted_import_as_reaches_the_module():
    link = EmuRepl(_board())
    out, err = _repl(link).exec("import ttboard.fpga.fabricfoxv2 as f\n"
                                "print(f.spi_transferPIO is not None)")
    assert err == "" and out.strip() == "True"
