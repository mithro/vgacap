# SPDX-License-Identifier: Apache-2.0
"""The vgacap MicroPython module, off hardware.

`build/mpy-unix/micropython` (mpy/build-unix.sh) is MicroPython's unix port
with the module built on the simulated board: the module's C binding
(mpy/modvgacap.c, mpy/hal_mpy.c) over the capture engines the pico-sdk
firmware runs (firmware/core), on firmware/sim's board. These tests drive
it through the raw REPL as ttcap drives a demo board (`mpy_unix_board.py`
plays the board side) and check, as test_fw_sim.py does for the firmware:

* frame and window captures are the firmware's chunks byte for byte and
  reconstruct pixel-exactly with vgacap-frames;
* a streamed capture is the design's consecutive samples;
* stops between chunks, overruns, rxstall, refusals (the firmware's `err`
  texts, as ValueError), and `ttcap capture --firmware mpy-module`.

Without the binary the tests skip, unless VGACAP_REQUIRE_MPY_UNIX is set
(CI sets it), when they fail instead.
"""

from __future__ import annotations

import io
import os
import struct
import threading
import types

import numpy as np
import pytest
from fake_repl import FakeBinaryBoard
from fw_sim_link import SimLink
from mpy_unix_link import MPY_UNIX, MpyUnixLink
from test_fw_sim import (  # noqa: F401 - bars_uo and frames_dir are fixtures
    VGA,
    assert_pictures_exact,
    bars_uo,
    frame_request,
    frames_dir,
    pictures,
    request,
)

from ttcap import cli
from ttcap import firmware as fwproto
from ttcap.boards import RP2350_DBV3
from ttcap.capture import CaptureError, run_session, select_project, stream_header_bytes
from ttcap.module import ModuleSession, capture_code, probe
from ttcap.repl import RawRepl
from vgacap.stream import read_chunks

if not MPY_UNIX.exists():
    if os.environ.get("VGACAP_REQUIRE_MPY_UNIX"):
        raise RuntimeError("%s is missing: run mpy/build-unix.sh" % MPY_UNIX)
    pytest.skip("%s not built: run mpy/build-unix.sh" % MPY_UNIX, allow_module_level=True)


def open_board(uo_path, **kwargs) -> tuple[MpyUnixLink, RawRepl]:
    link = MpyUnixLink(uo_path, **kwargs)
    repl = RawRepl(link)
    repl.enter()
    return link, repl


@pytest.fixture
def board(bars_uo):
    link, repl = open_board(bars_uo[0])
    yield repl
    link.close()


def capture(repl: RawRepl, req):
    select_project(repl, req)
    out = io.BytesIO()
    stats = run_session(ModuleSession(repl, req), out)
    return stats, out.getvalue()


def firmware_capture(uo_path, req) -> bytes:
    """The same request through the pico-sdk firmware on the simulator."""
    link = SimLink(uo_path)
    try:
        fw = fwproto.FirmwareLink(link)
        fw.sync()
        fwproto.setup(fw, req)
        out = io.BytesIO()
        run_session(fwproto.FirmwareSession(fw, req), out)
    finally:
        link.close()
    return out.getvalue()


def frams(data: bytes) -> list[tuple]:
    return [struct.unpack_from("<IHHII", p) for t, p in read_chunks(data) if t == "FRAM"]


# -- the module ------------------------------------------------------------------------


def test_probe_and_the_call(board):
    probe(board)
    req = frame_request()
    assert capture_code(req) == (
        "import vgacap\nvgacap.frame(clock_hz=25000000, cpl=800, region_first=34, "
        "region_lines=481, window_lines=0, hsync_active=-1, vsync_active=-1, max_frames=2, "
        "max_bytes=0, edge='rising', sample_delay=0, pio=1, sm=0)"
    )


def test_probe_refuses_a_firmware_without_the_module():
    board = FakeBinaryBoard(modules={"vgacap": ImportError("no module named 'vgacap'")})
    repl = RawRepl(board)
    repl.enter()
    with pytest.raises(CaptureError, match="this board's firmware has no vgacap module"):
        probe(repl)


