# SPDX-License-Identifier: Apache-2.0
"""Tests for the host capture flow: select_project() and run_capture().

No hardware: `FakeChunkBoard` plays the board, replaying chunks that are
built with `vgacap.stream`'s own writer so the bytes under test are packed
exactly the way the reader expects them. The end-to-end assertion is that
the file `run_capture()` produces parses with `vgacap.stream.read_stream`
-- the same code path `vgacap-frames` mirrors in C.
"""

from __future__ import annotations

import io
import struct

import pytest
from fake_repl import FakeChunkBoard

from ttcap.boards import RP2040_TT06, RP2350_DBV3
from ttcap.capture import (
    DEFAULT_PIO,
    MODE_EXTCLK,
    CaptureError,
    CaptureRequest,
    run_capture,
    select_project,
    stop_clock,
)
from ttcap.repl import RawRepl
from vgacap.stream import Header, Writer, read_stream


def header_for(profile, clock_hz: int = 100_000, desc: str = "") -> Header:
    return Header(
        version=1,
        sample_bits=profile.sample_bits,
        mode=MODE_EXTCLK,
        clock_hz=clock_hz,
        signal_map=profile.signal_map,
        samples_per_word=profile.samples_per_word,
        flags=profile.flags,
        desc=desc,
    )


def _after_header(header: Header, write) -> bytes:
    """Return just the chunk `write(writer)` appended, without the VGCH."""
    buf = io.BytesIO()
    writer = Writer(buf, header)
    mark = buf.tell()
    write(writer)
    return buf.getvalue()[mark:]


def raw_chunk(profile, samples) -> bytes:
    return _after_header(header_for(profile), lambda w: w.raw(samples))


def time_chunk(profile, dropped: int, msg: str) -> bytes:
    return _after_header(header_for(profile), lambda w: w.time(0, 0, dropped, msg))


def request(profile=RP2040_TT06, **kwargs) -> CaptureRequest:
    fields = dict(
        profile=profile,
        project=None,
        design=None,
        clock_hz=100_000,
        seconds=0,
        max_bytes=4096,
    )
    fields.update(kwargs)
    return CaptureRequest(**fields)


def connect(board: FakeChunkBoard) -> RawRepl:
    repl = RawRepl(board)
    repl.enter()
    return repl


# -- select_project -------------------------------------------------------


def test_sends_the_setup_commands_in_order():
    board = FakeChunkBoard([])
    repl = connect(board)

    did = select_project(repl, request(project="tt_um_rejunity_vga", clock_hz=250_000))

    assert board.commands == [
        "tt.shuttle['tt_um_rejunity_vga'].enable()",
        "tt.clock_project_stop()",
        "tt.reset_project(True)",
        "tt.clock_project_PWM(250000)",
        "tt.reset_project(False)",
    ]
    assert did == {
        "project": "tt_um_rejunity_vga",
        "enable": "index",
        "clock_hz": 250_000,
    }


def test_leaves_the_current_design_alone_when_no_project_is_named():
    board = FakeChunkBoard([])
    repl = connect(board)

    did = select_project(repl, request())

    assert not any("shuttle" in command for command in board.commands)
    assert did["enable"] is None
    # The clock is still stopped, reprogrammed and the project reset.
    assert board.commands[0] == "tt.clock_project_stop()"


def test_a_design_name_uses_the_same_shuttle_mechanism():
    board = FakeChunkBoard([])
    repl = connect(board)

    select_project(repl, request(profile=RP2350_DBV3, design="vga_test"))

    assert board.commands[0] == "tt.shuttle['vga_test'].enable()"


def test_falls_back_to_attribute_access_when_indexing_fails():
    # An SDK build where only ProjectMux.__getattr__ resolves the name.
    board = FakeChunkBoard([], errors={"tt.shuttle['tt_um_x'].enable()": "TypeError\r\n"})
    repl = connect(board)

    did = select_project(repl, request(project="tt_um_x"))

    assert board.commands[:2] == [
        "tt.shuttle['tt_um_x'].enable()",
        "getattr(tt.shuttle, 'tt_um_x').enable()",
    ]
    assert did["enable"] == "getattr"


