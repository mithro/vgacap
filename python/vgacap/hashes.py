# SPDX-License-Identifier: Apache-2.0
"""The frame hash methods, the reference for src/sample/hashes.c.

``xxh3-128-rgb6`` and ``fnv1a64-tb2026-rgb888``, over a frame as rgb6: one byte
per pixel, R<<4 | G<<2 | B, row-major. Both are for identity and integrity,
not security: neither resists a deliberate collision.

``xxh3_128_rgb6`` needs the ``xxhash`` package (the ``hashes`` extra); the
rest of this module, and vgacap.framesfile, use the standard library only.
"""
from __future__ import annotations

#: The definition lines, copied verbatim into every hash file as "# " lines.
DEFINITIONS: dict[str, list[str]] = {
    "xxh3-128-rgb6": [
        "XXH3-128 (xxHash, seed 0, canonical big-endian) of the frame as rgb6: one byte",
        "per pixel, R<<4 | G<<2 | B, row-major, active area only, at native resolution.",
    ],
    "fnv1a64-tb2026-rgb888": [
        "FNV-1a 64 loop (prime 0x100000001b3) with a NON-STANDARD offset basis,",
        "hash_offset_basis below, not 0xcbf29ce484222325: tb.cpp's constant lost a digit.",
        "Over the frame as RGB888, row-major, active area only, at native resolution,",
        "each 2-bit channel stored as value * 85.",
    ],
}

FNV_PRIME = 0x100000001B3
#: tb.cpp's 1469598103934665603: the standard 0xcbf29ce484222325 with a digit lost.
FNV_BASIS = 0x14650FB0739D0383
_MASK = 0xFFFFFFFFFFFFFFFF
_RGB888 = [(((v >> 4) & 3) * 85, ((v >> 2) & 3) * 85, (v & 3) * 85) for v in range(64)]


def xxh3_128_rgb6(rgb6) -> str:
    """Lowercase hex XXH3-128 (seed 0, canonical: high 64 bits first) of a
    bytes-like (or uint8 array) rgb6 frame: 32 digits."""
    import xxhash
    return xxhash.xxh3_128_hexdigest(memoryview(rgb6).cast("B"))


def fnv1a64_tb2026_rgb888(rgb6) -> int:
    """FNV-1a 64 over the frame as RGB888 (each rgb6 channel times 85)."""
    h = FNV_BASIS
    for v in memoryview(rgb6).cast("B"):
        r, g, b = _RGB888[v & 63]
        h = ((h ^ r) * FNV_PRIME) & _MASK
        h = ((h ^ g) * FNV_PRIME) & _MASK
        h = ((h ^ b) * FNV_PRIME) & _MASK
    return h
