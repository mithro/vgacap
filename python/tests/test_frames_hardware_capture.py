# SPDX-License-Identifier: Apache-2.0
"""Regression test over a real capture from tt08 silicon.

`tests/fixtures/tiny-logo-capture.vgacap.gz` is a serial capture of
tt_um_rejunity_vga_logo on a tt08 board (640x480@60, positive syncs). Its
hsync carries a couple of dozen spurious pulses of 2 to 30 clocks, spread
over four frame periods, and before the glitch filter every frame came out
short of lines: the reconstruction matched no mode and produced no picture at
all. It must now yield a complete 640x480@60 frame -- and the *right* one, so
the assertions below pin the picture's shape, not just its existence.

The picture is the Tiny Tapeout logo: a dark magenta ring and "TT" glyph on a
background that runs pale yellow at the top to blue at the bottom, speckled
throughout by the capture's own undersampling. Everything pinned here was read
off the reconstruction, not guessed. Row 0's colour histogram is

    (255,255,255) x 574, (170,0,170) x 27, (85,0,85) x 21,
    (255,0,170)   x  13, (255,255,170) x 5

and the white count alone identifies the row: rows 1, 2 and 3 have 545, 535
and 531. A picture shifted by even one line therefore fails, and so does one
shifted horizontally far enough to move the ring off the frame's centre.
"""
import gzip
import pathlib
import re
import subprocess

import numpy as np

from vgacap.ppm import read_ppm

ROOT = pathlib.Path(__file__).resolve().parents[2]
FRAMES = ROOT / "build" / "vgacap-frames"
FIXTURE = ROOT / "tests" / "fixtures" / "tiny-logo-capture.vgacap.gz"

#: Every colour the reconstruction contains, as read off it. Tiny VGA is 6-bit
#: RGB, so each channel can only be 0, 85, 170 or 255; these eight are the
#: subset this picture uses.
COLOURS = {
    (85, 0, 85), (85, 0, 170), (170, 0, 170), (170, 85, 170),
    (255, 0, 170), (255, 170, 170), (255, 255, 170), (255, 255, 255),
}
ROW0_WHITE = 574
#: First and last column of rows 200..280 that the logo darkens.
RING_COLUMNS = (223, 559)


def colours_of(img):
    return {tuple(int(c) for c in px) for px in np.unique(img.reshape(-1, 3), axis=0)}


def test_tt08_tiny_logo_reconstructs(tmp_path):
    stream = tmp_path / "tiny-logo.vgacap"
    stream.write_bytes(gzip.decompress(FIXTURE.read_bytes()))
    out = subprocess.run([str(FRAMES), str(stream), str(tmp_path / "f")],
                         capture_output=True, text=True, check=True).stdout
    complete = [ln for ln in out.splitlines()
                if "640x480 mode=640x480@60" in ln and "partial=0" in ln]
    assert complete, out

    # The glitches are counted, not silently swallowed: the capture really
    # does carry them, so a zero here would mean the filter never ran. The
    # frame line reports that frame's share, the summary the stream's total.
    summary = re.search(r"^frames=(\d+) glitches_total=(\d+)$", out, re.M)
    assert summary, out
    assert int(summary.group(1)) >= 1
    assert int(summary.group(2)) > 0
    per_frame = [int(m) for m in re.findall(r" glitches=(\d+)$", out, re.M)]
    assert per_frame and sum(per_frame) <= int(summary.group(2))

    ppms = sorted(tmp_path.glob("f-*.ppm"))
    assert ppms, out
    img = read_ppm(ppms[0])
    assert img.shape == (480, 640, 3)

    # A 6-bit RGB signal can only produce these four levels per channel, and
    # this picture is not a flat colour.
    assert set(np.unique(img).tolist()) <= {0, 85, 170, 255}
    assert len(colours_of(img)) >= 8
    assert colours_of(img) == COLOURS

    # Structure, which a shifted or garbled picture loses. The background is a
    # vertical gradient (bright at the top, dark at the bottom) and the logo
    # sits in the middle, darker than the background either side of it.
    lum = img.astype(np.int32).sum(axis=2)
    top, bottom = lum[0:20].mean(), lum[460:480].mean()
    assert top > bottom + 200, (top, bottom)                    # 655 vs 294
    interior = lum[200:280, 290:350].mean()                     # inside the ring
    left, right = lum[200:280, 0:40].mean(), lum[200:280, 600:640].mean()
    assert interior + 150 < min(left, right), (interior, left, right)   # 346 vs 602
    assert (img[0] == (255, 255, 255)).all(axis=1).sum() == ROW0_WHITE

    # Where the ring sits, to the column: the dark columns of that same row
    # band run 223..559, and any horizontal shift moves both ends with it (a
    # 4-pixel roll already breaks this, where the coarser checks above do not).
    dark_cols = np.flatnonzero(lum[200:280].mean(axis=0) < 450)
    assert (int(dark_cols[0]), int(dark_cols[-1])) == RING_COLUMNS

    # Two pinned pixels, checked against the rendered picture: the centre of
    # the logo is its darkest magenta, the top-left corner is background.
    assert tuple(img[240, 320]) == (85, 0, 85)
    assert tuple(img[0, 0]) == (255, 255, 255)

    if len(ppms) > 1:
        # The project's picture is static, so a second reconstructed frame
        # must look like the first one.
        second = read_ppm(ppms[1])
        assert second.shape == img.shape
        assert colours_of(second) <= COLOURS
        assert abs(second.astype(np.int32).sum(axis=2).mean() - lum.mean()) < 0.05 * lum.mean()
    else:
        # Only one frame: at least prove the picture reaches both edges of the
        # active area rather than being a band of black.
        assert img[0].any() or img[479].any()
