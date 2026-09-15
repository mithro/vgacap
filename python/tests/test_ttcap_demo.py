# SPDX-License-Identifier: Apache-2.0
"""Tests for `ttcap demo`, with no board anywhere.

Two halves. The first needs nothing installed: how a board slug becomes a
link, what the pipeline text says, and how the MJPEG broadcaster cuts a
multipart stream into parts. The second runs the real thing -- `gst-launch`,
the built plugin, and `gst/tests/fake_ttcap.py` standing in for the board --
and checks the files that come out, the stream a browser would see, and that
an interrupted run still leaves a playable video. That half skips cleanly
where GStreamer or the plugin is absent, so `uv run pytest` passes without
them.
"""

from __future__ import annotations

import os
import pathlib
import re
import shutil
import signal
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request

import pytest

from ttcap import demo as demo_mod
from ttcap.boards import WELLAND, bridge_ws_url
from ttcap.capture import CaptureError
from ttcap.cli import main

ROOT = pathlib.Path(__file__).resolve().parents[2]
BUILD = ROOT / "build"
PLUGIN = BUILD / "libgstvgacap.so"
FRAMES_TOOL = BUILD / "vgacap-frames"
FAKE_TTCAP = ROOT / "gst" / "tests" / "fake_ttcap.py"

_MISSING = (
    shutil.which("gst-launch-1.0") is None
    or shutil.which("gst-inspect-1.0") is None
    or not PLUGIN.exists()
)
needs_gstreamer = pytest.mark.skipif(
    _MISSING,
    reason="needs gst-launch-1.0/gst-inspect-1.0 and a built build/libgstvgacap.so "
           "(cmake -S . -B build && cmake --build build)",
)

#: Generous: every test that cares about time asserts its own bound.
TIMEOUT = 120


# ------------------------------------------------------------ board -> link


def test_a_welland_slug_resolves_to_its_bridge():
    link = demo_mod.resolve_link("tt07", hostname="someones-laptop")
    assert link.url == bridge_ws_url("tt07") == "ws://10.21.2.7:8765/serial"
    assert link.through_bridge


def test_every_welland_slug_resolves():
    for slug in WELLAND:
        assert demo_mod.resolve_link(slug, hostname="elsewhere").url == bridge_ws_url(slug)


def test_on_the_boards_own_pi_the_link_is_the_serial_device():
    link = demo_mod.resolve_link("tt07", hostname="pi-sw2-p7.welland.mithis.com")
    assert link.url == demo_mod.PI_SERIAL_LINK == "serial:/dev/ttboard"
    assert not link.through_bridge
    # The bridge holds the device open, so it has to be stopped first; that
    # is the one thing a person on the Pi has to be told.
    assert any("fpgas-tt" in note for note in link.notes)


def test_an_explicit_link_wins_over_the_board():
    link = demo_mod.resolve_link("tt07", "ws://127.0.0.1:18765/serial")
    assert link.url == "ws://127.0.0.1:18765/serial"
    # Nothing is known about where the far end of a tunnel goes, so a failure
    # there gets no bench-network advice.
    assert not link.through_bridge


def test_an_unknown_board_says_which_ones_there_are():
    with pytest.raises(CaptureError) as exc:
        demo_mod.resolve_link("tt99", hostname="elsewhere")
    message = str(exc.value)
    assert "tt99" in message
    assert "tt07" in message and "fpga-1" in message
    assert "--link" in message


def test_no_board_and_no_link_is_a_sentence_not_a_traceback():
    with pytest.raises(CaptureError) as exc:
        demo_mod.resolve_link(None)
    assert "--board" in str(exc.value) and "--link" in str(exc.value)


def test_the_tunnel_hint_is_the_command_to_type():
    hint = demo_mod.tunnel_hint("tt07")
    assert "ssh -N -L 7:10.21.2.7:8765 tweed.welland.mithis.com" in hint
    assert "ws://127.0.0.1:7/serial" in hint


# ------------------------------------------------------------- the pipeline