def test_raises_capture_error_with_the_board_traceback():
    traceback = "Traceback (most recent call last):\r\n  AttributeError: no clock\r\n"
    board = FakeChunkBoard([], errors={"tt.clock_project_PWM(100000)": traceback})
    repl = connect(board)

    with pytest.raises(CaptureError) as excinfo:
        select_project(repl, request())

    assert "tt.clock_project_PWM(100000)" in str(excinfo.value)
    assert "AttributeError: no clock" in str(excinfo.value)
    # It stopped there: reset was never released on a half-set-up board.
    assert "tt.reset_project(False)" not in board.commands


def test_both_failing_shuttle_spellings_raise():
    board = FakeChunkBoard(
        [],
        errors={
            "tt.shuttle['nope'].enable()": "KeyError: nope\r\n",
            "getattr(tt.shuttle, 'nope').enable()": "AttributeError: nope\r\n",
        },
    )
    repl = connect(board)

    with pytest.raises(CaptureError, match="AttributeError"):
        select_project(repl, request(project="nope"))


def test_stop_clock_sends_one_command():
    board = FakeChunkBoard([])
    repl = connect(board)

    stop_clock(repl)

    assert board.commands == ["tt.clock_project_stop()"]


# -- run_capture ----------------------------------------------------------


def test_writes_a_stream_that_parses_with_the_right_header_and_samples():
    profile = RP2040_TT06
    first = list(range(0, 64))
    second = list(range(64, 128))
    board = FakeChunkBoard(
        [
            raw_chunk(profile, first),
            raw_chunk(profile, second),
            time_chunk(profile, 0, "overruns=0 rxstall=0 sysclk_hz=133000000"),
        ]
    )
    repl = connect(board)
    out = io.BytesIO()

    stats = run_capture(repl, request(profile, clock_hz=100_000, desc="tt07"), out)

    header, items = read_stream(out.getvalue())
    assert header.sample_bits == 12
    assert header.samples_per_word == 2
    assert header.flags == profile.flags
    assert header.mode == MODE_EXTCLK
    assert header.clock_hz == 100_000
    assert tuple(header.signal_map) == profile.signal_map
    assert header.desc == "tt07 clock=100000 edge=falling profile=rp2040-tt06map"

    runs = [item[1] for item in items if item[0] == "run"]
    assert runs == first + second
    assert stats.samples == 128
    assert stats.chunks == 3
    assert (stats.overruns, stats.rxstall, stats.stderr) == (0, 0, "")
    assert stats.clean
    assert stats.messages == ("overruns=0 rxstall=0 sysclk_hz=133000000",)


def test_appends_the_board_chunks_byte_for_byte():
    profile = RP2350_DBV3
    chunks = [raw_chunk(profile, list(range(16))), time_chunk(profile, 0, "done")]
    board = FakeChunkBoard(list(chunks))
    repl = connect(board)
    out = io.BytesIO()

    stats = run_capture(repl, request(profile), out)

    body = b"".join(chunks)
    assert out.getvalue().endswith(body)
    assert stats.bytes == len(body)


def test_counts_overruns_and_rxstall_from_the_board_summary():
    profile = RP2040_TT06
    board = FakeChunkBoard(
        [
            raw_chunk(profile, [1, 2, 3, 4]),
            time_chunk(profile, 8192, "overrun"),
            time_chunk(profile, 16384, "overruns=2 rxstall=1 sysclk_hz=133000000"),
        ]
    )
    repl = connect(board)

    stats = run_capture(repl, request(profile), io.BytesIO())

    # The summary is the board's own running total, not the sum of the
    # interim increments, and it wins.
    assert stats.overruns == 2
    assert stats.rxstall == 1
    # `dropped_samples` is cumulative in every TIME chunk, so the stats
    # carry the last value -- 16384, not 8192 + 16384.
    assert stats.dropped == 16384
    assert not stats.clean


def test_dropped_samples_are_taken_from_the_last_time_chunk_not_summed():
    profile = RP2040_TT06
    board = FakeChunkBoard(
        [
            time_chunk(profile, 100, "overrun"),
            time_chunk(profile, 200, "overrun"),
            time_chunk(profile, 300, "overruns=3 rxstall=0"),
        ]
    )
    repl = connect(board)

    stats = run_capture(repl, request(profile), io.BytesIO())

    assert stats.dropped == 300
    assert "dropped=300" in stats.format()


def test_falls_back_to_counting_interim_overrun_reports():
    profile = RP2040_TT06
    board = FakeChunkBoard(
        [
            time_chunk(profile, 8192, "overrun"),
            time_chunk(profile, 8192, "overrun"),
        ]
    )
    repl = connect(board)

    stats = run_capture(repl, request(profile), io.BytesIO())

    assert stats.overruns == 2


