# SPDX-License-Identifier: Apache-2.0
"""The fake SDK (`fake_tt`) on the emulator, and 4d's board side on it.

4d loads a bitstream with the clock held: `tt.pins.safe_bidir()`,
`tt.clock_project_stop()`, `Pin(16, Pin.OUT, value=0)`, then
`fabricfoxv2.spi_transferPIO(path)`. The host checks the load by the
loader's "Transmission complete, total bytes: N" line, because the loader
swallows `OSError`.
"""

from __future__ import annotations

import pytest
from fake_tt import (
    ASIC_MANUAL_INPUTS,
    ASIC_RP_CONTROL,
    FakeLoader,
    board_sdk,
)

from ttcap.emu import Board, BoardError, ClockedDesign, Waveform

CLK, RST = 16, 14
UO = list(range(33, 41))
HELD = "/_vgacap/held.bin"

LOAD_BODY = """\
import ttboard.fpga.fabricfoxv2 as fabricfoxv2
from machine import Pin
tt.pins.safe_bidir()
tt.clock_project_stop()
Pin(16, Pin.OUT, value=0)
fabricfoxv2.spi_transferPIO("/_vgacap/held.bin")
print("clk=%d" % Pin(16).value())
"""


def _design_board(pulls=None) -> Board:
    return Board(ClockedDesign(UO, clk_gpio=CLK, rst_gpio=RST),
                 pulls={RST: 1} if pulls is None else pulls)


def _text(board: Board, start: int = 0) -> list[str]:
    return bytes(board.output[start:]).decode().split("\r\n")


# -- run_script: the REPL's globals, print, imports, time ---------------------


def test_print_goes_out_over_usb_and_takes_its_time():
    board = Board(Waveform(b"\0", [], clk_gpio=CLK, period=2))
    before = board.now
    out = board.run_script("print('hello', x)", globals={"x": 7})
    assert out == b"hello 7\r\n"
    assert board.now - before >= len(out) * board.cycles_per_byte


@pytest.mark.parametrize("line", [
    "import ttboard.fpga.fabricfoxv2 as f",
    "from ttboard.fpga import fabricfoxv2 as f",
    "import ttboard.fpga.fabricfoxv2; f = ttboard.fpga.fabricfoxv2",
])
def test_the_sdk_modules_import_every_way(line):
    board = _design_board()
    loader = FakeLoader(board)
    board_sdk(board, loader=loader)
    board.run_script(line + "\nprint(f.spi_transferPIO == SPI)",
                     globals={"SPI": loader.spi_transferPIO})
    assert _text(board)[-2] == "True"


def test_time_runs_on_the_board_clock():
    board = Board(Waveform(b"\0", [], clk_gpio=CLK, period=2), sysclk_hz=150_000_000)
    board.run_script("import time\nt = time.ticks_ms()\ntime.sleep_ms(250)\n"
                     "print(time.ticks_diff(time.ticks_ms(), t))")
    assert _text(board)[0] == "250"


def test_a_timer_fires_on_the_board_clock():
    board = Board(Waveform(b"\0", [], clk_gpio=CLK, period=2))
    fired = []
    timer = board.add_timer(1000, lambda: fired.append(board.now))
    board.advance(999)
    assert fired == []
    board.advance(1)
    board.advance(2500)
    assert fired == [1000, 3500, 3500]  # each at the end of the advance it fell due in
    board.remove_timer(timer)
    board.advance(5000)
    assert len(fired) == 3


# -- the fake tt on the emulator's pins ------------------------------------------


def test_rst_n_reads_1_when_released_the_pull_up_beating_the_pull_down():
    board = _design_board()
    tt = board_sdk(board)
    Pin = board.modules()["machine"].Pin
    assert tt.rst_n() == 1
    tt.reset_project(True)
    assert (tt.rst_n(), Pin(RST).value()) == (0, 0)
    tt.reset_project(False)
    assert (tt.rst_n(), Pin(RST).value()) == (1, 1)
    assert board.pads[RST] == (Pin.IN, Pin.PULL_DOWN)


def test_without_the_external_pull_up_the_pull_down_holds_rst_n_low():
    board = _design_board(pulls={})
    tt = board_sdk(board)
    tt.reset_project(True)
    tt.reset_project(False)
    assert tt.rst_n() == 0


def test_a_mode_write_rebuilds_the_pins_and_releases_reset():
    board = _design_board()
    tt = board_sdk(board, mode=ASIC_MANUAL_INPUTS)
    Pin = board.modules()["machine"].Pin
    tt.reset_project(True)
    tt.mode = ASIC_RP_CONTROL
    assert tt.rst_n() == 1
    assert board.pads[CLK][0] == Pin.OUT  # project_clk_driven_by_RP2(True)
    tt.mode = ASIC_MANUAL_INPUTS
    assert board.pads[CLK] == (Pin.IN, Pin.PULL_DOWN)


