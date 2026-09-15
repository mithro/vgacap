# SPDX-License-Identifier: Apache-2.0
"""End-to-end tests for the `vgadecode` GStreamer element.

They drive the real command line tools -- `gst-inspect-1.0` and
`gst-launch-1.0` with `GST_PLUGIN_PATH` pointed at the build directory --
because that is how the element is used, and they check the decoded pixels
against `build/vgacap-frames`, the reference consumer of the same two
libraries. The whole module skips cleanly when GStreamer or the built plugin
is absent, so `uv run pytest` still passes on a machine without them.
"""
from __future__ import annotations

import gzip
import os
import pathlib
import re
import shutil
import subprocess

import numpy as np
import pytest
from PIL import Image

from vgacap.modes import MODES
from vgacap.ppm import read_ppm
from vgacap.stream import Header, Writer, TINYVGA_MAP
from vgacap.synth import frame_samples, grid, write_stream

SEEK_PROBE = pathlib.Path(__file__).resolve().parent / "seek_probe.py"
ROOT = pathlib.Path(__file__).resolve().parents[2]
BUILD = ROOT / "build"
PLUGIN = BUILD / "libgstvgacap.so"
FRAMES_TOOL = BUILD / "vgacap-frames"
HARDWARE_FIXTURE = ROOT / "tests" / "fixtures" / "tiny-logo-capture.vgacap.gz"

_MISSING = (
    shutil.which("gst-inspect-1.0") is None
    or shutil.which("gst-launch-1.0") is None
    or not PLUGIN.exists()
)
pytestmark = pytest.mark.skipif(
    _MISSING,
    reason="needs gst-inspect-1.0/gst-launch-1.0 and a built build/libgstvgacap.so "
           "(cmake -S . -B build && cmake --build build)",
)

TIMEOUT = 180  # generous; the point of the truncated-stream test is a bound, not a race


def gst_env() -> dict:
    env = dict(os.environ)
    env["GST_PLUGIN_PATH"] = str(BUILD)
    return env


def run(argv, timeout: int = TIMEOUT, **env_extra) -> subprocess.CompletedProcess:
    env = gst_env()
    env.update(env_extra)
    return subprocess.run([str(a) for a in argv], capture_output=True, text=True,
                          env=env, timeout=timeout)


def decode_with_gst(stream: pathlib.Path, outdir: pathlib.Path, *props: str,
                    timeout: int = TIMEOUT) -> tuple[subprocess.CompletedProcess, list[pathlib.Path]]:
    """`filesrc ! vgadecode ! pngenc ! multifilesink`, the pipeline the demo uses."""
    outdir.mkdir(parents=True, exist_ok=True)
    proc = run(["gst-launch-1.0", "-m", "filesrc", f"location={stream}", "!",
                "vgadecode", *props, "!", "pngenc", "!", "multifilesink",
                f"location={outdir}/frame-%04d.png"], timeout=timeout)
    return proc, sorted(outdir.glob("frame-*.png"))


def decode_with_tool(stream: pathlib.Path, prefix: pathlib.Path,
                     *args: str) -> tuple[str, list[pathlib.Path]]:
    """The same stream through `vgacap-frames`, the reference implementation."""
    prefix.parent.mkdir(parents=True, exist_ok=True)
    out = subprocess.run([str(FRAMES_TOOL), str(stream), str(prefix), *args],
                         capture_output=True, text=True, check=True).stdout
    return out, sorted(prefix.parent.glob(prefix.name + "-*.ppm"))


def assert_same_frames(stream: pathlib.Path, work: pathlib.Path) -> int:
    """Every PNG from the pipeline equals, pixel for pixel, the PPM the
    reference tool makes from the same stream."""
    _, pngs = decode_with_gst(stream, work / "png")
    tool_out, ppms = decode_with_tool(stream, work / "ppm" / "f")
    assert ppms, f"vgacap-frames produced nothing: {tool_out}"
    assert len(pngs) == len(ppms), f"{len(pngs)} PNGs vs {len(ppms)} PPMs\n{tool_out}"
    for png, ppm in zip(pngs, ppms):
        got = np.asarray(Image.open(png).convert("RGB"))
        np.testing.assert_array_equal(got, read_ppm(ppm), err_msg=f"{png} != {ppm}")
    return len(pngs)


def system_python_path() -> str | None:
    """`gi` (gst-python) is an OS package, so the seek probe needs the system
    interpreter, not the uv venv this test runs in - which is what a plain
    `which python3` would find."""
    override = os.environ.get("VGACAP_SYSTEM_PYTHON")
    if override:
        return override
    for candidate in ("/usr/bin/python3", "/usr/local/bin/python3"):
        if pathlib.Path(candidate).exists():
            return candidate
    return None


