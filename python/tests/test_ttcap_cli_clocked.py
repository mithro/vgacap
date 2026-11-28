# SPDX-License-Identifier: Apache-2.0
"""`ttcap capture --mode clocked`, driven through `cli.main()`.

The link is the emulator (`emu_repl.EmuRepl`) with a `ClockedDesign`
counter behind the pins, as in `test_ttcap_clocked.py`: the whole path from
the arguments to the file runs, `load_held` and the clocked body included.

Clocked mode loads its own bitstream and makes exactly `--clocks` clocks,
so the options that pick a project, bound a run some other way or shape a
VGA sampler do not apply to it: each is refused with exit 2 before the
board is touched, as are the options that only clocked mode takes, given
to another mode.
"""

from __future__ import annotations

import io

import pytest
from emu_repl import EmuRepl
from fake_tt import ASIC_MANUAL_INPUTS

from ttcap import cli
from ttcap.boards import RP2040_TT06
from ttcap.capture import MODE_SELFCLK
from ttcap.emu import Board, ClockedDesign
from ttcap.repl import RawRepl
from vgacap.stream import read_stream

CLK, RST = 16, 14
UO = list(range(33, 41))
SYSCLK = 133_000_000
BITSTREAM = b"\xff\x00\x00\xff\x7e\xaa\x99\x7e" + bytes(range(256)) * 40


def counter(clocks: int, reset_clocks: int) -> list[int]:
    return [0 if k < reset_clocks else (k - reset_clocks + 1) & 0xFF for k in range(clocks)]


def samples_of(data: bytes) -> list[int]:
    _, items = read_stream(data)
    out = []
    for item in items:
        if item[0] == "run":
            out.extend([item[1]] * item[2])
    return out


class Bench:
    """An emulator board behind `cli.link_from_url`, and a bitstream file."""

    def __init__(self, monkeypatch, tmp_path, *, pulls=None, **link_kwargs) -> None:
        self.board = Board(ClockedDesign(UO, clk_gpio=CLK, rst_gpio=RST),
                           sysclk_hz=SYSCLK, pulls={RST: 1} if pulls is None else pulls)
        self.link = EmuRepl(self.board, **link_kwargs)
        monkeypatch.setattr(cli, "link_from_url", lambda url: self.link)
        self.bitstream = tmp_path / "counter.bin"
        self.bitstream.write_bytes(BITSTREAM)
        self.out = tmp_path / "clocked.vgacap"

    def args(self, *extra: str, clocks: int = 64) -> list[str]:
        return ["capture", "serial:/dev/null", "--profile", "rp2350", "--mode", "clocked",
                "--bitstream", str(self.bitstream), "--clocks", str(clocks),
                "--clock-hz", "5000000", "--buf-words", "4", "--out", str(self.out), *extra]

    def level(self, gpio: int) -> int:
        return self.board.pad_level(gpio, self.board.now)


def test_a_clocked_capture_writes_every_clock_and_exits_zero(monkeypatch, capsys, tmp_path):
    bench = Bench(monkeypatch, tmp_path)

    assert cli.main(bench.args()) == 0

    data = bench.out.read_bytes()
    header, _ = read_stream(data)
    assert header.mode == MODE_SELFCLK and header.clock_hz == round(SYSCLK / 27)
    assert samples_of(data) == counter(64, 10)
    out = capsys.readouterr().out
    assert "profile=rp2350-dbv3 bitstream=counter.bin clocks=64 reset_clocks=10" in out
    assert "clock_hz=4925926 pio_div=1 high=12 low=11" in out
    assert "status=ok board_samples=64" in out
    assert "wrote %s" % bench.out in out
    # Put back: the clock stopped and reset released.
    assert bench.level(CLK) == 0 and bench.level(RST) == 1


def test_reset_clocks_clock_high_ui_in_and_pio_reach_the_run(monkeypatch, capsys, tmp_path):
    bench = Bench(monkeypatch, tmp_path, mode=ASIC_MANUAL_INPUTS)

    code = cli.main(bench.args("--reset-clocks", "3", "--clock-high", "8", "--ui-in", "0x21",
                               "--pio", "1", clocks=16))

    assert code == 0, capsys.readouterr()
    data = bench.out.read_bytes()
    assert samples_of(data) == counter(16, 3)
    header, _ = read_stream(data)
    assert "ui_in=33" in header.desc and "high=8 low=15" in header.desc
    assert bench.link.tt.mode == ASIC_MANUAL_INPUTS  # put back


