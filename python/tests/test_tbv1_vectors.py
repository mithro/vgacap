# SPDX-License-Identifier: Apache-2.0
"""tb-v1 against tests/vectors/tbv1: traces of small synthetic designs that
tinytapeout-vga-videos simulated with tb.cpp, with tb.cpp's own frame hashes
from its pipeline, and the frames.txt that tests/vectors/tbv1/refresh_frames.py
wrote at the vgacap: commit in MANIFEST.txt (which also names the generator
commit). vgacap-sample --sampler tb-v1 on each
trace.vgch must reproduce the hash lines and frames.txt exactly. A difference
is a tb-v1 port bug, to be fixed in src/sample/tbv1.c and python/vgacap/tbv1.py,
never in the vectors.

No header key is excluded: frames.txt and the hash file carry no tool
version or path (the store name is derived from --name, which the test sets
to the case name, as the generator did)."""
from __future__ import annotations
import hashlib
import pathlib
import subprocess

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
SAMPLE = ROOT / "build" / "vgacap-sample"
VECTORS = ROOT / "tests" / "vectors" / "tbv1"
CASES = ("bars640", "bars_cpp2", "frac", "glitch", "nosync", "playground")
FNV = "frame_hashes.fnv1a64-tb2026-rgb888.txt"


def manifest_entries():
    """(sha256, relative path) of every checksum line; the generator: and
    vgacap: lines are provenance, not checksums."""
    out = []
    for line in (VECTORS / "MANIFEST.txt").read_text().splitlines():
        parts = line.split("  ")
        if len(parts) == 2 and len(parts[0]) == 64:
            out.append((parts[0], parts[1]))
    return out


def hash_lines(text: str):
    """The per-frame hash lines: 16 hex digits, one per line, after the header."""
    return [ln for ln in text.splitlines() if len(ln) == 16 and all(c in "0123456789abcdef" for c in ln)]


def test_manifest():
    entries = manifest_entries()
    assert sorted(p for _, p in entries) == sorted(f"{c}/{f}" for c in CASES
                                                    for f in ("trace.vgch", "frames.txt", FNV))
    for sha, rel in entries:
        assert hashlib.sha256((VECTORS / rel).read_bytes()).hexdigest() == sha, rel
    text = (VECTORS / "MANIFEST.txt").read_text()
    assert "generator: tinytapeout-vga-videos " in text and "vgacap: " in text


@pytest.mark.parametrize("case", CASES)
def test_tbv1_vector(case, tmp_path):
    src = VECTORS / case
    proc = subprocess.run([str(SAMPLE), str(src / "trace.vgch"), "--out", str(tmp_path), "--name", case,
                           "--sampler", "tb-v1"], capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
    out = tmp_path / "tb-v1"
    want_fnv = (src / FNV).read_text()
    got_fnv = (out / FNV).read_text()
    assert hash_lines(got_fnv) == hash_lines(want_fnv)
    assert got_fnv == want_fnv
    assert (out / "frames.txt").read_text() == (src / "frames.txt").read_text()
