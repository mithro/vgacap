# SPDX-License-Identifier: Apache-2.0
"""The frame hash methods (vgacap.hashes) against the vectors shared with
tests/test_hashes.c, and against a plain per-pixel loop. The 640x480 zero
FNV, 009b538b2455c383, is the black-frame hash in the published tt07
tt_um_MichaelBell_rle_vga frame_hashes.txt."""
from __future__ import annotations
import hashlib

import numpy as np
import pytest

from vgacap import hashes

B07 = bytes(range(8))
ZERO = bytes(640 * 480)
MOD64 = bytes(i % 64 for i in range(640 * 480))

VECTORS = [
    (B07, "8a851ff82ee7048ad09ec3847f1ddf44944104d2cbd17ef4e3db22c6785a0d45", 0x760BBC7B258DDB8B),
    (ZERO, "7818f5542a0404157573be6cffc0e0c8e68ce3c0f5d17d07ccdd9313fb700baf", 0x009B538B2455C383),
    (MOD64, "9cc7a2f413ef0ebe2541f5de1d77972dd92241ffff0c1b5f72279fc23608e8c6", 0x9D900346F0FE6783),
]


def plain_fnv(rgb6: bytes) -> int:
    h = 0x14650FB0739D0383
    for v in rgb6:
        for c in ((v >> 4) & 3, (v >> 2) & 3, v & 3):
            h = ((h ^ (c * 85)) * 0x100000001B3) & 0xFFFFFFFFFFFFFFFF
    return h


@pytest.mark.parametrize("px,sha,fnv", VECTORS)
def test_vectors(px, sha, fnv):
    assert hashes.sha256_rgb6(px) == sha
    assert hashes.fnv1a64_tb2026_rgb888(px) == fnv
    assert plain_fnv(px) == fnv
    assert hashlib.sha256(px).hexdigest() == sha


def test_numpy_input():
    a = np.frombuffer(MOD64, dtype=np.uint8)
    assert hashes.sha256_rgb6(a) == VECTORS[2][1]
    assert hashes.fnv1a64_tb2026_rgb888(a) == VECTORS[2][2]


def test_empty_is_offset_basis():
    assert hashes.fnv1a64_tb2026_rgb888(b"") == 0x14650FB0739D0383


def test_definitions_verbatim():
    assert hashes.DEFINITIONS["sha256-rgb6"] == [
        "SHA-256 of the frame as rgb6: one byte per pixel, R<<4 | G<<2 | B,",
        "row-major, active area only, at native resolution.",
    ]
    assert hashes.DEFINITIONS["fnv1a64-tb2026-rgb888"] == [
        "FNV-1a 64 loop (prime 0x100000001b3) with a NON-STANDARD offset basis,",
        "hash_offset_basis below, not 0xcbf29ce484222325: tb.cpp's constant lost a digit.",
        "Over the frame as RGB888, row-major, active area only, at native resolution,",
        "each 2-bit channel stored as value * 85.",
    ]
