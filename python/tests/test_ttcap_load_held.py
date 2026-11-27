# SPDX-License-Identifier: Apache-2.0
"""`load_held`: a bitstream loaded with the clock held low and reset held.

The board is the emulator (`emu_repl.EmuRepl`): a `ClockedDesign` counter
behind the pins, the fake SDK as `tt` and the fake loader, which reads the
file `RawRepl.upload()` wrote. The counter counts the rising edges really
driven on its clock pad, so "no clock reached the design" is checked by
counting them, not by trusting the commands.
"""

from __future__ import annotations

import errno
import io

import pytest
from emu_repl import EmuRepl
from fake_tt import ASIC_MANUAL_INPUTS, ASIC_RP_CONTROL

from ttcap.boards import RP2350_DBV3
from ttcap.capture import (
    HELD_DIR,
    HELD_PATH,
    CaptureError,
    CaptureRequest,
    load_held,
    load_held_cfg,
)
from ttcap.emu import Board, ClockedDesign
from ttcap.repl import RawRepl, UploadError

CLK, RST = 16, 14
UO = list(range(33, 41))
#: About the size of a real iCE40 bitstream, and not a multiple of 2048.
BITSTREAM = bytes(range(256)) * 400 + b"\x7e"


def request(**kwargs) -> CaptureRequest:
    fields = dict(profile=RP2350_DBV3, project=None, design=None, clock_hz=1_000_000,
                  seconds=0, max_bytes=4096)
    fields.update(kwargs)
    return CaptureRequest(**fields)


class Rig:
    """A board whose previous design ran 5 clocks, and a REPL onto it."""

    def __init__(self, tmp_path, *, bitstream: bytes = BITSTREAM, **link_kwargs) -> None:
        self.board = Board(ClockedDesign(UO, clk_gpio=CLK, rst_gpio=RST), pulls={RST: 1})
        self.link = EmuRepl(self.board, **link_kwargs)
        self.Pin = self.board.modules()["machine"].Pin
        clk = self.Pin(CLK, self.Pin.OUT, value=0)
        for _ in range(5):
            clk.value(1)
            clk.value(0)
        self.repl = RawRepl(self.link)
        self.repl.enter()
        self.bitstream = bitstream
        self.path = tmp_path / "design.bin"
        self.path.write_bytes(bitstream)
        self.rises_before = len(self.board.wave.clock_rises)

    def load(self, req: CaptureRequest | None = None) -> dict:
        return load_held(self.repl, req or request(), self.path)

    @property
    def rises(self) -> int:
        """Rising clock edges since the rig was made."""
        return len(self.board.wave.clock_rises) - self.rises_before

    def level(self, gpio: int) -> int:
        return self.board.pad_level(gpio, self.board.now)

    def loader_module(self):
        return self.board.extra_modules["ttboard.fpga.fabricfoxv2"]

    def sent(self, text: str) -> bool:
        return any(text in c for c in self.link.commands)


# -- the happy path --------------------------------------------------------------


def test_the_bitstream_loads_with_the_clock_low_and_reset_held(tmp_path):
    rig = Rig(tmp_path)
    did = rig.load()
    assert did == {"project": None, "enable": "held", "clock_hz": 1_000_000,
                   "monitoring_before": False, "bytes": len(BITSTREAM)}
    assert rig.link.loader.loads == [BITSTREAM]
    # No edge reached the design, which reads its reset value.
    assert rig.rises == 0
    assert rig.board.wave.configured
    assert rig.board.wave.count_at(rig.board.now) == 0
    assert rig.level(RST) == 0
    assert rig.level(CLK) == 0
    assert rig.board.pads[CLK] == (rig.Pin.OUT, None)
    assert rig.link.tt.uio_safe  # safe_bidir() ran after the last pin rebuild


def test_the_file_is_uploaded_to_its_directory_and_deleted_after(tmp_path):
    rig = Rig(tmp_path)
    rig.load()
    assert HELD_PATH == "/_vgacap/held.bin" and HELD_DIR == "/_vgacap"
    assert HELD_DIR in rig.link.dirs
    assert HELD_PATH not in rig.link.files
    assert rig.link.unclosed_handles() == []


def test_the_clock_pad_never_goes_high_from_the_reset_to_the_end(tmp_path):
    rig = Rig(tmp_path)
    start = rig.board.now
    rig.load()
    trace = rig.board.pad_trace[CLK]
    assert all(level == 0 for cycle, level in trace if cycle >= start)


