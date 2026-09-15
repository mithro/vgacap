# SPDX-License-Identifier: Apache-2.0
"""Tests for the streaming capture API: iter_capture() and CaptureSession.

`run_capture()`'s own tests live next door in `test_ttcap_capture.py` and
are unchanged -- that is the point of the refactor. What is tested here is
everything a *file* sink never exercises: a consumer that stops reading
part way, a stop arriving from another thread, a `stop` predicate standing
in for a pipeline's EOS, and `FRAM` chunks being counted like `RAW ` ones.

No hardware: `FakeChunkBoard` plays the board, and every assertion about
recovery is made against what the fake actually saw -- the stop byte, the
chunks it still had queued, the prompt the host got back -- rather than
against the host's own bookkeeping.
"""

from __future__ import annotations

import io
import struct
import threading
import time

import pytest
from fake_repl import FakeChunkBoard

from ttcap.capture import (
    CaptureRequest,
    CaptureSession,
    iter_capture,
    stream_header_bytes,
)
from ttcap.boards import RP2040_TT06
from ttcap.repl import RawRepl
from vgacap.stream import Header, Writer, read_stream

PROFILE = RP2040_TT06


def header_for(profile, clock_hz: int = 100_000) -> Header:
    return Header(
        version=1,
        sample_bits=profile.sample_bits,
        mode=0,
        clock_hz=clock_hz,
        signal_map=profile.signal_map,
        samples_per_word=profile.samples_per_word,
        flags=profile.flags,
    )


def _after_header(profile, write) -> bytes:
    """Just the chunk `write(writer)` appended, without the VGCH."""
    buf = io.BytesIO()
    writer = Writer(buf, header_for(profile))
    mark = buf.tell()
    write(writer)
    return buf.getvalue()[mark:]


def raw_chunk(profile, samples) -> bytes:
    return _after_header(profile, lambda w: w.raw(samples))


def fram_chunk(profile, samples, frame_counter: int = 0) -> bytes:
    return _after_header(
        profile, lambda w: w.frame(frame_counter, 0, 480, 800, samples)
    )


def time_chunk(profile, dropped: int, msg: str) -> bytes:
    return _after_header(profile, lambda w: w.time(0, 0, dropped, msg))


def request(profile=PROFILE, **kwargs) -> CaptureRequest:
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


# -- the stream itself ----------------------------------------------------


def test_the_header_comes_first_and_the_chunks_follow_verbatim():
    chunks = [
        raw_chunk(PROFILE, list(range(32))),
        time_chunk(PROFILE, 0, "overruns=0 rxstall=0"),
    ]
    board = FakeChunkBoard(list(chunks))
    repl = connect(board)
    req = request()

    blocks = list(iter_capture(repl, req))

    # One block for the header, then one per board chunk -- no re-framing,
    # no coalescing, nothing the reader has to undo.
    assert blocks[0] == stream_header_bytes(req)
    assert blocks[1:] == chunks
    header, items = read_stream(b"".join(blocks))
    assert header.clock_hz == 100_000
    assert [item[1] for item in items if item[0] == "run"] == list(range(32))


def test_the_header_bytes_are_what_run_capture_would_have_written():
    # The one thing that must not drift: a stream built from the iterator
    # and a stream built by the file writer are byte-identical.
    from ttcap.capture import run_capture

    chunks = [raw_chunk(PROFILE, [1, 2, 3, 4]), time_chunk(PROFILE, 0, "done")]
    to_file = io.BytesIO()
    run_capture(connect(FakeChunkBoard(list(chunks))), request(), to_file)
    streamed = b"".join(iter_capture(connect(FakeChunkBoard(list(chunks))), request()))

    assert streamed == to_file.getvalue()


