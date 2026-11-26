# SPDX-License-Identifier: Apache-2.0
"""Running `rle_rp2.make_rle()` on the emulated PIO, outside the script.

The board script decides the style and period for the clock it is given;
these helpers take that decision away so a test can put any program on any
clock -- including ones the script would refuse -- and look at the words
the state machine pushed, before the board's fix-up pass touches them.
"""

from __future__ import annotations

import ast
import types

import numpy as np

from ttcap import mp
from ttcap.boards import RP2040_TT06, RP2350_DBV3
from ttcap.emu import Board, Waveform
from ttcap.emu.board import Ptr32

SCRIPT = "rle_rp2.py"
SHIFTCTRL_SET_OFFSET = 0x2000 + 0x0D0
RXF0, PIO_STEP = 0x50200020, 0x100000


def lift(names, namespace):
    """Define the named top-level functions of rle_rp2.py in `namespace`."""
    tree = ast.parse(mp.source(SCRIPT))
    nodes = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in names]
    assert sorted(n.name for n in nodes) == sorted(names)
    exec(compile(ast.Module(body=nodes, type_ignores=[]), SCRIPT, "exec"), namespace)  # noqa: S102
    return namespace


def board_helpers():
    """fix_runs/write_pairs/write_rle_chunk with what they need around them."""
    prelude = {}
    tree = ast.parse(mp.source(SCRIPT))
    wanted = {"_le32", "_write_all"}
    nodes = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in wanted]
    exec(compile(ast.Module(body=nodes, type_ignores=[]), SCRIPT, "exec"), prelude)  # noqa: S102
    prelude["micropython"] = types.SimpleNamespace(viper=lambda f: f)
    prelude["ptr32"] = Ptr32
    return prelude


class Run:
    """One state machine running make_rle() against a waveform, its RX FIFO
    drained by an unpaced-on-the-far-side DMA into a big buffer."""

    def __init__(self, uo, period, *, locked=True, masked=True, prog_period=None,
                 high=None, delay=1, sample_delay=0, rising=True, chip="rp2350",
                 words=1 << 16, constants=None, set_in_count=True):
        profile = RP2350_DBV3 if chip == "rp2350" else RP2040_TT06
        self.profile = profile
        self.wave = Waveform(uo, profile.uo_gpios, profile.clk_gpio, period=period,
                             high=high, delay=delay, constants=constants)
        self.board = Board(self.wave, chip=chip, max_cycles=1 << 62)
        mods = self.board.modules()
        rp2, machine = mods["rp2"], mods["machine"]
        make_rle = lift(["make_rle"], {"rp2": rp2})["make_rle"]
        pio = 1
        self.prog = make_rle(profile.clk_gpio, 1 if rising else 0, sample_delay,
                             prog_period or period, 1 if masked else 0, profile.in_count, locked)
        if profile.pio_gpio_base:
            rp2.PIO(pio).gpio_base(profile.pio_gpio_base)
        self.sm = rp2.StateMachine(4 * pio, self.prog, in_base=machine.Pin(profile.in_base))
        self.pio_sm = self.board.blocks[pio].sms[0]
        if masked and set_in_count:
            base = 0x50200000 + PIO_STEP * pio
            machine.mem32[base + SHIFTCTRL_SET_OFFSET] = profile.in_count
        self.buf = bytearray(4 * words)
        self.dma = rp2.DMA()
        ctrl = self.dma.pack_ctrl(size=2, inc_read=False, inc_write=True, treq_sel=8 * pio + 4)
        self.dma.config(read=RXF0 + PIO_STEP * pio, write=self.buf, count=words, ctrl=ctrl,
                        trigger=True)
        self.sm.active(1)

    def advance(self, cycles):
        self.board.advance(cycles)

    def words(self):
        n = len(self.buf) // 4 - self.dma.count
        return np.frombuffer(bytes(self.buf[: 4 * n]), dtype="<u4").tolist()

    def rxstall(self):
        return self.board.blocks[1].fdebug & 1


def expand_words(words, limit=10_000_000):
    """The PIO's `v0, r0, v1, r1, ...` (runs one short) as samples; a
    trailing value without its run yet is dropped."""
    pairs = len(words) // 2
    values = np.array(words[0 : 2 * pairs : 2], dtype=np.int64)
    runs = np.array(words[1 : 2 * pairs : 2], dtype=np.int64) + 1
    if pairs and runs.max() > limit:
        raise ValueError("a run of %d samples: words out of step" % runs.max())
    return np.repeat(values, runs)


def matches_signal(samples, signal):
    """True when `samples` is an unbroken run of the cyclic `signal` (the
    sampled window's value per clock): no sample missed, doubled or
    altered. Returns (ok, index of the first sample in `signal`)."""
    got = np.asarray(samples, dtype=np.uint16).tobytes()
    if isinstance(signal, (bytes, bytearray)):
        signal = np.frombuffer(signal, dtype=np.uint8)
    hay = np.asarray(signal, dtype=np.uint16).tobytes()
    big = hay * (len(got) // len(hay) + 2)
    head = got[: min(len(got), 8000)]
    start = big.find(head)
    while start >= 0 and start % 2:  # a match must start on a sample
        start = big.find(head, start + 1)
    return start >= 0 and big[start : start + len(got)] == got, start // 2


def window_values(uo, profile, constants=None):
    """What `in pins`/`out y, in_count` reads for each uo_out byte: the
    profile's window, uo_out bits at their pads, other pads constant."""
    constants = constants or {}
    lut = np.zeros(256, dtype=np.uint16)
    for byte in range(256):
        word = 0
        for i in range(profile.in_count):
            gpio = profile.in_base + i
            if gpio in profile.uo_gpios:
                word |= ((byte >> profile.uo_gpios.index(gpio)) & 1) << i
            else:
                word |= (constants.get(gpio, 0) & 1) << i
        lut[byte] = word
    return lut[np.frombuffer(bytes(uo), dtype=np.uint8)]
