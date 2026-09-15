# SPDX-License-Identifier: Apache-2.0
"""Minimal binary PPM (P6) reader."""
from __future__ import annotations
import numpy as np


def read_ppm(path) -> np.ndarray:
    with open(path, "rb") as fp:
        data = fp.read()
    if not data.startswith(b"P6"):
        raise ValueError("not a binary PPM (P6) file")
    pos = 2
    fields = []
    while len(fields) < 3:
        while pos < len(data) and data[pos:pos + 1].isspace():
            pos += 1
        if data[pos:pos + 1] == b"#":
            while pos < len(data) and data[pos:pos + 1] != b"\n":
                pos += 1
            continue
        start = pos
        while pos < len(data) and not data[pos:pos + 1].isspace():
            pos += 1
        fields.append(int(data[start:pos]))
    pos += 1  # single whitespace byte after maxval
    width, height, maxval = fields
    n = width * height * 3
    pixels = np.frombuffer(data, dtype=np.uint8, count=n, offset=pos)
    return pixels.reshape(height, width, 3)