def test_reset_project_resets_the_clocked_design():
    board = _design_board()
    tt = board_sdk(board)
    clk = board.modules()["machine"].Pin(CLK, 1, value=0)
    design = board.wave
    for _ in range(3):
        clk.value(1)
        clk.value(0)
    assert design.count_at(board.now) == 3
    tt.reset_project(True)
    clk.value(1)
    clk.value(0)
    tt.reset_project(False)
    clk.value(1)
    assert design.count_at(board.now) == 1


def test_the_manual_clock_timer_clocks_the_design_once_per_press():
    board = _design_board()
    tt = board_sdk(board, mode=ASIC_RP_CONTROL)
    period = board.sysclk_hz * 333 // 1000
    board.advance(period)
    assert board.pio_rises[CLK] == []
    tt.manual_project_clock.pin_activated = True
    board.advance(period)
    assert len(board.pio_rises[CLK]) == 1
    board.advance(3 * period)
    assert len(board.pio_rises[CLK]) == 1
    tt.mode = ASIC_MANUAL_INPUTS  # monitoring off
    tt.manual_project_clock.pin_activated = True
    board.advance(3 * period)
    assert len(board.pio_rises[CLK]) == 1


def test_safe_bidir_makes_the_uio_pins_inputs_and_prints_nothing():
    board = _design_board()
    tt = board_sdk(board)
    Pin = board.modules()["machine"].Pin
    for gpio in range(25, 33):
        Pin(gpio, Pin.OUT, value=1)
    board.run_script("tt.pins.safe_bidir()", globals={"tt": tt})
    assert board.output == b""
    assert all(board.pads[g] == (Pin.IN, Pin.PULL_DOWN) for g in range(25, 33))
    assert all(Pin(g).value() == 0 for g in range(25, 33))


# -- 4d's board side on the emulator ---------------------------------------------


def test_the_held_load_feeds_the_whole_bitstream_with_the_clock_held_low():
    board = _design_board()
    bitstream = bytes(range(256)) * 400 + b"\x7e"
    loader = FakeLoader(board, {HELD: bitstream})
    tt = board_sdk(board, loader=loader)
    clk = board.modules()["machine"].Pin(CLK, 1, value=0)
    for _ in range(5):  # the previous design ran
        clk.value(1)
        clk.value(0)
    edges = len(board.pio_rises[CLK])
    start = board.now
    board.run_script(LOAD_BODY, globals={"tt": tt})
    assert _text(board) == [
        "Configuring PIO with frequency: 16000000 Hz",
        "State machine activated",
        "SS low, starting transmission",
        "Transmission complete, total bytes: %d" % len(bitstream),
        "State machine deactivated, SS high",
        "clk=0",
        "",
    ]
    assert loader.loads == [bitstream]
    # No edge through the load, and the new design reads its reset value.
    assert len(board.pio_rises[CLK]) == edges
    assert board.wave.count_at(board.now) == 0
    assert board.pad_level(CLK, start) == 0
    # The transfer took its time at 1 MHz: 8 bits a byte.
    assert board.now - start > board.sysclk_hz * 8 * len(bitstream) // 1_000_000


@pytest.mark.parametrize("files, fail, error", [
    ({}, None, "[Errno 2] ENOENT"),
    ({HELD: b"\x01" * 100}, OSError(5, "EIO"), "[Errno 5] EIO"),
])
def test_a_failed_load_prints_the_error_and_does_not_raise(files, fail, error):
    board = _design_board()
    loader = FakeLoader(board, files, fail=fail, fail_after=40)
    tt = board_sdk(board, loader=loader)
    board.run_script(LOAD_BODY, globals={"tt": tt})
    text = _text(board)
    assert "Error accessing file: %s" % error in text
    assert not any(line.startswith("Transmission complete") for line in text)
    assert text[-3:] == ["State machine deactivated, SS high", "clk=0", ""]
    assert loader.loads == [b"\x01" * 40 if fail else b""]


def test_a_timer_callback_that_takes_time_does_not_fire_itself_again():
    # Due moves on before the callback runs, so a callback that advances
    # the board (a Pin read, a sleep) cannot recurse into itself.
    board = Board(Waveform(b"\0", [], clk_gpio=CLK, period=2))
    fired = []

    def callback():
        fired.append(board.now)
        board.advance(10)

    board.add_timer(1000, callback)
    for _ in range(3):
        board.advance(1000)
    assert fired == [1000, 2010, 3020]