def build(**overrides):
    kwargs = dict(clock_hz=60000, seconds=5.0, project="tt_um_x")
    kwargs.update(overrides)
    argv, outputs = demo_mod.build_pipeline(
        "ws://host:8765/serial", pathlib.Path("/out"), **kwargs
    )
    return argv, outputs, demo_mod.format_pipeline(argv)


@needs_gstreamer
def test_the_source_is_named_and_set_by_property_never_by_uri():
    argv, _, text = build(ttcap_command="/usr/bin/true capture")
    # The bin's URI query may not set a command, on purpose: a URI can come
    # from somewhere that is not the operator's shell. So the demo names the
    # source itself and sets every setting, the command included, as a
    # property.
    assert "vgacapttsrc" in argv and "vgadecode" in argv
    assert not any(token.startswith("uri=") for token in argv)
    assert "ttcap-command=/usr/bin/true capture" in argv
    assert "link=ws://host:8765/serial" in argv
    assert "clock-hz=60000" in argv
    assert "project=tt_um_x" in argv
    assert text.startswith("gst-launch-1.0 -e -m ")


@needs_gstreamer
def test_a_default_run_writes_pngs_and_a_video():
    argv, outputs, _ = build()
    joined = " ".join(argv)
    assert "pngenc" in joined and "multifilesink" in joined
    assert "matroskamux" in joined
    assert any(name in joined for name, _ in demo_mod.VIDEO_ENCODERS)
    assert outputs == [str(pathlib.Path("/out/frame-%04d.png")),
                       str(pathlib.Path("/out/capture.mkv"))]


@needs_gstreamer
def test_window_and_serve_add_their_own_branches():
    argv, _, _ = build(window=True, mjpeg_fd=7)
    joined = " ".join(argv)
    assert "autovideosink" in joined
    assert "jpegenc" in joined and "multipartmux" in joined
    assert "fd=7" in joined
    assert joined.count("t.") == 4  # png, video, window, mjpeg


@needs_gstreamer
def test_a_run_with_no_outputs_at_all_is_refused():
    with pytest.raises(CaptureError) as exc:
        build(png=False, video=False)
    assert "nowhere to go" in str(exc.value)


@needs_gstreamer
def test_an_encoder_that_is_not_installed_is_named():
    missing = [name for name, _ in demo_mod.VIDEO_ENCODERS
               if not demo_mod.have_element(name)]
    if not missing:
        pytest.skip("every encoder the demo knows is installed here")
    with pytest.raises(CaptureError) as exc:
        build(video_encoder=missing[0])
    assert missing[0] in str(exc.value)


@needs_gstreamer
def test_without_fps_the_frames_keep_project_time():
    argv, _, _ = build()
    # No re-timing asked for, so vgadecode is left at its defaults and a
    # 60 kHz capture really is one frame every seven seconds.
    assert not any(token.startswith("output-fps") for token in argv)
    assert "repeat-last-frame=true" not in argv


@needs_gstreamer
def test_fps_re_times_the_video_through_vgadecode():
    argv, _, _ = build(fps="30/1")
    assert "repeat-last-frame=true" in argv
    assert "output-fps=30/1" in argv
    # On the decoder, so every output shares one cadence -- not on the
    # encoder branch, which would leave the PNGs and the browser view on a
    # different clock from the video.
    assert argv.index("output-fps=30/1") < argv.index("tee")


@pytest.mark.parametrize(
    "given, expected",
    [("30", "30/1"), ("30/1", "30/1"), ("25", "25/1"), (" 60 ", "60/1"),
     ("29.97", "2997/100"), ("7.5", "75/10"), ("1/2", "1/2")],
)
def test_a_frame_rate_can_be_written_any_of_the_usual_ways(given, expected):
    assert demo_mod.parse_fps(given) == expected


@pytest.mark.parametrize("given", ["nonsense", "30/", "", "-30", "0", "30/0", "1/2000"])
def test_a_frame_rate_that_is_not_one_says_so(given):
    with pytest.raises(CaptureError) as exc:
        demo_mod.parse_fps(given)
    assert "--fps" in str(exc.value)