def test_the_body_runs_the_four_steps_in_order(tmp_path):
    rig = Rig(tmp_path)
    rig.load()
    body = next(c for c in rig.link.commands if "spi_transferPIO" in c)
    steps = ["tt.pins.safe_bidir()", "tt.clock_project_stop()",
             "Pin(CLK_GPIO,Pin.OUT,value=0)", "fabricfoxv2.spi_transferPIO(HELD)"]
    where = [body.replace(" ", "").index(s.replace(" ", "")) for s in steps]
    assert where == sorted(where)


def test_reset_is_held_before_the_load_and_the_clock_never_started(tmp_path):
    rig = Rig(tmp_path)
    rig.load()
    commands = rig.link.commands
    hold = commands.index("tt.reset_project(True)")
    body = next(i for i, c in enumerate(commands) if "spi_transferPIO" in c)
    assert hold < body
    assert not rig.sent("clock_project_PWM")
    assert not rig.sent("tt.reset_project(False)")


def test_ui_in_is_set_and_the_monitor_is_off_through_the_load(tmp_path):
    # The load takes about 0.8 s at 1 MHz, longer than the monitor's
    # 333 ms period: left on, it would clock the design once.
    rig = Rig(tmp_path, mode=ASIC_MANUAL_INPUTS)
    rig.link.tt.manual_project_clock.pin_activated = True
    did = rig.load(request(ui_in=5))
    assert did["mode_before"] == ASIC_MANUAL_INPUTS
    assert did["monitoring_before"] is False  # off in ASIC_MANUAL_INPUTS
    assert did["ui_in"] == 5
    assert rig.link.tt.mode == ASIC_RP_CONTROL
    assert rig.link.tt.ui_in.value == 5
    assert rig.rises == 0
    assert rig.level(RST) == 0


def test_a_request_naming_a_design_is_refused_before_any_command(tmp_path):
    rig = Rig(tmp_path)
    before = list(rig.link.commands)
    with pytest.raises(ValueError, match="tt_um_x"):
        rig.load(request(design="tt_um_x"))
    assert rig.link.commands == before


# -- the directory and the upload ------------------------------------------------


def test_an_existing_directory_is_fine(tmp_path):
    rig = Rig(tmp_path, dirs={HELD_DIR})
    assert rig.load()["bytes"] == len(BITSTREAM)


def test_a_mkdir_error_other_than_eexist_fails_before_the_upload(tmp_path):
    rig = Rig(tmp_path)

    def broken(path):
        raise OSError(errno.EIO, "EIO")

    rig.link._modules["os"].mkdir = broken
    with pytest.raises(CaptureError, match="EIO"):
        rig.load()
    assert not rig.sent("'wb'")
    assert rig.link.loader.loads == []


def test_an_upload_skip_still_loads_and_deletes_the_file(tmp_path):
    rig = Rig(tmp_path, files={HELD_PATH: BITSTREAM}, dirs={HELD_DIR})
    assert rig.load()["bytes"] == len(BITSTREAM)
    assert not rig.sent("'wb'")
    assert rig.link.loader.loads == [BITSTREAM]
    assert HELD_PATH not in rig.link.files


def test_an_upload_that_does_not_verify_runs_no_load(tmp_path):
    rig = Rig(tmp_path)
    rig.link.on_file_close = lambda path, data: data[:-1]
    with pytest.raises(UploadError):
        rig.load()
    assert rig.link.loader.loads == []
    assert not rig.sent("spi_transferPIO")
    assert not rig.sent("tt.reset_project(True)")
    # Left for the next upload's check, as upload() documents.
    assert rig.link.files[HELD_PATH] == BITSTREAM[:-1]


# -- a load that did not work ----------------------------------------------------


def _assert_put_back(rig: Rig) -> None:
    """Reset released, the clock stopped, the file deleted."""
    assert rig.level(RST) == 1
    assert rig.board.pads[CLK] == (rig.Pin.IN, rig.Pin.PULL_DOWN)
    assert HELD_PATH not in rig.link.files


def test_a_short_count_is_an_error(tmp_path):
    rig = Rig(tmp_path)
    files = rig.link.files
    rig.link.loader.open = lambda path, mode: io.BytesIO(files[path][:-7])
    with pytest.raises(CaptureError, match="total bytes: %d" % (len(BITSTREAM) - 7)) as raised:
        rig.load()
    assert str(len(BITSTREAM)) in str(raised.value)
    _assert_put_back(rig)


def test_a_loader_oserror_is_an_error_though_the_loader_swallows_it(tmp_path):
    rig = Rig(tmp_path, loader={"fail": OSError(errno.EIO, "EIO"), "fail_after": 40})
    with pytest.raises(CaptureError, match=r"^the loader failed and carried on: "
                       r"'Error accessing file: \[Errno 5\] EIO'$"):
        rig.load()
    _assert_put_back(rig)


