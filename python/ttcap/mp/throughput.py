# SPDX-License-Identifier: Apache-2.0
"""Board-side USB-CDC throughput generator (MicroPython).

Run by the host via `RawRepl.exec_stream()` with a `CFG` dict prepended by
`ttcap.mp.with_cfg()`:

    CFG = {"total": <bytes to send>, "block": 32768}

It writes exactly `CFG["total"]` bytes of a repeating 255-byte pattern to
`sys.stdout.buffer` in `CFG["block"]`-sized writes and prints nothing else,
so everything the host sees between the raw REPL's `OK` ack and its 0x04
stdout terminator is payload. `ttcap.throughput.measure_throughput()` times
those bytes and checks each one against its position in the pattern.

The pattern is `bytes(range(256))` with 0x04 removed, i.e.

    PATTERN = ALL[:4] + ALL[5:]        # 255 bytes, 0x00..0xFF minus 0x04

0x04 is the raw REPL's end-of-stdout marker and the protocol has no escaping,
so a payload byte of 0x04 would truncate the stream at the host. Dropping that
one value keeps every other byte value (including 0x00, 0x11/0x13 and 0xFF) in
the test pattern while staying transparent over the raw REPL.

MicroPython notes: `sys.stdout.buffer.write()` exists on the rp2 port and is
the binary path to the USB CDC endpoint; it can return a short count, so the
writes below loop until each block has been handed over.
"""

_ALL = bytes(range(256))
PATTERN = _ALL[:4] + _ALL[5:]
PATTERN_LEN = 255


def build_block_source(block):
    """Return a buffer long enough to slice any `block` bytes at any phase.

    The pattern repeats every `PATTERN_LEN` bytes, so a buffer of
    `block + 2 * PATTERN_LEN` bytes can serve a `block`-byte slice starting
    at any offset in `0 .. PATTERN_LEN - 1`.
    """
    return PATTERN * (block // PATTERN_LEN + 2)


def write_all(out, data):
    """Write every byte of `data` to `out`, tolerating short writes.

    `data[pos:]` would copy the whole block on the common first pass, i.e.
    an extra `block`-sized allocation per write on exactly the path whose
    speed is being measured -- so pass `data` itself while `pos` is 0.
    """
    pos = 0
    n = len(data)
    while pos < n:
        written = out.write(data[pos:] if pos else data)
        if written is None:
            return
        pos += written


def run(cfg, out):
    """Write `cfg["total"]` bytes of the pattern to `out` in blocks."""
    total = cfg["total"]
    block = cfg["block"]
    source = build_block_source(block)
    phase = 0
    sent = 0
    while sent < total:
        remaining = total - sent
        n = block
        if remaining < n:
            n = remaining
        write_all(out, source[phase:phase + n])
        sent += n
        phase = (phase + n) % PATTERN_LEN


import sys  # noqa: E402 - keep the pattern constants at the top of the script

run(CFG, sys.stdout.buffer)  # noqa: F821 - CFG is prepended by the host
