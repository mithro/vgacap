# SPDX-License-Identifier: Apache-2.0
"""Regression test over a real capture from tt08 silicon.

`tests/fixtures/tiny-logo-capture.vgacap.gz` is a serial capture of
tt_um_rejunity_vga_logo on a tt08 board (640x480@60, positive syncs). Its
hsync carries a couple of dozen spurious pulses of 2 to 30 clocks, spread
over four frame periods, and before the glitch filter every frame came out
short of lines: the reconstruction matched no mode and produced no picture at
all. It must now yield a complete 640x480@60 frame.
"""
import gzip
import pathlib
import re
import subprocess

ROOT = pathlib.Path(__file__).resolve().parents[2]
FRAMES = ROOT / "build" / "vgacap-frames"
FIXTURE = ROOT / "tests" / "fixtures" / "tiny-logo-capture.vgacap.gz"


def test_tt08_tiny_logo_reconstructs(tmp_path):
    stream = tmp_path / "tiny-logo.vgacap"
    stream.write_bytes(gzip.decompress(FIXTURE.read_bytes()))
    out = subprocess.run([str(FRAMES), str(stream), str(tmp_path / "f")],
                         capture_output=True, text=True, check=True).stdout
    complete = [ln for ln in out.splitlines()
                if "640x480 mode=640x480@60" in ln and "partial=0" in ln]
    assert complete, out
    assert (tmp_path / "f-0000.ppm").exists()
    # The glitches are counted, not silently swallowed: the capture really
    # does carry them, so a zero here would mean the filter never ran.
    summary = re.search(r"^frames=(\d+) glitches=(\d+)$", out, re.M)
    assert summary, out
    assert int(summary.group(1)) >= 1
    assert int(summary.group(2)) > 0
