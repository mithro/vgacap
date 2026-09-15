# SPDX-License-Identifier: Apache-2.0
"""Host side of a vgacap capture run.

Only `capture_cfg()` lives here so far: it turns a `BoardProfile` into the
`CFG` dict that `ttcap.mp.with_cfg()` prepends to `ttcap/mp/capture_rp2.py`.
The rest of the capture flow (enabling the design, clocking it, writing the
`VGCH` header and splicing the board's chunks after it) arrives with the
`ttcap capture` subcommand.
"""

from __future__ import annotations

from .boards import BoardProfile

EDGES = ("falling", "rising")

#: Informational only, and only a guess: the rp2 port boots RP2040 at
#: 133 MHz but RP2350 at 150 MHz, and the capture never changes the system
#: clock. The authoritative value comes back from the board, which reports
#: `machine.freq()` as `sysclk_hz=` in its closing `TIME` chunk.
DEFAULT_SYSCLK_HZ = 133_000_000

DEFAULT_BUF_WORDS = 4096


def capture_cfg(
    profile: BoardProfile,
    buf_words: int = DEFAULT_BUF_WORDS,
    max_bytes: int = 0,
    edge: str = "falling",
    pio: int = 0,
    sm: int = 0,
) -> dict:
    """Build the `CFG` dict for `capture_rp2.py` from a board profile.

    `buf_words` is the size of each of the two ping-pong DMA buffers in
    32-bit words, `max_bytes` caps the bytes the script emits (0 = run until
    Ctrl-C), `edge` selects which project-clock edge is sampled, and
    `pio`/`sm` pick the state machine (0/0 unless something else on the
    board has already claimed it).
    """
    if edge not in EDGES:
        raise ValueError(f"edge must be one of {EDGES}, got {edge!r}")
    if buf_words <= 0:
        raise ValueError(f"buf_words must be positive, got {buf_words}")
    if max_bytes < 0:
        raise ValueError(f"max_bytes must not be negative, got {max_bytes}")
    if not 0 <= pio <= 2:
        raise ValueError(f"pio must be 0..2 (RP2040 has 0..1), got {pio}")
    if not 0 <= sm <= 3:
        raise ValueError(f"sm must be 0..3, got {sm}")

    return {
        "clk_gpio": profile.clk_gpio,
        "in_base": profile.in_base,
        "in_count": profile.in_count,
        "gpio_base": profile.pio_gpio_base,
        "push_thresh": profile.push_thresh,
        "buf_words": buf_words,
        "max_bytes": max_bytes,
        "edge": edge,
        "pio": pio,
        "sm": sm,
        "sysclk_hz": DEFAULT_SYSCLK_HZ,
    }
