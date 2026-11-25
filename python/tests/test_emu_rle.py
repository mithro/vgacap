# SPDX-License-Identifier: Apache-2.0
"""`rle_rp2.py`, the PIO run-length capture, end to end on the emulator.

The built script runs on the emulated board; its `RLE ` chunks must expand
into exactly the samples a `RAW ` capture of the same design gives, and
`vgacap-frames` must make pixel-exact pictures of them.
"""

from __future__ import annotations

import pathlib
import subprocess

import numpy as np
import pytest
from rle_pio import matches_signal, window_values

from ttcap import mp
from ttcap.boards import RP2040_TT06, RP2350_DBV3
from ttcap.capture import CaptureRequest, capture_cfg, rle_cfg, stream_header_bytes
from ttcap.emu import Board, Waveform
from vgacap.modes import MODES
from vgacap.ppm import read_ppm
from vgacap.stream import read_chunks, read_stream
from vgacap.synth import bars, colour6_to_rgb, frame_samples, grid

FRAMES = pathlib.Path(__file__).resolve().parents[2] / "build" / "vgacap-frames"
MODE = MODES["640x480@60"]
FRAME = MODE.cpl * MODE.lpf
RP2040_PADS = {9: 1, 10: 0, 11: 1, 12: 0}


@pytest.fixture(scope="module")
def bars_uo() -> bytes:
    return frame_samples(MODE, bars(640, 480)).tobytes()


def run(profile, uo, period, sysclk, script="rle_rp2.py", stop_bytes=None, chip="rp2350",
        cycles_per_byte=0.05, clock=None, **cfg_kwargs):
    clock = clock or sysclk // period
    wave = Waveform(uo, profile.uo_gpios, profile.clk_gpio, period=period, delay=1,
                    constants=RP2040_PADS)
    board = Board(wave, chip=chip, sysclk_hz=sysclk, cycles_per_byte=cycles_per_byte,
                  max_cycles=1 << 40)
    board.stop_after_bytes = stop_bytes
    if script == "rle_rp2.py":
        cfg = rle_cfg(profile, clock_hz=clock, **cfg_kwargs)
    else:
        cfg = capture_cfg(profile, **cfg_kwargs)
    out = board.run_script(mp.build(cfg, script))
    req = CaptureRequest(profile=profile, project=None, design=None, clock_hz=clock,
                         seconds=0, max_bytes=1, script=script)
    return stream_header_bytes(req) + out


def samples_of(stream: bytes) -> np.ndarray:
    _, items = read_stream(stream)
    runs = [(i[1], i[2]) for i in items if i[0] == "run"]
    return np.repeat(np.array([v for v, _ in runs], dtype=np.int64), [r for _, r in runs])


def messages(stream: bytes) -> list[str]:
    return [p[18:].decode() for tag, p in read_chunks(stream) if tag == "TIME"]


def pictures(tmp_path, stream: bytes):
    path = tmp_path / "rle.vgacap"
    path.write_bytes(stream)
    report = subprocess.run([str(FRAMES), str(path), str(tmp_path / "f")],
                            capture_output=True, text=True, check=True).stdout
    return report, sorted(tmp_path.glob("f-*.ppm"))


def assert_pictures(frames, image6):
    assert frames, "no picture"
    want = colour6_to_rgb(image6)
    for f in frames:
        assert np.array_equal(read_ppm(f), want), f.name


@pytest.mark.parametrize("image", [bars, grid], ids=["bars", "grid"])
def test_25_mhz_at_150_mhz_is_pixel_exact(tmp_path, image):
    # Locked style, 6 system clocks a pixel, the masked one-instruction
    # sample. A link far faster than USB, so the PIO is the only limit.
    uo = frame_samples(MODE, image(640, 480)).tobytes()
    stream = run(RP2350_DBV3, uo, 6, 150_000_000, stop_bytes=3_000_000)

    msgs = messages(stream)
    assert msgs[0] == "rle style=locked period=6 mask=1"
    assert msgs[-1].startswith("overruns=0 rxstall=0 saturated=0")
    got = samples_of(stream)
    assert len(got) > 2 * FRAME
    assert matches_signal(got, uo)[0]
    _, frames = pictures(tmp_path, stream)
    assert len(frames) >= 1
    assert_pictures(frames, image(640, 480))


