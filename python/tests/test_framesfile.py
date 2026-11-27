# SPDX-License-Identifier: Apache-2.0
"""frames.txt and frame_hashes.<method>.txt: files written by the C writers
(build/vgacap-outputs-dump) are read by vgacap.framesfile and written back
byte-identically, for both samplers' header variants and both hash methods.
tests/test_outputs.c has the C-side format cases."""
from __future__ import annotations
import pathlib
import subprocess

import pytest

from vgacap import framesfile as ff
from vgacap import hashes
from vgacap.timebase import fmt_double

ROOT = pathlib.Path(__file__).resolve().parents[2]
DUMP = ROOT / "build" / "vgacap-outputs-dump"
METHODS = ["xxh3-128-rgb6", "fnv1a64-tb2026-rgb888"]
VARIANTS = ["tb", "centre", "centre-fraction", "nosync"]
FC = 420000
XXH = "0123456789abcdef" * 2


def c_files(tmp_path, variant) -> pathlib.Path:
    d = tmp_path / variant
    d.mkdir()
    subprocess.run([str(DUMP), variant, str(d)], check=True, capture_output=True, text=True)
    return d


def hash_path(d, method):
    return d / f"frame_hashes.{method}.txt"


@pytest.mark.parametrize("variant", VARIANTS)
def test_frames_txt_roundtrip(tmp_path, variant):
    d = c_files(tmp_path, variant)
    f = ff.read_frames_txt(d / "frames.txt")
    out = tmp_path / f"{variant}.out.txt"
    ff.write_frames_txt(out, f)
    assert out.read_bytes() == (d / "frames.txt").read_bytes()


@pytest.mark.parametrize("variant", VARIANTS)
@pytest.mark.parametrize("method", METHODS)
def test_hash_file_roundtrip(tmp_path, variant, method):
    d = c_files(tmp_path, variant)
    h = ff.read_hash_file(hash_path(d, method))
    out = tmp_path / "out.txt"
    ff.write_hash_file(out, h)
    assert out.read_bytes() == hash_path(d, method).read_bytes()
    # The hash file is the frames file's frames, frame 0 first.
    f = ff.read_frames_txt(d / "frames.txt")
    assert len(h.hashes) == len(f.frames)
    assert [x is None for x in h.hashes] == [fr.distinct < 0 for fr in f.frames]
    for field in ("id", "status", "sampler", "sampler_status", "mode", "width", "height", "clock_hz",
                  "clocks_per_pixel", "capture_after_clock"):
        assert getattr(h, field) == getattr(f, field)


def test_tb_values(tmp_path):
    f = ff.read_frames_txt(c_files(tmp_path, "tb") / "frames.txt")
    assert (f.id, f.status, f.sampler, f.sampler_status) == ("tt07/tt_um_example", "ok", "tb-v1", "ok")
    assert (f.width, f.height, f.mode, f.clock_hz, f.clocks_per_pixel) == (640, 480, "640x480@60", 25175000, 1.0)
    assert f.capture_after_clock == 4096 and f.frame_clocks == FC
    assert f.centre is None and len(f.distinct) == 3 and len(f.frames) == 11
    assert [x.distinct for x in f.frames] == [0, 0, 0, 1, 1, -1, -1, 0, 2, 2, 2]
    assert f.frames[3] == ff.Frame(1, 5000 + 3 * FC, 0)
    assert f.frames[8].start_clock == 5000 + 8 * FC + 3 and f.frames[10].missing == 17
    # Late frame 8 and the missing change on 10 each begin a run.
    assert ff.runs(f.frames) == [
        (0, 3, 0, 5000, 0), (3, 2, 1, 5000 + 3 * FC, 0), (5, 2, None, None, None),
        (7, 1, 0, 5000 + 7 * FC, 0), (8, 1, 2, 5000 + 8 * FC + 3, 0), (9, 1, 2, 5000 + 9 * FC, 0),
        (10, 1, 2, 5000 + 10 * FC, 17)]


def test_centre_values(tmp_path):
    f = ff.read_frames_txt(c_files(tmp_path, "centre") / "frames.txt")
    assert f.sampler == "centre-v1"
    assert f.centre == ff.Centre((-2, 1), (48, 33), 12, (1, -1), 345, 6)
    f = ff.read_frames_txt(c_files(tmp_path, "centre-fraction") / "frames.txt")
    assert f.clocks_per_pixel == 800.0 / 840.0 and f.status == "truncated"
    assert f.centre.colour_changes_off_grid is None


def test_nosync_has_no_frames(tmp_path):
    d = c_files(tmp_path, "nosync")
    f = ff.read_frames_txt(d / "frames.txt")
    assert (f.status, f.sampler_status, f.frames, f.distinct) == ("ok", "no-sync", [], [])
    assert ff.read_hash_file(hash_path(d, "xxh3-128-rgb6")).hashes == []