def test_a_clock_high_under_the_floor_warns_and_still_runs(monkeypatch, capsys, tmp_path):
    bench = Bench(monkeypatch, tmp_path)
    args = bench.args("--clock-high", "0", clocks=16)
    args[args.index("--clock-hz") + 1] = "7000000"

    assert cli.main(args) == 0

    err = capsys.readouterr().err
    assert ("warning: --clock-high 0 reads uo_out 8 ns after the rising edge, which sees "
            "the pin at -8 ns, under the 65 ns pin-time floor") in err
    assert "a sweep point, not a reference" in err


def test_the_under_the_floor_warning_comes_before_the_board_is_loaded(monkeypatch, capsys,
                                                                       tmp_path):
    # Re-review R1-2: said once the timing is known, not after the run.
    import ttcap.capture as capture

    bench = Bench(monkeypatch, tmp_path)
    args = bench.args("--clock-high", "0", clocks=16)
    args[args.index("--clock-hz") + 1] = "7000000"
    seen = []
    load_held = capture.load_held

    def loading(*a, **k):
        seen.append(capsys.readouterr().err)
        return load_held(*a, **k)

    monkeypatch.setattr(capture, "load_held", loading)

    assert cli.main(args) == 0

    assert len(seen) == 1 and "a sweep point, not a reference" in seen[0]
    assert "a sweep point" not in capsys.readouterr().err  # said once


class _BinaryStdout:
    """`sys.stdout` with a `.buffer` that keeps the bytes."""

    def __init__(self) -> None:
        self.buffer = io.BytesIO()

    def write(self, data) -> int:
        return len(data)

    def flush(self) -> None:
        pass


def test_a_clocked_capture_can_stream_to_stdout(monkeypatch, capsys, tmp_path):
    bench = Bench(monkeypatch, tmp_path)
    stdout = _BinaryStdout()
    monkeypatch.setattr(cli.sys, "stdout", stdout)
    args = bench.args(clocks=32)
    args[args.index("--out") + 1] = "-"

    assert cli.main(args) == 0

    assert samples_of(stdout.buffer.getvalue()) == counter(32, 10)
    assert "status=ok" in capsys.readouterr().err


class _ClosingBuffer(io.BytesIO):
    """A pipe whose reader leaves after `keep` writes."""

    def __init__(self, keep: int) -> None:
        super().__init__()
        self.keep = keep

    def write(self, data) -> int:
        if self.keep <= 0:
            raise BrokenPipeError(32, "Broken pipe")
        self.keep -= 1
        return super().write(data)


def _leaving_consumer(monkeypatch, bench) -> list[str]:
    stdout = _BinaryStdout()
    stdout.buffer = _ClosingBuffer(keep=3)
    monkeypatch.setattr(cli.sys, "stdout", stdout)
    monkeypatch.setattr(cli, "_silence_stdout", lambda: None)
    args = bench.args("--buf-words", "16", clocks=4000)
    args[args.index("--out") + 1] = "-"
    return args


def test_a_consumer_that_leaves_after_a_clean_run_exits_zero(monkeypatch, capsys, tmp_path):
    # The emulator runs the body to its end before the host reads a chunk,
    # so the stop the closed pipe asks for comes too late to matter: the
    # board made every clock, and a consumer may leave when it likes.
    bench = Bench(monkeypatch, tmp_path)

    assert cli.main(_leaving_consumer(monkeypatch, bench)) == 0

    err = capsys.readouterr().err
    assert cli.CONSUMER_CLOSED_MESSAGE in err and "status=ok" in err