def _writer(fp, clock_hz: int = 0, desc: str = "synth") -> Writer:
    return Writer(fp, Header(sample_bits=8, samples_per_word=4, signal_map=TINYVGA_MAP,
                             mode=3, clock_hz=clock_hz, desc=desc))


def _write_frames(writer: Writer, samples, frames: int) -> None:
    for _ in range(frames):
        for start in range(0, len(samples), 65536):
            writer.raw(samples[start:start + 65536].tolist())


def write_clocked_stream(path: pathlib.Path, mode, image6, frames: int, clock_hz: int) -> None:
    """A synthetic stream that declares a project clock, so `vgadecode` times
    the frames from it rather than counting at output-fps."""
    with open(path, "wb") as fp:
        _write_frames(_writer(fp, clock_hz, f"synth {mode.name} clocked"),
                      frame_samples(mode, image6), frames)


def write_rate_change_stream(path: pathlib.Path, mode, image6, frames_before: int,
                             frames_after: int, hz_before: int, hz_after: int) -> None:
    """A stream whose header rate and a later TIME chunk disagree, as a device
    reporting a measured clock produces."""
    samples = frame_samples(mode, image6)
    with open(path, "wb") as fp:
        writer = _writer(fp, hz_before, f"synth {mode.name} rate change")
        _write_frames(writer, samples, frames_before)
        writer.time(0, hz_after, 0, "measured clock")
        _write_frames(writer, samples, frames_after)


def write_multi_mode_stream(path: pathlib.Path, specs, frames_each: int = 4) -> None:
    """One header, then several modes back to back: the element has to
    renegotiate its caps as the detected size changes."""
    with open(path, "wb") as fp:
        writer = _writer(fp, desc="synth multi mode")
        for mode, image6 in specs:
            _write_frames(writer, frame_samples(mode, image6), frames_each)


# ------------------------------------------------------------------ fixtures

@pytest.fixture(scope="module")
def synth_stream(tmp_path_factory) -> pathlib.Path:
    mode = MODES["640x480@60"]
    path = tmp_path_factory.mktemp("synth") / "synth.vgacap"
    write_stream(path, mode, grid(mode.h_active, mode.v_active), frames=4)
    return path


@pytest.fixture(scope="module")
def hardware_stream(tmp_path_factory) -> pathlib.Path:
    if not HARDWARE_FIXTURE.exists():
        pytest.skip(f"missing hardware fixture {HARDWARE_FIXTURE}")
    path = tmp_path_factory.mktemp("hardware") / "tiny-logo-capture.vgacap"
    with gzip.open(HARDWARE_FIXTURE, "rb") as src, open(path, "wb") as dst:
        shutil.copyfileobj(src, dst)
    return path


# --------------------------------------------------------------------- tests

def test_inspect_lists_the_element_and_its_properties():
    proc = run(["gst-inspect-1.0", "vgadecode"])
    assert proc.returncode == 0, proc.stderr
    assert "vgacap stream decoder" in proc.stdout
    assert "application/x-vgacap" in proc.stdout
    assert "video/x-raw" in proc.stdout
    for prop in ("repeat-last-frame", "output-fps", "max-width", "max-height",
                 "force-mode", "partial"):
        assert re.search(rf"^\s+{re.escape(prop)}\s+:", proc.stdout, re.M), \
            f"{prop} missing from gst-inspect output"
    # The defaults the plan pins down.
    assert re.search(r"output-fps.*\n.*\n.*Default: 30/1", proc.stdout)
    assert re.search(r"max-width.*\n(?:.*\n)*?.*Default: 1400", proc.stdout)
    assert re.search(r"max-height.*\n(?:.*\n)*?.*Default: 900", proc.stdout)


def test_synthetic_stream_matches_vgacap_frames(tmp_path, synth_stream):
    assert assert_same_frames(synth_stream, tmp_path) > 0


@pytest.mark.parametrize("chunk", ["rle", "fram"])
def test_other_chunk_encodings_match_vgacap_frames(tmp_path, chunk):
    mode = MODES["800x600@60"]
    stream = tmp_path / f"{chunk}.vgacap"
    write_stream(stream, mode, grid(mode.h_active, mode.v_active), frames=4, chunk=chunk)
    assert assert_same_frames(stream, tmp_path) > 0