def test_a_session_reports_the_same_stats_the_file_path_does():
    from ttcap.capture import run_capture

    chunks = [
        raw_chunk(PROFILE, list(range(16))),
        time_chunk(PROFILE, 12, "overruns=2 rxstall=1 sysclk_hz=133000000"),
    ]
    to_file = io.BytesIO()
    file_stats = run_capture(connect(FakeChunkBoard(list(chunks))), request(), to_file)

    session = CaptureSession(connect(FakeChunkBoard(list(chunks))), request())
    for _block in session.chunks():
        pass
    stats = session.stats()

    assert (stats.bytes, stats.samples, stats.chunks) == (
        file_stats.bytes,
        file_stats.samples,
        file_stats.chunks,
    )
    assert (stats.overruns, stats.rxstall, stats.dropped) == (2, 1, 12)
    assert stats.messages == file_stats.messages
    assert stats.mem_free_before == file_stats.mem_free_before


def test_header_bytes_is_available_before_the_capture_starts():
    # The GStreamer element wants the caps before it has a single sample.
    req = request()
    session = CaptureSession(connect(FakeChunkBoard([])), req)

    assert session.header_bytes == stream_header_bytes(req)


def test_chunks_returns_the_same_iterator_every_time():
    session = CaptureSession(connect(FakeChunkBoard([])), request())

    assert session.chunks() is session.chunks()


# -- a consumer that stops reading ---------------------------------------


def test_a_consumer_that_closes_the_generator_leaves_the_board_recovered():
    # The crux of the whole refactor: a GStreamer element stops consuming at
    # an arbitrary moment, and the script on the board is still writing. If
    # nothing stops it, every later command reads its chunks instead of its
    # own output.
    chunk = raw_chunk(PROFILE, [1, 2, 3, 4])
    board = FakeChunkBoard(
        [chunk] * 8,
        on_interrupt=time_chunk(PROFILE, 0, "overruns=0 rxstall=0"),
        replies={"print(1)": "1\r\n"},
    )
    repl = connect(board)

    stream = iter_capture(repl, request(max_bytes=10**6))
    taken = [next(stream), next(stream)]  # the header, then one chunk
    stream.close()

    assert taken[1] == chunk
    # The board saw the cooperative stop byte, and the host read the command
    # out to its terminator -- no last-resort Ctrl-C was needed.
    assert board.interrupts == 1
    assert board.remaining == []
    # And the proof that the link is sane: the very next command gets its
    # own output back, not the capture's leftovers.
    assert repl.exec("print(1)") == ("1\r\n", "")


def test_closing_the_session_is_the_same_recovery_and_is_idempotent():
    chunk = raw_chunk(PROFILE, [1, 2])
    board = FakeChunkBoard(
        [chunk] * 8,
        on_interrupt=time_chunk(PROFILE, 0, "stopped"),
        replies={"print(1)": "1\r\n"},
    )
    repl = connect(board)
    session = CaptureSession(repl, request(max_bytes=10**6))

    stream = session.chunks()
    next(stream)
    next(stream)
    session.close()
    session.close()  # idempotent: no second stop byte, no second drain

    assert board.interrupts == 1
    assert repl.exec("print(1)") == ("1\r\n", "")
    # The stats are final once close() has run, and they are the ones the
    # capture actually accumulated.
    assert session.stats().chunks == 1
    assert session.stats() is session.stats()


def test_closing_a_session_that_never_started_touches_nothing():
    board = FakeChunkBoard([])
    repl = connect(board)
    session = CaptureSession(repl, request())

    session.close()

    assert board.interrupts == 0
    assert session.stats().chunks == 0


def test_a_consumer_that_closes_right_after_the_header_stops_nothing():
    # Nothing has been run on the board yet at that point -- the header is
    # the host's own bytes -- so there is no script to interrupt and no
    # stray Ctrl-C to leave behind.
    board = FakeChunkBoard([raw_chunk(PROFILE, [1])], replies={"print(1)": "1\r\n"})
    repl = connect(board)

    stream = iter_capture(repl, request())
    next(stream)
    stream.close()

    assert board.interrupts == 0
    assert repl.exec("print(1)") == ("1\r\n", "")