def test_hash_values_and_layout(tmp_path):
    d = c_files(tmp_path, "tb")
    f = ff.read_frames_txt(d / "frames.txt")
    # The dump's distinct frame k is bytes (i * (k + 3) + k) & 63, i < 192.
    px = [bytes((i * (k + 3) + k) & 63 for i in range(192)) for k in range(3)]
    xxh = ff.read_hash_file(hash_path(d, "xxh3-128-rgb6"))
    fnv = ff.read_hash_file(hash_path(d, "fnv1a64-tb2026-rgb888"))
    for fr, hs, hf in zip(f.frames, xxh.hashes, fnv.hashes):
        if fr.distinct < 0:
            assert hs is None and hf is None
        else:
            assert hs == hashes.xxh3_128_rgb6(px[fr.distinct]) == f.distinct[fr.distinct]
            assert hf == f"{hashes.fnv1a64_tb2026_rgb888(px[fr.distinct]):016x}"
    lines = hash_path(d, "fnv1a64-tb2026-rgb888").read_text().split("\n")
    assert lines[0] == "hash_method: fnv1a64-tb2026-rgb888"
    assert lines[1:5] == ["# " + x for x in hashes.DEFINITIONS["fnv1a64-tb2026-rgb888"]]
    assert lines[5] == "hash_offset_basis: 0x14650fb0739d0383"
    header = 17    # method, 4 definition lines, the parameter, id .. frames (11 lines)
    assert lines[header - 1] == "frames: 11"
    assert lines[header + 5] == "-" and lines[header + 6] == "-"   # frame k is line header + k
    assert lines[header + 11] == "" and len(lines) == header + 12
    # The published tt07 files' hash lines: lowercase hex, 16 digits.
    assert all(len(x) == 16 and x == x.lower() for x in lines[header:header + 5])
    xxhlines = hash_path(d, "xxh3-128-rgb6").read_text().split("\n")
    assert not any(x.startswith("hash_offset_basis") for x in xxhlines)
    assert all(len(x) == 32 for x in xxhlines[14:] if x not in ("", "-"))


# ---- the grouping rules, Python side alone (the C side is test_outputs.c) ----

def fr(*d, step=100, base=1000):
    return [ff.Frame(x, 0 if x < 0 else base + i * step, 0) for i, x in enumerate(d)]


def test_runs_grouping():
    assert ff.runs(fr(0, 0, 0, 1, 1, -1, -1, 0)) == [
        (0, 3, 0, 1000, 0), (3, 2, 1, 1300, 0), (5, 2, None, None, None), (7, 1, 0, 1700, 0)]


def test_runs_break_on_late_start_and_missing():
    a = fr(0, 0, 0, 0)
    a[2].start_clock += 1
    a[3].start_clock += 1
    assert ff.frame_clocks(a) == 100
    assert ff.runs(a) == [(0, 2, 0, 1000, 0), (2, 2, 0, 1201, 0)]
    b = fr(0, 0, 0, 0)
    b[2].missing = b[3].missing = 6
    assert ff.runs(b) == [(0, 2, 0, 1000, 0), (2, 2, 0, 1200, 6)]


def test_frame_clocks_median():
    f = [ff.Frame(0, 0), ff.Frame(0, 10), ff.Frame(-1), ff.Frame(0, 50)]
    assert ff.frame_clocks(f) == 40          # distances 10, 40; element 2 // 2 = 1
    assert ff.frame_clocks(f[:1]) == 0 and ff.frame_clocks([]) == 0


# ---- the reader refuses what a writer would not write ----

@pytest.fixture
def tb_text(tmp_path):
    return (c_files(tmp_path, "tb") / "frames.txt").read_text()


def read_text(tmp_path, text):
    p = tmp_path / "bad.txt"
    p.write_text(text)
    return ff.read_frames_txt(p)


@pytest.mark.parametrize("old,new", [
    ("sampler_status: ok\n", ""),                                    # a missing key
    ("pixel_format: rgb6\nwidth: 640\n", "width: 640\npixel_format: rgb6\n"),   # key order
    ("sampler_status: ok", "sampler_status: fine"),
    ("frame_clocks: 420000", "frame_clocks: 5"),
    ("frames: 11", "frames: 12"),
    ("clocks_per_pixel: 1\n", "clocks_per_pixel: 1.0\n"),            # not the canonical text
    ("0 3 0 5000 0\n3 2 1", "0 2 0 5000 0\n2 1 0 845000 0\n3 2 1"),   # not the grouping
    ("5 2 - - -\n", "5 1 - - -\n6 1 - - -\n"),
    ("7 1 0", "7 1 3"),                                              # distinct out of range
])
def test_reader_refuses(tmp_path, tb_text, old, new):
    assert old in tb_text
    with pytest.raises(ff.FormatError):
        read_text(tmp_path, tb_text.replace(old, new, 1))


