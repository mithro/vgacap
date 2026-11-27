# SPDX-License-Identifier: Apache-2.0
"""`select_project`'s reset sequence against a `tt` that behaves like the SDK's.

`fake_tt.FakeTT` copies tt-micropython-firmware v3.1.1: a mode write
rebuilds every pin, which lets `rst_n` go (issue #57), and the release
relies on a pull-up nothing used to check (issue #58).
"""

from __future__ import annotations

import pytest
from fake_repl import FakeBinaryBoard
from fake_tt import ASIC_MANUAL_INPUTS, ASIC_RP_CONTROL, IN, FakeTT, sdk_modules

from ttcap import capture
from ttcap.boards import RP2350_DBV3
from ttcap.capture import (
    CaptureError,
    CaptureRequest,
    select_project,
    undo_selection,
)
from ttcap.repl import RawRepl


class TTBoard(FakeBinaryBoard):
    """A raw REPL whose globals hold a `FakeTT`. The manual-clock timer
    fires once after every command, as a 3 Hz timer would during a run."""

    def __init__(self, tt: FakeTT) -> None:
        super().__init__(modules=sdk_modules(tt))
        self.tt = tt
        self._globals["tt"] = tt
        #: Each command's stdout, so a test can see the SDK's own prints.
        self.outputs: list[str] = []

    def _run(self, code: str) -> bytes:
        reply = super()._run(code)
        self.outputs.append(reply.decode("utf-8", "replace"))
        self.tt.tick()
        return reply


def request(**kwargs) -> CaptureRequest:
    fields = dict(profile=RP2350_DBV3, project=None, design=None, clock_hz=1_000_000,
                  seconds=0, max_bytes=4096)
    fields.update(kwargs)
    return CaptureRequest(**fields)


def connect(tt: FakeTT) -> tuple[TTBoard, RawRepl]:
    board = TTBoard(tt)
    repl = RawRepl(board)
    repl.enter()
    return board, repl


# -- the fake itself does what the SDK does ---------------------------------


def test_the_fake_releases_reset_on_a_mode_write_as_the_sdk_did_on_fpga4():
    # fpga-4, 2026-11-27, stock SDK 3.1.1: this line printed 0, then 1.
    tt = FakeTT()
    board, repl = connect(tt)
    out, err = repl.exec(
        "from ttboard.mode import RPMode\n"
        "tt.reset_project(True); print(tt.rst_n()); tt.mode = RPMode.ASIC_RP_CONTROL; print(tt.rst_n())")
    assert err == ""
    assert out.split() == ["ttboard.demoboard:", "Changing", "reset", "to", "output", "mode", "0",
                           "ttboard.pins.pins:", "Setting", "mode", "to", "ASIC_RP_CONTROL",
                           "ttboard.pins.manual_clock:", "Enabling", "manual", "clock", "pin",
                           "monitoring", "1"]


def test_the_fakes_manual_clock_timer_stops_the_pwm():
    tt = FakeTT(mode=ASIC_RP_CONTROL)
    tt.clock_project_PWM(1000)
    tt.manual_project_clock.pin_activated = True
    tt.tick()
    assert tt.clock_hz == 0


# -- hole 1 (#57): the mode write released reset ------------------------------


def test_ui_in_is_set_and_the_design_clocked_while_held_in_reset():
    """The regression for #57. Before the fix the mode write released
    reset, so the clock never ran while rst_n was low and reset ended with
    ui_in still 0."""
    tt = FakeTT(mode=ASIC_MANUAL_INPUTS)
    board, repl = connect(tt)

    did = select_project(repl, request(ui_in=5))

    assert tt.design.edges_in_reset >= 16
    assert tt.design.ui_in_at_release == [5]
    assert tt.design.rst_n_levels == [0, 1]
    assert tt.rst_n() == 1 and tt.clock_hz == 1_000_000
    assert tt.mode == ASIC_RP_CONTROL
    assert did["ui_in"] == 5 and did["mode_before"] == ASIC_MANUAL_INPUTS


def test_without_ui_in_the_design_is_still_clocked_in_reset():
    tt = FakeTT()
    board, repl = connect(tt)
    select_project(repl, request())
    assert tt.design.edges_in_reset >= 16
    assert tt.design.rst_n_levels == [0, 1]
    assert tt.pin_rebuilds == 1  # no mode write at all


def test_the_mode_is_written_before_reset_is_asserted():
    tt = FakeTT()
    board, repl = connect(tt)
    select_project(repl, request(ui_in=5))
    first_reset = next(i for i, c in enumerate(board.commands) if "tt.reset_project(True)" in c)
    mode_write = next(i for i, c in enumerate(board.commands) if "tt.mode = RPMode" in c)
    ui_in_write = next(i for i, c in enumerate(board.commands) if "tt.ui_in.value = 5" in c)
    assert mode_write < ui_in_write < first_reset


# -- the manual-clock monitor -----------------------------------------------------


def test_the_manual_clock_monitor_is_off_for_the_run_and_restored_after():
    tt = FakeTT(mode=ASIC_MANUAL_INPUTS)
    board, repl = connect(tt)

    did = select_project(repl, request(ui_in=5))
    # A press of the manual-clock button during the run.
    tt.manual_project_clock.pin_activated = True
    repl.exec("pass")
    assert tt.clock_hz == 1_000_000, "the monitor's timer stopped the project clock"
    assert not tt.manual_project_clock.monitoring

    undo_selection(repl, did, stop=True)
    assert tt.mode == ASIC_MANUAL_INPUTS
    assert not tt.manual_project_clock.monitoring