def test_a_stubborn_board_still_gets_a_real_ctrl_c_when_the_consumer_leaves(
    monkeypatch,
):
    from ttcap import capture as capture_mod

    class _Stubborn(FakeChunkBoard):
        """Ignores the cooperative stop; only a real Ctrl-C ends it."""

        def _request_stop(self) -> None:
            if self.interrupts >= 2:
                super()._request_stop()

    monkeypatch.setattr(capture_mod, "STOP_DRAIN_TIMEOUT", 0.05)
    board = _Stubborn(
        [raw_chunk(PROFILE, [1, 2])] * 8,
        terminate=False,
        on_quiet_stderr="KeyboardInterrupt\r\n",
        replies={"print(1)": "1\r\n"},
    )
    repl = connect(board)

    stream = iter_capture(repl, request(max_bytes=10**6))
    next(stream)
    next(stream)
    stream.close()

    assert board.interrupts == 2
    assert repl.exec("print(1)") == ("1\r\n", "")


# -- stopping from somewhere else ----------------------------------------


def test_request_stop_from_another_thread_ends_the_stream_whole():
    trailer = time_chunk(PROFILE, 0, "overruns=0 rxstall=0")
    chunk = raw_chunk(PROFILE, list(range(64)))
    board = FakeChunkBoard(
        [chunk] * 200, delay=0.005, on_interrupt=trailer, replies={"print(1)": "1\r\n"}
    )
    repl = connect(board)
    # No seconds, no byte budget: only the out-of-band stop ends this one,
    # which is the shape a pipeline's source element has.
    session = CaptureSession(repl, request(seconds=0, max_bytes=0, stop=lambda: False))

    blocks = []
    stopper = threading.Timer(0.05, session.request_stop)
    stopper.start()
    started = time.monotonic()
    try:
        for block in session.chunks():
            blocks.append(block)
    finally:
        stopper.cancel()
    elapsed = time.monotonic() - started

    # Promptly: nowhere near the 200 queued chunks.
    assert elapsed < 2.0
    assert 1 < len(blocks) < 201
    assert board.interrupts == 1
    # It ends with a whole chunk and the board's own trailer behind it, so
    # the bytes parse as a complete stream.
    assert blocks[-1] == trailer
    header, items = read_stream(b"".join(blocks))
    assert header.clock_hz == 100_000
    assert session.stats().messages == ("overruns=0 rxstall=0",)
    assert not session.stats().error and not session.stats().timed_out
    assert repl.exec("print(1)") == ("1\r\n", "")


def test_request_stop_before_the_capture_starts_is_remembered():
    # A pipeline can go to NULL between `start()` and the first pull; the
    # byte has nowhere to go yet, so the request is held until the script is
    # actually running.
    board = FakeChunkBoard(
        [raw_chunk(PROFILE, [1, 2])] * 8,
        on_interrupt=time_chunk(PROFILE, 0, "stopped"),
    )
    repl = connect(board)
    session = CaptureSession(repl, request(seconds=0, max_bytes=0, stop=lambda: False))

    session.request_stop()
    assert board.interrupts == 0  # nothing running: nothing to interrupt

    blocks = list(session.chunks())

    assert board.interrupts == 1
    assert session.stats().messages == ("stopped",)
    assert blocks[-1] == time_chunk(PROFILE, 0, "stopped")


def test_request_stop_sends_the_byte_once_however_often_it_is_called():
    board = FakeChunkBoard(
        [raw_chunk(PROFILE, [1, 2])] * 4,
        on_interrupt=time_chunk(PROFILE, 0, "stopped"),
    )
    repl = connect(board)
    session = CaptureSession(repl, request(max_bytes=10**6))

    stream = session.chunks()
    next(stream)
    session.request_stop()
    session.request_stop()
    session.request_stop()
    list(stream)

    assert board.interrupts == 1


