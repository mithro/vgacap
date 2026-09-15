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

import argparse
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
    assert "ssh -N -L 18007:10.21.2.7:8765 tweed.welland.mithis.com" in hint
    assert "ws://127.0.0.1:18007/serial" in hint


@pytest.mark.parametrize("slug", sorted(WELLAND))
def test_no_tunnel_hint_asks_for_a_privileged_port(slug):
    # Every Welland octet is 3-8 or 33-36, so using it as the local port made
    # every board's headline command one ssh refuses without root
    # ("Privileged ports can only be forwarded by root").
    port = demo_mod.tunnel_port(slug)
    assert port > 1024
    hint = demo_mod.tunnel_hint(slug)
    assert "-L %d:" % port in hint
    assert "127.0.0.1:%d/serial" % port in hint


def test_every_board_gets_its_own_tunnel_port():
    # Two boards tunnelled at once must not want the same local port.
    ports = [demo_mod.tunnel_port(slug) for slug in WELLAND]
    assert len(set(ports)) == len(ports)


# --------------------------------------------------- what a board can take


def test_a_design_on_an_asic_shuttle_is_refused():
    with pytest.raises(CaptureError) as exc:
        demo_mod.check_board_wants("tt07", None, "some_bitstream")
    message = str(exc.value)
    assert "--design" in message and "--project" in message
    assert "tt07" in message and "fpga-1" in message


def test_a_project_on_an_fpga_board_is_refused():
    with pytest.raises(CaptureError) as exc:
        demo_mod.check_board_wants("fpga-1", "tt_um_x", None)
    message = str(exc.value)
    assert "--project" in message and "--design" in message
    assert "fpga-1" in message and "tt07" in message


@pytest.mark.parametrize("slug", sorted(WELLAND))
def test_each_board_accepts_exactly_one_of_them(slug):
    fpga = slug.startswith("fpga-")
    demo_mod.check_board_wants(slug, None, "d" if fpga else None)
    demo_mod.check_board_wants(slug, None if fpga else "p", None)


def test_nothing_is_assumed_about_a_board_reached_by_link():
    # With --link there is no telling what is on the other end, and refusing
    # a good run would be worse than letting the board say so.
    demo_mod.check_board_wants(None, "tt_um_x", None)
    demo_mod.check_board_wants(None, None, "bitstream")


# ------------------------------------------------------------- the numbers


def numbers(**overrides) -> argparse.Namespace:
    values = dict(board=None, seconds=10.0, clock_hz=60000, buf_words=None,
                  serve=None)
    values.update(overrides)
    return argparse.Namespace(**values)


@pytest.mark.parametrize(
    "field, value",
    [
        ("seconds", -5.0), ("seconds", -0.001), ("seconds", 86400.5),
        ("clock_hz", 0), ("clock_hz", -1), ("clock_hz", 200_000_001),
        ("buf_words", 0), ("buf_words", -1), ("buf_words", (1 << 24) + 1),
        ("serve", 0), ("serve", -1), ("serve", 65536), ("serve", 70000),
    ],
)
def test_a_number_the_pipeline_would_ignore_is_refused(field, value):
    """The defect: GObject refuses an out-of-range property with a CRITICAL
    on stderr and then *ignores* it, so `--seconds -5` left `seconds` at its
    default of 0 -- and 0 means "capture until stopped". A typo started an
    unbounded capture on a shared bench board."""
    with pytest.raises(CaptureError) as exc:
        demo_mod.check_numbers(numbers(**{field: value}))
    message = str(exc.value)
    flag = "--" + field.replace("_", "-")
    assert flag in message
    low, high = demo_mod.NUMBER_RANGES[field][:2]
    # The accepted range, in full, in figures a person can retype.
    assert demo_mod._number(low) in message
    assert demo_mod._number(high) in message
    assert "e+" not in message


