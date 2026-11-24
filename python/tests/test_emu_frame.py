# SPDX-License-Identifier: Apache-2.0
"""Whole-frame and windowed capture (`frame_rp2.py`), run on the emulator.

The script under test is exactly what `ttcap capture --mode frame` ships:
`mp.build(frame_cfg(...), "frame_rp2.py")`. It runs on `ttcap.emu.Board`
against a VGA waveform at the project's real clock, and its stream is
reconstructed by `vgacap-frames` and compared pixel for pixel.
"""

from __future__ import annotations

import pathlib
import struct
import subprocess

import numpy as np
import pytest

from ttcap import mp
from ttcap.boards import RP2040_TT06, RP2350_DBV3
from ttcap.capture import CaptureRequest, frame_cfg, stream_header_bytes
from ttcap.emu import Board, Waveform
from vgacap.modes import MODES
from vgacap.ppm import read_ppm
from vgacap.stream import read_chunks
from vgacap.synth import bars, colour6_to_rgb, frame_samples, grid

FRAMES = pathlib.Path(__file__).resolve().parents[2] / "build" / "vgacap-frames"
VGA = MODES["640x480@60"]


def run_frame(profile, uo, *, period=5, high=2, delay=1, chip="rp2350", heap=None,
              stop_after_bytes=None, **cfg_kwargs):
    cfg = frame_cfg(profile, **cfg_kwargs)
    kwargs = {}
    if heap is not None:
        kwargs["heap_blocks"] = heap
    board = Board(
        Waveform(uo, profile.uo_gpios, profile.clk_gpio, period=period, high=high, delay=delay,
                 constants={9: 0, 10: 0, 11: 0, 12: 0}),
        chip=chip,
        max_cycles=20_000_000_000,
        **kwargs,
    )
    board.stop_after_bytes = stop_after_bytes
    return board, board.run_script(mp.build(cfg, "frame_rp2.py"))


def chunks(out: bytes):
    frams, times = [], []
    for tag, payload in read_chunks(out):
        if tag == "FRAM":
            frams.append(struct.unpack_from("<IHHII", payload, 0))
        elif tag == "TIME":
            times.append(payload[18:].decode())
    return frams, times


def pictures(tmp_path, profile, out, vga_mode="640x480@60"):
    req = CaptureRequest(
        profile=profile, project=None, design=None, clock_hz=26_600_000, seconds=0,
        max_bytes=0, max_frames=1, script="frame_rp2.py", vga_mode=vga_mode,
    )
    stream = tmp_path / "frame.vgacap"
    stream.write_bytes(stream_header_bytes(req) + out)
    report = subprocess.run(
        [str(FRAMES), str(stream), str(tmp_path / "f")], capture_output=True, text=True, check=True
    ).stdout
    return report, sorted(tmp_path.glob("f-*.ppm"))


def assert_pictures_exact(frames, image6):
    assert frames, "no picture was reconstructed"
    want = colour6_to_rgb(image6)
    for frame in frames:
        got = read_ppm(frame)
        assert got.shape == want.shape, frame.name
        bad = np.argwhere((got != want).any(axis=2))
        assert len(bad) == 0, "%s: %d pixels differ, first at %s" % (frame.name, len(bad), bad[:3].tolist())


@pytest.fixture(scope="module")
def bars_uo() -> bytes:
    return frame_samples(VGA, bars(640, 480)).tobytes()


@pytest.mark.parametrize("high,delay", [(2, 0), (2, 1), (2, 2), (3, 0), (3, 2)])
def test_whole_frames_at_26_6_mhz_are_pixel_exact(tmp_path, bars_uo, high, delay):
    # 133 MHz / 5: the RP2350's PWM cannot make 25.175 MHz, and this is the
    # nearest it gets. `high` and `delay` cover the PWM duty and the
    # design's clock-to-output path, which decide whether the trigger sees
    # the hsync edge one sample late.
    board, out = run_frame(RP2350_DBV3, bars_uo, high=high, delay=delay, max_frames=2)

    frams, times = chunks(out)
    assert times[0].startswith("frame window=438 buffers=8 hsync_active=0 vsync_active=0")
    assert times[-1] == "frames=2 stalls=0 rxstall=0 sysclk_hz=133000000"
    # fpga-1's heap holds 438 of the 481 lines, so each picture is two
    # captures, and they share its frame counter.
    assert [(c, first, n) for c, first, n, _, _ in frams] == [
        (0, 34, 438), (0, 472, 43), (1, 34, 438), (1, 472, 43)
    ]
    assert all(cpl == 800 and samples == n * 800 for _, _, n, cpl, samples in frams)
    report, frames = pictures(tmp_path, RP2350_DBV3, out)
    assert "partial=1" not in report
    assert len(frames) == 2
    assert_pictures_exact(frames, bars(640, 480))