def test_records_the_board_stderr():
    profile = RP2040_TT06
    board = FakeChunkBoard([], stderr="Traceback:\r\n  MemoryError\r\n")
    repl = connect(board)

    stats = run_capture(repl, request(profile), io.BytesIO())

    assert "MemoryError" in stats.stderr
    assert not stats.clean
    assert stats.samples == 0


def test_sends_ctrl_c_once_after_seconds_elapse():
    profile = RP2040_TT06
    chunk = raw_chunk(profile, list(range(8)))
    board = FakeChunkBoard(
        [chunk] * 50,
        delay=0.02,
        on_interrupt=time_chunk(profile, 0, "overruns=0 rxstall=0"),
    )
    repl = connect(board)

    stats = run_capture(repl, request(profile, seconds=0.1, max_bytes=0), io.BytesIO())

    assert board.interrupts == 1
    # It stopped early -- the board still had queued chunks to send.
    assert stats.chunks < 51
    # ... and it kept reading afterwards, so the trailer was not lost.
    assert stats.messages == ("overruns=0 rxstall=0",)
    assert stats.seconds >= 0.1


def test_sends_ctrl_c_once_after_max_bytes():
    profile = RP2040_TT06
    chunk = raw_chunk(profile, list(range(8)))
    board = FakeChunkBoard([chunk] * 20, on_interrupt=time_chunk(profile, 0, "stopped"))
    repl = connect(board)

    stats = run_capture(repl, request(profile, max_bytes=len(chunk) * 3), io.BytesIO())

    assert board.interrupts == 1
    assert stats.bytes >= len(chunk) * 3
    assert stats.messages == ("stopped",)


def test_interrupts_and_takes_the_traceback_when_the_board_goes_quiet():
    # The state machine stuck in its `wait` because the project clock is
    # not running: no chunk ever arrives, and the script is still up there.
    profile = RP2040_TT06
    board = FakeChunkBoard([], terminate=False, on_quiet_stderr="KeyboardInterrupt\r\n")
    repl = connect(board)

    stats = run_capture(repl, request(profile), io.BytesIO(), chunk_timeout=0.2)

    assert stats.timed_out
    assert board.interrupts == 1
    assert "KeyboardInterrupt" in stats.stderr
    assert not stats.clean
    assert any("host timeout" in m for m in stats.messages)


def test_ships_the_profile_and_pio_in_the_script_it_runs():
    profile = RP2350_DBV3
    board = FakeChunkBoard([])
    repl = connect(board)

    run_capture(repl, request(profile, buf_words=1024, edge="rising"), io.BytesIO())

    (script,) = board.commands
    assert script.startswith("CFG = ")
    cfg = eval(script.splitlines()[0][len("CFG = ") :])  # noqa: S307 - our own repr
    assert cfg["gpio_base"] == 16
    assert cfg["push_thresh"] == 32
    assert cfg["buf_words"] == 1024
    assert cfg["edge"] == "rising"
    assert cfg["pio"] == DEFAULT_PIO != 0


def test_stream_with_no_samples_is_still_a_valid_file():
    profile = RP2040_TT06
    board = FakeChunkBoard([time_chunk(profile, 0, "overruns=0 rxstall=1")])
    repl = connect(board)
    out = io.BytesIO()

    stats = run_capture(repl, request(profile), out)

    header, items = read_stream(out.getvalue())
    assert header.clock_hz == 100_000
    assert stats.samples == 0
    assert [kind for kind, *_ in items] == ["time"]


# -- CaptureRequest validation -------------------------------------------


@pytest.mark.parametrize(
    "kwargs",
    [
        {"project": "a", "design": "b"},
        {"edge": "both"},
        {"clock_hz": 0},
        {"seconds": -1},
        {"seconds": 0, "max_bytes": 0},
    ],
)
def test_capture_request_rejects_impossible_requests(kwargs):
    with pytest.raises(ValueError):
        request(**kwargs)


def test_time_chunk_shorter_than_its_fixed_fields_is_an_error():
    board = FakeChunkBoard([b"TIME" + struct.pack("<I", 4) + b"\x00\x00\x00\x00"])
    repl = connect(board)

    with pytest.raises(CaptureError, match="TIME chunk"):
        run_capture(repl, request(), io.BytesIO())
