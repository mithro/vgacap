# SPDX-License-Identifier: Apache-2.0
"""frames.txt and frame_hashes.<method>.txt: the reader and the reference
writer for the C writers in src/sample/outputs.c. docs/frames-format.md is the
format. Both writers produce the same bytes; the readers refuse anything
that is not exactly that format (key order, canonical doubles, run grouping,
counts), so a file that reads is a file the writers could have written.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field

from . import hashes
from .timebase import fmt_double

_HEX32 = re.compile(r"[0-9a-f]{32}\Z")   # an XXH3-128 digest
_INT = re.compile(r"-?[0-9]+\Z")
_UINT = re.compile(r"[0-9]+\Z")
SAMPLER_STATUSES = ("ok", "no-sync", "bad-timing", "bad-window")
#: The hash methods' file parameter lines, after the definition.
_PARAMS = {"fnv1a64-tb2026-rgb888": [("hash_offset_basis", "0x14650fb0739d0383")]}
_HASH_WIDTH = {"xxh3-128-rgb6": 32, "fnv1a64-tb2026-rgb888": 16}


class FormatError(ValueError):
    """The file is not in the format."""


@dataclass
class Centre:
    """The centre-v1 diagnostics. colour_changes_off_grid None is "-" (cpp not whole)."""
    window_offset: tuple[int, int]
    porches: tuple[int, int]
    outside: int
    suggested_offset: tuple[int, int]
    colour_changes: int
    colour_changes_off_grid: int | None


@dataclass
class Frame:
    """One frame: distinct is an index into FramesFile.distinct, or -1 for a
    gap (then start_clock and missing are 0)."""
    distinct: int
    start_clock: int = 0
    missing: int = 0


@dataclass
class FramesFile:
    id: str
    status: str
    sampler: str
    sampler_status: str
    width: int
    height: int
    mode: str
    clock_hz: int
    clocks_per_pixel: float
    capture_after_clock: int
    store: str
    store_length: int
    store_xxh3_128: str
    distinct: list[str] = field(default_factory=list)   # lowercase hex XXH3-128, store order
    frames: list[Frame] = field(default_factory=list)   # frame k is frames[k]
    centre: Centre | None = None                        # exactly for sampler centre-v1

    @property
    def frame_clocks(self) -> int:
        return frame_clocks(self.frames)


@dataclass
class HashFile:
    method: str
    id: str
    status: str
    sampler: str
    sampler_status: str
    mode: str
    width: int
    height: int
    clock_hz: int
    clocks_per_pixel: float
    capture_after_clock: int
    hashes: list[str | None] = field(default_factory=list)   # frame k's hash, None for a gap

    @classmethod
    def from_frames(cls, ff: FramesFile, method: str, per_distinct: list[str]) -> "HashFile":
        """The hash file for ff, given the method's hash (hex) of each distinct frame."""
        return cls(method, ff.id, ff.status, ff.sampler, ff.sampler_status, ff.mode, ff.width, ff.height,
                   ff.clock_hz, ff.clocks_per_pixel, ff.capture_after_clock,
                   [None if f.distinct < 0 else per_distinct[f.distinct] for f in ff.frames])