def test_the_printed_pipeline_keeps_the_command_in_one_piece():
    # `ttcap-command` is several words; a printed pipeline that loses the
    # quoting round it is not one anybody can paste back.
    argv = ["gst-launch-1.0", "-e", "-m", "vgacapttsrc",
            "ttcap-command=uv run --no-sync ttcap", "!", "vgadecode", "!",
            "tee", "name=t", "t.", "!", "fakesink"]
    text = demo_mod.format_pipeline(argv)
    assert "'ttcap-command=uv run --no-sync ttcap'" in text
    assert " ! " in text  # the separators stay bare, the way they are written
    assert "'!'" not in text
    assert text.count("\\\n") == 1  # one line per chain


# ------------------------------------------------------- the timing message


def test_the_timing_message_is_read_back_off_the_bus():
    line = (
        'Got message #80 from element "dec" (element): vgacap-timing, '
        'mode=(string)"640x480\\@60", clocks-per-line=(uint)800, '
        'lines-per-frame=(uint)525, hsync-positive=(boolean)false, '
        'vsync-positive=(boolean)false, glitches=(uint)3;'
    )
    fields = demo_mod.parse_timing(line)
    assert fields is not None
    assert fields["mode"] == "640x480@60"
    assert fields["clocks-per-line"] == "800"
    assert fields["glitches"] == "3"
    described = demo_mod.describe_timing(fields)
    assert "640x480@60" in described and "glitches 3" in described


def test_a_progress_report_counts_the_files_that_exist():
    progress = demo_mod.Progress(stream=open(os.devnull, "w"), interval=0.0)
    for index in range(3):
        progress.line(
            'Got message #%d from element "multifilesink0" (element): '
            'GstMultiFileSink, filename=(string)/out/frame-%04d.png, '
            "index=(int)%d;" % (100 + index, index, index)
        )
    assert progress.frames == 3


# ------------------------------------------------------------ the whole run


def free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def fake_ttcap_command(*extra: str) -> str:
    """`ttcap-command` that runs the fake instead of a board.

    `sys.executable` is the environment that can import `vgacap`, named
    outright so no shebang and no PATH are involved.
    """
    return " ".join([sys.executable, str(FAKE_TTCAP), *extra])


def demo_argv(outdir: pathlib.Path, *extra: str, fake: tuple[str, ...] = ("--frames", "8"),
              clock_hz: int = 25_000_000) -> list[str]:
    return [
        sys.executable, "-m", "ttcap.cli", "demo",
        "--link", "serial:/dev/null",
        "--clock-hz", str(clock_hz),
        "--seconds", "0",
        "--outdir", str(outdir),
        "--ttcap-command", fake_ttcap_command(*fake),
        *extra,
    ]


def gst_env() -> dict:
    env = dict(os.environ)
    env["GST_PLUGIN_PATH"] = str(BUILD)
    return env


def run_demo_process(argv: list[str], timeout: int = TIMEOUT) -> subprocess.CompletedProcess:
    return subprocess.run(argv, capture_output=True, text=True, env=gst_env(),
                          timeout=timeout)


def video_duration_ns(path: pathlib.Path) -> int:
    """The video's duration according to `gst-discoverer-1.0`.

    Asked of the file rather than of the pipeline that wrote it: a Matroska
    file whose muxer never finished has no duration to report, which is
    exactly the difference an interrupted run has to not make.
    """
    proc = subprocess.run(["gst-discoverer-1.0", str(path)], capture_output=True,
                          text=True, timeout=60)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    match = re.search(r"Duration: (\d+):(\d\d):(\d\d)\.(\d+)", proc.stdout)
    assert match, "no duration in:\n" + proc.stdout
    hours, minutes, seconds, fraction = match.groups()
    return (
        ((int(hours) * 60 + int(minutes)) * 60 + int(seconds)) * 1_000_000_000
        + int(fraction.ljust(9, "0")[:9])
    )


