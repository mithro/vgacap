# SPDX-License-Identifier: Apache-2.0
import pathlib, subprocess
import numpy as np
import pytest
from vgacap.modes import MODES
from vgacap.synth import bars, grid, colour6_to_rgb, write_stream, frame_samples
from vgacap.ppm import read_ppm

FRAMES = pathlib.Path(__file__).resolve().parents[2] / "build" / "vgacap-frames"


def run_frames(stream: pathlib.Path, prefix: pathlib.Path, *args):
    out = subprocess.run([str(FRAMES), str(stream), str(prefix), *args], capture_output=True, text=True, check=True).stdout
    return out, sorted(prefix.parent.glob(prefix.name + "-*.ppm"))


def test_frame_samples_shape():
    m = MODES["640x480@60"]
    s = frame_samples(m, bars(640, 480))
    assert s.shape == (800 * 525,) and s.dtype == np.uint8
    assert (s[0] >> 7) & 1 == 0 and (s[0] >> 3) & 1 == 0          # negative syncs: both pulses low at frame start


@pytest.mark.parametrize("chunk", ["raw", "rle", "fram"])
@pytest.mark.parametrize("mode_name", ["640x480@60", "800x600@60", "720x400@70"])
def test_roundtrip_exact(tmp_path, chunk, mode_name):
    m = MODES[mode_name]
    img = grid(m.h_active, m.v_active)
    stream = tmp_path / "s.vgacap"
    write_stream(stream, m, img, frames=3, chunk=chunk)
    out, files = run_frames(stream, tmp_path / "f")
    assert files, out
    got = read_ppm(files[-1])
    assert got.shape == (m.v_active, m.h_active, 3)
    np.testing.assert_array_equal(got, colour6_to_rgb(img))
    assert f"mode={mode_name}" in out


def test_12bit_two_per_word_msb(tmp_path):
    m = MODES["640x480@60"]; img = bars(640, 480)
    stream = tmp_path / "s.vgacap"
    write_stream(stream, m, img, frames=3, sample_bits=12, samples_per_word=2, flags=1)
    _, files = run_frames(stream, tmp_path / "f")
    np.testing.assert_array_equal(read_ppm(files[-1]), colour6_to_rgb(img))
