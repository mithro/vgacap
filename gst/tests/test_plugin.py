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


def write_clocked_stream(path: pathlib.Path, mode, image6, frames: int, clock_hz: int) -> None:
    """A synthetic stream that declares a project clock, so `vgadecode` times
    the frames from it rather than counting at output-fps."""
    samples = frame_samples(mode, image6)
    with open(path, "wb") as fp:
        writer = Writer(fp, Header(sample_bits=8, samples_per_word=4, signal_map=TINYVGA_MAP,
                                   mode=3, clock_hz=clock_hz, desc=f"synth {mode.name} clocked"))
        for _ in range(frames):
            for start in range(0, len(samples), 65536):
                writer.raw(samples[start:start + 65536].tolist())


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


def identity_buffers(stream: pathlib.Path, *props: str) -> list[tuple[int, int]]:
    """(offset, flags) of each buffer, as `identity silent=false` reports it
    (GStreamer routes that report through the debug log, hence GST_DEBUG)."""
    proc = run(["gst-launch-1.0", "filesrc", f"location={stream}", "!", "vgadecode", *props,
                "!", "identity", "silent=false", "!", "fakesink"], GST_DEBUG="identity:7")
    assert proc.returncode == 0, proc.stderr
    text = proc.stdout + proc.stderr
    found = re.findall(r"offset:\s*(\d+),\s*offset_end:\s*\d+,\s*flags:\s*([0-9a-f]+)", text)
    assert found, f"identity printed no buffers:\n{text[-2000:]}"
    return [(int(off), int(flags, 16)) for off, flags in found]


def test_buffer_offset_carries_the_frame_counter(synth_stream):
    offsets = [off for off, _ in identity_buffers(synth_stream)]
    assert offsets == sorted(offsets) and len(set(offsets)) == len(offsets)


def test_partial_frames_are_flagged_corrupted(synth_stream):
    GST_BUFFER_FLAG_CORRUPTED = 0x100  # GST_MINI_OBJECT_FLAG_LAST << 4
    complete = identity_buffers(synth_stream)
    with_partial = identity_buffers(synth_stream, "partial=true")
    assert len(with_partial) > len(complete), "partial=true pushed no extra frames"
    assert not any(flags & GST_BUFFER_FLAG_CORRUPTED for _, flags in complete)
    assert any(flags & GST_BUFFER_FLAG_CORRUPTED for _, flags in with_partial)


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