def video_framerate(path: pathlib.Path) -> str:
    """The video's frame rate as `gst-discoverer-1.0` reports it, e.g. `10/1`."""
    proc = subprocess.run(["gst-discoverer-1.0", str(path)], capture_output=True,
                          text=True, timeout=60)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    match = re.search(r"Frame rate: (\d+/\d+)", proc.stdout)
    assert match, "no frame rate in:\n" + proc.stdout
    return match.group(1)


def reference_frame_count(stream: pathlib.Path, work: pathlib.Path) -> int:
    """How many frames the reference renderer finds in the same bytes."""
    work.mkdir(parents=True, exist_ok=True)
    subprocess.run([str(FRAMES_TOOL), str(stream), str(work / "f")],
                   capture_output=True, text=True, check=True, timeout=60)
    return len(list(work.glob("f-*.ppm")))


@needs_gstreamer  # the encoder for capture.mkv is chosen from the real registry
def test_dry_run_prints_a_pipeline_and_touches_nothing(tmp_path, capsys):
    outdir = tmp_path / "never-made"
    code = main([
        "demo", "--board", "tt07", "--project", "tt_um_rejunity_vga",
        "--clock-hz", "60000", "--seconds", "60", "--outdir", str(outdir),
        "--window", "--serve", "8099", "--dry-run",
    ])
    assert code == 0
    printed = capsys.readouterr().out
    assert printed.startswith("gst-launch-1.0 -e -m ")
    assert "vgacapttsrc" in printed and "link=ws://10.21.2.7:8765/serial" in printed
    assert "autovideosink" in printed and "multipartmux" in printed
    assert not outdir.exists(), "a dry run made a directory"


@needs_gstreamer
def test_dry_run_binds_no_port(tmp_path):
    # `--serve` on a dry run must not take the port: the whole point is that
    # nothing happens, and a port left bound would fail the next real run.
    port = free_port()
    assert main([
        "demo", "--link", "serial:/dev/null", "--clock-hz", "60000",
        "--outdir", str(tmp_path / "out"), "--serve", str(port), "--dry-run",
    ]) == 0
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", port))  # still free; raises if the demo took it


def test_an_unknown_board_fails_helpfully_from_the_command_line(tmp_path, capsys):
    code = main(["demo", "--board", "tt99", "--clock-hz", "60000",
                 "--outdir", str(tmp_path / "out"), "--dry-run"])
    assert code == 1
    assert "tt99" in capsys.readouterr().err