def test_probe_refuses_another_api():
    board = FakeBinaryBoard(modules={"vgacap": types.SimpleNamespace(API=2)})
    repl = RawRepl(board)
    repl.enter()
    with pytest.raises(CaptureError, match="speaks API '2', this host speaks 1"):
        probe(repl)


def test_refusals_are_the_firmwares_err_texts(board):
    def refused(call: str) -> str:
        stdout, stderr = board.exec("import vgacap\n" + call)
        assert stdout == ""
        return stderr.strip().splitlines()[-1]

    common = "buf_words=64, max_bytes=0, edge='falling', pio=1, sm=0"
    assert refused("vgacap.stream(%s, bogus=1)" % common) == "ValueError: unknown key 'bogus'"
    assert refused("vgacap.stream(max_bytes=0, edge='falling', pio=1, sm=0)") == (
        "ValueError: missing key 'buf_words'")
    assert refused("vgacap.stream(buf_words=64, max_bytes=0, edge='up', pio=1, sm=0)") == (
        "ValueError: edge must be falling|rising, got 'up'")
    assert refused("vgacap.stream(buf_words=64, max_bytes=0, edge='falling', pio=3, sm=0)") == (
        "ValueError: pio must be an integer 0..2, got '3'")
    assert refused("vgacap.stream(1)") == "TypeError: vgacap captures take keyword arguments only"
    assert refused("vgacap.stream(%s, buf=bytearray(100))" % common) == (
        "ValueError: buf_words must be an integer 1..12, got '64'")
    # The system clock the SDK picked for 100 kHz is no whole multiple.
    board.exec("tt.clock_project_PWM(100000)")
    req = frame_request(clock_hz=100_000)
    assert refused(capture_code(req).split("\n")[1]) == (
        "ValueError: sysclk 133000000 is not 2..32 whole project clocks of 100000")
    # and the board is still there for the next command
    assert board.exec("print(vgacap.API)")[0].strip() == "1"


def test_a_refused_capture_is_a_capture_error_before_the_header(board):
    req = frame_request(clock_hz=100_000)
    select_project(board, req)
    session = ModuleSession(board, req)
    with pytest.raises(CaptureError, match="refused the capture: ValueError: sysclk 133000000"):
        next(session.chunks())
    assert board.exec("print(1 + 1)")[0].strip() == "2"


# -- frame and window -----------------------------------------------------------------


def test_frame_pictures_are_the_firmwares_chunks(board, bars_uo, tmp_path):
    req = frame_request()
    stats, data = capture(board, req)
    assert stats.clean, stats.format()
    assert stats.messages[0] == "frame window=481 buffers=1 hsync_active=0 vsync_active=0"
    assert stats.messages[-1] == "frames=2 stalls=0 rxstall=0 sysclk_hz=100000000"
    assert frams(data) == [(0, 34, 481, 800, 481 * 800), (1, 34, 481, 800, 481 * 800)]
    # The firmware's engines under both: the same bytes after the header.
    assert data == firmware_capture(bars_uo[0], req)
    frames = pictures(tmp_path, data)
    assert len(frames) == 2
    assert_pictures_exact(frames, bars_uo[1])


def test_window_pictures(board, bars_uo, tmp_path):
    req = frame_request(window_lines=48, max_frames=1)
    stats, data = capture(board, req)
    assert stats.clean, stats.format()
    assert stats.messages[0] == "frame window=48 buffers=1 hsync_active=0 vsync_active=0"
    assert [f[1] for f in frams(data)] == list(range(34, 34 + 481, 48))
    assert data == firmware_capture(bars_uo[0], req)
    assert_pictures_exact(pictures(tmp_path, data), bars_uo[1])


def test_a_callers_buffer_sets_the_window(board, bars_uo, tmp_path):
    # A buffer allocated early, while the heap is whole, as a board would.
    req = frame_request(max_frames=1)
    select_project(board, req)
    call = capture_code(req).split("\n")[1][:-1] + ", buf=bytearray(100 * 800))"
    chunks = list(board.exec_chunks("import vgacap\n" + call))
    assert board.last_stderr == ""
    data = stream_header_bytes(req) + b"".join(t + struct.pack("<I", len(p)) + p for t, p in chunks)
    times = [p[18:].decode() for t, p in read_chunks(data) if t == "TIME"]
    assert times[0] == "frame window=100 buffers=1 hsync_active=0 vsync_active=0"
    assert [f[1:3] for f in frams(data)] == [(34, 100), (134, 100), (234, 100), (334, 100), (434, 81)]
    assert_pictures_exact(pictures(tmp_path, data), bars_uo[1])