@pytest.mark.parametrize(
    "field, value",
    [
        ("seconds", 0.0), ("seconds", 0.125), ("seconds", 86400.0),
        ("clock_hz", 1), ("clock_hz", 200_000_000),
        ("buf_words", 1), ("buf_words", 4096), ("buf_words", 1 << 24),
        ("serve", 1), ("serve", 8080), ("serve", 65535),
    ],
)
def test_the_edges_of_each_range_are_accepted(field, value):
    assert demo_mod.check_numbers(numbers(**{field: value})) == []


def test_seconds_zero_still_means_until_stopped():
    # The one value that is both suspicious-looking and documented: it is
    # what `--serve`/Ctrl-C runs use, and it must survive the range check
    # and reach the element unchanged.
    assert demo_mod.check_numbers(numbers(seconds=0.0)) == []
    argv, _ = demo_mod.build_pipeline(
        "serial:/dev/null", pathlib.Path("/out"), clock_hz=60000, seconds=0.0,
        png=False, video=False, window=True,
    )
    assert "seconds=0" in argv


@pytest.mark.parametrize(
    "value, printed",
    [(0.0, "0"), (10.0, "10"), (2.5, "2.5"), (0.125, "0.125"),
     (86400.0, "86400"), (1234567.0, "1234567")],
)
def test_a_duration_prints_as_a_number_a_person_would_type(value, printed):
    # `%g` turned anything above six significant figures into `1.23457e+06`,
    # which is lossy and is not what the pipeline would read back.
    assert demo_mod._number(value) == printed


def test_a_clock_above_the_boards_measured_ceiling_warns_but_is_allowed():
    # Watching the board overrun is a legitimate thing to want -- it is how
    # the ceilings were measured -- so this is a warning, not an error.
    warnings = demo_mod.check_numbers(numbers(board="tt07", clock_hz=1_000_000))
    assert len(warnings) == 1
    assert "--clock-hz 1000000" in warnings[0]
    assert "60000" in warnings[0]  # the RP2040 ceiling


def test_each_board_family_is_warned_at_its_own_ceiling():
    assert demo_mod.clock_ceiling("tt07")[0] == 60_000      # RP2040 demo board
    assert demo_mod.clock_ceiling("fpga-1")[0] == 750_000   # RP2350
    # 700 kHz is over one ceiling and under the other.
    assert demo_mod.check_numbers(numbers(board="tt07", clock_hz=700_000))
    assert demo_mod.check_numbers(numbers(board="fpga-1", clock_hz=700_000)) == []


@pytest.mark.parametrize("slug", sorted(WELLAND))
def test_every_board_has_a_ceiling_to_warn_about(slug):
    # `CLOCK_CEILINGS.get()` fails open, so a profile added to WELLAND later
    # would silently get no warning at all. This is what notices.
    measured = demo_mod.clock_ceiling(slug)
    assert measured is not None, "no measured ceiling for %s" % slug
    ceiling, board_name = measured
    assert ceiling > 0 and board_name


def test_the_ceiling_warning_names_the_demo_board_not_the_slug():
    # Only tt07 and fpga-1 were on the bench; the rest inherit the number by
    # sharing a profile. "a tt03p5 board has been measured" claimed an
    # experiment that never happened.
    warning = demo_mod.check_numbers(numbers(board="tt03p5", clock_hz=25_000_000))[0]
    assert "an RP2040 demo board" in warning
    assert "tt03p5 board has been measured" not in warning
    # And the ceiling is not a hard limit: --buf-words moves it.
    assert "default buffer size" in warning and "--buf-words" in warning


def test_no_ceiling_is_guessed_for_a_board_reached_by_link():
    # Behind a --link there is no telling which demo board is there, and a
    # warning about the wrong ceiling is worse than none.
    assert demo_mod.clock_ceiling(None) is None
    assert demo_mod.check_numbers(numbers(board=None, clock_hz=200_000_000)) == []


