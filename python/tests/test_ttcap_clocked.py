# SPDX-License-Identifier: Apache-2.0
"""Clocked capture: `ClockedSession` and `clocked_rp2.py`, end to end.

The board is the emulator (`emu_repl.EmuRepl`): a `ClockedDesign` counter
behind the pins, the fake SDK as `tt` and the fake loader. `load_held`
loads the bitstream with the clock and reset held, and the clocked body
then clocks the counter from its own PIO side-set. The counter counts the
rising edges really driven on its clock pad and resets synchronously while
`rst_n` reads 0, so sample k must be 0 for k < reset_clocks and
k - reset_clocks + 1 after: any edge lost, added or numbered wrong shows.

The emulator runs state machines at the system clock (no divider), so
these runs use clocks of 3.9 to 33 MHz at its 133 MHz: `pio_div` 1.
"""

from __future__ import annotations

import io

import pytest
from emu_repl import EmuRepl
from fake_tt import ASIC_MANUAL_INPUTS, ASIC_RP_CONTROL, log

from ttcap import mp
from ttcap.boards import RP2040_TT06, RP2350_DBV3
from ttcap.capture import (
    CLOCKED_RESET_CLOCKS,
    MODE_SELFCLK,
    CaptureError,
    CaptureRequest,
    CaptureSession,
    CaptureStats,
    ClockedSession,
    ClockedTiming,
    clocked_problems,
    clocked_timing,
    recover_clocked,
    run_capture,
    undo_clocked,
)
from ttcap.emu import Board, ClockedDesign
from ttcap.repl import RawRepl
from vgacap.stream import read_stream

CLK, RST = 16, 14
UO = list(range(33, 41))
SYSCLK = 133_000_000
#: 27 cycles a clock at 133 MHz: H 12, L 11, pio_div 1.
CLOCK_HZ = 5_000_000
HEADER = b"\xff\x00\x00\xff\x7e\xaa\x99\x7e"
BITSTREAM = HEADER + bytes(range(256)) * 40
PIO1 = 0x50300000


def counter(clocks: int, reset_clocks: int) -> list[int]:
    """What the counter reads after each of `clocks` edges."""
    return [0 if k < reset_clocks else (k - reset_clocks + 1) & 0xFF for k in range(clocks)]


class Rig:
    """An emulator board with a counter design, and a REPL onto it."""

    def __init__(self, tmp_path, *, settle: int = 0, initial: int = 0, pulls=None,
                 **link_kwargs) -> None:
        self.board = Board(ClockedDesign(UO, clk_gpio=CLK, rst_gpio=RST, settle=settle,
                                         initial=initial),
                           sysclk_hz=SYSCLK, pulls={RST: 1} if pulls is None else pulls)
        self.link = EmuRepl(self.board, **link_kwargs)
        self.repl = RawRepl(self.link)
        self.repl.enter()
        self.path = tmp_path / "design.bin"
        self.path.write_bytes(BITSTREAM)

    def request(self, **kwargs) -> CaptureRequest:
        fields = dict(profile=RP2350_DBV3, project=None, design=None, clock_hz=CLOCK_HZ,
                      seconds=0, max_bytes=0, script="clocked_rp2.py",
                      bitstream=str(self.path), clocks=64, reset_clocks=CLOCKED_RESET_CLOCKS,
                      buf_words=4)
        fields.update(kwargs)
        return CaptureRequest(**fields)

    def run(self, req: CaptureRequest | None = None, **kwargs):
        req = req or self.request(**kwargs)
        out = io.BytesIO()
        self.rises_before = len(self.board.wave.clock_rises)
        stats = run_capture(self.repl, req, out)
        return stats, out.getvalue()

    @property
    def rises(self) -> int:
        return len(self.board.wave.clock_rises) - self.rises_before

    def level(self, gpio: int) -> int:
        return self.board.pad_level(gpio, self.board.now)

    def sent(self, text: str) -> bool:
        return any(text in c for c in self.link.commands)


def samples_of(data: bytes) -> list[int]:
    header, items = read_stream(data)
    out = []
    for item in items:
        if item[0] == "run":
            out.extend([item[1]] * item[2])
    return out


# -- the host's clock --------------------------------------------------------------


