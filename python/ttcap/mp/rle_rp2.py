# SPDX-License-Identifier: Apache-2.0
"""Continuous capture, run-length encoded by the PIO, as `RLE ` chunks.

A program body: `ttcap.mp.build(cfg, "rle_rp2.py")` puts `prelude.py` in
front of it -- read that docstring first. Keys this body reads on top of
the prelude's:

    buf_words      32-bit words per DMA buffer (two are allocated); even,
                   so a buffer is whole (value, run) pairs
    max_bytes      stop after this many emitted bytes; 0 = until stopped
    oneshot        1 = fill the two buffers once, back to back, stop the
                   state machine and send them: buf_words pairs in one
                   unbroken stretch at any clock, however busy the
                   picture, for a link that could never stream it
    clock_hz       the project clock the host programmed
    sample_delay   system clocks after the sampled edge before the first
                   read (frame_rp2.py's SAMPLE POINT)
    style          "locked", "edge" or "auto" -- see TWO STYLES
    mask           1 = read the sample with one `mov y, pins` under
                   SHIFTCTRL.IN_COUNT (RP2350 only), 0 = two instructions
                   through the OSR (any RP2), -1 = 1 on an RP2350

Output: `RLE ` chunks (u32 value, u32 run pairs, `stream.h`) and `TIME`
chunks. The values are the sampled window, exactly what a `RAW ` sample of
the same board holds, so a host expands the runs into the samples a RAW
capture would have given.

THE PROGRAM. Per project clock the state machine reads the pins once and
compares them with the run's value; equal adds one to the run, different
pushes the run and starts a new one. Registers: X = the run's value, Y =
scratch, ISR = the run counter, OSR = the pins on their way to Y (generic
form only). The counter lives in the ISR because `jmp x!=y` needs both
values in X and Y, and `jmp y--` is the only arithmetic: it is fetched
inverted, decremented and stored back inverted, i.e. incremented -- and a
`push` leaves the ISR at 0, so starting a run costs nothing:

    W:  mov isr, ~y          store the counter          (equal path)
    S:  mov y, pins          THE SAMPLE                 (wrap target)
    C:  jmp x!=y chg
    L:  mov y, ~isr
    D:  jmp y--, W           Y = 0 only when the counter is all ones
        push noblock         saturated: 2^32 - 1 + 1 samples so far,
        in x, 32             push it, push the value again, count on [wrap]
    chg: push noblock        the run, minus one
        in y, 32             the new value (autopush at 32 bits), ISR = 0
        mov x, y
        jmp S

So the words come out as `v0, r0, v1, r1, ...` with each r one less than
the run (the change sample is the new run's first and is not counted by
the loop), and a DMA buffer of an even number of words is already an
`RLE ` payload except for that one: `fix_runs()` adds it in viper. A run
word of all ones (the counter saturated, at 2^32 samples) becomes 0 and is
sent as two pairs of 2^31.

Every path from one S to the next is 5 or 6 instructions: equal W S C L D
= 5, change S C push in mov jmp = 6, saturation S C L D push in = 6. The
generic form adds `out y, n` after a `mov osr, pins` (6 and 7): on RP2040
`mov y, pins` reads all 32 pins of the window, uio and the ui_in pads
included, which would make spurious runs (harmless to the values, which
pass through `out` cleanly, but they cost pairs).

TWO STYLES. "locked" is frame_rp2.py's PHASE-LOCKED SAMPLING: it waits for
one clock edge and then relies on sysclk being P whole project clocks,
with delays padding every path to exactly P cycles, so the sample is never
early, late, missed or doubled -- the emulator test checks this on whole
frames of every calibration pattern. P must be at least the longest path:
6 (masked) or 7 (generic). "edge" waits for each clock edge instead (two
`wait` instructions on top), which needs no ratio but a longer clock
(EDGE_MIN_PERIOD below, measured on the emulator, plus sample_delay: the
second wait's delay is spent on every clock); it is what a reduced
streaming clock with a fractional divider needs. "auto" takes locked when
the ratio is exactly whole (sysclk == P * clock_hz: 0.4% off drifts a
sample every 250 clocks) and in range, edge otherwise.

RX STALLS ARE FATAL. A `push noblock` to a full RX FIFO drops the word,
a value or a run, which puts every later pair out of step; and the next
value's autopush stalls the state machine anyway, so the locked phase is
lost too. Either sets FDEBUG.RXSTALL (RP2040 datasheet, FDEBUG). So the
main loop polls it, and at the first stall sends an `error=rxstall` TIME
chunk and stops without sending the buffer in hand -- even one that was
complete before the stall, which the loop cannot tell apart; every chunk
before it was complete, and checked, before it was sent. The summary's
rxstall=1 makes `ttcap capture --mode rle` exit non-zero.

DMA, overruns, the cooperative stop and the hard-IRQ rules are
capture_rp2.py's, repeated here rather than shared so that neither script
grows by the other's code on a board that compiles each in ~80 KB. So an
overrun is counted when the chain starts on a buffer still FULL, which is
marked TORN, and the `overrun` TIME chunk follows the torn chunk itself --
under RLE a torn chunk is worse than a gap: the new pairs in it are one
short (fix_runs has already run) and spliced onto old ones; vgacap-frames
and vgadecode drop the picture in progress at the marker. fix_runs works
in place, so a tear that began before or during it may have added 1 to
new run words, which the next fill's fix adds 1 to again: a buffer torn
by the time its fix is done keeps TORN past its send, so that next chunk
gets a marker too. (Copying before the fix needs a third buffer the heap
cannot spare, and fixing on the host would change the wire format.) An overrun's
lost samples cannot be counted from pairs, so `dropped_samples` is only
an estimate, one sample per pair of each counted buffer: a pair is often
many samples, and a tear that never overtook the send lost none.
"""