def test_an_empty_fps_is_refused_rather_than_ignored():
    # `--fps ''` used to be dropped by a truthiness test -- the same
    # "ignored rather than refused" that made a bad --seconds dangerous.
    with pytest.raises(CaptureError) as exc:
        demo_mod.parse_fps("")
    assert "--fps" in str(exc.value)


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


def alive(pid: int) -> bool:
    """Is `pid` still a live process?

    An orphan is reparented to init and reaped there, so it disappears
    outright rather than lingering as a zombie -- `ProcessLookupError` is a
    reliable "gone".
    """
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:  # pragma: no cover - alive, and not ours
        return True
    return True


def wait_for(predicate, timeout: float = 30.0, step: float = 0.1) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(step)
    return False


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


def test_every_signal_a_person_can_send_on_purpose_is_handled():
    # SIGQUIT (Ctrl-\) was missing, and it is default-fatal like the rest, so
    # it orphaned the capture exactly as SIGTERM used to. SIGKILL is the only
    # one that cannot be caught.
    assert set(demo_mod.STOP_SIGNALS) == {"SIGINT", "SIGTERM", "SIGHUP", "SIGQUIT"}
    for name in demo_mod.STOP_SIGNALS:
        assert hasattr(signal, name)


@needs_gstreamer
@pytest.mark.parametrize(
    "signum",
    [signal.SIGTERM, signal.SIGHUP, signal.SIGQUIT],
    ids=["SIGTERM", "SIGHUP", "SIGQUIT"],
)
def test_a_demo_killed_by_anything_does_not_leave_the_board_captured(tmp_path, signum):
    """The worst failure this command had: a capture nobody can stop.

    The pipeline runs in a session of its own so it hears only what this
    process forwards -- which used to be SIGINT and nothing else, so a
    `kill`, a closed terminal or an OOM kill left `ttcap` holding a shared
    bench board with no terminal left to own it.

    `--no-png` on purpose: with the PNG branch, `multifilesink
    post-messages=true` makes the child write a bus line per frame, so it
    takes SIGPIPE the moment the demo dies. That is luck, not a shutdown
    path, and it hid this.
    """
    pid_file = tmp_path / "ttcap.pid"
    child = subprocess.Popen(
        demo_argv(
            tmp_path / "out", "--no-png",
            fake=("--frames", "0", "--chunk-delay", "0.02",
                  "--pid-file", str(pid_file)),
        ),
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=gst_env(),
    )
    try:
        assert wait_for(pid_file.exists), "the fake ttcap never started"
        capture_pid = int(pid_file.read_text())
        assert wait_for(lambda: alive(capture_pid), timeout=10)

        child.send_signal(signum)
        child.communicate(timeout=90)
        assert wait_for(lambda: not alive(capture_pid)), (
            "the capture outlived the demo: pid %d is still holding the board"
            % capture_pid
        )
    finally:
        if child.poll() is None:  # pragma: no cover - only on a failure
            child.kill()
            child.communicate(timeout=30)


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
    assert "SIGINT: asking the pipeline to end the stream" in err
    # The point of the test: a Matroska file whose muxer never finished has
    # no readable duration, and `gst-launch -e` is what turns the interrupt
    # into an end-of-stream that reaches it.
    assert video_duration_ns(video) > 0


# --------------------------------------------------------------- the outdir


def test_an_outdir_holding_a_previous_run_is_refused(tmp_path):
    outdir = tmp_path / "out"
    outdir.mkdir()
    (outdir / "frame-0000.png").write_bytes(b"old")
    (outdir / "capture.mkv").write_bytes(b"old")
    with pytest.raises(CaptureError) as exc:
        demo_mod.prepare_outdir(outdir)
    message = str(exc.value)
    assert "--force" in message and "frame-0000.png" in message
    # Refused means refused: nothing was touched.
    assert (outdir / "frame-0000.png").read_bytes() == b"old"