def frame_clocks(frames: list[Frame]) -> int:
    """The median (element size / 2 after sorting) of the distances between
    consecutive non-gap frame starts; 0 with fewer than two such frames."""
    starts = [f.start_clock for f in frames if f.distinct >= 0]
    if starts != sorted(starts):
        raise FormatError("frame starts go backwards")
    d = sorted(b - a for a, b in zip(starts, starts[1:]))
    return d[len(d) // 2] if d else 0


def runs(frames: list[Frame], fc: int | None = None) -> list[tuple]:
    """The run lines as tuples: (first, count, distinct, start, missing), or
    (first, count, None, None, None) for a gap run."""
    if fc is None:
        fc = frame_clocks(frames)
    out: list[list] = []
    for i, f in enumerate(frames):
        if out:
            r = out[-1]
            if f.distinct < 0 and r[2] is None:
                r[1] += 1
                continue
            if f.distinct >= 0 and r[2] == f.distinct and r[4] == f.missing \
                    and f.start_clock == r[3] + r[1] * fc:
                r[1] += 1
                continue
        out.append([i, 1, None, None, None] if f.distinct < 0 else [i, 1, f.distinct, f.start_clock, f.missing])
    return [tuple(r) for r in out]


# ---- writers ---------------------------------------------------------------

def _check_text(**kv: str) -> None:
    for k, v in kv.items():
        if not isinstance(v, str) or "\n" in v or v == "":
            raise FormatError(f"{k}: not a one-line string: {v!r}")


def _check_store(store: str, length: int, digest: str) -> None:
    _check_text(store=store)
    if re.search(r"[\s/\\]", store):
        raise FormatError(f"store: not a bare file name: {store!r}")
    if not isinstance(digest, str) or not _HEX32.match(digest):
        raise FormatError(f"store_xxh3_128: not 32 lowercase hex digits: {digest!r}")
    if not isinstance(length, int) or isinstance(length, bool) or length < 0:
        raise FormatError(f"store_length: not an unsigned integer: {length!r}")


def _check_common(o) -> None:
    _check_text(id=o.id, status=o.status, sampler=o.sampler, sampler_status=o.sampler_status, mode=o.mode)
    if o.sampler_status not in SAMPLER_STATUSES:
        raise FormatError(f"sampler_status: {o.sampler_status!r}")


def format_frames_txt(ff: FramesFile) -> str:
    _check_common(ff)
    _check_store(ff.store, ff.store_length, ff.store_xxh3_128)
    if (ff.sampler == "centre-v1") != (ff.centre is not None):
        raise FormatError("the centre-v1 keys belong to sampler centre-v1, and it always has them")
    for f in ff.frames:
        if not -1 <= f.distinct < len(ff.distinct):
            raise FormatError(f"distinct {f.distinct} out of range")
    for h in ff.distinct:
        if not _HEX32.match(h):
            raise FormatError(f"distinct: not lowercase hex XXH3-128: {h!r}")
    fc = frame_clocks(ff.frames)
    L = [f"id: {ff.id}", f"status: {ff.status}", f"sampler: {ff.sampler}", f"sampler_status: {ff.sampler_status}",
         "pixel_format: rgb6", f"width: {ff.width}", f"height: {ff.height}", f"mode: {ff.mode}",
         f"clock_hz: {ff.clock_hz}", f"clocks_per_pixel: {fmt_double(ff.clocks_per_pixel)}",
         f"capture_after_clock: {ff.capture_after_clock}", f"frame_clocks: {fc}", f"frames: {len(ff.frames)}",
         f"distinct_frames: {len(ff.distinct)}", f"store: {ff.store}", f"store_length: {ff.store_length}",
         f"store_xxh3_128: {ff.store_xxh3_128}"]
    c = ff.centre
    if c is not None:
        og = "-" if c.colour_changes_off_grid is None else str(c.colour_changes_off_grid)
        L += [f"window_offset: {c.window_offset[0]} {c.window_offset[1]}",
              f"porches: {c.porches[0]} {c.porches[1]}", f"outside: {c.outside}",
              f"suggested_offset: {c.suggested_offset[0]} {c.suggested_offset[1]}",
              f"colour_changes: {c.colour_changes}", f"colour_changes_off_grid: {og}"]
    L.append("distinct:")
    L += ff.distinct
    L.append("runs:")
    for first, count, d, start, missing in runs(ff.frames, fc):
        L.append(f"{first} {count} - - -" if d is None else f"{first} {count} {d} {start} {missing}")
    return "\n".join(L) + "\n"


def format_hash_file(hf: HashFile) -> str:
    _check_common(hf)
    if hf.method not in hashes.DEFINITIONS:
        raise FormatError(f"unknown hash method {hf.method!r}")
    w = _HASH_WIDTH[hf.method]
    L = [f"hash_method: {hf.method}"] + [f"# {d}" for d in hashes.DEFINITIONS[hf.method]]
    L += [f"{k}: {v}" for k, v in _PARAMS.get(hf.method, [])]
    L += [f"id: {hf.id}", f"status: {hf.status}", f"sampler: {hf.sampler}", f"sampler_status: {hf.sampler_status}",
          f"mode: {hf.mode}", f"width: {hf.width}", f"height: {hf.height}", f"clock_hz: {hf.clock_hz}",
          f"clocks_per_pixel: {fmt_double(hf.clocks_per_pixel)}",
          f"capture_after_clock: {hf.capture_after_clock}", f"frames: {len(hf.hashes)}"]
    for h in hf.hashes:
        if h is None:
            L.append("-")
        elif len(h) == w and re.fullmatch(r"[0-9a-f]+", h):
            L.append(h)
        else:
            raise FormatError(f"hash is not {w} lowercase hex digits: {h!r}")
    return "\n".join(L) + "\n"


def write_frames_txt(path: str | os.PathLike, ff: FramesFile) -> None:
    with open(path, "w", newline="\n") as f:
        f.write(format_frames_txt(ff))


def write_hash_file(path: str | os.PathLike, hf: HashFile) -> None:
    with open(path, "w", newline="\n") as f:
        f.write(format_hash_file(hf))


# ---- readers ---------------------------------------------------------------

class _Lines:
    def __init__(self, text: str):
        if not text.endswith("\n"):
            raise FormatError("the last line has no newline")
        if "\r" in text:
            raise FormatError("carriage return")
        self.lines = text[:-1].split("\n")
        self.i = 0

    def peek(self) -> str | None:
        return self.lines[self.i] if self.i < len(self.lines) else None

    def next(self) -> str:
        if self.i >= len(self.lines):
            raise FormatError("unexpected end of file")
        self.i += 1
        return self.lines[self.i - 1]

    def value(self, key: str) -> str:
        line = self.next()
        if not line.startswith(key + ": "):
            raise FormatError(f"line {self.i}: expected '{key}: ', got {line!r}")
        v = line[len(key) + 2:]
        if v == "":
            raise FormatError(f"line {self.i}: {key} is empty")
        return v

    def uint(self, key: str) -> int:
        v = self.value(key)
        if not _UINT.match(v) or (len(v) > 1 and v[0] == "0"):
            raise FormatError(f"{key}: not a canonical unsigned integer: {v!r}")
        return int(v)

    def pair(self, key: str) -> tuple[int, int]:
        parts = self.value(key).split(" ")
        if len(parts) != 2 or not all(_INT.match(p) and str(int(p)) == p for p in parts):
            raise FormatError(f"{key}: expected two integers")
        return int(parts[0]), int(parts[1])

    def double(self, key: str) -> float:
        v = self.value(key)
        try:
            d = float(v)
        except ValueError:
            raise FormatError(f"{key}: not a number: {v!r}") from None
        if fmt_double(d) != v:
            raise FormatError(f"{key}: not the canonical text of the double: {v!r}")
        return d

    def end(self) -> None:
        if self.i != len(self.lines):
            raise FormatError(f"line {self.i + 1}: trailing data")


def _read(path) -> tuple[_Lines, str]:
    with open(path, "r", newline="") as f:
        text = f.read()
    return _Lines(text), text


def _sampler_status(v: str) -> str:
    if v not in SAMPLER_STATUSES:
        raise FormatError(f"sampler_status: {v!r}")
    return v


def read_frames_txt(path: str | os.PathLike) -> FramesFile:
    ls, text = _read(path)
    id_ = ls.value("id")
    status = ls.value("status")
    sampler = ls.value("sampler")
    sampler_status = _sampler_status(ls.value("sampler_status"))
    if ls.value("pixel_format") != "rgb6":
        raise FormatError("pixel_format must be rgb6")
    width = ls.uint("width")
    height = ls.uint("height")
    mode = ls.value("mode")
    clock_hz = ls.uint("clock_hz")
    cpp = ls.double("clocks_per_pixel")
    capture = ls.uint("capture_after_clock")
    fc = ls.uint("frame_clocks")
    n_frames = ls.uint("frames")
    n_distinct = ls.uint("distinct_frames")
    store = ls.value("store")
    store_length = ls.uint("store_length")
    if (ls.peek() or "").startswith("store_sha256: "):
        raise FormatError("pre-XXH3 frames format (store_sha256); re-derive with vgacap-sample")
    store_xxh3_128 = ls.value("store_xxh3_128")
    _check_store(store, store_length, store_xxh3_128)
    centre = None
    if sampler == "centre-v1":
        wo = ls.pair("window_offset")
        po = ls.pair("porches")
        outside = ls.uint("outside")
        so = ls.pair("suggested_offset")
        cc = ls.uint("colour_changes")
        og_text = ls.value("colour_changes_off_grid")
        if og_text == "-":
            og = None
        elif _UINT.match(og_text) and str(int(og_text)) == og_text:
            og = int(og_text)
        else:
            raise FormatError(f"colour_changes_off_grid: {og_text!r}")
        centre = Centre(wo, po, outside, so, cc, og)
    if ls.next() != "distinct:":
        raise FormatError("expected 'distinct:'")
    distinct = []
    for _ in range(n_distinct):
        h = ls.next()
        if not _HEX32.match(h):
            raise FormatError(f"distinct: not lowercase hex XXH3-128: {h!r}")
        distinct.append(h)
    if ls.next() != "runs:":
        raise FormatError("expected 'runs:'")
    frames: list[Frame] = []
    while ls.peek() is not None:
        parts = ls.next().split(" ")
        if len(parts) != 5 or not _UINT.match(parts[0]) or not _UINT.match(parts[1]):
            raise FormatError(f"bad run line {' '.join(parts)!r}")
        first, count = int(parts[0]), int(parts[1])
        if first != len(frames) or count < 1:
            raise FormatError(f"run starts at {first}, expected {len(frames)} (count {count})")
        if parts[2:] == ["-", "-", "-"]:
            frames += [Frame(-1) for _ in range(count)]
            continue
        if not all(_UINT.match(p) for p in parts[2:]):
            raise FormatError(f"bad run line {' '.join(parts)!r}")
        d, start, missing = int(parts[2]), int(parts[3]), int(parts[4])
        if d >= n_distinct:
            raise FormatError(f"run distinct {d} out of range")
        frames += [Frame(d, start + k * fc, missing) for k in range(count)]
    if len(frames) != n_frames:
        raise FormatError(f"frames: {n_frames}, runs cover {len(frames)}")
    ff = FramesFile(id_, status, sampler, sampler_status, width, height, mode, clock_hz, cpp, capture,
                    store, store_length, store_xxh3_128, distinct, frames, centre)
    if ff.frame_clocks != fc:
        raise FormatError(f"frame_clocks: {fc}, the frames give {ff.frame_clocks}")
    # A writer would have grouped these frames into exactly these runs.
    if format_frames_txt(ff) != text:
        raise FormatError("runs are not the grouping of the frames")
    return ff


def read_hash_file(path: str | os.PathLike) -> HashFile:
    ls, _ = _read(path)
    method = ls.value("hash_method")
    if method == "sha256-rgb6":
        raise FormatError("pre-XXH3 hash method (sha256-rgb6); re-derive with vgacap-sample")
    if method not in hashes.DEFINITIONS:
        raise FormatError(f"unknown hash method {method!r}")
    for d in hashes.DEFINITIONS[method]:
        if ls.next() != f"# {d}":
            raise FormatError("the definition lines differ from the method's")
    for k, v in _PARAMS.get(method, []):
        if ls.value(k) != v:
            raise FormatError(f"{k} must be {v}")
    id_ = ls.value("id")
    status = ls.value("status")
    sampler = ls.value("sampler")
    sampler_status = _sampler_status(ls.value("sampler_status"))
    mode = ls.value("mode")
    width = ls.uint("width")
    height = ls.uint("height")
    clock_hz = ls.uint("clock_hz")
    cpp = ls.double("clocks_per_pixel")
    capture = ls.uint("capture_after_clock")
    n = ls.uint("frames")
    w = _HASH_WIDTH[method]
    out: list[str | None] = []
    for _ in range(n):
        h = ls.next()
        if h == "-":
            out.append(None)
        elif len(h) == w and re.fullmatch(r"[0-9a-f]+", h):
            out.append(h)
        else:
            raise FormatError(f"hash is not {w} lowercase hex digits: {h!r}")
    ls.end()
    return HashFile(method, id_, status, sampler, sampler_status, mode, width, height, clock_hz, cpp, capture, out)