BUF_WORDS = CFG["buf_words"]  # noqa: F821 - CFG is prepended by the host
MAX_BYTES = CFG["max_bytes"]  # noqa: F821
CLOCK_HZ = CFG["clock_hz"]  # noqa: F821
SAMPLE_DELAY = CFG["sample_delay"]  # noqa: F821
STYLE = CFG["style"]  # noqa: F821
ONESHOT = CFG["oneshot"]  # noqa: F821
MASK = CFG["mask"]  # noqa: F821

BUF_BYTES = 4 * BUF_WORDS
PAIRS_PER_BUF = BUF_WORDS // 2

#: Shortest project clock, in system clocks, each style is exact at: the
#: longest path (locked), measured on the emulator's sweep (edge, at
#: sample_delay 0; each cycle of delay adds one, as every loop waits it).
LOCKED_MIN_PERIOD = (7, 6)  # indexed by mask
EDGE_MIN_PERIOD = (9, 8)
#: A delay field holds 31, and the entry path is 4 instructions long.
LOCKED_MAX_PERIOD = 35

#: DMA channel n's WRITE_ADDR (CHn + 0x04); TRANS_COUNT is the next word.
#: Non-trigger aliases both, as in capture_rp2.py.
DMA_WRITE_ADDR0 = 0x50000004
SHIFTCTRL_SET = PIO_BASE + 0x2000 + 0x0D0 + 0x18 * SM_NUM  # noqa: F821 - prelude


