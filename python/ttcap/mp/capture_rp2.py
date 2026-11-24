# SPDX-License-Identifier: Apache-2.0
"""Continuous capture: PIO + DMA ping-pong, streamed as `RAW ` chunks.

A program body: `ttcap.mp.build(cfg, "capture_rp2.py")` puts
`prelude.py` in front of it, which supplies the imports, the shared CFG
keys, the register arithmetic, the chunk writers, `make_sampler()`,
`set_gpio_base()`, `init_input_pins()` and the cooperative stop -- read that
file's docstring first. This body adds the DMA register map it re-arms
channels through and `make_sampler()`, the plain sampler program. Keys this body reads on top of the prelude's:

    buf_words    32-bit words per DMA buffer (two are allocated), e.g. 4096
    max_bytes    stop after this many emitted bytes; 0 = run until Ctrl-C

Output: `RAW ` and `TIME` chunks only.

DMA: two channels ping-pong into two buffers, each chained to the other, so
the PIO never stalls while the main loop is writing a buffer out over USB.
The chain re-triggers a channel but reloads neither its write address nor
its transfer count, so each channel's own completion IRQ re-arms it -- from
the hard IRQ, before its partner can finish, which is what makes the handoff
race-free. Only the prelude's non-trigger aliases (`DMA_CH_WRITE_ADDR`,
`DMA_CH_TRANS_COUNT`) are written, so a re-arm never starts a channel; the
partner's chain does that. The offsets are self-checked at run time against
the address MicroPython's `DMA.config(write=...)` programmed. The handlers
and every address they touch are module-level, because a hard IRQ runs with
the heap locked and a nested closure over main()'s locals raises
MemoryError at its first statement -- see the "hard IRQ state" block below.

There is therefore at most one buffer of latency. If a handler
finds its buffer still marked full, the main loop did not consume it in
time: the DMA is now overwriting data the host never saw. That is counted
as an overrun and reported in a `TIME` chunk whose `dropped_samples` is the
CUMULATIVE total so far, `overruns * buf_words * samples_per_word` -- every
`TIME` chunk here carries a running total, never an increment, so a reader
takes the last value instead of summing. `FDEBUG`'s
RXSTALL bit is cleared at the start and read back at the end -- if it is
set, the RX FIFO overflowed and samples were lost regardless.

MEMORY: this script's `finally` drops the two DMA buffers (32 KB at the
default `buf_words`) and collects before the host's next command.
"""

BUF_WORDS = CFG["buf_words"]  # noqa: F821 - CFG is prepended by the host
MAX_BYTES = CFG["max_bytes"]  # noqa: F821

BUF_BYTES = 4 * BUF_WORDS
SAMPLES_PER_CHUNK = BUF_WORDS * SAMPLES_PER_WORD  # noqa: F821 - prelude


DMA_BASE = 0x50000000
DMA_CH_STRIDE = 0x40
DMA_CH_WRITE_ADDR = 0x04
DMA_CH_TRANS_COUNT = 0x08


def dma_reg(channel, offset):
    """Absolute address of one register of DMA channel `channel`."""
    return DMA_BASE + DMA_CH_STRIDE * channel + offset


def make_sampler(clk_gpio, in_count, push, rising):
    """Assemble the sampler program with its operands as closure cells.

    `clk_gpio` is the *absolute* GPIO number of the project clock, which is
    what `wait ... gpio` wants even on a relocated RP2350 window -- see the
    prelude's CLK_WAIT_GPIO note.

    `clk_gpio` and `in_count` are read inside the program body, so they
    must be cells (`LOAD_DEREF`) rather than module globals: `asm_pio`
    clears `__globals__` while it assembles. `push` is only a decorator
    argument, evaluated before that happens.
    """
    if rising:

        @rp2.asm_pio(
            in_shiftdir=rp2.PIO.SHIFT_LEFT,
            autopush=True,
            push_thresh=push,
            fifo_join=rp2.PIO.JOIN_RX,
        )
        def sampler():
            wrap_target()  # noqa: F821 - PIO assembler directives
            wait(0, gpio, clk_gpio)  # noqa: F821
            wait(1, gpio, clk_gpio)  # noqa: F821 - rising edge
            in_(pins, in_count)  # noqa: F821
            wrap()  # noqa: F821

    else:

        @rp2.asm_pio(
            in_shiftdir=rp2.PIO.SHIFT_LEFT,
            autopush=True,
            push_thresh=push,
            fifo_join=rp2.PIO.JOIN_RX,
        )
        def sampler():
            wrap_target()  # noqa: F821
            wait(1, gpio, clk_gpio)  # noqa: F821
            wait(0, gpio, clk_gpio)  # noqa: F821 - falling: outputs settled
            in_(pins, in_count)  # noqa: F821
            wrap()  # noqa: F821

    return sampler