def test_rxstall_is_reported(bars_uo):
    link, repl = open_board(bars_uo[0], rxstall=True)
    try:
        stats, _ = capture(repl, frame_request(max_frames=1))
    finally:
        link.close()
    assert stats.rxstall == 1
    assert "rxstall" in stats.messages
    assert stats.messages[-1] == "frames=1 stalls=1 rxstall=1 sysclk_hz=100000000"


# -- streaming -------------------------------------------------------------------------


def test_stream_is_the_designs_consecutive_samples(board, bars_uo, tmp_path):
    uo_path, image = bars_uo
    from ttcap.capture import frames_to_max_bytes

    req = request(clock_hz=100_000, max_bytes=frames_to_max_bytes(RP2350_DBV3, 1))
    stats, data = capture(board, req)
    assert stats.clean, stats.format()
    assert stats.messages == ("overruns=0 rxstall=0 sysclk_hz=133000000",)
    raws = [payload for tag, payload in read_chunks(data) if tag == "RAW "]
    assert all(struct.unpack_from("<I", p)[0] == 4096 * 4 for p in raws)
    got = np.concatenate([np.frombuffer(p[4:], dtype=np.uint8).reshape(-1, 4)[:, ::-1].ravel()
                          for p in raws])
    uo = np.frombuffer(uo_path.read_bytes(), dtype=np.uint8)
    vsync = (got >> 3) & 1
    edge = int(np.flatnonzero((vsync[1:] == 0) & (vsync[:-1] == 1))[0]) + 1
    start = (-edge) % len(uo)
    want = np.take(uo, np.arange(start, start + len(got)), mode="wrap")
    assert np.array_equal(got, want), "the stream is not the design's consecutive samples"
    assert stats.samples == len(got)
    assert_pictures_exact(pictures(tmp_path, data), image)


def test_stream_overruns_are_counted_like_capture_rp2(bars_uo):
    link, repl = open_board(bars_uo[0], bytes_per_s=20000)
    try:
        stats, _ = capture(repl, request(clock_hz=100_000, max_bytes=200_000, buf_words=1024))
    finally:
        link.close()
    assert stats.overruns > 0
    assert "overrun" in stats.messages
    assert stats.dropped == stats.overruns * 1024 * 4
    assert stats.messages[-1] == "overruns=%d rxstall=0 sysclk_hz=133000000" % stats.overruns


def test_stream_stops_between_chunks_on_request(board):
    seen = []
    req = request(clock_hz=100_000, buf_words=512, stop=lambda: len(seen) >= 3)
    select_project(board, req)
    session = ModuleSession(board, req)
    for block in session.chunks():
        seen.append(block[:4])
    stats = session.stats()
    assert stats.clean, stats.format()
    assert seen[-1] == b"TIME"
    assert stats.chunks >= 4
    assert all(tag == b"RAW " for tag in seen[1:-1])
    # the stop byte was the module's, not a KeyboardInterrupt: the REPL is fine
    assert board.exec("print(1 + 1)")[0].strip() == "2"


def test_request_stop_from_another_thread(board):
    req = request(clock_hz=100_000, buf_words=512, stop=lambda: False)
    select_project(board, req)
    session = ModuleSession(board, req)
    timer = threading.Timer(0.3, session.request_stop)
    timer.start()
    try:
        blocks = list(session.chunks())
    finally:
        timer.cancel()
    assert blocks[-1][:4] == b"TIME"
    assert session.stats().clean


def test_a_stream_whose_first_buffer_never_fills_leaves_the_board_at_a_prompt(board):
    req = request(clock_hz=100_000, buf_words=512, stop=lambda: False)
    select_project(board, req)
    board.exec("import vgacap\nvgacap.sim_clock(0)")  # the design never clocks
    session = ModuleSession(board, req, chunk_timeout=1.0)
    with pytest.raises(CaptureError, match="board went quiet before the first chunk"):
        next(session.chunks())
    assert board.exec("print(1 + 1)")[0].strip() == "2"


