# SPDX-License-Identifier: Apache-2.0
"""A stub `rp2` module that reproduces MicroPython's `asm_pio` contract.

The real decorator does two things the host must imitate for a board-side
PIO program to be testable:

1. It **replaces the decorated function's globals** for the duration of the
   assembly passes, installing only the instruction mnemonics
   (micropython v1.24.0 `ports/rp2/modules/rp2.py:241-268`)::

       gl = _pio_funcs; gl["wait"] = emit.wait; ...
       old_gl = f.__globals__.copy()
       f.__globals__.clear()
       f.__globals__.update(gl)
       emit.start_pass(0); f()
       emit.start_pass(1); f()
       f.__globals__.clear(); f.__globals__.update(old_gl)

   So a program body that reads a module-level constant raises `NameError`
   on the board. Closure cells and default arguments are untouched by the
   swap, which is why `capture_rp2.make_sampler()` takes its operands as
   parameters. (v1.24.0 has no `try/finally` around the swap, so the
   NameError also leaves the module's globals cleared; this stub restores
   them either way, so a failing test does not poison the rest of the run.)

2. It assembles real instruction words, so a test can assert encodings
   rather than substrings::

       WAIT  0x2000 | delay<<8 | polarity<<7 | src<<5 | index
       IN    0x4000 | delay<<8 | src<<5      | (bits & 31)

   with `gpio`/`pins` both src 0. The index is emitted verbatim and
   unmasked, exactly as `PIOASMEmit.wait()` does, so an out-of-range GPIO
   index corrupts the `src` field here just as it would on hardware.

Verified against the real module: `tmp/c1_cases.py` in this worktree runs
micropython v1.24.0's own `rp2.py` under CPython and produces
`['0x2087', '0x2007', '0x400c']` for `wait(1, gpio, 7); wait(0, gpio, 7);
in_(pins, 12)` -- the same words this stub emits.
"""

from __future__ import annotations


class PIO:
    SHIFT_LEFT = 0
    SHIFT_RIGHT = 1
    JOIN_NONE = 0
    JOIN_TX = 1
    JOIN_RX = 2


#: The subset of MicroPython's `_pio_funcs` these programs can reach.
PIO_FUNCS = {
    "gpio": 0,
    "pins": 0,
    "x": 1,
    "y": 2,
    "null": 3,
    "pindirs": 4,
    "pc": 5,
    "isr": 6,
    "osr": 7,
}


class Program:
    """What the stub `asm_pio` returns: instruction words plus the config."""

    def __init__(self, instructions, config, wrap_target, wrap):
        self.instructions = tuple(instructions)
        self.config = dict(config)
        self.wrap_target = wrap_target
        self.wrap = wrap

    def __getitem__(self, index):
        # The real decorator returns `emit.prog`, whose element 0 is the
        # instruction array; keep that shape so board code can pass it on.
        if index == 0:
            return list(self.instructions)
        raise IndexError(index)

    def __repr__(self) -> str:
        return "Program(%s)" % ", ".join(hex(w) for w in self.instructions)


class _Emit:
    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        self.instructions: list[int] = []
        self.wrap_target_at: int | None = None
        self.wrap_at: int | None = None

    # -- directives -------------------------------------------------------
    def wrap_target(self) -> None:
        self.wrap_target_at = len(self.instructions)

    def wrap(self) -> None:
        self.wrap_at = len(self.instructions)

    # -- instructions -----------------------------------------------------
    def wait(self, polarity, src, index, rel=False):
        self.instructions.append(0x2000 | (polarity << 7) | (src << 5) | index)

    def in_(self, src, bits):
        self.instructions.append(0x4000 | (src << 5) | (bits & 0x1F))

    def nop(self):
        self.instructions.append(0xA042)


def asm_pio(**kwargs):
    """Stub of `rp2.asm_pio`, faithful to the globals swap and the encoding."""

    def dec(f):
        emit = _Emit()
        gl = dict(PIO_FUNCS)
        gl["wrap_target"] = emit.wrap_target
        gl["wrap"] = emit.wrap
        gl["wait"] = emit.wait
        gl["in_"] = emit.in_
        gl["nop"] = emit.nop

        old_gl = f.__globals__.copy()
        f.__globals__.clear()
        f.__globals__.update(gl)
        try:
            emit.reset()
            f()  # pass 0
            emit.reset()
            f()  # pass 1
        finally:
            f.__globals__.clear()
            f.__globals__.update(old_gl)

        return Program(emit.instructions, kwargs, emit.wrap_target_at, emit.wrap_at)

    return dec
