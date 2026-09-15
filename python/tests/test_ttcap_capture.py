# SPDX-License-Identifier: Apache-2.0
"""Tests for the host capture flow: select_project() and run_capture().

No hardware: `FakeChunkBoard` plays the board, replaying chunks that are
built with `vgacap.stream`'s own writer so the bytes under test are packed
exactly the way the reader expects them. The end-to-end assertion is that
the file `run_capture()` produces parses with `vgacap.stream.read_stream`
-- the same code path `vgacap-frames` mirrors in C.
"""

from __future__ import annotations

import gc
import io
import struct
import sys
import types

import pytest
from fake_repl import FakeChunkBoard

from ttcap import capture, mp

from ttcap.boards import RP2040_TT06, RP2350_DBV3
from ttcap.capture import (
    CLOCKS_PER_FRAME_640X480,
    DEFAULT_BUF_WORDS,
    DEFAULT_PIO,
    MODE_EXTCLK,
    CaptureError,
    CaptureRequest,
    frames_to_max_bytes,
    min_free_bytes,
    prepare_board,
    run_capture,
    select_project,
    stop_clock,
)
from ttcap.capture import _CLEANUP
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


def sample_run(profile, samples) -> list[bytes]:
    """One RAW chunk plus a clean trailer, as a whole capture would look."""
    return [
        raw_chunk(profile, samples),
        time_chunk(profile, 0, "overruns=0 rxstall=0 sysclk_hz=133000000"),
    ]


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
    assert header.desc == (
        "tt07 clock=100000 mode=extclk edge=falling profile=rp2040-tt06map"
    )

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


def test_a_stop_arriving_mid_chunk_still_delivers_the_whole_chunk():
    # The regression that cost a 10 s fpga-1 capture: the stop used to raise
    # KeyboardInterrupt from inside `out.write()`, cutting a chunk at
    # 2183 of its declared 16388 bytes. The stop is cooperative now -- the
    # board picks the byte up between chunk writes -- so a stop landing
    # while a chunk is going out must not shorten it.
    profile = RP2040_TT06
    samples = list(range(256))
    big = raw_chunk(profile, samples)
    board = FakeChunkBoard(
        [big, big, big],
        split=64,
        stop_at_read=3,  # mid-way through the first chunk
        on_interrupt=time_chunk(profile, 0, "overruns=0 rxstall=0"),
    )
    repl = connect(board)
    out = io.BytesIO()

    stats = run_capture(repl, request(profile, max_bytes=10**6), out)

    # Whole chunk, whole sample list, and the trailer behind it.
    _header, items = read_stream(out.getvalue())
    assert [item[1] for item in items if item[0] == "run"] == samples
    assert stats.samples == len(samples)
    assert stats.messages == ("overruns=0 rxstall=0",)
    assert not stats.timed_out and not stats.error


def test_a_stop_between_chunks_drops_only_the_chunks_not_started():
    profile = RP2040_TT06
    chunk = raw_chunk(profile, [1, 2, 3, 4])
    board = FakeChunkBoard(
        [chunk] * 8, stop_at_read=2, on_interrupt=time_chunk(profile, 0, "stopped")
    )
    repl = connect(board)

    stats = run_capture(repl, request(profile, max_bytes=10**6), io.BytesIO())

    assert stats.chunks == 2  # the one in flight, then the trailer
    assert stats.messages == ("stopped",)


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
    assert "board went quiet" in stats.error


def test_ships_the_profile_and_pio_in_the_script_it_runs():
    profile = RP2350_DBV3
    board = FakeChunkBoard([])
    repl = connect(board)

    run_capture(repl, request(profile, buf_words=1024, edge="rising"), io.BytesIO())

    (script,) = [c for c in board.commands if c.startswith("CFG = ")]
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


def test_clears_the_previous_run_names_before_sending_the_script():
    profile = RP2040_TT06
    board = FakeChunkBoard(sample_run(profile, [1, 2, 3, 4]), mem_free=90_000)
    repl = connect(board)

    stats = run_capture(repl, request(profile), io.BytesIO())

    cleanup = board.commands[0]
    assert cleanup.startswith("for _n in [")
    assert "gc.collect()" in cleanup and "gc.mem_free()" in cleanup
    # Every name the script is about to bind, so a second run has room.
    for name in ("'CFG'", "'main'", "'FULL'", "'on_a'", "'machine'"):
        assert name in cleanup
    # ... and it runs before the script, not after.
    assert board.commands[1].startswith("CFG = ")
    assert stats.mem_free_before == 90_000
    assert "mem_free=90000" in stats.format()