def test_no_completion_line_is_an_error(tmp_path):
    rig = Rig(tmp_path)
    rig.loader_module().spi_transferPIO = lambda path: print("State machine activated")
    with pytest.raises(CaptureError, match="Transmission complete"):
        rig.load()
    _assert_put_back(rig)


def test_two_completion_lines_are_an_error(tmp_path):
    rig = Rig(tmp_path)
    real = rig.loader_module().spi_transferPIO

    def twice(path):
        real(path)
        real(path)

    rig.loader_module().spi_transferPIO = twice
    with pytest.raises(CaptureError, match="2 lines"):
        rig.load()


def test_no_output_at_all_is_an_error(tmp_path):
    rig = Rig(tmp_path)
    body_ran = []
    real_run = rig.link._run

    def silent(code):
        if "spi_transferPIO" in code:
            body_ran.append(code)
            return b"OK\x04\x04>"
        return real_run(code)

    rig.link._run = silent
    with pytest.raises(CaptureError, match="said nothing"):
        rig.load()
    assert body_ran


def test_rst_n_not_reading_0_after_the_load_is_an_error(tmp_path):
    rig = Rig(tmp_path)
    real = rig.loader_module().spi_transferPIO
    tt = rig.link.tt

    def releasing(path):
        real(path)
        tt.reset_project(False)

    rig.loader_module().spi_transferPIO = releasing
    with pytest.raises(CaptureError, match="rst_n reads 1 after the load"):
        rig.load()


def test_the_clock_pad_not_reading_0_after_the_load_is_an_error(tmp_path):
    rig = Rig(tmp_path)
    real = rig.loader_module().spi_transferPIO
    Pin = rig.Pin

    def raising_clk(path):
        real(path)
        Pin(CLK).value(1)

    rig.loader_module().spi_transferPIO = raising_clk
    with pytest.raises(CaptureError, match="clock pad reads 1 after the load"):
        rig.load()


def test_a_board_traceback_is_an_error(tmp_path):
    rig = Rig(tmp_path)

    def boom(path):
        raise MemoryError("memory allocation failed")

    rig.loader_module().spi_transferPIO = boom
    with pytest.raises(CaptureError, match="MemoryError"):
        rig.load()
    _assert_put_back(rig)


def test_a_failed_delete_after_a_good_load_is_an_error(tmp_path):
    rig = Rig(tmp_path)

    def broken(path):
        raise OSError(errno.EIO, "EIO")

    rig.link._modules["os"].remove = broken
    with pytest.raises(CaptureError, match="EIO"):
        rig.load()
    assert rig.level(RST) == 1


def test_a_failed_delete_is_a_note_on_a_failed_load(tmp_path):
    rig = Rig(tmp_path, loader={"fail": OSError(errno.EIO, "EIO"), "fail_after": 40})

    def broken(path):
        raise OSError(errno.EPERM, "EPERM")

    rig.link._modules["os"].remove = broken
    with pytest.raises(CaptureError, match="Error accessing file") as raised:
        rig.load()
    assert any("EPERM" in note for note in raised.value.__notes__)


def test_a_timeout_in_the_body_sends_nothing_more(tmp_path, monkeypatch):
    # The loader may still be running: a release, a delete or a mode write
    # sent now would be read as its reply. The caller recover()s.
    rig = Rig(tmp_path)
    real_exec = rig.repl.exec

    def timing_out(code, timeout=10.0, **kwargs):
        if "spi_transferPIO" in code:
            raise TimeoutError("timed out waiting for b'\\x04'")
        return real_exec(code, timeout=timeout, **kwargs)

    monkeypatch.setattr(rig.repl, "exec", timing_out)
    sent = len(rig.link.commands)
    with pytest.raises(TimeoutError) as raised:
        rig.load()
    assert len(rig.link.commands) == sent + _commands_before_the_body(tmp_path)
    assert any("recover()" in note for note in raised.value.__notes__)


def _commands_before_the_body(tmp_path) -> int:
    """How many commands a good load sends before its body."""
    rig = Rig(tmp_path)
    rig.load()
    return next(i for i, c in enumerate(rig.link.commands) if "spi_transferPIO" in c)


# -- the board script ------------------------------------------------------------


def test_the_body_drives_gpio_16_and_loads_the_held_file():
    cfg = load_held_cfg(RP2350_DBV3)
    assert cfg["clk_gpio"] == 16
    assert cfg["path"] == HELD_PATH