# --- hard IRQ state ------------------------------------------------------
#
# `on_a`/`on_b` run as *hard* IRQ handlers, where the heap is locked: any
# allocation raises `MemoryError` ("Uncaught exception in IRQ callback
# handler"), and the capture dies with no samples. Measured on fpga-1 under
# `micropython.heap_lock()`:
#
#   * the handler body as a nested closure over main()'s locals raises
#     MemoryError at its *first* statement -- building the closure's cell
#     access is enough;
#   * the same body as a module-level function reading module globals runs
#     clean, and driven by a real `rp2.DMA` interrupt it re-armed
#     WRITE_ADDR and TRANS_COUNT correctly;
#   * a precomputed address is fine as a `mem32` index, but *arithmetic* on
#     one inside the handler allocates -- these are big ints on this
#     31-bit-small-int build, so 0x50000000 + x is a heap object. (The
#     negative-index trick does not help: the magnitude is still over 2^30.)
#
# Hence: every address is computed once in `main()` and stored here, and
# the handlers below do nothing but stores and small-int list updates.
MEM = machine.mem32
WR_A = 0
WR_B = 0
TC_A = 0
TC_B = 0
ADDR_A = 0
ADDR_B = 0
#: Whether each buffer holds samples the main loop has not written out yet.
FULL = [False, False]
#: Buffers the main loop failed to drain before the DMA came round again.
OVERRUNS = [0]


def on_a(_channel):
    # Re-arm channel A first: its partner's chain re-triggers it the moment
    # B completes, which can be long before the main loop gets back here.
    # Both are non-trigger aliases, so this arms the channel without
    # starting it.
    MEM[WR_A] = ADDR_A
    MEM[TC_A] = BUF_WORDS
    if FULL[0]:
        OVERRUNS[0] += 1
    FULL[0] = True


def on_b(_channel):
    MEM[WR_B] = ADDR_B
    MEM[TC_B] = BUF_WORDS
    if FULL[1]:
        OVERRUNS[0] += 1
    FULL[1] = True