def test_reader_refuses_no_final_newline_and_trailing(tmp_path, tb_text):
    with pytest.raises(ff.FormatError):
        read_text(tmp_path, tb_text[:-1])
    with pytest.raises(ff.FormatError):
        read_text(tmp_path, tb_text + "11 1 0 1 0\n")


def test_hash_reader_refuses(tmp_path):
    d = c_files(tmp_path, "tb")
    text = hash_path(d, "fnv1a64-tb2026-rgb888").read_text()
    for old, new in [("0x14650fb0739d0383", "0xcbf29ce484222325"), ("frames: 11", "frames: 10"),
                     ("# FNV-1a 64 loop", "# FNV-1a 63 loop"), ("hash_method: fnv", "hash_method: fnx")]:
        assert old in text
        p = tmp_path / "bad.txt"
        p.write_text(text.replace(old, new, 1))
        with pytest.raises(ff.FormatError):
            ff.read_hash_file(p)
    p.write_text(text.replace("\n-\n", "\nAB\n", 1))
    with pytest.raises(ff.FormatError):
        ff.read_hash_file(p)


def test_writer_refuses():
    f = ff.FramesFile("i", "ok", "centre-v1", "ok", 1, 1, "m", 1, 1.0, 0, "s", 0, XXH)
    with pytest.raises(ff.FormatError):
        ff.format_frames_txt(f)              # centre-v1 without its keys
    f = ff.FramesFile("i", "ok", "tb-v1", "bad", 1, 1, "m", 1, 1.0, 0, "s", 0, XXH)
    with pytest.raises(ff.FormatError):
        ff.format_frames_txt(f)              # unknown sampler_status


def test_fmt_double_is_the_shared_formatter():
    assert fmt_double(800.0 / 840.0) == "0.9523809523809523"


@pytest.mark.parametrize("kw", [
    dict(store_xxh3_128="x"), dict(store_xxh3_128="AB" * 16), dict(store_xxh3_128="ab" * 15),
    dict(store_xxh3_128="gg" * 16), dict(store_xxh3_128="ab" * 32), dict(store=""), dict(store="a b"), dict(store="d/f.bin"),
    dict(store="d\\f.bin"), dict(store="a\nb"), dict(store_length=-1), dict(store_length="7"),
    dict(id=""), dict(mode="a\nb"),
])
def test_writer_refuses_bad_store_and_strings(kw):
    args = dict(id="i", status="ok", sampler="tb-v1", sampler_status="ok", width=1, height=1, mode="m",
                clock_hz=1, clocks_per_pixel=1.0, capture_after_clock=0, store="frames.bin", store_length=0,
                store_xxh3_128=XXH)
    ff.format_frames_txt(ff.FramesFile(**args))      # the unmodified one is fine
    args.update(kw)
    with pytest.raises(ff.FormatError):
        ff.format_frames_txt(ff.FramesFile(**args))


@pytest.mark.parametrize("key,new", [
    ("store_xxh3_128: " + "0123456789abcdef" * 2, "store_xxh3_128: ab"),
    ("store_xxh3_128: " + "0123456789abcdef" * 2, "store_xxh3_128: " + "0123456789ABCDEF" * 2),
    ("store_xxh3_128: " + "0123456789abcdef" * 2, "store_sha256: " + "0123456789abcdef" * 4),
    ("store: frames.bin", "store: a/frames.bin"), ("store: frames.bin", "store: a frames.bin"),
    ("store_length: 1234567", "store_length: 01234567"), ("store_length: 1234567", "store_length: -1"),
])
def test_reader_refuses_bad_store(tmp_path, tb_text, key, new):
    assert key in tb_text
    with pytest.raises(ff.FormatError):
        read_text(tmp_path, tb_text.replace(key, new, 1))


def test_reader_names_the_pre_xxh3_frames_txt(tmp_path, tb_text):
    """A frames.txt from before vgacap #62 is refused with a message that says
    what it is and what to do, not a generic key mismatch."""
    old = tb_text.replace("store_xxh3_128: " + "0123456789abcdef" * 2, "store_sha256: " + "0123456789abcdef" * 4, 1)
    with pytest.raises(ff.FormatError, match=r"pre-XXH3 frames format \(store_sha256\); re-derive with vgacap-sample"):
        read_text(tmp_path, old)


def test_reader_names_the_pre_xxh3_hash_file(tmp_path):
    d = c_files(tmp_path, "tb")
    text = hash_path(d, "xxh3-128-rgb6").read_text().replace("hash_method: xxh3-128-rgb6", "hash_method: sha256-rgb6", 1)
    p = tmp_path / "old.txt"
    p.write_text(text)
    with pytest.raises(ff.FormatError, match=r"pre-XXH3 hash method \(sha256-rgb6\); re-derive with vgacap-sample"):
        ff.read_hash_file(p)