def test_prepare_board_returns_the_free_heap():
    board = FakeChunkBoard([], mem_free=84_208)
    repl = connect(board)

    assert prepare_board(repl, ["A", "B"]) == 84_208
    assert "['A', 'B']" in board.commands[0]


def test_the_cleanup_snippet_actually_runs_on_a_dirty_namespace(monkeypatch, capsys):
    # Regression: the snippet used to `import gc` *first*, and the names it
    # deletes include the script's own imports -- so the loop popped `gc`
    # and the next line died with `NameError: name 'gc' isn't defined`, on
    # every run on tt07. Run the real generated code against a namespace
    # that already holds a previous run's names, `gc` among them.
    stub = types.SimpleNamespace(collect=gc.collect, mem_free=lambda: 84_208)
    monkeypatch.setitem(sys.modules, "gc", stub)

    names = mp.module_level_names(mp.load("capture_rp2.py")) + ["CFG"]
    assert "gc" in names, "the script imports gc, so the snippet must survive losing it"
    namespace: dict = {"__builtins__": __builtins__}
    for name in names:
        namespace[name] = object()

    exec(_CLEANUP % (sorted(names),), namespace)  # noqa: S102 - that is the point

    assert int(capsys.readouterr().out.strip()) == 84_208
    for name in ("machine", "main", "FULL", "CFG", "on_a"):
        assert name not in namespace
    assert "_n" not in namespace
    # `gc` is back, and it is the freshly imported module, not the leftover.
    assert namespace["gc"] is stub


def test_the_cleanup_snippet_imports_gc_after_the_deletion_loop():
    body = _CLEANUP % (["gc"],)

    assert body.index("globals().pop(_n, None)") < body.index("import gc")
    assert body.index("import gc") < body.index("gc.collect()")


def test_prepare_board_reports_a_board_error():
    code = _CLEANUP % (["A"],)
    board = FakeChunkBoard([], errors={code: "MemoryError\r\n"})
    repl = connect(board)

    with pytest.raises(CaptureError, match="MemoryError"):
        prepare_board(repl, ["A"])


def test_prepare_board_rejects_a_reply_that_is_not_a_number():
    board = FakeChunkBoard([], replies={_CLEANUP % (["A"],): "no idea\r\n"})
    repl = connect(board)

    with pytest.raises(CaptureError, match="gc.mem_free"):
        prepare_board(repl, ["A"])


def test_refuses_to_run_when_the_board_has_too_little_heap():
    # The script may not even compile there, and on tt07 that failure was
    # twice a `FATAL: uncaught exception` that needed a power cycle.
    profile = RP2040_TT06
    floor = min_free_bytes(DEFAULT_BUF_WORDS)
    board = FakeChunkBoard(sample_run(profile, [1, 2]), mem_free=floor - 1)
    repl = connect(board)
    out = io.BytesIO()

    with pytest.raises(CaptureError, match="Reset the board"):
        run_capture(repl, request(profile), out)

    # Nothing was sent and no header-only file was left behind.
    assert not any(c.startswith("CFG = ") for c in board.commands)
    assert out.getvalue() == b""


def test_the_heap_floor_scales_with_the_dma_buffers():
    # Two buffers of 4 * buf_words, plus a fixed allowance for compiling the
    # script. The old floor was a flat 40,000 whatever was asked for, which
    # is below even the default request's two 16 KB buffers.
    assert min_free_bytes(1024) == 8 * 1024 + 24_000
    assert min_free_bytes(DEFAULT_BUF_WORDS) == 56_768
    assert min_free_bytes(8192) == 8 * 8192 + 24_000
    assert min_free_bytes(8192) - min_free_bytes(4096) == 4 * 2 * 4096


