# SPDX-License-Identifier: Apache-2.0
"""`load_held`: a bitstream loaded with the clock held low and reset held.

The board is the emulator (`emu_repl.EmuRepl`): a `ClockedDesign` counter
behind the pins, the fake SDK as `tt` and the fake loader, which reads the
file `RawRepl.upload()` wrote. The counter counts the rising edges really
driven on its clock pad, so "no clock reached the design" is checked by
counting them, not by trusting the commands.
"""

from __future__ import annotations

import dataclasses
import errno
import io
import types

import pytest
from emu_repl import EmuRepl
from fake_tt import ASIC_MANUAL_INPUTS, ASIC_RP_CONTROL, FakeFPGAMux, FakeProjectMux

from ttcap.boards import RP2040_TT06, RP2350_DBV3
from ttcap.capture import (
    HELD_DIR,
    HELD_PATH,
    SYNC_WINDOW,
    CaptureError,
    CaptureRequest,
    LoadHeldTimeout,
    cleanup_names,
    load_held,
    load_held_cfg,
    select_project,
    undo_load_held,
)
from ttcap.emu import Board, ClockedDesign
from ttcap.repl import LinkClosed, RawRepl, UploadError

CLK, RST = 16, 14
UO = list(range(33, 41))
#: The header icepack writes with no comment lines, as in
#: tt-vga-testpatterns' bitstreams: FF 00, 00 FF, then the sync word.
HEADER = b"\xff\x00\x00\xff\x7e\xaa\x99\x7e"
#: About the size of a real iCE40 bitstream, and not a multiple of 2048.
BITSTREAM = HEADER + bytes(range(256)) * 400 + b"\x7e"


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
    assert rig.sent("_ttcap_mkdir('/_vgacap')")
    assert HELD_PATH not in rig.link.files
    assert HELD_DIR not in rig.link.dirs  # removed once empty
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
    steps = ["tt.pins.safe_bidir()", "tt.shuttle.reset()", "tt.clock_project_stop()",
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
    # A file that did not verify is deleted, and its empty directory too.
    assert HELD_PATH not in rig.link.files
    assert HELD_DIR not in rig.link.dirs


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


# -- round 1: what is refused before any board command --------------------------


@pytest.mark.parametrize("data, match", [
    (b"", "empty"),
    (b"\x01" * 300, "sync word"),
    (b"\x00" * SYNC_WINDOW + b"\x7e\xaa\x99\x7e" + b"\x01" * 100, "sync word"),
], ids=["empty", "no-sync", "sync-starting-at-the-window"])
def test_data_that_cannot_configure_an_ice40_is_refused_before_any_command(
        tmp_path, data, match):
    # fpga_reset() clears the FPGA before the transfer (fabricfoxv2.py:110-
    # 112), so a file that cannot configure it would leave no design while
    # the count still matched.
    rig = Rig(tmp_path, bitstream=data)
    before = list(rig.link.commands)
    with pytest.raises(ValueError, match=match):
        rig.load()
    assert rig.link.commands == before


def test_a_sync_word_starting_on_the_windows_last_byte_is_accepted(tmp_path):
    data = b"\x00" * (SYNC_WINDOW - 1) + b"\x7e\xaa\x99\x7e" + b"\x01" * 100
    rig = Rig(tmp_path, bitstream=data)
    assert rig.load()["bytes"] == len(data)


def test_a_sync_word_after_a_comment_block_is_accepted(tmp_path):
    # Comment lines between FF 00 and 00 FF, as some tools write them.
    comment = b"Lattice\x00iCEcube2 2020.12.27940\x00Part: iCE40UP5K-SG48\x00" * 3
    data = b"\xff\x00" + comment + b"\x00\xff\x7e\xaa\x99\x7e" + b"\x01" * 1000
    rig = Rig(tmp_path, bitstream=data)
    assert rig.load()["bytes"] == len(data)


@pytest.mark.parametrize("profile", [
    RP2040_TT06,
    # A clock on GPIO 16 does not make an RP2040 board the FPGA breakout's.
    dataclasses.replace(RP2040_TT06, clk_gpio=16),
    dataclasses.replace(RP2350_DBV3, clk_gpio=15),
], ids=["rp2040", "rp2040-clock-on-16", "rp2350-clock-on-15"])
def test_a_board_without_the_fpga_loader_or_its_clock_pin_is_refused(tmp_path, profile):
    rig = Rig(tmp_path)
    before = list(rig.link.commands)
    with pytest.raises(ValueError, match="FPGA"):
        rig.load(request(profile=profile))
    assert rig.link.commands == before


# -- round 1: a timeout keeps what is needed to put the board back ----------------


def test_a_timeout_carries_the_selection_and_undo_load_held_puts_the_board_back(
        tmp_path, monkeypatch):
    rig = Rig(tmp_path, mode=ASIC_MANUAL_INPUTS)
    real_exec = rig.repl.exec

    def timing_out(code, timeout=10.0, **kwargs):
        if "spi_transferPIO" in code:
            raise TimeoutError("timed out waiting for b'\\x04'")
        return real_exec(code, timeout=timeout, **kwargs)

    monkeypatch.setattr(rig.repl, "exec", timing_out)
    with pytest.raises(LoadHeldTimeout) as raised:
        rig.load(request(ui_in=3))
    assert isinstance(raised.value, TimeoutError)
    selection = raised.value.selection
    assert selection["mode_before"] == ASIC_MANUAL_INPUTS
    assert selection["ui_in"] == 3
    # What the board is left in: RP control, reset held, the file kept.
    assert rig.link.tt.mode == ASIC_RP_CONTROL
    assert rig.level(RST) == 0
    assert HELD_PATH in rig.link.files
    monkeypatch.undo()
    rig.repl.recover(timeout=0.2)
    undo_load_held(rig.repl, selection)
    assert rig.link.tt.mode == ASIC_MANUAL_INPUTS
    assert rig.level(RST) == 1
    assert rig.board.pads[CLK] == (rig.Pin.IN, rig.Pin.PULL_DOWN)
    assert HELD_PATH not in rig.link.files


# -- round 1: a failed reset sequence -------------------------------------------


def test_a_failed_reset_sequence_deletes_the_file(tmp_path):
    rig = Rig(tmp_path)
    rig.link.tt.reset_project = lambda put_in_reset: None  # rst_n never goes low
    with pytest.raises(CaptureError, match="rst_n reads 1 while reset is held"):
        rig.load()
    assert HELD_PATH not in rig.link.files
    assert rig.link.loader.loads == []


def test_a_timeout_in_the_reset_sequence_keeps_the_file(tmp_path, monkeypatch):
    rig = Rig(tmp_path)
    real_exec = rig.repl.exec

    def timing_out(code, timeout=10.0, **kwargs):
        if code == "tt.reset_project(True)":
            raise TimeoutError("timed out waiting for b'OK'")
        return real_exec(code, timeout=timeout, **kwargs)

    monkeypatch.setattr(rig.repl, "exec", timing_out)
    with pytest.raises(TimeoutError):
        rig.load()
    assert HELD_PATH in rig.link.files
    assert not rig.sent("os.remove")


# -- round 1: the body's names, and the monitor firing early -----------------------


def test_the_next_capture_deletes_the_load_bodys_names():
    names = cleanup_names(load_held_cfg(RP2350_DBV3))
    assert {"Pin", "fabricfoxv2", "HELD"} <= set(names)


def test_the_monitor_firing_before_it_is_turned_off_leaves_the_clock_driven_low(tmp_path):
    # ASIC_RP_CONTROL turns the monitor on; if its 333 ms timer fires
    # before `monitoring = False`, clock_project_once() takes GPIO16 as an
    # SIO output (demoboard.py:317-330). The body's clock_project_stop()
    # and drive-low must still leave it low with no edge after the load.
    rig = Rig(tmp_path, mode=ASIC_MANUAL_INPUTS)
    rig.link.tt.manual_project_clock.pin_activated = True
    real_run = rig.link._run
    rises_at_body = []

    def run(code):
        if code == "tt.manual_project_clock.monitoring = False":
            rig.board.advance(rig.board.sysclk_hz * 334 // 1000)
        if "spi_transferPIO" in code:
            rises_at_body.append(len(rig.board.wave.clock_rises))
        return real_run(code)

    rig.link._run = run
    rig.load(request(ui_in=1))
    assert rig.rises == 1  # the monitor's one clock_project_once() edge
    assert len(rig.board.wave.clock_rises) == rises_at_body[0]
    assert rig.board.pads[CLK] == (rig.Pin.OUT, None)
    assert rig.level(CLK) == 0
    assert rig.board.wave.count_at(rig.board.now) == 0


def test_a_lost_link_in_the_body_carries_the_selection_and_sends_nothing_more(
        tmp_path, monkeypatch):
    rig = Rig(tmp_path)
    real_exec = rig.repl.exec

    def lost(code, timeout=10.0, **kwargs):
        if "spi_transferPIO" in code:
            raise LinkClosed("the link closed")
        return real_exec(code, timeout=timeout, **kwargs)

    monkeypatch.setattr(rig.repl, "exec", lost)
    sent = len(rig.link.commands)
    with pytest.raises(LinkClosed) as raised:
        rig.load()
    assert raised.value.selection["enable"] == "held"
    assert len(rig.link.commands) == sent + _commands_before_the_body(tmp_path)
    assert HELD_PATH in rig.link.files


# -- round 2 -----------------------------------------------------------------------


def test_an_asic_carrier_is_refused_before_the_mkdir(tmp_path):
    # RP2350_DBV3 is told by the GPIO layout, which an ASIC carrier shares;
    # there spi_transferPIO pulses GPIO 1, the TT mux's reset, and still
    # prints the right count. Only the FPGA mux's `run` is 'FPGA'
    # (fpga_mux.py:163; project_mux.py:593 gives the shuttle, 'tt06').
    rig = Rig(tmp_path, shuttle=FakeProjectMux("tt06"))
    with pytest.raises(CaptureError, match="load_held needs the FPGA breakout: "
                       "tt.shuttle is not the FPGA mux"):
        rig.load()
    assert not rig.sent("_ttcap_mkdir")
    assert HELD_DIR not in rig.link.dirs
    assert rig.link.writers == []
    assert not rig.sent("spi_transferPIO")
    assert not rig.sent("tt.reset_project(True)")
    assert rig.link.loader.loads == []


def test_a_shuttle_with_no_run_is_refused(tmp_path):
    rig = Rig(tmp_path, shuttle=types.SimpleNamespace(enabled=None))
    with pytest.raises(CaptureError, match="tt.shuttle is not the FPGA mux"):
        rig.load()
    assert not rig.sent("_ttcap_mkdir")


@pytest.mark.parametrize("error", [TimeoutError("timed out waiting for b'OK'"),
                                   LinkClosed("the link closed")])
def test_an_upload_with_no_reply_sends_nothing_more(tmp_path, monkeypatch, error):
    # The board may still be writing; the caller recovers, then deletes.
    rig = Rig(tmp_path)

    def no_reply(name, data):
        raise error

    monkeypatch.setattr(rig.repl, "upload", no_reply)
    sent = len(rig.link.commands)
    with pytest.raises(type(error)):
        rig.load()
    assert len(rig.link.commands) == sent + 2  # the FPGA check and the mkdir
    assert not rig.sent("_ttcap_remove")
    assert HELD_DIR in rig.link.dirs


def test_an_rmdir_failing_otherwise_fails_the_load(tmp_path):
    rig = Rig(tmp_path)

    def failing(path):
        raise OSError(errno.EIO, "EIO")

    rig.link._modules["os"].rmdir = failing
    with pytest.raises(CaptureError, match="EIO"):
        rig.load()
    _assert_put_back(rig)


def test_an_upload_that_fails_mid_write_deletes_the_file(tmp_path):
    rig = Rig(tmp_path)

    def failing(path, n, data):
        if n == 3:
            raise OSError(errno.EIO, "EIO")
        return data

    rig.link.on_file_write = failing
    with pytest.raises(RuntimeError, match="EIO"):
        rig.load()
    assert HELD_PATH not in rig.link.files
    assert HELD_DIR not in rig.link.dirs
    assert not rig.sent("tt.reset_project(True)")


def test_a_count_one_over_the_length_is_an_error(tmp_path):
    rig = Rig(tmp_path)
    files = rig.link.files
    rig.link.loader.open = lambda path, mode: io.BytesIO(files[path] + b"\x00")
    with pytest.raises(CaptureError, match="fed the FPGA %d bytes" % (len(BITSTREAM) + 1)):
        rig.load()


def test_the_sdk_forgets_the_previous_design_as_its_own_enable_does(tmp_path):
    # FPGAMux.enable -> reset_and_clock_mux() -> reset() sets enabled to
    # None (fpga_mux.py:137-160); the body runs the same reset().
    rig = Rig(tmp_path, shuttle=FakeFPGAMux(enabled="tt_um_vgacal_bars"))
    rig.load()
    assert rig.link.tt.shuttle.enabled is None
    with pytest.raises(CaptureError, match="not the enabled design"):
        select_project(rig.repl, request(design="tt_um_vgacal_bars"))


def test_the_directory_is_kept_while_it_holds_another_file(tmp_path):
    rig = Rig(tmp_path, files={HELD_DIR + "/other.bin": b"x"}, dirs={HELD_DIR})
    rig.load()
    assert HELD_PATH not in rig.link.files
    assert HELD_DIR in rig.link.dirs


def test_a_failed_load_removes_the_empty_directory_too(tmp_path):
    rig = Rig(tmp_path, loader={"fail": OSError(errno.EIO, "EIO"), "fail_after": 40})
    with pytest.raises(CaptureError):
        rig.load()
    assert HELD_DIR not in rig.link.dirs