def test_clock_project_stop_makes_the_clock_pad_an_input_with_its_pull_down():
    board = _design_board()
    tt = board_sdk(board)
    Pin = board.modules()["machine"].Pin
    Pin(CLK, Pin.OUT, value=0)
    tt.clock_project_stop()  # demoboard.py:394-401, with no clock running
    assert board.pads[CLK] == (Pin.IN, Pin.PULL_DOWN)


def test_a_mode_write_makes_the_uio_pins_inputs_again():
    board = _design_board()
    tt = board_sdk(board)
    Pin = board.modules()["machine"].Pin
    Pin(25, Pin.OUT, value=1)
    tt.mode = ASIC_RP_CONTROL
    assert all(board.pads[g] == (Pin.IN, Pin.PULL_DOWN) for g in range(25, 33))


def test_the_loader_leaves_ss_high():
    board = _design_board()
    loader = FakeLoader(board, {HELD: b"\x01" * 10})
    loader.spi_transferPIO(HELD)
    Pin = board.modules()["machine"].Pin
    assert Pin(FakeLoader.SS).value() == 1


def test_the_loader_can_print_the_fifo_drain_lines_before_the_count():
    # fabricfoxv2.py:163-165: while the last word is still in the TX FIFO
    # the loader prints its level; on hardware that is 0 or more lines.
    board = _design_board()
    loader = FakeLoader(board, {HELD: b"\x01" * 10}, drain_lines=2)
    tt = board_sdk(board, loader=loader)
    board.run_script(LOAD_BODY, globals={"tt": tt})
    assert _text(board)[2:6] == [
        "SS low, starting transmission",
        "Waiting for FIFO to drain, free slots: 1",
        "Waiting for FIFO to drain, free slots: 1",
        "Transmission complete, total bytes: 10",
    ]


def _count_three(board: Board) -> None:
    clk = board.modules()["machine"].Pin(CLK, 1, value=0)
    for _ in range(3):
        clk.value(1)
        clk.value(0)


def test_a_load_that_fails_after_the_fpga_reset_leaves_no_design():
    # fabricfoxv2.py:110-112: the file opens, then fpga_reset() pulses
    # CRESET (:67-73), clearing the FPGA; an OSError mid-transfer is
    # swallowed (:169-170) and nothing loads it again (:171-181). Reading
    # uo_out is then refused until a load succeeds.
    board = _design_board()
    loader = FakeLoader(board, {HELD: b"\x01" * 100}, fail=OSError(5, "EIO"), fail_after=10)
    _count_three(board)
    loader.spi_transferPIO(HELD)
    Pin = board.modules()["machine"].Pin
    with pytest.raises(BoardError, match="no design"):
        Pin(33).value()
    loader.fail = None
    loader.spi_transferPIO(HELD)
    assert Pin(33).value() == 0


def test_a_missing_file_leaves_the_old_design_running():
    # open() fails before fpga_reset (fabricfoxv2.py:110-112).
    board = _design_board()
    loader = FakeLoader(board, {})
    _count_three(board)
    loader.spi_transferPIO(HELD)
    assert board.wave.count_at(board.now) == 3


def test_clock_project_pwm_on_the_emulator_is_a_board_error():
    tt = board_sdk(_design_board())
    with pytest.raises(BoardError, match="Waveform is the PWM"):
        tt.clock_project_PWM(1000)


def test_timers_are_told_apart_by_identity():
    # Two timers equal in due, period and callback are still two timers.
    board = Board(Waveform(b"\0", [], clk_gpio=CLK, period=2))
    fired = []

    def shared():
        fired.append("tick")

    first = board.add_timer(1000, shared)
    second = board.add_timer(1000, shared)
    board.remove_timer(second)
    assert board.timers == [first] and board.timers[0] is first
    board.advance(1000)
    assert fired == ["tick"]


def test_a_timer_removed_by_another_timers_callback_does_not_fire():
    # Two timers due in one advance: the first removes the second, which
    # must then not fire, in that pass or any later one (re-review N7).
    board = Board(Waveform(b"\0", [], clk_gpio=CLK, period=2))
    fired = []
    second = None

    def first_cb():
        fired.append("first")
        board.remove_timer(second)

    def second_cb():
        fired.append("second")

    board.add_timer(1000, first_cb)
    second = board.add_timer(1000, second_cb)
    board.advance(1000)
    assert fired == ["first"]
    board.advance(1000)
    assert fired == ["first", "first"]