def test_hardware_capture_matches_vgacap_frames(tmp_path, hardware_stream):
    assert assert_same_frames(hardware_stream, tmp_path) > 0


def test_timing_is_reported_on_the_bus(tmp_path, synth_stream):
    proc, _ = decode_with_gst(synth_stream, tmp_path / "png")
    assert proc.returncode == 0, proc.stderr
    line = next((l for l in proc.stdout.splitlines() if "vgacap-timing" in l), None)
    assert line, "no vgacap-timing element message on the bus"
    assert "640x480" in line and "clocks-per-line=(uint)800" in line
    assert "lines-per-frame=(uint)525" in line and "glitches=(uint)" in line
    assert "hsync-positive=(boolean)false" in line
    # One message for one unchanging timing, not one per frame.
    assert sum("vgacap-timing" in l for l in proc.stdout.splitlines()) == 1


_BUFFER_RE = re.compile(
    r"pts:\s*(\S+?),\s*duration:\s*(\S+?),\s*offset:\s*(\d+),\s*offset_end:\s*\d+,"
    r"\s*flags:\s*([0-9a-f]+)")
_TIME_RE = re.compile(r"^(\d+):(\d\d):(\d\d)\.(\d{9})$")


def _ns(text: str) -> int | None:
    """GStreamer's H:MM:SS.nnnnnnnnn, exactly, without going through a float."""
    match = _TIME_RE.match(text)
    if not match:
        return None  # "none" / "99:99:99.999999999"
    hours, minutes, seconds, frac = match.groups()
    return (int(hours) * 3600 + int(minutes) * 60 + int(seconds)) * 10**9 + int(frac)


def identity_buffers(stream: pathlib.Path, *props: str) -> list[dict]:
    """pts/duration/offset/flags of each buffer, as `identity silent=false`
    reports it (GStreamer routes that report through the debug log, hence
    GST_DEBUG)."""
    proc = run(["gst-launch-1.0", "filesrc", f"location={stream}", "!", "vgadecode", *props,
                "!", "identity", "silent=false", "!", "fakesink"], GST_DEBUG="identity:7")
    assert proc.returncode == 0, proc.stderr
    text = proc.stdout + proc.stderr
    found = _BUFFER_RE.findall(text)
    assert found, f"identity printed no buffers:\n{text[-2000:]}"
    return [{"pts": _ns(pts), "dur": _ns(dur), "offset": int(off), "flags": int(flags, 16)}
            for pts, dur, off, flags in found]


def test_buffer_offset_carries_the_frame_counter(synth_stream):
    offsets = [b["offset"] for b in identity_buffers(synth_stream)]
    assert offsets == sorted(offsets) and len(set(offsets)) == len(offsets)


def test_partial_frames_are_flagged_corrupted(synth_stream):
    GST_BUFFER_FLAG_CORRUPTED = 0x100  # GST_MINI_OBJECT_FLAG_LAST << 4
    complete = identity_buffers(synth_stream)
    with_partial = identity_buffers(synth_stream, "partial=true")
    assert len(with_partial) > len(complete), "partial=true pushed no extra frames"
    assert not any(b["flags"] & GST_BUFFER_FLAG_CORRUPTED for b in complete)
    assert any(b["flags"] & GST_BUFFER_FLAG_CORRUPTED for b in with_partial)


def test_pts_is_monotonic_across_a_clock_rate_change(tmp_path):
    # A device that measures its own clock reports it in a TIME chunk, and the
    # measured value need not match the header's nominal one. Rescaling the
    # whole elapsed time by the new rate sent PTS backwards (84 ms became
    # 16.8 ms); the timeline base has to be frozen at the change instead.
    mode = MODES["640x480@60"]
    stream = tmp_path / "rate-change.vgacap"
    write_rate_change_stream(stream, mode, grid(mode.h_active, mode.v_active),
                             frames_before=4, frames_after=4,
                             hz_before=5_000_000, hz_after=25_175_000)
    buffers = identity_buffers(stream)
    pts = [b["pts"] for b in buffers]
    assert all(p is not None for p in pts), buffers
    assert pts == sorted(pts), f"PTS goes backwards: {pts}"
    # The rate change really happened: 420000 clocks a frame is 84 ms at
    # 5 MHz and 16.68 ms at 25.175 MHz.
    durations = {b["dur"] for b in buffers}
    assert len(durations) >= 2, f"the TIME chunk changed nothing: {durations}"
    assert 84_000_000 in durations and 16_683_217 in durations, durations


