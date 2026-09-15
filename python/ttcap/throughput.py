# SPDX-License-Identifier: Apache-2.0
"""Host side of the USB-CDC throughput measurement.

`measure_throughput()` ships `ttcap/mp/throughput.py` to the board, times the
bytes that come back over the raw REPL, and checks every one of them against
its position in the test pattern. The result feeds the `ttcap throughput`
subcommand and the research note that derives the maximum usable project
clock from the measured KB/s:

    clock_max = kbytes_per_s * 1024 / bytes_per_sample

The pattern is the board-side `PATTERN` (see `ttcap/mp/throughput.py`):
`bytes(range(256))` minus 0x04, because 0x04 is the raw REPL's end-of-stdout
marker and the protocol does not escape it.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

from . import mp
from .repl import RawRepl

_ALL = bytes(range(256))
#: 255-byte test pattern: every byte value except the raw REPL's 0x04 marker.
PATTERN = _ALL[:4] + _ALL[5:]
PATTERN_LEN = len(PATTERN)

DEFAULT_TOTAL = 1_000_000
DEFAULT_BLOCK = 32768


def pattern_slice(offset: int, length: int) -> bytes:
    """Return `length` bytes of the repeating pattern starting at `offset`."""
    start = offset % PATTERN_LEN
    if start + length <= PATTERN_LEN:
        return PATTERN[start : start + length]
    repeats = (start + length + PATTERN_LEN - 1) // PATTERN_LEN
    return (PATTERN * repeats)[start : start + length]


@dataclass(frozen=True)
class ThroughputResult:
    """Outcome of one throughput run."""

    requested: int
    received: int
    seconds: float
    first_mismatch: int | None
    #: stderr text the board reported for the run (empty when it ran cleanly)
    stderr: str = ""

    @property
    def corrupt(self) -> bool:
        return self.first_mismatch is not None

    @property
    def short(self) -> bool:
        return self.received < self.requested

    @property
    def kbytes_per_s(self) -> float:
        if self.seconds <= 0.0:
            return 0.0
        return self.received / 1024.0 / self.seconds

    def format(self) -> str:
        line = "bytes=%d seconds=%.3f kbytes_per_s=%.1f corrupt=%s" % (
            self.received,
            self.seconds,
            self.kbytes_per_s,
            "yes" if self.corrupt else "no",
        )
        if self.corrupt:
            line += " first_mismatch=%d" % self.first_mismatch
        if self.short:
            line += " short_by=%d" % (self.requested - self.received)
        if self.stderr:
            line += " board_error=%r" % self.stderr.strip()
        return line


def _first_mismatch(chunk: bytes, offset: int) -> int | None:
    expected = pattern_slice(offset, len(chunk))
    if chunk == expected:
        return None
    for i, (got, want) in enumerate(zip(chunk, expected)):
        if got != want:
            return offset + i
    return None


def measure_throughput(
    repl: RawRepl,
    total: int = DEFAULT_TOTAL,
    block: int = DEFAULT_BLOCK,
    read_timeout: float = 1.0,
) -> ThroughputResult:
    """Run the board-side generator and time/verify `total` bytes of pattern.

    `repl` must already be in raw REPL mode (`RawRepl.enter()`).
    """
    if total <= 0:
        raise ValueError("total must be positive, got %d" % total)
    if block <= 0:
        raise ValueError("block must be positive, got %d" % block)

    source = mp.with_cfg(mp.load("throughput.py"), {"total": total, "block": block})

    received = 0
    first_mismatch: int | None = None
    start = time.monotonic()
    for chunk in repl.exec_stream(source, timeout=read_timeout):
        if first_mismatch is None:
            first_mismatch = _first_mismatch(chunk, received)
        received += len(chunk)
    elapsed = time.monotonic() - start

    return ThroughputResult(
        requested=total,
        received=received,
        seconds=elapsed,
        first_mismatch=first_mismatch,
        stderr=repl.last_stderr,
    )
