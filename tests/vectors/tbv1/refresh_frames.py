# SPDX-License-Identifier: Apache-2.0
"""Rewrite each case's frames.txt with this checkout's vgacap-sample, after a
change to the frames format's digests (docs/frames-format.md).

    uv run python tests/vectors/tbv1/refresh_frames.py

tinytapeout-vga-videos' scripts/export_tbv1_vectors.py made the vectors:
it simulated each design, checked tb-v1 against tb.cpp, and copied the
frames.txt and FNV file that vgacap-sample --sampler tb-v1 wrote for the
trace. This script runs that same command on the committed trace.vgch and
refuses to write anything unless:

- the FNV file comes out byte for byte as committed, so tb-v1's frames, and
  with them the tb.cpp check, are unchanged;
- the new frames.txt differs from the committed one only in the store digest
  line and the `distinct:` lines.

It then writes the frames.txt files and MANIFEST.txt: every file's SHA-256
again, the generator line as it was, and `vgacap:` this checkout's commit.
The work tree must be clean, so that commit is the one that wrote the files.
Build first (cmake -S . -B build && cmake --build build).
"""
from __future__ import annotations

import hashlib
import pathlib
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[3]
VECTORS = ROOT / "tests" / "vectors" / "tbv1"
SAMPLE = ROOT / "build" / "vgacap-sample"
CASES = ("bars640", "bars_cpp2", "frac", "glitch", "nosync", "playground")
FILES = ("trace.vgch", "frames.txt", "frame_hashes.fnv1a64-tb2026-rgb888.txt")
FNV = FILES[2]


def git(*args: str) -> str:
    return subprocess.run(["git", "-C", str(ROOT), *args], capture_output=True, text=True, check=True).stdout


def masked(text: str) -> list[str]:
    """frames.txt's lines with the store digest and the distinct digests
    replaced by placeholders: what must not change."""
    out, in_distinct = [], False
    for line in text.split("\n"):
        if line == "distinct:":
            in_distinct = True
        elif line == "runs:":
            in_distinct = False
        elif in_distinct:
            line = "<digest>"
        elif line.startswith(("store_sha256: ", "store_xxh3_128: ")):
            line = "<store digest>"
        out.append(line)
    return out


def main() -> int:
    dirty = git("status", "--porcelain", "--untracked-files=no")
    if dirty:
        raise SystemExit(f"the work tree has uncommitted changes:\n{dirty}")
    commit = git("rev-parse", "HEAD").strip()
    new = {}
    with tempfile.TemporaryDirectory(dir=ROOT / "build") as tmp:
        for case in CASES:
            src = VECTORS / case
            out = pathlib.Path(tmp) / case
            p = subprocess.run([str(SAMPLE), str(src / "trace.vgch"), "--out", str(out), "--name", case,
                                "--sampler", "tb-v1"], capture_output=True, text=True)
            if p.returncode != 0:
                raise SystemExit(f"{case}: vgacap-sample exit {p.returncode}: {p.stderr}")
            if (out / "tb-v1" / FNV).read_bytes() != (src / FNV).read_bytes():
                raise SystemExit(f"{case}: the FNV file differs: tb-v1 changed, not only the digests")
            text = (out / "tb-v1" / "frames.txt").read_text()
            if masked(text) != masked((src / "frames.txt").read_text()):
                raise SystemExit(f"{case}: frames.txt differs in more than the digests")
            new[case] = text
    for case, text in new.items():
        (VECTORS / case / "frames.txt").write_text(text)
    manifest = (VECTORS / "MANIFEST.txt").read_text().splitlines()
    generator = [ln for ln in manifest if ln.startswith("generator: ")]
    if len(generator) != 1:
        raise SystemExit("MANIFEST.txt has no single generator: line")
    lines = [f"{hashlib.sha256((VECTORS / c / n).read_bytes()).hexdigest()}  {c}/{n}" for c in CASES for n in FILES]
    lines += generator + [f"vgacap: {commit}"]
    (VECTORS / "MANIFEST.txt").write_text("\n".join(lines) + "\n")
    print(f"wrote {len(new)} frames.txt and MANIFEST.txt (vgacap {commit})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