def test_a_consumer_that_stops_the_run_short_exits_one(monkeypatch, capsys, tmp_path):
    # When the stop does reach the board (here: as soon as the clock runs,
    # standing in for the stop byte the closed pipe sends), the run is short
    # of --clocks, status=stopped, so not clean, and says so.
    bench = Bench(monkeypatch, tmp_path)
    started = []

    def stop_when(board):
        if not board.blocks[1].sms[0].enabled:
            return False
        started.append(board.now)
        return board.now > started[0] + 40_000

    bench.board.stop_when = stop_when

    assert cli.main(_leaving_consumer(monkeypatch, bench)) == 1

    err = capsys.readouterr().err
    assert cli.CONSUMER_CLOSED_MESSAGE in err
    assert "not clean" in err and "status=stopped" in err
    assert bench.level(CLK) == 0 and bench.level(RST) == 1


def test_a_run_that_is_not_clean_exits_one_and_keeps_its_file(monkeypatch, capsys, tmp_path):
    # rst_n never reads high: the body says so, and the run is not clean.
    bench = Bench(monkeypatch, tmp_path, pulls={}, mode=ASIC_MANUAL_INPUTS)

    assert cli.main(bench.args("--ui-in", "0", clocks=16)) == 1

    captured = capsys.readouterr()
    assert "not clean" in captured.err and "status=error" in captured.err
    assert "error=rst_n stuck low" in captured.out
    read_stream(bench.out.read_bytes())  # what was written is a valid stream
    assert bench.level(CLK) == 0
    assert bench.link.tt.mode == ASIC_MANUAL_INPUTS


def test_a_no_reply_failure_is_recovered_and_the_board_put_back(monkeypatch, capsys, tmp_path):
    bench = Bench(monkeypatch, tmp_path)
    exec_ = RawRepl.exec
    state = {"loaded": False, "quiet": True}

    def quiet_after_the_load(self, code, *args, **kwargs):
        if state["loaded"] and state["quiet"] and "gc.mem_free()" in code:
            state["quiet"] = False
            raise TimeoutError("no reply")
        if "spi_transferPIO" in code:
            state["loaded"] = True
        return exec_(self, code, *args, **kwargs)

    monkeypatch.setattr(RawRepl, "exec", quiet_after_the_load)

    assert cli.main(bench.args()) == 1

    err = capsys.readouterr().err
    assert "capture failed: TimeoutError: no reply" in err
    assert not bench.out.exists()  # nothing was written
    # recover_clocked put the board back: reset released, clock low.
    assert bench.level(RST) == 1 and bench.level(CLK) == 0


def _refused(monkeypatch, capsys, args) -> str:
    def no_board(url):
        raise AssertionError("the board was touched")

    monkeypatch.setattr(cli, "link_from_url", no_board)
    with pytest.raises(SystemExit) as info:
        cli.main(args)
    assert info.value.code == 2
    return capsys.readouterr().err


CLOCKED = ["capture", "serial:/dev/null", "--profile", "rp2350", "--mode", "clocked",
           "--bitstream", "x.bin", "--clocks", "64", "--clock-hz", "500000", "--out", "o.vgacap"]


@pytest.mark.parametrize("extra, option", [
    (["--seconds", "1"], "--seconds"),
    (["--seconds", "0"], "--seconds"),
    (["--seconds", "5"], "--seconds"),  # typed, though it is the default
    (["--max-bytes", "0"], "--max-bytes"),
    (["--max-bytes", "1000"], "--max-bytes"),
    (["--frames", "2"], "--frames"),
    (["--project", "tt_um_x"], "--project"),
    (["--design", "tt_um_x"], "--design"),
    (["--edge", "rising"], "--edge"),
    (["--firmware", "picosdk"], "--firmware picosdk"),
    (["--firmware", "mpy-module"], "--firmware mpy-module"),
    (["--vga-mode", "640x480@60"], "--vga-mode"),
    (["--window-lines", "8"], "--window-lines"),
    (["--sample-delay", "0"], "--sample-delay"),
    (["--rle-mask", "1"], "--rle-mask"),
    (["--rle-oneshot"], "--rle-oneshot"),
    (["--no-stop-clock"], "--no-stop-clock"),
    (["--asic-index", "3"], "--asic-index"),
    (["--profile", "rp2040"], "rp2040"),
])
def test_what_clocked_mode_refuses_before_the_board_is_touched(monkeypatch, capsys, extra, option):
    err = _refused(monkeypatch, capsys, CLOCKED + extra)
    assert option in err and "--mode clocked" in err