def test_force_clears_the_previous_run_but_nothing_else(tmp_path):
    outdir = tmp_path / "out"
    outdir.mkdir()
    (outdir / "frame-0000.png").write_bytes(b"old")
    (outdir / "capture.mkv").write_bytes(b"old")
    (outdir / "notes.md").write_text("mine")
    demo_mod.prepare_outdir(outdir, force=True)
    assert not list(outdir.glob("frame-*.png"))
    assert not (outdir / "capture.mkv").exists()
    # Only this command's own outputs go; the rest is not ours to delete.
    assert (outdir / "notes.md").read_text() == "mine"


def test_a_fresh_outdir_is_made(tmp_path):
    outdir = tmp_path / "deep" / "out"
    demo_mod.prepare_outdir(outdir)
    assert outdir.is_dir()


def test_an_outdir_that_is_a_file_says_so(tmp_path):
    path = tmp_path / "afile"
    path.write_text("not a directory")
    with pytest.raises(CaptureError) as exc:
        demo_mod.prepare_outdir(path)
    assert "--outdir" in str(exc.value)


def test_an_outdir_that_is_a_broken_symlink_says_so(tmp_path):
    # `exists()` follows the link and answers False, so this reached `mkdir`
    # and came back out as a raw FileExistsError -- the class of message
    # this function exists to replace.
    path = tmp_path / "dangling"
    path.symlink_to(tmp_path / "nowhere")
    with pytest.raises(CaptureError) as exc:
        demo_mod.prepare_outdir(path)
    assert "--outdir" in str(exc.value) and "not a directory" in str(exc.value)


def test_a_directory_where_a_frame_would_go_says_so(tmp_path):
    # `unlink` on a directory raises IsADirectoryError. It is contrived, and
    # it is still not a raw errno that reaches the user.
    outdir = tmp_path / "out"
    (outdir / "frame-0000.png").mkdir(parents=True)
    with pytest.raises(CaptureError) as exc:
        demo_mod.prepare_outdir(outdir, force=True)
    message = str(exc.value)
    assert "frame-0000.png" in message and "directory" in message
    assert "IsADirectoryError" not in message
    assert (outdir / "frame-0000.png").is_dir()  # and it is still there


def test_an_unwritable_outdir_is_a_sentence(tmp_path):
    if os.geteuid() == 0:  # pragma: no cover - root ignores the mode
        pytest.skip("running as root, so an unwritable directory is not")
    parent = tmp_path / "ro"
    parent.mkdir(mode=0o500)
    try:
        with pytest.raises(CaptureError) as exc:
            demo_mod.prepare_outdir(parent / "sub")
        assert "--outdir" in str(exc.value)
        assert "PermissionError" not in str(exc.value)
    finally:
        parent.chmod(0o700)


@needs_gstreamer
def test_a_second_run_into_the_same_outdir_refuses_rather_than_blending(tmp_path):
    """The lie this used to tell.

    A short capture into a directory holding a long one overwrote the first
    frames and left the rest, and the summary counted every `frame-*.png` it
    found -- so a run that wrote nothing reported the previous run's six.
    """
    outdir = tmp_path / "out"
    first = run_demo_process(demo_argv(outdir, "--no-video", fake=("--frames", "8")))
    assert first.returncode == 0, first.stdout + first.stderr
    kept = {p.name: p.read_bytes() for p in outdir.glob("frame-*.png")}
    assert kept

    second = run_demo_process(demo_argv(outdir, "--no-video", fake=("--frames", "3")))
    assert second.returncode != 0
    assert "--force" in second.stderr
    assert {p.name: p.read_bytes() for p in outdir.glob("frame-*.png")} == kept

    forced = run_demo_process(
        demo_argv(outdir, "--no-video", "--force", fake=("--frames", "3"))
    )
    assert forced.returncode == 0, forced.stdout + forced.stderr
    assert "removed %d file(s)" % len(kept) in forced.stderr
    now = sorted(outdir.glob("frame-*.png"))
    # The shorter run, alone -- not blended with the longer one underneath.
    assert 0 < len(now) < len(kept)
    assert "%d png(s)" % len(now) in forced.stderr