def test_the_stop_predicate_ends_the_capture():
    # The pipeline's own EOS, consulted between chunks.
    seen: list[int] = []

    def stop() -> bool:
        seen.append(1)
        return len(seen) >= 3

    board = FakeChunkBoard(
        [raw_chunk(PROFILE, [1, 2, 3, 4])] * 20,
        on_interrupt=time_chunk(PROFILE, 0, "stopped"),
    )
    repl = connect(board)
    session = CaptureSession(repl, request(seconds=0, max_bytes=0, stop=stop))

    blocks = list(session.chunks())

    assert board.interrupts == 1
    # Three chunks were consulted for, so three were delivered before the
    # stop went out; the trailer follows them.
    assert session.stats().chunks == 4
    assert session.stats().messages == ("stopped",)
    assert blocks[-1] == time_chunk(PROFILE, 0, "stopped")


def test_a_request_with_a_stop_callable_may_have_no_other_limit():
    # The case `CaptureRequest` used to refuse outright, and the one a
    # source element needs: run until the consumer says otherwise.
    req = request(seconds=0, max_bytes=0, stop=lambda: False)

    assert req.seconds == 0 and req.max_bytes == 0
    assert req.cfg()["max_bytes"] == 0  # and the board is told "no limit" too


def test_a_request_with_no_limit_and_no_stop_callable_is_still_refused():
    with pytest.raises(ValueError, match="never stop"):
        request(seconds=0, max_bytes=0)


# -- chunk accounting -----------------------------------------------------


def test_fram_chunks_are_counted_like_raw_ones():
    # M6 captures whole frames into SRAM and ships them as FRAM chunks.
    # Counting only RAW would report samples=0 on a perfect capture, and
    # `ttcap capture` exits 3 on that ("the sampler never saw a clock edge").
    samples = list(range(48))
    board = FakeChunkBoard(
        [
            fram_chunk(PROFILE, samples, frame_counter=7),
            time_chunk(PROFILE, 0, "overruns=0 rxstall=0"),
        ]
    )
    session = CaptureSession(connect(board), request())

    blocks = list(session.chunks())

    assert session.stats().samples == len(samples)
    assert session.stats().chunks == 2
    # And the bytes are still a stream the reader accepts, frame and all.
    _header, items = read_stream(b"".join(blocks))
    assert ("frame", 7, 0, 480, 800, len(samples)) in items


def test_a_mixed_raw_and_fram_stream_adds_both():
    board = FakeChunkBoard(
        [
            raw_chunk(PROFILE, [1, 2, 3, 4]),
            fram_chunk(PROFILE, list(range(8))),
            time_chunk(PROFILE, 0, "overruns=0 rxstall=0"),
        ]
    )
    session = CaptureSession(connect(board), request())

    list(session.chunks())

    assert session.stats().samples == 12


def test_the_fram_sample_count_is_read_from_the_documented_offset():
    # A guard on the constant rather than on the behaviour: `FRAM` opens
    # <I frame_counter, H first_line, H line_count, I clocks_per_line,
    #  I sample_count>, so the count is 12 bytes in.
    from ttcap.capture import FRAM_SAMPLE_COUNT_OFFSET

    payload = fram_chunk(PROFILE, list(range(20)), frame_counter=3)[8:]

    assert struct.unpack_from("<I", payload, FRAM_SAMPLE_COUNT_OFFSET)[0] == 20


# -- stats while it runs --------------------------------------------------


def test_stats_can_be_read_while_the_capture_is_still_streaming():
    board = FakeChunkBoard(
        [raw_chunk(PROFILE, [1, 2, 3, 4])] * 4
        + [time_chunk(PROFILE, 5, "overruns=1 rxstall=0")]
    )
    session = CaptureSession(connect(board), request(max_bytes=10**6))

    stream = session.chunks()
    next(stream)  # the header
    next(stream)
    mid = session.stats()
    list(stream)
    final = session.stats()

    assert mid.chunks == 1 and mid.samples == 4
    assert final.chunks == 5 and final.samples == 16
    assert final.dropped == 5 and final.overruns == 1
