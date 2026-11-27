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

from ttcap.emu import Board, ClockedDesign, Waveform

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