@needs_gstreamer
def test_a_run_that_captures_nothing_does_not_report_success(tmp_path):
    # `--frames 1` is too little for vgadecode to close a frame, so the
    # pipeline runs happily and produces no picture at all. Exit 0 there is
    # the one lie this command must not tell.
    outdir = tmp_path / "out"
    proc = run_demo_process(
        demo_argv(outdir, "--no-video", fake=("--frames", "1"))
    )
    assert not list(outdir.glob("frame-*.png"))
    assert proc.returncode == demo_mod.EXIT_NOTHING_CAPTURED
    assert "nothing was captured" in proc.stderr


# ----------------------------------------------------------- the verdict


def test_an_output_that_exists_settles_it():
    assert not demo_mod.Outcome(made=True, conclusive=True).nothing_captured
    assert not demo_mod.Outcome(made=True, conclusive=False).nothing_captured


def test_an_empty_file_output_is_conclusive():
    # PNGs and the video are countable and complete: if one was asked for
    # and nothing came out, nothing was captured, whatever the bus said.
    assert demo_mod.Outcome(conclusive=True, saw_frames=True).nothing_captured
    assert demo_mod.Outcome(conclusive=True, saw_frames=False).nothing_captured


def test_a_run_with_nothing_countable_is_judged_on_the_bus():
    """`--window` has no counter at all, and `--serve`'s count is one part
    behind by construction. Reading either zero as "nothing was captured"
    made a working window-only run exit 3 every single time."""
    assert not demo_mod.Outcome(conclusive=False, saw_frames=True).nothing_captured
    assert demo_mod.Outcome(conclusive=False, saw_frames=False).nothing_captured


@needs_gstreamer
@pytest.mark.parametrize(
    "extra, frames",
    [
        (("--no-png", "--no-video", "--window"), "8"),
        (("--no-png", "--no-video", "--serve", "0"), "2"),
        (("--no-png", "--no-video", "--serve", "0"), "8"),
    ],
    ids=["window-only", "serve-only-short", "serve-only-long"],
)
def test_a_run_with_no_file_output_does_not_claim_it_captured_nothing(
    tmp_path, extra, frames
):
    """Exit 3 is reserved for "the sampler never saw a clock edge". A window
    that worked, or a capture too short to close an MJPEG part, is not that
    -- and both used to land on it."""
    argv = demo_argv(tmp_path / "out", *extra, fake=("--frames", frames))
    if "--serve" in extra:  # a real port, chosen late so it is still free
        argv[argv.index("0", argv.index("--serve"))] = str(free_port())
    proc = run_demo_process(argv)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "nothing was captured" not in proc.stderr