def test_rle_expands_to_exactly_what_raw_captures(bars_uo):
    # 500 kHz at 133 MHz: a ratio of 266, past the delay field's reach, so
    # the RLE program waits for each edge -- and RAW streaming keeps up.
    raw = samples_of(run(RP2350_DBV3, bars_uo, 266, 133_000_000, script="capture_rp2.py",
                         stop_bytes=4 * FRAME // 4 * 4, cycles_per_byte=190.0))
    stream = run(RP2350_DBV3, bars_uo, 266, 133_000_000, stop_bytes=600_000,
                 cycles_per_byte=190.0)
    rle = samples_of(stream)

    assert messages(stream)[0] == "rle style=edge period=266 mask=1"
    assert messages(stream)[-1].startswith("overruns=0 rxstall=0")
    assert len(raw) > 3 * FRAME and len(rle) > FRAME
    # The RLE capture, wherever it began in the frame, is a stretch of the
    # RAW one: same values, sample for sample.
    ok, _ = matches_signal(rle, raw[: 2 * FRAME])
    assert ok


def test_an_rp2040_encodes_its_12_bit_window(tmp_path, bars_uo):
    # No IN_COUNT on RP2040: the generic two-instruction sample, at 7
    # system clocks a pixel (19 MHz at 133 MHz), ui_in pads included.
    stream = run(RP2040_TT06, bars_uo, 7, 133_000_000, chip="rp2040", stop_bytes=3_000_000)

    assert messages(stream)[0] == "rle style=locked period=7 mask=0"
    got = samples_of(stream)
    assert matches_signal(got, window_values(bars_uo, RP2040_TT06, RP2040_PADS))[0]
    _, frames = pictures(tmp_path, stream)
    assert_pictures(frames, bars(640, 480))


@pytest.mark.parametrize("period", [5, 4])
def test_a_clock_no_style_is_exact_at_is_refused(bars_uo, period):
    stream = run(RP2350_DBV3, bars_uo, period, 150_000_000, stop_bytes=1000)

    msgs = messages(stream)
    assert len(msgs) == 1 and msgs[0].startswith("error=no exact rle style")


def test_forcing_locked_on_a_fractional_ratio_is_refused(bars_uo):
    # 19.3 MHz at 133 MHz is 6.9 system clocks: a fractional divider,
    # which "locked" would drift against; "auto" takes the edge style.
    clock = 19_300_000
    locked = run(RP2350_DBV3, bars_uo, 7, 133_000_000, stop_bytes=1000, style="locked", clock=clock)
    assert messages(locked)[0].startswith("error=no exact rle style")
    # 12.6 MHz: 10.6 system clocks, so periods of 10 and 11 -- the edge
    # style allows a cycle under the shortest for that unevenness.
    auto = run(RP2350_DBV3, bars_uo, 10, 133_000_000, stop_bytes=1000, clock=12_600_000)
    assert messages(auto)[0] == "rle style=edge period=11 mask=1"


@pytest.mark.parametrize("masked,period", [(1, 8), (0, 9)])
def test_the_edge_style_counts_the_sample_delay_in_its_shortest_clock(masked, period):
    # Every edge-style loop waits `sample_delay` cycles after the edge, so
    # its shortest clock grows by that much: 8 + 1 would read about every
    # other clock of this signal, and is refused rather than run.
    uo = np.random.default_rng(5).integers(0, 256, 50_000, dtype=np.uint8).tobytes()
    chip = "rp2350" if masked else "rp2040"
    profile = RP2350_DBV3 if masked else RP2040_TT06
    kw = dict(chip=chip, style="edge", mask=masked, stop_bytes=200_000)
    refused = run(profile, uo, period, 144_000_000, sample_delay=1, **kw)
    assert messages(refused)[0].startswith("error=no exact rle style")

    ok = run(profile, uo, period + 1, 144_000_000, sample_delay=1, **kw)
    assert messages(ok)[0] == "rle style=edge period=%d mask=%d" % (period + 1, masked)
    want = uo if masked else window_values(uo, profile, RP2040_PADS)
    got = samples_of(ok)
    assert len(got) > 10_000 and matches_signal(got, want)[0]


def test_a_clock_a_fraction_off_a_whole_ratio_is_not_phase_locked(bars_uo):
    # 150 MHz / 6 is 25 MHz; 25.1 MHz is 5.98 system clocks, which a loop
    # padded to 6 would drift against by a sample every 250. Only an exact
    # ratio gets the locked style; this one waits for each edge -- and 6
    # is too short for that, so it is refused.
    near = run(RP2350_DBV3, bars_uo, 6, 150_000_000, stop_bytes=1000, clock=25_100_000)
    assert messages(near)[0].startswith("error=no exact rle style")
    # 13.3 MHz is 11.28 at 150 MHz: edge, a cycle under the shortest allowed.
    edge = run(RP2350_DBV3, bars_uo, 11, 150_000_000, stop_bytes=1000, clock=13_300_000)
    assert messages(edge)[0] == "rle style=edge period=11 mask=1"


def test_more_pairs_than_the_link_carries_are_reported_as_overruns(bars_uo):
    # 25 MHz bars at the modelled USB rate: ~2 MB/s of pairs for 0.7 MB/s.
    stream = run(RP2350_DBV3, bars_uo, 6, 150_000_000, stop_bytes=200_000,
                 cycles_per_byte=190.0)

    summary = messages(stream)[-1]
    overruns = int(summary.split()[0].split("=")[1])
    assert overruns > 0
    # The dropped count is a lower bound: one sample for every lost pair.
    dropped = [int.from_bytes(p[12:16], "little") for t, p in read_chunks(stream) if t == "TIME"]
    assert dropped[-1] == overruns * 4096 // 2


@pytest.mark.parametrize("oneshot", [False, True], ids=["stream", "oneshot"])
def test_a_stopped_rle_capture_leaves_no_dma_channel_running(bars_uo, oneshot):
    # A and B chain to each other, so on RP2350 aborting one re-triggers
    # the other (RP2350-E5) unless the prelude's stop_dma clears EN first.
    profile = RP2350_DBV3
    board = Board(Waveform(bars_uo, profile.uo_gpios, profile.clk_gpio, period=266),
                  sysclk_hz=133_000_000)
    board.stop_after_bytes = 20_000
    cfg = rle_cfg(profile, clock_hz=500_000, buf_words=2048, oneshot=oneshot)

    board.run_script(mp.build(cfg, "rle_rp2.py"))

    assert [ch.num for ch in board.channels if ch.busy] == []


@pytest.mark.parametrize("profile", [RP2040_TT06, RP2350_DBV3], ids=lambda p: p.name)
def test_the_uploaded_rle_script_fits_in_the_board(profile):
    assert len(mp.build(rle_cfg(profile, clock_hz=25_000_000), "rle_rp2.py")) < 10_000


def test_the_request_builds_the_rle_cfg():
    req = CaptureRequest(profile=RP2350_DBV3, project=None, design=None, clock_hz=25_000_000,
                         seconds=1, max_bytes=0, script="rle_rp2.py", edge="rising")
    cfg = req.cfg()
    assert cfg["style"] == "auto" and cfg["mask"] == -1 and cfg["clock_hz"] == 25_000_000
    with pytest.raises(ValueError, match="even"):
        rle_cfg(RP2350_DBV3, clock_hz=1, buf_words=4095)


@pytest.mark.parametrize("name", ["bars", "random"])
def test_oneshot_takes_an_unbroken_stretch_the_link_could_not_stream(bars_uo, name):
    # 25 MHz at 150 MHz with the real USB rate: streaming overruns (above),
    # but one shot of the two buffers is exact however busy the picture.
    uo = bars_uo if name == "bars" else np.random.default_rng(3).integers(
        0, 256, 50_000, dtype=np.uint8).tobytes()
    stream = run(RP2350_DBV3, uo, 6, 150_000_000, cycles_per_byte=190.0,
                 buf_words=20_000, oneshot=True)

    msgs = messages(stream)
    assert msgs[-1].startswith("overruns=0 rxstall=0")
    rle_chunks = [p for t, p in read_chunks(stream) if t == "RLE "]
    assert len(rle_chunks) == 2
    got = samples_of(stream)
    ok, _ = matches_signal(got, uo)
    assert ok and len(got) >= 20_000