def test_500_khz_at_150_mhz_is_490196_hz_with_h_and_l_15():
    t = clocked_timing(150_000_000, 500_000)
    assert (t.pio_div, t.high, t.low, t.cycles_per_clock) == (9, 15, 15, 34)
    assert round(t.clock_hz, 3) == 490196.078


def test_500_khz_at_fpga_4s_132_mhz_is_exact():
    t = clocked_timing(132_000_000, 500_000)
    assert (t.pio_div, t.high, t.low) == (8, 15, 14)
    assert t.clock_hz == 500_000


@pytest.mark.parametrize("sysclk", [125_000_000, 132_000_000, 133_000_000, 150_000_000])
@pytest.mark.parametrize("hz", [100, 1_000, 61_234, 400_000, 500_000, 1_000_000, 2_500_000,
                                4_000_000, 9_999_999, 14_000_000])
def test_the_clock_is_never_faster_than_asked_and_the_read_at_least_40_ns_late(sysclk, hz):
    t = clocked_timing(sysclk, hz)
    assert t.clock_hz <= hz
    assert 0 <= t.high <= 15 and 0 <= t.low <= 15
    assert t.sample_ns >= 40
    # As close as an integer divider allows: one PIO cycle more would be
    # the next slower clock.
    assert sysclk / (t.pio_div * (t.cycles_per_clock - 1)) > hz or t.cycles_per_clock == 4 + t.high


def test_the_fastest_clock_at_150_mhz_reads_40_ns_after_the_edge():
    # 9 cycles: 16,666,666.7 Hz, the read 6 cycles after the edge.
    t = clocked_timing(150_000_000, 16_666_667)
    assert (t.pio_div, t.high, t.low) == (1, 5, 0)
    assert t.sample_ns == pytest.approx(40)
    # Anything up to 8 cycles' worth still gets 9; 8 cycles would read early.
    assert clocked_timing(150_000_000, 18_749_999).cycles_per_clock == 9
    with pytest.raises(ValueError, match="fastest"):
        clocked_timing(150_000_000, 18_750_000)


def test_a_clock_below_the_largest_divider_is_refused():
    with pytest.raises(ValueError, match="slowest"):
        clocked_timing(150_000_000, 60)


def test_a_chosen_h_keeps_the_period_and_moves_the_read():
    t = clocked_timing(SYSCLK, 7_000_000, high=0)
    assert (t.pio_div, t.high, t.low) == (1, 0, 15)
    assert clocked_timing(SYSCLK, 7_000_000, high=15).low == 0
    with pytest.raises(ValueError, match="low"):
        clocked_timing(SYSCLK, 5_000_000, high=0)  # L would be 23
    with pytest.raises(ValueError, match="high"):
        clocked_timing(SYSCLK, 5_000_000, high=16)


def test_the_timing_goes_to_the_board_as_cfg():
    t = ClockedTiming(sysclk_hz=SYSCLK, pio_div=3, high=7, low=2)
    assert t.cfg() == {"sysclk_hz": SYSCLK, "pio_div": 3, "high": 7, "low": 2}


# -- the request ----------------------------------------------------------------------


@pytest.mark.parametrize("clocks, reset_clocks", [(4, 1), (8, 1), (12, 1), (12, 10)])
def test_valid_clock_counts(tmp_path, clocks, reset_clocks):
    Rig(tmp_path).request(clocks=clocks, reset_clocks=reset_clocks)


@pytest.mark.parametrize("clocks, reset_clocks, match", [
    (6, 1, "multiple of 4"), (13, 1, "multiple of 4"), (0, 1, "multiple of 4"),
    (4, 10, "more than reset_clocks"), (8, 10, "more than reset_clocks"),
    (4, 17, "more than reset_clocks"), (12, 17, "more than reset_clocks"),
    (12, 12, "more than reset_clocks"), (8, 0, "reset_clocks"),
])
def test_refused_clock_counts(tmp_path, clocks, reset_clocks, match):
    with pytest.raises(ValueError, match=match):
        Rig(tmp_path).request(clocks=clocks, reset_clocks=reset_clocks)