def test_a_heap_that_holds_the_whole_region_takes_one_capture_a_picture(tmp_path, bars_uo):
    board, out = run_frame(RP2350_DBV3, bars_uo, heap=(400_000,), max_frames=2)

    frams, times = chunks(out)
    assert [(c, first, n) for c, first, n, _, _ in frams] == [(0, 34, 481), (1, 34, 481)]
    assert times[0].startswith("frame window=481 buffers=1")
    _, frames = pictures(tmp_path, RP2350_DBV3, out)
    assert_pictures_exact(frames, bars(640, 480))


def test_window_mode_sweeps_the_picture_in_small_windows(tmp_path):
    image = grid(640, 480)
    uo = frame_samples(VGA, image).tobytes()

    board, out = run_frame(RP2350_DBV3, uo, window_lines=60, max_frames=1)

    frams, _ = chunks(out)
    assert [first for _, first, _, _, _ in frams] == list(range(34, 515, 60))
    assert sum(n for _, _, n, _, _ in frams) == 481
    assert {c for c, _, _, _, _ in frams} == {0}
    _, frames = pictures(tmp_path, RP2350_DBV3, out)
    assert len(frames) == 1
    assert_pictures_exact(frames, image)


def test_an_rp2040_board_assembles_pictures_from_many_windows(tmp_path):
    # 12-bit samples two to a word: 1,600 bytes a line, and an RP2040 heap.
    image = grid(640, 480)
    uo = frame_samples(VGA, image).tobytes()

    board, out = run_frame(RP2040_TT06, uo, chip="rp2040", heap=(40_000, 24_000), max_frames=1)

    frams, times = chunks(out)
    # Bisection finds each block's exact capacity: 25 + 15 lines.
    assert times[0].startswith("frame window=40 buffers=2")
    assert len(frams) == 13  # ceil(481 / 40)
    _, frames = pictures(tmp_path, RP2040_TT06, out)
    assert_pictures_exact(frames, image)


def test_a_design_that_moves_vsync_mid_line_is_still_exact(tmp_path):
    # The calibration designs move vsync on an hsync leading edge; the
    # classic hvsync generator moves it where hpos wraps, 144 clocks into
    # libvgaframe's line. The trigger's first counted hsync edge is the
    # start of line 1 either way.
    samples = frame_samples(VGA, bars(640, 480)).astype(np.uint8)
    vsync = samples & 0x08
    moved = (samples & ~np.uint8(0x08)) | np.roll(vsync, 144)

    board, out = run_frame(RP2350_DBV3, moved.tobytes(), max_frames=1)

    _, frames = pictures(tmp_path, RP2350_DBV3, out)
    assert_pictures_exact(frames, bars(640, 480))


def test_positive_sync_polarity_is_measured_on_the_board(tmp_path):
    mode = MODES["800x600@60"]
    image = bars(800, 600)
    uo = frame_samples(mode, image).tobytes()

    board, out = run_frame(RP2350_DBV3, uo, vga_mode="800x600@60", max_frames=1)

    frams, times = chunks(out)
    assert "hsync_active=1 vsync_active=1" in times[0]
    assert all(cpl == 1056 for _, _, _, cpl, _ in frams)
    _, frames = pictures(tmp_path, RP2350_DBV3, out, vga_mode="800x600@60")
    assert_pictures_exact(frames, image)


def test_a_stop_request_ends_cleanly_between_captures(bars_uo):
    board, out = run_frame(RP2350_DBV3, bars_uo, stop_after_bytes=500_000)

    frams, times = chunks(out)
    assert frams
    assert times[-1].startswith("frames=")
    assert "rxstall=0" in times[-1]
    # The board stopped at the first safe point after the request: no more
    # than one capture past it.
    assert len(out) < 500_000 + 481 * 800 + 1000


@pytest.mark.parametrize("profile", [RP2040_TT06, RP2350_DBV3], ids=lambda p: p.name)
def test_the_uploaded_frame_script_fits_in_the_board(profile):
    script = mp.build(frame_cfg(profile), "frame_rp2.py")

    assert len(script) < 10_000


def test_the_measured_default_sample_point_is_exact(tmp_path, bars_uo):
    # Rising edge, read at once: the middle of the window swept on fpga-4.
    board, out = run_frame(RP2350_DBV3, bars_uo, edge="rising", sample_delay=0, max_frames=1)

    _, frames = pictures(tmp_path, RP2350_DBV3, out)
    assert_pictures_exact(frames, bars(640, 480))


def test_a_sample_delay_that_does_not_fit_one_pixel_is_refused(bars_uo):
    # 133 MHz / 25 MHz = 5 cycles a pixel; the loop is 3 + delay. On fpga-4
    # (4 cycles a pixel) delays 2 and 3 skipped samples and gave no picture
    # at all, with nothing to say why. Refused with a reason instead.
    board, out = run_frame(RP2350_DBV3, bars_uo, sample_delay=3, clock_hz=25_000_000, max_frames=1)

    frams, times = chunks(out)
    assert frams == []
    assert times == ["error=sample_delay does not fit one project clock"]