@needs_gstreamer
def test_a_whole_demo_writes_the_frames_its_own_bytes_hold(tmp_path):
    copy = tmp_path / "sent.vgacap"
    outdir = tmp_path / "out"
    proc = run_demo_process(
        demo_argv(outdir, fake=("--frames", "8", "--copy-to", str(copy)))
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr

    pngs = sorted(outdir.glob("frame-*.png"))
    expected = reference_frame_count(copy, tmp_path / "ref")
    assert expected > 0
    assert len(pngs) == expected, proc.stderr
    assert all(png.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n" for png in pngs)

    video = outdir / "capture.mkv"
    assert video.exists()
    assert video_duration_ns(video) > 0
    # The run says what it did, not just that it did something.
    assert "640x480@60" in proc.stderr
    assert "%d png(s)" % len(pngs) in proc.stderr


@needs_gstreamer
def test_no_png_leaves_only_the_video(tmp_path):
    outdir = tmp_path / "out"
    proc = run_demo_process(demo_argv(outdir, "--no-png"))
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert not list(outdir.glob("frame-*.png"))
    assert video_duration_ns(outdir / "capture.mkv") > 0


@needs_gstreamer
def test_fps_turns_the_slideshow_into_a_video(tmp_path):
    # At the RP2040's 60 kHz floor a 640x480 frame is 800x525 clocks, so
    # seven seconds each: the honest timing, and unwatchable. --fps repeats
    # the last frame to fill the gaps.
    slideshow, retimed = tmp_path / "slideshow", tmp_path / "retimed"
    assert run_demo_process(
        demo_argv(slideshow, "--no-png", fake=("--frames", "5"), clock_hz=60_000)
    ).returncode == 0
    assert video_framerate(slideshow / "capture.mkv") == "1/7"

    assert run_demo_process(
        demo_argv(retimed, "--no-png", "--fps", "10",
                  fake=("--frames", "5"), clock_hz=60_000)
    ).returncode == 0
    assert video_framerate(retimed / "capture.mkv") == "10/1"
    assert video_duration_ns(retimed / "capture.mkv") > 0


@needs_gstreamer
def test_serve_answers_the_index_and_streams_jpegs(tmp_path):
    port = free_port()
    outdir = tmp_path / "out"
    argv = demo_argv(
        outdir, "--no-video", "--serve", str(port),
        fake=("--frames", "40", "--chunk-delay", "0.02"),
    )
    child = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                             text=True, env=gst_env())
    try:
        index = _get_when_ready("http://127.0.0.1:%d/" % port)
        assert b'<img src="/stream.mjpg"' in index

        with urllib.request.urlopen(
            "http://127.0.0.1:%d/stream.mjpg" % port, timeout=30
        ) as stream:
            content_type = stream.headers["Content-Type"]
            assert content_type.startswith("multipart/x-mixed-replace")
            assert "boundary=%s" % demo_mod.MJPEG_BOUNDARY in content_type
            body = _read_two_parts(stream)
        separator = b"--%s\r\n" % demo_mod.MJPEG_BOUNDARY.encode()
        assert body.startswith(separator)
        assert body.count(separator) >= 2
        assert body.count(b"\xff\xd8\xff") >= 2  # two JPEG SOIs
    finally:
        child.send_signal(signal.SIGINT)
        _, err = child.communicate(timeout=60)
    # The run says how much it published, not just that it was serving.
    assert re.search(r"[1-9]\d* frame\(s\) published on port %d" % port, err), err


def _get_when_ready(url: str, timeout: float = 30.0) -> bytes:
    """GET `url`, waiting for the server to come up first.

    The pipeline is started before the port is listening and there is no
    other signal that it is; a poll is the whole of the synchronisation.
    """
    deadline = time.monotonic() + timeout
    last: Exception | None = None
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=5) as response:
                assert response.status == 200
                assert response.headers["Content-Type"].startswith("text/html")
                return response.read()
        except (urllib.error.URLError, ConnectionError, OSError) as exc:
            last = exc
            time.sleep(0.2)
    raise AssertionError("the demo never answered on %s: %s" % (url, last))


def _read_two_parts(stream, limit: int = 8 << 20) -> bytes:
    """Read until the third boundary, so two parts are certainly whole."""
    separator = b"--%s\r\n" % demo_mod.MJPEG_BOUNDARY.encode()
    body = b""
    while body.count(separator) < 3 and len(body) < limit:
        chunk = stream.read(65536)
        if not chunk:
            break
        body += chunk
    return body


@needs_gstreamer
def test_an_interrupted_run_leaves_a_playable_video(tmp_path):
    outdir = tmp_path / "out"
    argv = demo_argv(
        outdir, "--no-png", fake=("--frames", "0", "--chunk-delay", "0.02")
    )
    child = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                             text=True, env=gst_env())
    video = outdir / "capture.mkv"
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline and not video.exists():
        time.sleep(0.2)
    assert video.exists(), "the pipeline never started writing"
    time.sleep(3)  # let a few frames through, so there is something to finalise

    child.send_signal(signal.SIGINT)
    out, err = child.communicate(timeout=90)
    assert child.returncode == 0, out + err
    assert "interrupted" in err
    # The point of the test: a Matroska file whose muxer never finished has
    # no readable duration, and `gst-launch -e` is what turns the interrupt
    # into an end-of-stream that reaches it.
    assert video_duration_ns(video) > 0