def test_mid_stream_caps_renegotiation(tmp_path):
    mode_a, mode_b = MODES["640x480@60"], MODES["800x600@60"]
    specs = [(mode_a, grid(mode_a.h_active, mode_a.v_active)),
             (mode_b, grid(mode_b.h_active, mode_b.v_active)),
             (mode_a, grid(mode_a.h_active, mode_a.v_active))]
    stream = tmp_path / "modes.vgacap"
    write_multi_mode_stream(stream, specs, frames_each=4)

    count = assert_same_frames(stream, tmp_path)
    assert count >= 6, count
    proc, pngs = decode_with_gst(stream, tmp_path / "png2")
    sizes = [Image.open(png).size for png in pngs]
    # 640x480, then 800x600, then 640x480 again: two renegotiations.
    transitions = [b for a, b in zip(sizes, sizes[1:]) if a != b]
    assert transitions == [(800, 600), (640, 480)], sizes
    assert sum("vgacap-timing" in line for line in proc.stdout.splitlines()) == 3


def test_flushing_seek_recovers_mid_stream(tmp_path):
    # A seek lands far from the VGCH header, so after FLUSH_STOP the element
    # has to re-establish it itself or decode nothing ever again.
    system_python = system_python_path()
    if system_python is None:
        pytest.skip("no system python3 to run the gst-python seek probe with")
    mode = MODES["640x480@60"]
    stream = tmp_path / "seek.vgacap"
    write_stream(stream, mode, grid(mode.h_active, mode.v_active), frames=8)

    proc = run([system_python, SEEK_PROBE, stream, stream.stat().st_size // 3], timeout=120)
    if proc.returncode != 0 and "No module named 'gi'" in proc.stderr:
        pytest.skip("gst-python (gi) is not importable by the system python3")
    assert proc.returncode == 0, proc.stderr
    counts = {k: int(v) for k, v in (kv.split("=") for kv in proc.stdout.split())}
    assert counts["before"] > 0, counts
    assert counts["after_zero"] == counts["before"], counts
    assert counts["after_mid"] > 0, f"the element went mute after a mid-stream flush: {counts}"


def test_repeat_last_frame_yields_at_least_as_many_buffers(tmp_path):
    # A project clock of 4.2 MHz over 800x525 clocks is 10 frames a second, so
    # at the default output-fps of 30 each decoded frame is followed by two
    # repeats and the pipeline must emit strictly more buffers than frames.
    mode = MODES["640x480@60"]
    stream = tmp_path / "clocked.vgacap"
    write_clocked_stream(stream, mode, grid(mode.h_active, mode.v_active), 4, 4_200_000)

    _, plain = decode_with_gst(stream, tmp_path / "plain")
    _, repeated = decode_with_gst(stream, tmp_path / "repeat", "repeat-last-frame=true")
    assert plain, "the clocked stream decoded to nothing"
    assert len(repeated) >= len(plain)
    assert len(repeated) > len(plain), "repeat-last-frame inserted no repeats"
    # A repeat is the previous frame again, byte for byte.
    first = np.asarray(Image.open(repeated[0]).convert("RGB"))
    np.testing.assert_array_equal(np.asarray(Image.open(repeated[1]).convert("RGB")), first)


@pytest.mark.parametrize("keep", [8, 512, 700_000])
def test_truncated_stream_does_not_hang(tmp_path, synth_stream, keep):
    truncated = tmp_path / f"cut-{keep}.vgacap"
    truncated.write_bytes(synth_stream.read_bytes()[:keep])
    try:
        proc, _ = decode_with_gst(truncated, tmp_path / f"png-{keep}", timeout=60)
    except subprocess.TimeoutExpired:  # pragma: no cover - the failure we are testing for
        pytest.fail(f"the pipeline hung on a stream truncated to {keep} bytes")
    # Either clean EOS or a reported error, but always a decision.
    assert proc.returncode is not None
    assert "Segmentation fault" not in proc.stderr and proc.returncode >= 0, proc.stderr


def test_force_mode_rejects_an_unknown_name(tmp_path, synth_stream):
    proc, pngs = decode_with_gst(synth_stream, tmp_path / "png", "force-mode=nope", timeout=60)
    assert proc.returncode != 0
    assert "force-mode" in proc.stdout + proc.stderr
    assert not pngs


def test_force_mode_accepts_a_table_name(tmp_path, synth_stream):
    _, pngs = decode_with_gst(synth_stream, tmp_path / "png", "force-mode=640x480@60")
    assert pngs
    got = np.asarray(Image.open(pngs[-1]).convert("RGB"))
    assert got.shape == (480, 640, 3)