def main(out):
    """Run the sampler, streaming RAW chunks to `out` until told to stop."""
    if not claim_pio(out):
        return

    sampler = make_sampler(CLK_WAIT_GPIO, IN_COUNT, PUSH_THRESH, EDGE == "rising")
    sm = new_state_machine(sampler)

    global WR_A, WR_B, TC_A, TC_B, ADDR_A, ADDR_B

    bufs = [bytearray(BUF_BYTES), bytearray(BUF_BYTES)]
    FULL[0] = False
    FULL[1] = False
    OVERRUNS[0] = 0
    dma_a = None
    dma_b = None
    poller = None
    stdin = None

    try:
        # Ctrl-C becomes an ordinary byte for the whole capture, so that a
        # stop can only ever be noticed between chunk writes and no chunk
        # can be cut short on the wire. Restored in `finally`.
        micropython.kbd_intr(-1)
        stdin = stdin_stream(sys.stdin)
        poller = select.poll()
        poller.register(stdin, select.POLLIN)

        dma_a = rp2.DMA()
        dma_b = rp2.DMA()

        ctrl_a = dma_a.pack_ctrl(
            size=2,
            inc_read=False,
            inc_write=True,
            treq_sel=DREQ,
            chain_to=dma_b.channel,
            irq_quiet=False,
        )
        ctrl_b = dma_b.pack_ctrl(
            size=2,
            inc_read=False,
            inc_write=True,
            treq_sel=DREQ,
            chain_to=dma_a.channel,
            irq_quiet=False,
        )
        dma_a.config(read=RXF_ADDR, write=bufs[0], count=BUF_WORDS, ctrl=ctrl_a, trigger=False)
        dma_b.config(read=RXF_ADDR, write=bufs[1], count=BUF_WORDS, ctrl=ctrl_b, trigger=False)

        # Every address the hard IRQ handlers touch is computed here, once,
        # and stored in a module global: see the `on_a`/`on_b` note above.
        WR_A = dma_reg(dma_a.channel, DMA_CH_WRITE_ADDR)
        WR_B = dma_reg(dma_b.channel, DMA_CH_WRITE_ADDR)
        TC_A = dma_reg(dma_a.channel, DMA_CH_TRANS_COUNT)
        TC_B = dma_reg(dma_b.channel, DMA_CH_TRANS_COUNT)
        ADDR_A = uctypes.addressof(bufs[0])
        ADDR_B = uctypes.addressof(bufs[1])

        # Self-check the register map before anything can run: config() has
        # just programmed these two write addresses, so if the offsets were
        # wrong the re-arm below would point the DMA at arbitrary memory.
        if MEM[WR_A] != ADDR_A or MEM[WR_B] != ADDR_B:
            write_time_chunk(out, 0, b"error=dma write-addr register offset mismatch")
            return

        dma_a.irq(on_a, hard=True)
        dma_b.irq(on_b, hard=True)

        MEM[FDEBUG_ADDR] = RXSTALL_BIT  # write-1-to-clear a stale stall

        # Arm the DMA before the state machine, so the first samples cannot
        # stall the RX FIFO during the call overhead between the two.
        dma_a.active(1)
        sm.active(1)

        sent = 0
        reported = 0
        index = 0
        stopping = False
        while True:
            while not FULL[index]:
                if stop_requested(poller, stdin):
                    stopping = True
                    break
                machine.idle()
            if stopping:
                break
            sent += write_raw_chunk(out, bufs[index], SAMPLES_PER_CHUNK)
            FULL[index] = False
            if OVERRUNS[0] > reported:
                # Cumulative, not an increment: see write_time_chunk().
                sent += write_time_chunk(
                    out, OVERRUNS[0] * SAMPLES_PER_CHUNK, b"overrun"
                )
                reported = OVERRUNS[0]
            index = 1 - index
            # Both stop checks are here, between whole chunks: MAX_BYTES so
            # the board can finish on its own if the host never asks, and
            # the stdin byte for when it does.
            if MAX_BYTES and sent >= MAX_BYTES:
                break
            if stop_requested(poller, stdin):
                break
    except KeyboardInterrupt:
        # Only reachable from outside the kbd_intr(-1) window: a Ctrl-C that
        # arrived in the handful of instructions before the capture armed
        # itself. Once `finally` has restored the keyboard interrupt there
        # is nothing left in this try, so a later Ctrl-C simply ends the
        # script -- which is what the host's last-resort one is for.
        pass
    finally:
        # NOTE: `micropython.kbd_intr(3)` is deliberately the LAST statement
        # of this block, not the first. Everything below writes to `out`,
        # and a multi-byte write blocks in `mp_hal_stdout_tx_strn()` waiting
        # for CDC TX space while pending handlers run -- so re-enabling the
        # keyboard interrupt any earlier puts a `KeyboardInterrupt` right
        # through the trailer write and truncates the one chunk that carries
        # the overrun and RXSTALL counts. The host's own recovery path
        # (`RawRepl.recover()`) writes a real Ctrl-C into exactly that
        # window when a slow board looks like a dead one.
        overrun_total = OVERRUNS[0]
        rxstall = (MEM[FDEBUG_ADDR] >> SM_NUM) & 1
        # Drop the IRQ handlers before aborting: an abort can raise a
        # spurious completion IRQ (and on RP2350, errata RP2350-E5, a chain
        # partner can re-trigger the aborted channel), which would inflate
        # the overrun count that was just snapshotted.
        for dma in (dma_a, dma_b):
            if dma is not None:
                dma.irq(None)
        sm.active(0)
        # Remove the sampler from the block's instruction memory so the next
        # capture does not add on top of it -- see the pre-add removal above
        # for what happens when this is skipped. Guarded: the program may
        # never have been added (e.g. StateMachine() itself raised ENOMEM).
        try:
            rp2.PIO(PIO_NUM).remove_program(sampler)
        except Exception:
            pass
        for dma in (dma_a, dma_b):
            if dma is not None:
                dma.active(0)
        for dma in (dma_a, dma_b):
            if dma is not None:
                dma.close()
        # Unconditional: with a cooperative stop there is no such thing as a
        # half-written chunk to desync the stream, so the host always gets
        # its overruns/rxstall summary -- which is the only place those
        # numbers exist.
        summary = (
            "overruns="
            + str(overrun_total)
            + " rxstall="
            + str(rxstall)
            + " sysclk_hz="
            + str(machine.freq())
        )
        write_time_chunk(out, overrun_total * SAMPLES_PER_CHUNK, summary.encode())
        if hasattr(out, "flush"):
            out.flush()
        # Hand the heap back before the host's next command. The two DMA
        # buffers are 32 KB at the default buf_words, on a board with ~80 KB
        # free -- waiting for main() to return is too late to be sure the
        # next compile has room.
        del bufs
        dma_a = None
        dma_b = None
        poller = None
        stdin = None
        gc.collect()
        # Last: from here a Ctrl-C is an exception again, and there is
        # nothing left to write that it could cut in half.
        micropython.kbd_intr(3)


_OUT = sys.stdout.buffer  # noqa: F821 - the prelude imports sys
_PROBLEM = firmware_problem()  # noqa: F821
if _PROBLEM:
    write_time_chunk(_OUT, 0, _PROBLEM)  # noqa: F821
else:
    main(_OUT)