@pytest.mark.parametrize("buf_words", [1024, 4096, 8192])
def test_a_bigger_buffer_request_needs_a_bigger_heap(buf_words):
    # The tt07 case the flat 40,000-byte floor let through: ~84 KB free is
    # plenty for the default request and nowhere near enough for 8192 words
    # (64 KB of buffers on an 80 KB heap).
    profile = RP2040_TT06
    floor = min_free_bytes(buf_words)
    board = FakeChunkBoard(sample_run(profile, [1, 2]), mem_free=floor - 1)
    repl = connect(board)

    with pytest.raises(CaptureError) as excinfo:
        run_capture(repl, request(profile, buf_words=buf_words), io.BytesIO())

    # The message carries the numbers, so the operator can see why.
    message = str(excinfo.value)
    assert str(floor) in message
    assert str(floor - 1) in message
    assert "--buf-words %d" % buf_words in message
    assert not any(c.startswith("CFG = ") for c in board.commands)

    # One byte more and the same request is accepted.
    ok_board = FakeChunkBoard(sample_run(profile, [1, 2]), mem_free=floor)
    stats = run_capture(connect(ok_board), request(profile, buf_words=buf_words), io.BytesIO())
    assert stats.mem_free_before == floor


def test_a_lost_framing_ends_the_run_instead_of_escaping():
    # A bad tag means nothing further can be read by length. The script is
    # still running on the board, so it has to be interrupted -- otherwise
    # every later command reads its output.
    profile = RP2040_TT06
    board = FakeChunkBoard(
        [raw_chunk(profile, [1, 2]), b"JUNK\x04\x00\x00\x00payload!"],
        on_quiet_stderr="KeyboardInterrupt\r\n",
    )
    repl = connect(board)
    out = io.BytesIO()

    stats = run_capture(repl, request(profile), out)

    assert "framing lost" in stats.error
    assert board.interrupts == 1
    assert not stats.clean
    # What arrived before the desync is still a valid stream.
    _header, items = read_stream(out.getvalue())
    assert [item[1] for item in items if item[0] == "run"] == [1, 2]


def test_frames_to_max_bytes_covers_the_requested_frames_plus_two():
    # 640x480@60 is 800 x 525 = 420_000 clocks per frame, and *two* extra
    # frame periods of margin: a capture starts mid-frame, so the tail of
    # that frame is unusable and the next boundary only locates the frames.
    # Measured on tt07: one extra frame gave 837,632 samples and
    # `vgacap-frames` rendered nothing at all.
    #
    # Plus 12 bytes per RAW chunk, which the board counts towards the limit
    # it stops on: 4 tag + 4 length + the 4-byte sample_count that opens
    # the payload.
    words_2040 = 420_000 * 5 // 2
    words_2350 = 420_000 * 5 // 4
    assert frames_to_max_bytes(RP2040_TT06, 3) == 4 * words_2040 + 12 * 257
    assert frames_to_max_bytes(RP2350_DBV3, 3) == 4 * words_2350 + 12 * 129
    # More frames is more bytes, and the RP2350 packs twice as densely.
    assert frames_to_max_bytes(RP2040_TT06, 9) > frames_to_max_bytes(RP2040_TT06, 3)
    assert frames_to_max_bytes(RP2040_TT06, 3) > 2 * 4 * words_2350

    with pytest.raises(ValueError):
        frames_to_max_bytes(RP2040_TT06, 0)
    with pytest.raises(ValueError):
        frames_to_max_bytes(RP2040_TT06, 1, buf_words=0)


@pytest.mark.parametrize("profile", [RP2040_TT06, RP2350_DBV3], ids=lambda p: p.name)
@pytest.mark.parametrize("frames", [1, 3, 9])
@pytest.mark.parametrize("buf_words", [1024, 4096, 8192])
def test_the_frames_budget_actually_buys_the_samples(profile, frames, buf_words):
    # Replay the board's own stop rule: it writes whole chunks and stops
    # once `sent` -- which counts each chunk's 12 non-sample bytes -- has
    # reached the limit. Budgeting only the sample bytes left the capture
    # ~0.2% short (1,257,472 samples where 1,260,000 were asked for), which
    # the +2 frame margin covered but the arithmetic should not need it to.
    budget = frames_to_max_bytes(profile, frames, buf_words=buf_words)
    wire_per_chunk = 12 + 4 * buf_words
    samples_per_chunk = buf_words * profile.samples_per_word

    sent = 0
    captured = 0
    while sent < budget:
        sent += wire_per_chunk
        captured += samples_per_chunk

    assert captured >= CLOCKS_PER_FRAME_640X480 * (frames + 2)