def test_an_interrupt_waiting_for_the_first_chunk_leaves_the_board_at_a_prompt(board, monkeypatch):
    req = request(clock_hz=100_000, buf_words=512, stop=lambda: False)
    select_project(board, req)
    board.exec("import vgacap\nvgacap.sim_clock(0)")

    def interrupted(n, timeout):  # the operator's Ctrl-C, once the call is running
        monkeypatch.undo()
        raise KeyboardInterrupt

    monkeypatch.setattr(board, "read_exact", interrupted)
    with pytest.raises(KeyboardInterrupt):
        next(ModuleSession(board, req).chunks())
    assert board.exec("print(1 + 1)")[0].strip() == "2"


def test_consumer_closing_early_leaves_the_board_at_a_prompt(board):
    req = request(clock_hz=100_000, buf_words=512, stop=lambda: False)
    select_project(board, req)
    session = ModuleSession(board, req)
    chunks = session.chunks()
    next(chunks)  # the header
    next(chunks)  # one RAW chunk
    session.close()
    assert board.exec("print(1 + 1)")[0].strip() == "2"


# -- the CLI ---------------------------------------------------------------------------


def test_cli_frame_capture(monkeypatch, bars_uo, tmp_path, capsys):
    link = MpyUnixLink(bars_uo[0])
    monkeypatch.setattr(cli, "link_from_url", lambda url: link)
    out = tmp_path / "cli.vgacap"
    code = cli.main([
        "capture", "serial:/dev/ttyACM0", "--firmware", "mpy-module", "--profile", "rp2350",
        "--clock-hz", "25000000", "--mode", "frame", "--frames", "1", "--out", str(out),
    ])
    printed = capsys.readouterr().out
    assert code == 0, printed
    assert "frames=1 stalls=0 rxstall=0" in printed
    assert link.closed
    assert_pictures_exact(pictures(tmp_path, out.read_bytes()), bars_uo[1])


def test_cli_refuses_a_firmware_without_the_module(monkeypatch, tmp_path, capsys):
    board = FakeBinaryBoard(modules={"vgacap": ImportError("no module named 'vgacap'")})
    monkeypatch.setattr(cli, "link_from_url", lambda url: board)
    out = tmp_path / "never.vgacap"
    code = cli.main([
        "capture", "serial:/dev/ttyACM0", "--firmware", "mpy-module", "--profile", "rp2350",
        "--clock-hz", "25000000", "--mode", "frame", "--frames", "1", "--out", str(out),
    ])
    assert code == 1
    assert "has no vgacap module" in capsys.readouterr().err
    assert not out.exists()
    # nothing was set up on the board: the probe came first
    assert not any("clock_project" in c for c in board.commands)


def test_a_small_heap_gets_most_of_it_as_window(bars_uo, tmp_path):
    # 640x480 needs 481 x 800 bytes; a 256 KB heap holds a fraction. The
    # module takes the largest block of whole lines that leaves its 4 KB
    # reserve, not merely the first power-of-two size that fitted.
    link, repl = open_board(bars_uo[0], heapsize="256K")
    try:
        stats, data = capture(repl, frame_request(max_frames=1))
    finally:
        link.close()
    assert stats.clean, stats.format()
    window = int(stats.messages[0].split()[1].split("=")[1])
    assert 0 < window < 481
    assert window * 800 > 0.9 * (stats.mem_free_before - 4096 - 800), (window, stats.mem_free_before)
    assert sum(f[2] for f in frams(data)) == 481
    assert_pictures_exact(pictures(tmp_path, data), bars_uo[1])


def test_keys_cannot_carry_protocol_text(board):
    stdout, stderr = board.exec(
        "import vgacap\nvgacap.stream(**{'buf_words': 64, 'max_bytes': 0, 'edge': 'falling', "
        "'pio=1 sm': 0})")
    assert stderr.strip().splitlines()[-1] == "ValueError: 'pio=1 sm' is not a key"