def make_rle(clk_gpio, clk_act, delay, period, masked, in_count, locked):
    """Assemble the run-length program; operands are closure cells (see
    the prelude on asm_pio's globals swap). `period` is P for "locked";
    "edge" ignores it. `clk_gpio` is taken mod 32 as frame_rp2.py does."""
    clk_gpio &= 31
    # Padding so each path is P cycles in "locked"; 0 in "edge". Path
    # lengths (cycles from S to the next S, without padding): equal 5,
    # change 6, saturation 6, entry 4 -- one more each when not masked.
    pad_long = period - 7 + masked if locked else 0
    pad_eq = pad_long + locked  # `locked` is a bool: + 1 or + 0
    pad_entry = pad_long + 2 * locked

    # Both run inside rle() while asm_pio has swapped the globals, so
    # the mnemonics resolve there, as rle()'s own do.
    def edge():
        wait(1 - clk_act, gpio, clk_gpio)  # noqa: F821 - the idle level
        wait(clk_act, gpio, clk_gpio)[delay]  # noqa: F821

    def sample():
        if masked:
            mov(y, pins)  # noqa: F821 - THE SAMPLE
        else:
            mov(osr, pins)  # noqa: F821 - THE SAMPLE
            out(y, in_count)  # noqa: F821

    # The ISR shifts left (the default) with autopush at 32 (also the
    # default threshold); the OSR shifts right so `out` takes the low pins.
    @rp2.asm_pio(  # noqa: F821
        out_shiftdir=rp2.PIO.SHIFT_RIGHT,  # noqa: F821
        autopush=True,
        fifo_join=rp2.PIO.JOIN_RX,  # noqa: F821
    )
    def rle():
        edge()
        sample()
        in_(y, 32)  # noqa: F821 - v0, autopushed; ISR = 0
        mov(x, y)  # noqa: F821
        jmp("S")[pad_entry]  # noqa: F821
        label("W")  # noqa: F821
        mov(isr, invert(y))[pad_eq]  # noqa: F821 - counter += 1
        wrap_target()  # noqa: F821
        label("S")  # noqa: F821
        if not locked:
            edge()
        sample()
        jmp(x_not_y, "chg")  # noqa: F821
        mov(y, invert(isr))  # noqa: F821
        jmp(y_dec, "W")  # noqa: F821
        push(noblock)  # noqa: F821 - saturated: 2^32 samples
        in_(x, 32)[pad_long]  # noqa: F821 - same value again, ISR = 0
        wrap()  # noqa: F821
        label("chg")  # noqa: F821
        push(noblock)  # noqa: F821 - the run, minus one
        in_(y, 32)  # noqa: F821 - the new value; ISR = 0
        mov(x, y)  # noqa: F821
        jmp("S")[pad_long]  # noqa: F821

    return rle


@micropython.viper  # noqa: F821 - the prelude imports micropython
def fix_runs(buf, words: int) -> int:
    """Add the one the PIO leaves out of every run word (odd indices), in
    place; return how many became 0, i.e. were 2^32 (saturated) runs."""
    p = ptr32(buf)  # noqa: F821 - a viper builtin
    zeros = 0
    i = 1
    while i < words:
        p[i] = p[i] + 1
        if p[i] == 0:
            zeros += 1
        i += 2
    return zeros


def write_rle_chunk(out, buf, pairs):
    """Emit one `RLE ` chunk of the first `pairs` pairs of `buf`."""
    head = b"RLE " + _le32(4 + 8 * pairs) + _le32(pairs)  # noqa: F821 - prelude
    return _write_all(out, head) + _write_all(out, memoryview(buf)[: 8 * pairs])  # noqa: F821


def write_pairs(out, buf):
    """Emit one fixed buffer, splitting each 2^32 run into two of 2^31."""
    view = memoryview(buf)
    sent = start = 0
    for pair in range(PAIRS_PER_BUF):
        at = 8 * pair
        if any(buf[at + 4 : at + 8]):
            continue
        if pair > start:
            sent += write_rle_chunk(out, view[8 * start :], pair - start)
        half = buf[at : at + 4] + _le32(0x80000000)  # noqa: F821
        sent += write_rle_chunk(out, half + half, 2)
        start = pair + 1
    if start < PAIRS_PER_BUF:
        sent += write_rle_chunk(out, view[8 * start :], PAIRS_PER_BUF - start)
    return sent


# --- hard IRQ state: see capture_rp2.py ----------------------------------
MEM = machine.mem32  # noqa: F821
WR_A = 0
WR_B = 0
TC_A = 0
TC_B = 0
ADDR_A = 0
ADDR_B = 0
FULL = [False, False]
TORN = [False, False]
OVERRUNS = [0]


def on_a(_channel):
    MEM[WR_A] = ADDR_A
    MEM[TC_A] = BUF_WORDS
    if FULL[1]:  # A's chain has just started B on buffer 1
        OVERRUNS[0] += 1
        TORN[1] = True
    FULL[0] = True


def on_b(_channel):
    MEM[WR_B] = ADDR_B
    MEM[TC_B] = BUF_WORDS
    if FULL[0] and not ONESHOT:  # a oneshot's B chains to the stopper
        OVERRUNS[0] += 1
        TORN[0] = True
    FULL[1] = True