def test_tt_rst_n_reads_the_pin_and_costs_a_pin_read():
    # standard.py:124-129: StandardPin.__call__ is raw_pin.value().
    board = _design_board()
    tt = board_sdk(board)
    before = board.now
    assert tt.rst_n() == 1
    assert board.now - before == board.pin_read_cycles


def _edge(board: Board) -> int:
    """One CPU clock edge; the counter just after it."""
    clk = board.modules()["machine"].Pin(CLK)
    clk.value(1)
    value = board.wave.count_at(board.now)
    clk.value(0)
    return value


def test_a_load_after_a_design_held_in_reset_counts_from_1_again():
    # 4d's load after 4e-style reset edges: the new design's edges must not
    # reuse the old configuration's counts (review #85 P1).
    board = _design_board()
    Pin = board.modules()["machine"].Pin
    Pin(CLK, Pin.OUT, value=0)
    rst = Pin(RST, Pin.OUT, value=0)
    assert [_edge(board) for _ in range(3)] == [0, 0, 0]
    rst.init(Pin.IN, pull=Pin.PULL_DOWN)  # released: the pull-up wins
    assert [_edge(board) for _ in range(3)] == [1, 2, 3]
    FakeLoader(board, {HELD: b"\x01" * 10}).spi_transferPIO(HELD)
    assert [_edge(board) for _ in range(3)] == [1, 2, 3]


def test_rst_n_falling_in_the_cycle_of_an_edge_after_a_load_resets_it():
    # rst_changed must count edges from the load, not from the first edge.
    board = _design_board()
    Pin = board.modules()["machine"].Pin
    clk = Pin(CLK, Pin.OUT, value=0)
    _count_three(board)
    FakeLoader(board, {HELD: b"\x01" * 10}).spi_transferPIO(HELD)
    clk.value(1)
    now = board.now
    assert board.wave.count_at(now) == 1
    Pin(RST, Pin.OUT, value=0)
    assert board.now == now
    assert board.wave.count_at(now) == 0


def test_board_sdk_scripts_sleep_on_the_board_clock():
    board = _design_board()
    tt = board_sdk(board)
    before = board.now
    board.run_script("import time\ntime.sleep_ms(5)\n", globals={"tt": tt})
    assert board.now - before == board.sysclk_hz * 5 // 1000


def test_each_time_function_runs_on_the_board_clock():
    board = Board(Waveform(b"\0", [], clk_gpio=CLK, period=2), sysclk_hz=150_000_000)
    hz = board.sysclk_hz
    for call, cycles in (("sleep_us(100)", hz * 100 // 1_000_000),
                         ("sleep_ms(3)", hz * 3 // 1000),
                         ("sleep(2)", 2 * hz)):
        before = board.now
        board.run_script("import time\ntime." + call + "\n")
        assert board.now - before == cycles, call
    board.advance(12_345)
    now = board.now
    start = len(board.output)
    board.run_script("import time\nprint(time.ticks_us(), time.ticks_ms(), time.ticks_cpu())")
    assert _text(board, start)[0] == "%d %d %d" % (now * 1_000_000 // hz, now * 1000 // hz, now)


def test_two_manual_clock_presses_give_two_edges_and_leave_the_clock_low():
    board = _design_board()
    tt = board_sdk(board, mode=ASIC_RP_CONTROL)
    period = board.sysclk_hz * 333 // 1000
    for _ in range(2):
        tt.manual_project_clock.pin_activated = True
        board.advance(period)
    assert len(board.pio_rises[CLK]) == 2
    assert board.pad_level(CLK, board.now) == 0


def test_the_manual_clock_timer_fires_every_333_ms():
    board = _design_board()
    tt = board_sdk(board, mode=ASIC_RP_CONTROL)
    period = board.sysclk_hz * 333 // 1000
    tt.manual_project_clock.pin_activated = True
    board.advance(period - 1)
    assert board.pio_rises[CLK] == []
    board.advance(1)
    assert len(board.pio_rises[CLK]) == 1


def test_construction_leaves_the_clock_pad_to_the_boards_pwm():
    board = Board(Waveform(b"\0", [], clk_gpio=CLK, period=2))
    board_sdk(board)
    assert not board.wave.clock_stopped and CLK not in board.pads


def test_an_empty_file_leaves_no_design():
    # fpga_reset() clears the FPGA after the open (fabricfoxv2.py:110-112);
    # an empty file then sends only the 6 termination bytes, no sync word,
    # so the FPGA stays unconfigured though the count line still prints.
    board = _design_board()
    loader = FakeLoader(board, {HELD: b""})
    tt = board_sdk(board, loader=loader)
    board.run_script(LOAD_BODY, globals={"tt": tt})
    assert "Transmission complete, total bytes: 0" in _text(board)
    assert not board.wave.configured