@needs_gstreamer
def test_a_window_only_run_still_says_what_it_did(tmp_path):
    proc = run_demo_process(
        demo_argv(tmp_path / "out", "--no-png", "--no-video", "--window",
                  fake=("--frames", "8"))
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "a window (autovideosink)" in proc.stderr
    assert "640x480@60" in proc.stderr


@needs_gstreamer
def test_sync_without_a_closed_frame_says_which_of_the_two_it_was(tmp_path):
    # Two frames of samples is enough to learn the timing and not enough to
    # close a frame, so the diagnosis is "capture for longer", not "check
    # the Pmod". Both are exit 3; they send you to different places.
    proc = run_demo_process(
        demo_argv(tmp_path / "out", "--no-video", fake=("--frames", "2"))
    )
    assert proc.returncode == demo_mod.EXIT_NOTHING_CAPTURED
    assert "sync was detected but no complete frame closed" in proc.stderr
    assert "three frame periods" in proc.stderr


# ------------------------------------------------- the plugin and the port


def test_a_missing_plugin_is_named_with_what_to_do_about_it(monkeypatch):
    monkeypatch.setattr(demo_mod, "have_element", lambda name: False)
    monkeypatch.setattr(demo_mod.shutil, "which", lambda name: "/usr/bin/" + name)
    monkeypatch.setenv("GST_PLUGIN_PATH", "/somewhere/else")
    with pytest.raises(CaptureError) as exc:
        demo_mod.check_gstreamer()
    message = str(exc.value)
    # All of: which element, what it is, how to build it, how to be found,
    # and where it looked.
    assert "vgacapttsrc" in message
    assert "libgstvgacap.so" in message
    assert "cmake --build build" in message
    assert "GST_PLUGIN_PATH" in message
    assert "/somewhere/else" in message


def test_a_dry_run_warns_about_a_missing_plugin_but_still_prints(monkeypatch, capsys):
    # Printing a pipeline to run on the Pi, from a workstation with no
    # plugin, is a fair thing to ask for -- a dry run cannot fail on a
    # missing element because it runs nothing.
    monkeypatch.setattr(demo_mod, "have_element", lambda name: False)
    monkeypatch.setattr(demo_mod.shutil, "which", lambda name: "/usr/bin/" + name)
    demo_mod.check_gstreamer(required=False)
    assert "warning:" in capsys.readouterr().err


def test_a_missing_gst_launch_is_named(monkeypatch):
    monkeypatch.setattr(demo_mod.shutil, "which", lambda name: None)
    with pytest.raises(CaptureError) as exc:
        demo_mod.check_gstreamer()
    assert "gst-launch-1.0" in str(exc.value)


@needs_gstreamer
def test_a_taken_serve_port_names_the_flag_and_the_port(tmp_path):
    with socket.socket() as held:
        held.bind(("127.0.0.1", 0))
        held.listen(1)
        port = held.getsockname()[1]
        proc = run_demo_process(
            demo_argv(tmp_path / "out", "--serve", str(port), fake=("--frames", "1"))
        )
    assert proc.returncode != 0
    assert "--serve %d" % port in proc.stderr
    assert "in use" in proc.stderr.lower()
    assert "OSError" not in proc.stderr


@needs_gstreamer
def test_a_privileged_serve_port_says_why(tmp_path):
    if os.geteuid() == 0:  # pragma: no cover - root can bind port 80
        pytest.skip("running as root, so a privileged port is not refused")
    proc = run_demo_process(
        demo_argv(tmp_path / "out", "--serve", "80", fake=("--frames", "1"))
    )
    assert proc.returncode != 0
    assert "--serve 80" in proc.stderr
    assert "below 1024" in proc.stderr


@pytest.mark.parametrize(
    "flag, value",
    [("--seconds", "-5"), ("--clock-hz", "0"), ("--buf-words", "0"),
     ("--serve", "70000"), ("--fps", "")],
)
def test_a_bad_number_stops_the_run_before_the_board(tmp_path, capsys, flag, value):
    """No board, no outdir, no GStreamer needed: the number is wrong and
    that is answerable on its own."""
    outdir = tmp_path / "never-made"
    argv = ["demo", "--board", "tt07", "--clock-hz", "60000",
            "--outdir", str(outdir), "--dry-run", flag, value]
    assert main(argv) == 1
    captured = capsys.readouterr()
    assert flag in captured.err
    assert not outdir.exists()
    assert "gst-launch" not in captured.out  # no pipeline was printed


@needs_gstreamer
def test_project_and_design_together_is_a_usage_error(tmp_path):
    proc = run_demo_process(
        demo_argv(tmp_path / "out", "--project", "p", "--design", "d", "--dry-run")
    )
    assert proc.returncode == 2  # argparse's own
    assert "not allowed with argument --project" in proc.stderr
