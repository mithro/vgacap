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


def test_bars_no_wrap():
    # Regression: np.arange(w, dtype=np.uint8) wraps modulo 256, breaking the
    # ramp past column 255 for every mode width in the table (640/720/800/1024).
    b = bars(640, 1)
    assert b[0, 300] == (300 // 10) & 0x3F
    assert b[0, 639] == 63


def test_bars_matches_c_bars_formula():
    # tests/test_frame.c's `bars` helper is `(uint8_t)((x / 10) & 0x3F)` for
    # x a uint16_t. Assert Python's bars() agrees at every column of a
    # 640-wide row so the two synthetic generators cannot silently drift.
    b = bars(640, 1)[0]
    for x in range(640):
        assert int(b[x]) == (x // 10) & 0x3F


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


SIM_TRACE_REFUSAL = ("vgacap-frames: a simulation trace (samples_per_clock 2) is read with "
                     "vgacap-sample, not vgacap-frames")


@pytest.mark.parametrize("header", [
    dict(sample_bits=43, samples_per_word=0, samples_per_clock=2, clock_bit=42),
    dict(sample_bits=43, samples_per_word=0),                      # wide, no clock bit
    dict(sample_bits=8, samples_per_word=1, samples_per_clock=2, clock_bit=7),
])
def test_refuses_a_simulation_trace(tmp_path, header):
    # vgacap-frames counts every sample as a pixel clock, so a stream with two
    # samples a clock, or samples wider than 32 bits, is not its input.
    import io
    from vgacap.stream import Header, Writer
    fp = io.BytesIO()
    w = Writer(fp, Header(version=2, mode=2, **header))
    w.block(0); w.runs([(1, 10), (2, 5)])
    stream = tmp_path / "trace.vgacap"; stream.write_bytes(fp.getvalue())
    proc = subprocess.run([str(FRAMES), str(stream), str(tmp_path / "f")], capture_output=True, text=True)
    assert proc.returncode == 1, proc
    assert SIM_TRACE_REFUSAL in proc.stderr
    assert not list(tmp_path.glob("f-*.ppm"))


def test_v2_narrow_runs_stream_decodes(tmp_path):
    # A v2 board-style stream (8 bits, one sample a clock) of RUNS decodes
    # exactly as the same samples in a v1 RLE stream do.
    import io
    from vgacap.stream import Header, Writer
    m = MODES["640x480@60"]; img = grid(640, 480)
    s = frame_samples(m, img)
    change = np.flatnonzero(np.diff(s)) + 1
    starts = np.concatenate([[0], change]); ends = np.concatenate([change, [len(s)]])
    pairs = [(int(s[a]), int(b - a)) for a, b in zip(starts, ends)]
    fp = io.BytesIO(); w = Writer(fp, Header(version=2, sample_bits=8, samples_per_word=1, mode=3))
    for _ in range(3):
        w.runs(pairs)
    stream = tmp_path / "v2.vgacap"; stream.write_bytes(fp.getvalue())
    out, files = run_frames(stream, tmp_path / "f")
    assert files, out
    np.testing.assert_array_equal(read_ppm(files[-1]), colour6_to_rgb(img))


def write_overrun_stream(path, marker: bool = True):
    """Grid and bars on alternate frames, with one whole frame of samples
    lost part way down the fourth: line and frame timing stay intact, so
    only the overrun marker can stop a picture being built from the top of
    one frame and the bottom of another. Returns (wanted pictures, sample
    count before the gap)."""
    from vgacap.stream import TINYVGA_MAP, Header, Writer

    mode = MODES["640x480@60"]
    images = [grid(mode.h_active, mode.v_active), bars(mode.h_active, mode.v_active)]
    per = [frame_samples(mode, im) for im in images]
    frame = len(per[0])
    flat = np.concatenate([per[k % 2] for k in range(7)])
    cut = frame * 3 + 200_000
    with open(path, "wb") as fp:
        w = Writer(fp, Header(sample_bits=8, samples_per_word=4, signal_map=TINYVGA_MAP,
                              mode=3, clock_hz=0, desc="overrun"))
        pos = 0
        while pos < len(flat):
            if pos <= cut < pos + 16384:
                w.raw(flat[pos:cut].tolist())
                if marker:
                    w.time(0, 0, frame, "overrun")
                pos = cut + frame
                continue
            w.raw(flat[pos:pos + 16384].tolist())
            pos += 16384
    return [colour6_to_rgb(im) for im in images], cut


def _is_one_of(ppm, wanted) -> bool:
    got = read_ppm(ppm)
    return any(np.array_equal(got, w) for w in wanted)


def test_an_overrun_marker_stops_a_torn_picture(tmp_path):
    stream = tmp_path / "overrun.vgacap"
    wanted, cut = write_overrun_stream(stream)

    proc = subprocess.run([str(FRAMES), str(stream), str(tmp_path / "f")],
                          capture_output=True, text=True, check=True)

    frames = sorted(tmp_path.glob("f-*.ppm"))
    assert frames and all(_is_one_of(f, wanted) for f in frames)
    assert "vgacap-frames: overrun after sample %d (dropped=420000): picture reset" % cut in proc.stderr


def test_without_the_marker_the_same_gap_tears_a_picture(tmp_path):
    # What the marker is for: the gap alone keeps every timing check happy.
    stream = tmp_path / "gap.vgacap"
    wanted, _ = write_overrun_stream(stream, marker=False)

    _, frames = run_frames(stream, tmp_path / "f")

    assert not all(_is_one_of(f, wanted) for f in frames)