@pytest.mark.parametrize("drop, option", [("--bitstream", "--bitstream"), ("--clocks", "--clocks")])
def test_clocked_mode_needs_a_bitstream_and_a_clock_count(monkeypatch, capsys, drop, option):
    args = list(CLOCKED)
    at = args.index(drop)
    del args[at:at + 2]
    assert option in _refused(monkeypatch, capsys, args)


@pytest.mark.parametrize("extra, match", [
    (["--reset-clocks", "0"], "--reset-clocks must be at least 1"),
    (["--reset-clocks", "64"], "--clocks must be more than --reset-clocks"),
    (["--reset-clocks", "100"], "--clocks must be more than --reset-clocks"),
    (["--clock-high", "16"], "--clock-high must be 0..15"),
    (["--clock-high", "-1"], "--clock-high must be 0..15"),
    (["--buf-words", "4", "--reset-clocks", "36"], "--reset-clocks must be at most 35"),
])
def test_counts_known_before_the_link_are_usage_errors(monkeypatch, capsys, extra, match):
    # Review M4: like --clocks 6, these are exit 2, not a board failure.
    assert match in _refused(monkeypatch, capsys, CLOCKED + extra)


@pytest.mark.parametrize("clocks", ["6", "63", "0", "-4"])
def test_clocks_must_be_a_positive_multiple_of_4(monkeypatch, capsys, clocks):
    args = list(CLOCKED)
    args[args.index("--clocks") + 1] = clocks
    assert "multiple of 4" in _refused(monkeypatch, capsys, args)


@pytest.mark.parametrize("extra, option", [
    (["--bitstream", "x.bin"], "--bitstream"),
    (["--clocks", "64"], "--clocks"),
    (["--reset-clocks", "4"], "--reset-clocks"),
    (["--reset-clocks", "10"], "--reset-clocks"),  # the default, typed
    (["--clock-high", "4"], "--clock-high"),
])
@pytest.mark.parametrize("mode", ["stream", "frame", "window", "rle"])
def test_the_clocked_options_are_refused_in_every_other_mode(monkeypatch, capsys, mode, extra,
                                                              option):
    args = ["capture", "serial:/dev/null", "--profile", "rp2350", "--mode", mode,
            "--clock-hz", "500000", "--out", "o.vgacap", *extra]
    err = _refused(monkeypatch, capsys, args)
    assert option in err and "--mode clocked" in err


def test_the_stock_firmware_and_rp2350_named_outright_are_accepted(monkeypatch, capsys,
                                                                    tmp_path):
    bench = Bench(monkeypatch, tmp_path)
    assert cli.main(bench.args("--firmware", "micropython", "--reset-clocks", "10")) == 0


def test_an_rp2040_found_by_auto_is_refused_before_the_load(monkeypatch, capsys, tmp_path):
    bench = Bench(monkeypatch, tmp_path)
    monkeypatch.setattr(cli, "resolve_profile", lambda repl, name: RP2040_TT06)
    args = bench.args()
    args[args.index("--profile") + 1] = "auto"

    assert cli.main(args) == 1

    assert "RP2350" in capsys.readouterr().err
    assert not any("spi_transferPIO" in c for c in bench.link.commands)
    assert not bench.out.exists()


def test_a_board_gone_quiet_mid_stream_is_recovered_by_the_cli(monkeypatch, capsys, tmp_path):
    from ttcap.capture import ClockedSession

    bench = Bench(monkeypatch, tmp_path)
    original = ClockedSession._start_stream

    def stream_then_quiet(self):
        chunks = original(self)
        yield next(chunks)
        raise TimeoutError("timed out waiting for a chunk")

    monkeypatch.setattr(ClockedSession, "_start_stream", stream_then_quiet)

    assert cli.main(bench.args("--buf-words", "16", clocks=4000)) == 1

    err = capsys.readouterr().err
    assert "capture failed: TimeoutError: timed out waiting for a chunk" in err
    assert bench.out.exists()  # the opening record was written, and is kept
    assert bench.level(RST) == 1 and bench.level(CLK) == 0
    body = next(i for i, c in enumerate(bench.link.commands) if "clocked clocks=" in c)
    assert "tt.reset_project(False)" in bench.link.commands[body + 1:]