@pytest.mark.parametrize("kwargs, match", [
    (dict(bitstream=None), "bitstream"),
    (dict(design="tt_um_x"), "loads its own"),
    (dict(seconds=1.0), "seconds"),
    (dict(max_bytes=100), "max_bytes"),
    (dict(profile=RP2040_TT06), "RP2350"),
    (dict(clock_high=16), "clock_high"),
])
def test_refused_requests(tmp_path, kwargs, match):
    with pytest.raises(ValueError, match=match):
        Rig(tmp_path).request(**kwargs)


def test_a_plain_session_refuses_a_clocked_request(tmp_path):
    rig = Rig(tmp_path)
    with pytest.raises(ValueError, match="ClockedSession"):
        CaptureSession(rig.repl, rig.request())


# -- the run: exact samples, exact edges ------------------------------------------------


@pytest.mark.parametrize("clocks, reset_clocks", [(4, 1), (8, 1), (12, 1), (12, 10), (64, 17)])
def test_sample_k_is_the_counter_after_edge_k(tmp_path, clocks, reset_clocks):
    rig = Rig(tmp_path)
    stats, data = rig.run(clocks=clocks, reset_clocks=reset_clocks)
    assert stats.clean, stats.format()
    assert samples_of(data) == counter(clocks, reset_clocks)
    assert rig.rises == clocks  # exactly N edges, none from the load or the put-back
    assert stats.samples == stats.board_samples == clocks
    assert stats.status == "ok"


def test_the_stream_reads_back_as_selfclk_at_the_real_clock(tmp_path):
    rig = Rig(tmp_path)
    stats, data = rig.run(clocks=64, reset_clocks=10, buf_words=4)
    header, items = read_stream(data)
    assert header.version == 1 and header.mode == MODE_SELFCLK
    assert header.clock_hz == round(SYSCLK / 27)
    assert "capture=clocked" in header.desc and "reset_clocks=10" in header.desc
    times = [item[-1] for item in items if item[0] == "time"]
    assert times[0] == "clocked clocks=64 reset_clocks=10 pio_div=1 cycles_per_clock=27"
    assert times[-1].startswith("samples=64 stalled_buffers=")
    assert " overruns=0 status=ok sysclk_hz=133000000 elapsed_ms=" in times[-1]
    assert stats.chunks == 2 + 64 // 16  # two TIME records, 4-word buffers


def test_a_long_run_through_many_buffers_stalls_and_stays_exact(tmp_path):
    rig = Rig(tmp_path)
    stats, data = rig.run(clocks=4000, reset_clocks=10, buf_words=16)
    assert stats.clean, stats.format()
    assert samples_of(data) == counter(4000, 10)
    assert stats.stalled_buffers > 0  # information only
    assert rig.rises == 4000