def choose(sysclk, masked):
    """(locked, period) for this clock, or None when no style is exact."""
    period = (sysclk + CLOCK_HZ // 2) // CLOCK_HZ
    # Exactly whole, or the padded loop drifts against the clock.
    whole = sysclk == period * CLOCK_HZ
    if STYLE != "edge" and whole and LOCKED_MIN_PERIOD[masked] <= period <= LOCKED_MAX_PERIOD:
        return True, period
    if STYLE == "locked":
        return None
    # Edge style: the shortest clock it may see (a fractional divider's
    # short period is one system clock under the average), less the delay
    # every loop spends after the edge.
    if sysclk // CLOCK_HZ - (0 if whole else 1) - SAMPLE_DELAY < EDGE_MIN_PERIOD[masked]:
        return None
    return False, period


def main(out):
    """Run the RLE program, streaming RLE chunks to `out` until stopped."""
    sysclk = machine.freq()  # noqa: F821
    # -1: the masked form wherever IN_COUNT exists, i.e. on an RP2350.
    masked = MASK if MASK >= 0 else int(hasattr(rp2.PIO, "gpio_base"))  # noqa: F821
    picked = choose(sysclk, masked)
    if picked is None:
        msg = "error=no exact rle style: sysclk %d clock %d style=%s mask=%d delay=%d"
        write_time_chunk(out, 0, (msg % (sysclk, CLOCK_HZ, STYLE, masked, SAMPLE_DELAY)).encode())  # noqa: F821
        return
    locked, period = picked
    if not claim_pio(out):  # noqa: F821 - prelude
        return

    program = make_rle(
        CLK_WAIT_GPIO, 1 if EDGE == "rising" else 0, SAMPLE_DELAY, period,  # noqa: F821
        masked, IN_COUNT, locked,  # noqa: F821
    )
    sm = new_state_machine(program)  # noqa: F821 - prelude
    if masked:
        # StateMachine() wrote SHIFTCTRL with IN_COUNT = 0 (32 pins); set
        # it through the atomic SET alias and read it back.
        MEM[SHIFTCTRL_SET] = IN_COUNT  # noqa: F821
        if MEM[SHIFTCTRL_SET - 0x2000] & 0x1F != IN_COUNT:  # noqa: F821
            write_time_chunk(out, 0, b"error=SHIFTCTRL.IN_COUNT not set")  # noqa: F821
            return

    global WR_A, WR_B, TC_A, TC_B, ADDR_A, ADDR_B

    bufs = [bytearray(BUF_BYTES), bytearray(BUF_BYTES)]
    FULL[0] = FULL[1] = TORN[0] = TORN[1] = False
    OVERRUNS[0] = 0
    dma_a = dma_b = dma_s = poller = stdin = None
    saturated = 0

    try:
        micropython.kbd_intr(-1)  # noqa: F821 - see the prelude: cooperative stop
        stdin = stdin_stream(sys.stdin)  # noqa: F821
        poller = select.poll()  # noqa: F821
        poller.register(stdin, select.POLLIN)  # noqa: F821

        dma_a = rp2.DMA()  # noqa: F821
        dma_b = rp2.DMA()  # noqa: F821
        after_b = dma_a.channel
        if ONESHOT:
            # frame_rp2.py's stopper: B chains to a one-word unpaced copy
            # of this state machine's bit into PIO CTRL's clear alias, so
            # it stops a few cycles after the last word and RXSTALL keeps
            # meaning a word lost inside the stretch.
            dma_s = rp2.DMA()  # noqa: F821
            stop_word = bytearray(4)
            stop_word[0] = 1 << SM_NUM  # noqa: F821
            dma_s.config(
                read=stop_word, write=PIO_BASE + 0x3000, count=1,  # noqa: F821
                ctrl=dma_s.pack_ctrl(size=2, inc_read=False, inc_write=False, treq_sel=0x3F,
                                     chain_to=dma_s.channel, irq_quiet=True),
            )
            after_b = dma_s.channel
        for dma, buf, to in ((dma_a, bufs[0], dma_b.channel), (dma_b, bufs[1], after_b)):
            ctrl = dma.pack_ctrl(size=2, inc_read=False, inc_write=True, treq_sel=DREQ,  # noqa: F821
                                 chain_to=to, irq_quiet=False)
            dma.config(read=RXF_ADDR, write=buf, count=BUF_WORDS, ctrl=ctrl)  # noqa: F821
        WR_A = DMA_WRITE_ADDR0 + 0x40 * dma_a.channel
        WR_B = DMA_WRITE_ADDR0 + 0x40 * dma_b.channel
        TC_A = WR_A + 4
        TC_B = WR_B + 4
        ADDR_A = uctypes.addressof(bufs[0])  # noqa: F821
        ADDR_B = uctypes.addressof(bufs[1])  # noqa: F821
        if MEM[WR_A] != ADDR_A or MEM[WR_B] != ADDR_B:
            write_time_chunk(out, 0, b"error=dma write-addr mismatch")  # noqa: F821
            return
        dma_a.irq(on_a, hard=True)
        dma_b.irq(on_b, hard=True)

        MEM[FDEBUG_ADDR] = RXSTALL_BIT  # noqa: F821
        report = "rle style=%s period=%d mask=%d" % (("edge", "locked")[locked], period, masked)
        sent = write_time_chunk(out, 0, report.encode())  # noqa: F821
        dma_a.active(1)
        sm.active(1)

        index = 0
        stopping = False
        while True:
            while not FULL[index] and not MEM[FDEBUG_ADDR] & RXSTALL_BIT:  # noqa: F821
                if stop_requested(poller, stdin):  # noqa: F821
                    stopping = True
                    break
                machine.idle()  # noqa: F821
            if MEM[FDEBUG_ADDR] & RXSTALL_BIT:  # noqa: F821 - see RX STALLS
                write_time_chunk(out, OVERRUNS[0] * PAIRS_PER_BUF, b"error=rxstall")  # noqa: F821
                break
            if stopping:
                break
            buf = bufs[index]
            zeros = fix_runs(buf, BUF_WORDS)
            torn = TORN[index]  # already torn: the fix may have hit new words
            if zeros:
                saturated += zeros
                sent += write_pairs(out, buf)
            else:
                sent += write_rle_chunk(out, buf, PAIRS_PER_BUF)
            FULL[index] = False  # first, so a tear in the send is seen below
            if TORN[index]:
                TORN[index] = torn  # set: the next chunk is fixed twice, mark it too
                sent += write_time_chunk(out, OVERRUNS[0] * PAIRS_PER_BUF, b"overrun")  # noqa: F821
            index = 1 - index
            if ONESHOT and index == 0:
                break
            if MAX_BYTES and sent >= MAX_BYTES:
                break
            if stop_requested(poller, stdin):  # noqa: F821
                break
    except KeyboardInterrupt:
        pass
    finally:
        # Same order and reasons as capture_rp2.py's finally; kbd_intr(3)
        # stays the very last statement.
        overrun_total = OVERRUNS[0]
        rxstall = (MEM[FDEBUG_ADDR] >> SM_NUM) & 1  # noqa: F821
        for dma in (dma_a, dma_b):
            if dma is not None:
                dma.irq(None)
        sm.active(0)
        try:
            rp2.PIO(PIO_NUM).remove_program(program)  # noqa: F821
        except Exception:
            pass
        stop_dma((dma_a, dma_b, dma_s))  # noqa: F821 - prelude: EN off, then abort
        summary = "overruns=%d rxstall=%d saturated=%d sysclk_hz=%d" % (
            overrun_total, rxstall, saturated, machine.freq()  # noqa: F821
        )
        write_time_chunk(out, overrun_total * PAIRS_PER_BUF, summary.encode())  # noqa: F821
        if hasattr(out, "flush"):
            out.flush()
        del bufs
        dma_a = dma_b = dma_s = poller = stdin = None
        gc.collect()  # noqa: F821
        micropython.kbd_intr(3)  # noqa: F821


_OUT = sys.stdout.buffer  # noqa: F821
_PROBLEM = firmware_problem()  # noqa: F821
if _PROBLEM:
    write_time_chunk(_OUT, 0, _PROBLEM)  # noqa: F821
else:
    main(_OUT)