def test_a_board_already_in_rp_control_gets_its_monitor_back():
    tt = FakeTT(mode=ASIC_RP_CONTROL)
    board, repl = connect(tt)

    did = select_project(repl, request())
    assert not tt.manual_project_clock.monitoring
    undo_selection(repl, did, stop=True)
    assert tt.manual_project_clock.monitoring


def test_a_board_without_the_monitored_pin_needs_no_restore():
    # fpga-4's SDK 3.1.1 has tt.manual_project_clock None.
    tt = FakeTT(manual_clock=False)
    board, repl = connect(tt)
    did = select_project(repl, request(ui_in=5))
    assert "monitoring_before" not in did
    undo_selection(repl, did, stop=True)
    assert tt.mode == ASIC_MANUAL_INPUTS


# -- hole 2 (#58): rst_n read back -------------------------------------------------


def test_rst_n_stuck_low_after_release_is_an_error():
    tt = FakeTT(pullup=False)
    board, repl = connect(tt)
    with pytest.raises(CaptureError, match="^rst_n stuck low after release"):
        select_project(repl, request())
    # It waited the whole timeout on the board's own clock before failing.
    assert tt.now_ms >= capture.RST_N_RELEASE_TIMEOUT_MS


def test_rst_n_stuck_low_with_ui_in_still_puts_the_board_back():
    tt = FakeTT(pullup=False)
    board, repl = connect(tt)
    with pytest.raises(CaptureError, match="rst_n stuck low after release"):
        select_project(repl, request(ui_in=5))
    assert tt.mode == ASIC_MANUAL_INPUTS
    assert tt.clock_hz == 0


def test_a_slow_release_is_waited_for():
    tt = FakeTT(release_ms=50)
    board, repl = connect(tt)
    did = select_project(repl, request())
    assert tt.rst_n() == 1
    assert did["rst_n_wait_ms"] == 50


def test_the_stuck_low_error_gives_the_time_the_board_measured():
    class CoarseClock(FakeTT):
        def advance(self, ms):
            super().advance(7 * ms)  # each sleep_ms(1) takes 7 ms

    tt = CoarseClock(pullup=False)
    board, repl = connect(tt)
    with pytest.raises(CaptureError, match="it read 0 for 105 ms after"):
        select_project(repl, request())


# -- a failed reset leaves no clock running and no reset driven ------------------


def test_rst_n_stuck_low_without_ui_in_stops_the_clock():
    # fpga-4 and every fpgas.online board: no --ui-in, no monitor.
    tt = FakeTT(manual_clock=False, pullup=False)
    board, repl = connect(tt)
    with pytest.raises(CaptureError, match="rst_n stuck low after release"):
        select_project(repl, request())
    assert tt.clock_hz == 0


def test_rst_n_stuck_high_leaves_reset_released():
    class StuckHigh(FakeTT):
        def rst_n_level(self, pin):
            return 1

    tt = StuckHigh(manual_clock=False)
    board, repl = connect(tt)
    with pytest.raises(CaptureError, match="rst_n reads 1 while reset is held"):
        select_project(repl, request())
    assert tt.rst_n.mode == IN


def test_a_failed_clock_command_leaves_reset_released():
    class NoClock(FakeTT):
        def clock_project_PWM(self, freq_hz, *args, **kwargs):
            raise OSError("no clock")

    tt = NoClock(manual_clock=False)
    board, repl = connect(tt)
    with pytest.raises(CaptureError, match="no clock"):
        select_project(repl, request())
    assert tt.rst_n.mode == IN
    assert tt.rst_n() == 1
    assert tt.clock_hz == 0


# -- reset is held for enough clocks ---------------------------------------------


@pytest.mark.parametrize("hz", [100, 1_000, 25_000_000])
def test_reset_is_held_for_at_least_16_clocks(hz):
    tt = FakeTT(manual_clock=False)
    board, repl = connect(tt)
    select_project(repl, request(clock_hz=hz))
    assert tt.design.edges_in_reset >= 16
    assert tt.rst_n() == 1


def test_rst_n_that_does_not_go_low_is_an_error():
    class StuckHigh(FakeTT):
        def rst_n_level(self, pin):
            return 1

    tt = StuckHigh()
    board, repl = connect(tt)
    with pytest.raises(CaptureError, match="rst_n reads 1 while reset is held"):
        select_project(repl, request())
    # Never clocked a design whose reset could not be seen.
    assert not any("clock_project_PWM" in c for c in board.commands)


# -- ui_in read back ----------------------------------------------------------------


def test_a_ui_in_the_board_does_not_drive_is_an_error_and_the_mode_goes_back():
    # A switch holds ui_in[0] high, so ASIC_RP_CONTROL leaves it an input.
    tt = FakeTT(switches=0x01)
    board, repl = connect(tt)
    with pytest.raises(CaptureError, match=r"^ui_in reads 0x07 after writing 0x06"):
        select_project(repl, request(ui_in=6))
    assert tt.mode == ASIC_MANUAL_INPUTS
    # The design was never reset with the wrong inputs.
    assert not any("tt.reset_project" in c for c in board.commands)


def test_the_sdks_prints_do_not_confuse_the_read_backs():
    tt = FakeTT()
    board, repl = connect(tt)
    select_project(repl, request(ui_in=5))
    printed = "".join(board.outputs)
    assert "Setting mode to ASIC_RP_CONTROL" in printed
    assert "Changing reset to output mode" in printed