def test_time_chunk_shorter_than_its_fixed_fields_is_an_error():
    board = FakeChunkBoard(
        [b"TIME" + struct.pack("<I", 4) + b"\x00\x00\x00\x00"],
        replies={"print(1)": "1\r\n"},
    )
    repl = connect(board)

    with pytest.raises(CaptureError, match="TIME chunk"):
        run_capture(repl, request(), io.BytesIO())

    # The error is the caller's, but the board is not left running: it was
    # asked to stop and the rest of its command was taken off the wire.
    assert board.interrupts == 1
    assert repl.exec("print(1)") == ("1\r\n", "")


class _FailingWrites:
    """A file object that fails on the `nth` write, like a full disk."""

    def __init__(self, nth: int, exc: BaseException) -> None:
        self.nth = nth
        self.exc = exc
        self.writes = 0
        self.buf = io.BytesIO()

    def write(self, data: bytes) -> int:
        self.writes += 1
        if self.writes == self.nth:
            raise self.exc
        return self.buf.write(data)


class _StubbornBoard(FakeChunkBoard):
    """Keeps streaming through the cooperative stop; only Ctrl-C ends it.

    A board that is mid-chunk, or busy enough that the stop byte waits,
    looks exactly like this for a while -- and a board that never picks the
    byte up at all looks like it forever.
    """

    def _request_stop(self) -> None:
        if self.interrupts >= 2:
            super()._request_stop()


@pytest.mark.parametrize(
    "exc",
    [OSError(28, "No space left on device"), KeyboardInterrupt()],
    ids=["oserror", "keyboardinterrupt"],
)
def test_a_failure_in_the_chunk_loop_still_stops_the_board(exc):
    # Neither is the board's fault and neither used to tear it down: the
    # generator was dropped with the script still writing chunks, and the
    # next command read those instead of its own output.
    profile = RP2040_TT06
    board = FakeChunkBoard(
        [raw_chunk(profile, [1, 2])] * 6,
        on_interrupt=time_chunk(profile, 0, "overruns=0 rxstall=0"),
        terminate=False,
        replies={"print(1)": "1\r\n"},
    )
    repl = connect(board)
    out = _FailingWrites(3, exc)  # the header, one chunk, then the failure

    with pytest.raises(type(exc)):
        run_capture(repl, request(profile, max_bytes=0, seconds=30), out)

    # Stopped cooperatively -- one stop byte, no last-resort Ctrl-C -- and
    # the link is back at a prompt for whatever the caller does next.
    assert board.interrupts == 1
    assert repl.exec("print(1)") == ("1\r\n", "")


def test_a_board_that_ignores_the_stop_gets_a_real_ctrl_c(monkeypatch):
    profile = RP2040_TT06
    monkeypatch.setattr(capture, "STOP_DRAIN_TIMEOUT", 0.05)
    board = _StubbornBoard(
        [raw_chunk(profile, [1, 2])] * 6,
        terminate=False,
        on_quiet_stderr="KeyboardInterrupt\r\n",
        replies={"print(1)": "1\r\n"},
    )
    repl = connect(board)
    out = _FailingWrites(3, OSError(28, "No space left on device"))

    with pytest.raises(OSError):
        run_capture(repl, request(profile, max_bytes=0, seconds=30), out)

    # The stop byte, then the real Ctrl-C the script could not ignore.
    assert board.interrupts == 2
    assert repl.exec("print(1)") == ("1\r\n", "")


def test_a_short_raw_payload_stops_the_board_too():
    # `struct.unpack_from("<I", payload, 0)` on a RAW chunk carrying fewer
    # than four bytes raises `struct.error`, which is neither a
    # `CaptureError` nor an `OSError`.
    profile = RP2040_TT06
    board = FakeChunkBoard(
        [b"RAW " + struct.pack("<I", 2) + b"\x00\x01"],
        terminate=False,
        replies={"print(1)": "1\r\n"},
    )
    repl = connect(board)

    with pytest.raises(struct.error):
        run_capture(repl, request(profile), io.BytesIO())

    assert board.interrupts == 1
    assert repl.exec("print(1)") == ("1\r\n", "")