@pytest.mark.parametrize("high, hz", [(15, 3_911_765), (15, 7_000_000), (0, 7_000_000),
                                      (0, SYSCLK // 4)])
def test_h_and_l_at_their_extremes(tmp_path, high, hz):
    t = clocked_timing(SYSCLK, hz, high=high)
    rig = Rig(tmp_path, settle=high + 1)  # uo_out changes in the cycle of the read
    stats, data = rig.run(clock_hz=hz, clock_high=high, clocks=32, reset_clocks=3)
    assert stats.clean, stats.format()
    assert samples_of(data) == counter(32, 3)
    assert (t.high, t.low) in ((15, 15), (15, 0), (0, 15), (0, 0))


def test_the_read_is_h_plus_1_cycles_after_the_edge(tmp_path):
    # Settling one cycle later than the read sees the value before the edge.
    rig = Rig(tmp_path, settle=12 + 2)
    stats, data = rig.run(clocks=16, reset_clocks=2)
    assert samples_of(data) == [0] + counter(16, 2)[:-1]


def test_a_stale_irq_flag_does_not_release_reset_early(tmp_path):
    rig = Rig(tmp_path)
    rig.board.blocks[1].irq |= 1
    stats, data = rig.run(clocks=32, reset_clocks=10)
    assert stats.clean, stats.format()
    assert samples_of(data) == counter(32, 10)


def test_a_stale_rx_stall_is_not_counted(tmp_path):
    rig = Rig(tmp_path)
    rig.board.blocks[1].fdebug |= 1
    stats, _ = rig.run(clocks=8, reset_clocks=1, buf_words=64)
    assert stats.clean and stats.stalled_buffers == 0


def test_with_ui_in_the_mode_write_comes_before_the_reset(tmp_path):
    # The mode write rebuilds the pins and releases reset (#57): it has to
    # come before the reset is held, or the reset edges would see rst_n high
    # and the counter would count on from its power-up value.
    rig = Rig(tmp_path, initial=0x55, mode=ASIC_MANUAL_INPUTS)
    stats, data = rig.run(clocks=16, reset_clocks=4, ui_in=0x05)
    assert stats.clean, stats.format()
    assert samples_of(data) == counter(16, 4)
    assert rig.link.tt.mode == ASIC_MANUAL_INPUTS  # put back


# -- the board after the run ------------------------------------------------------------


def test_after_the_run_the_clock_is_stopped_reset_released_and_mode_put_back(tmp_path):
    rig = Rig(tmp_path, mode=ASIC_RP_CONTROL)
    stats, _ = rig.run(clocks=8, reset_clocks=1, ui_in=0)
    assert stats.clean
    # The SDK has the clock pad again (ASIC_RP_CONTROL drives it, low).
    assert rig.board.funcsel(CLK) == 5
    assert rig.level(CLK) == 0
    assert rig.level(RST) == 1
    assert rig.link.tt.mode == ASIC_RP_CONTROL
    assert rig.link.tt.manual_project_clock.monitoring is True
    body = next(i for i, c in enumerate(rig.link.commands) if "clocked clocks=" in c)
    after = rig.link.commands[body + 1:]
    assert after[:3] == ["tt.clock_project_stop()", "tt.reset_project(False)", "tt.mode = 1"]
    assert after[3] == "tt.manual_project_clock.monitoring = True"


def test_no_stray_bytes_after_the_run(tmp_path):
    rig = Rig(tmp_path)
    rig.run(clocks=8, reset_clocks=1)
    assert not rig.link._pending and not rig.link._in
    assert rig.repl.exec("print(6 * 7)") == ("42\r\n", "")


def test_the_heap_is_cleared_before_the_load_and_again_before_the_body(tmp_path):
    rig = Rig(tmp_path)
    rig.run(clocks=8, reset_clocks=1)
    cleanups = [i for i, c in enumerate(rig.link.commands) if "gc.mem_free()" in c]
    load = next(i for i, c in enumerate(rig.link.commands) if "spi_transferPIO" in c)
    body = next(i for i, c in enumerate(rig.link.commands) if "clocked clocks=" in c)
    assert cleanups[0] < load < cleanups[1] < body
    # Nothing between the load and the body touches GPIO 16 or the mode.
    between = rig.link.commands[load + 1:body]
    assert not any("tt.mode" in c or "clock_project" in c or "Pin(" in c for c in between)


# -- failures -------------------------------------------------------------------------------


def test_rst_n_stuck_low_is_an_error_and_the_board_is_put_back(tmp_path):
    rig = Rig(tmp_path, pulls={}, mode=ASIC_MANUAL_INPUTS)
    stats, data = rig.run(clocks=16, reset_clocks=4, ui_in=0)
    assert not stats.clean
    assert "error=rst_n stuck low" in stats.messages
    assert stats.status == "error" and "not clean" in stats.error
    assert stats.messages[-1].startswith("samples=0 ")
    assert rig.rises == 4  # the reset edges, then low
    assert rig.level(CLK) == 0
    assert rig.link.tt.mode == ASIC_MANUAL_INPUTS


def test_the_manual_clock_timer_firing_mid_run_fails_loudly(tmp_path):
    rig = Rig(tmp_path)
    tt = rig.link.tt
    fired = []

    def press():
        if tt.board.blocks[1].sms[0].enabled and not fired:
            fired.append(rig.board.now)
            tt.clock_project_once()  # what the monitor's timer calls

    rig.board.add_timer(20_000, press)
    stats, _ = rig.run(clocks=4000, reset_clocks=10, buf_words=16)
    assert fired
    assert "error=clock pad was taken from PIO1 mid-run" in stats.messages
    assert stats.status == "error" and not stats.clean


def test_an_sdk_print_during_the_stream_is_silenced(tmp_path):
    rig = Rig(tmp_path)
    tt = rig.link.tt
    release = tt.reset_project

    def loud_release(put_in_reset):
        log("ttboard.demoboard", "a line at INFO in the middle of the stream")
        release(put_in_reset)

    tt.reset_project = loud_release
    stats, data = rig.run(clocks=16, reset_clocks=4)
    assert stats.clean, stats.format()
    assert samples_of(data) == counter(16, 4)
    # The levels are back afterwards: the same call prints again.
    out, _ = rig.repl.exec("tt.reset_project(False)")
    assert "a line at INFO" in out


def test_the_loader_failing_leaves_no_stream_and_runs_no_body(tmp_path):
    rig = Rig(tmp_path, loader=dict(fail=OSError(5, "EIO"), fail_after=100))
    out = io.BytesIO()
    with pytest.raises(CaptureError, match="the loader failed"):
        run_capture(rig.repl, rig.request(), out)
    assert out.getvalue() == b""
    assert not rig.sent("clocked clocks=")
    assert rig.level(RST) == 1  # load_held put the board back


def test_a_sysclk_that_changed_since_it_was_read_is_refused_on_the_board(tmp_path):
    rig = Rig(tmp_path)
    load = rig.link.loader.spi_transferPIO

    def load_then_reclock(*args, **kwargs):
        load(*args, **kwargs)
        rig.board.sysclk_hz = 150_000_000  # e.g. a clock_project_PWM in between

    rig.board.extra_modules["ttboard.fpga.fabricfoxv2"].spi_transferPIO = load_then_reclock
    stats, _ = rig.run(clocks=8, reset_clocks=1)
    assert "error=sysclk_hz 150000000 is not the 133000000 the clock was worked out for" \
        in stats.messages
    assert stats.status == "error" and not stats.clean
    assert rig.rises == 0
    assert rig.level(RST) == 1  # released afterwards


def test_a_host_stop_ends_the_run_between_buffers(tmp_path):
    rig = Rig(tmp_path)
    started = []

    def stop_when(board):
        if not board.blocks[1].sms[0].enabled:
            return False
        started.append(board.now)
        return board.now > started[0] + 40_000

    rig.board.stop_when = stop_when
    stats, data = rig.run(clocks=4000, reset_clocks=10, buf_words=16)
    assert stats.status == "stopped" and not stats.clean
    assert "status=stopped" in stats.error
    got = samples_of(data)
    assert 0 < len(got) < 4000 and got == counter(len(got), 10)
    assert rig.level(CLK) == 0


def test_a_failure_after_the_load_puts_the_board_back(tmp_path):
    rig = Rig(tmp_path, mode=ASIC_MANUAL_INPUTS)
    exec_ = rig.repl.exec
    loaded = []

    def low_heap_after_the_load(code, *args, **kwargs):
        if "spi_transferPIO" in code:
            loaded.append(True)
        out, err = exec_(code, *args, **kwargs)
        if loaded and "gc.mem_free()" in code:
            return "100\r\n", err
        return out, err

    rig.repl.exec = low_heap_after_the_load
    with pytest.raises(CaptureError, match="free heap"):
        rig.run(clocks=8, reset_clocks=1, ui_in=0)
    assert rig.level(RST) == 1
    assert rig.board.pads[CLK][0] == rig.board.modules()["machine"].Pin.IN
    assert rig.link.tt.mode == ASIC_MANUAL_INPUTS
    assert not rig.sent("clocked clocks=")


def test_a_no_reply_failure_after_the_load_sends_nothing_more(tmp_path):
    rig = Rig(tmp_path)
    exec_ = rig.repl.exec
    state = {"loaded": False}

    def quiet_after_the_load(code, *args, **kwargs):
        if state["loaded"] and "gc.mem_free()" in code:
            raise TimeoutError("no reply")
        if "spi_transferPIO" in code:
            state["loaded"] = True
        return exec_(code, *args, **kwargs)

    rig.repl.exec = quiet_after_the_load
    with pytest.raises(TimeoutError) as info:
        rig.run(clocks=8, reset_clocks=1)
    sent = len(rig.link.commands)
    assert info.value.selection["enable"] == "held"
    assert any("recover()" in note for note in info.value.__notes__)
    assert rig.level(RST) == 0  # still held: nothing was sent
    rig.repl.exec = exec_
    assert recover_clocked(rig.repl, info.value)
    assert len(rig.link.commands) > sent
    assert rig.level(RST) == 1 and rig.level(CLK) == 0


def test_undo_clocked_tries_every_step_and_notes_failures(tmp_path):
    rig = Rig(tmp_path, mode=ASIC_RP_CONTROL)
    exec_ = rig.repl.exec

    def stop_fails(code, *args, **kwargs):
        if code == "tt.clock_project_stop()":
            return "", "Traceback: boom\r\n"
        return exec_(code, *args, **kwargs)

    rig.repl.exec = stop_fails
    error = RuntimeError("the capture's own")
    undo_clocked(rig.repl, {"mode_before": ASIC_MANUAL_INPUTS}, error=error)
    assert any("boom" in note for note in error.__notes__)
    assert rig.link.tt.mode == ASIC_MANUAL_INPUTS
    with pytest.raises(CaptureError, match="boom"):
        undo_clocked(rig.repl, {})


# -- "clean" ---------------------------------------------------------------------------------


def _stats(**kwargs) -> CaptureStats:
    fields = dict(bytes=0, samples=8, chunks=4, overruns=0, seconds=0.0, rxstall=0,
                  messages=("clocked clocks=8 reset_clocks=1 pio_div=1 cycles_per_clock=27",
                            "samples=8 stalled_buffers=2 overruns=0 status=ok "
                            "sysclk_hz=133000000 elapsed_ms=0"),
                  status="ok", board_samples=8, stalled_buffers=2)
    fields.update(kwargs)
    return CaptureStats(**fields)


TIMING = ClockedTiming(sysclk_hz=SYSCLK, pio_div=1, high=12, low=11)


def test_a_clean_run(tmp_path):
    assert clocked_problems(Rig(tmp_path).request(clocks=8, reset_clocks=1), TIMING,
                            _stats()) == []


@pytest.mark.parametrize("kwargs, match", [
    (dict(samples=4), "RAW chunks hold 4 samples"),
    (dict(board_samples=4), "samples=4"),
    (dict(status="stopped"), "status=stopped"),
    (dict(overruns=1), "overruns=1"),
    (dict(messages=("clocked clocks=8 reset_clocks=1 pio_div=2 cycles_per_clock=27",
                    "samples=8 status=ok")), "opening record"),
    (dict(messages=("samples=8 status=ok",)), "opening record"),
    (dict(messages=("clocked clocks=8 reset_clocks=1 pio_div=1 cycles_per_clock=27",),
          status=""), "closing record"),
])
def test_what_is_not_clean(tmp_path, kwargs, match):
    problems = clocked_problems(Rig(tmp_path).request(clocks=8, reset_clocks=1), TIMING,
                                _stats(**kwargs))
    assert any(match in p for p in problems), problems


def test_an_opening_record_that_does_not_match_makes_the_run_not_clean(tmp_path):
    class Lying(ClockedSession):
        def _prepare(self):
            super()._prepare()
            self.timing = ClockedTiming(sysclk_hz=SYSCLK, pio_div=2, high=12, low=11)

    rig = Rig(tmp_path)
    req = rig.request(clocks=8, reset_clocks=1)
    session = Lying(rig.repl, req)
    out = io.BytesIO()
    for block in session.chunks():
        out.write(block)
    assert "opening record" in session.stats().error


def test_the_session_exposes_timing_and_selection(tmp_path):
    rig = Rig(tmp_path)
    session = ClockedSession(rig.repl, rig.request(clocks=8, reset_clocks=1))
    with pytest.raises(CaptureError, match="sysclk"):
        session.header_bytes
    for _ in session.chunks():
        pass
    assert session.timing == clocked_timing(SYSCLK, CLOCK_HZ)
    assert session.selection["bytes"] == len(BITSTREAM)


# -- the script --------------------------------------------------------------------------


def test_the_clocked_script_builds_under_10000_bytes():
    cfg = dict(CaptureRequest(profile=RP2350_DBV3, project=None, design=None,
                              clock_hz=500_000, seconds=0, max_bytes=0,
                              script="clocked_rp2.py", bitstream="x.bin", clocks=4_000_000).cfg(),
               **clocked_timing(150_000_000, 500_000).cfg())
    assert len(mp.build(cfg, "clocked_rp2.py").encode()) < 10_000
