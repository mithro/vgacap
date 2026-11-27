# SPDX-License-Identifier: Apache-2.0
"""Clocked capture: the PIO makes every project clock and samples uo_out once
per clock, numbered from reset. `RAW ` and `TIME` chunks.

A program body: `ttcap.mp.build(cfg, "clocked_rp2.py")` puts `prelude.py`
in front of it -- read that docstring first. Run by
`ttcap.capture.ClockedSession` in the same REPL session as `load_held()`,
which leaves the design loaded with `rst_n` held at 0 and the clock pad
driven at 0 by a `machine.Pin`. Nothing between the two touches GPIO 16 or
`tt.mode`. CFG keys on top of the prelude's:

    clocks        rising edges to make, a multiple of 4; one sample each
    reset_clocks  how many of them come with rst_n low, at least 1
    pio_div       the state machine's integer clock divider (SMn_CLKDIV)
    high, low     the delays H and L of the program below, 0..15 each
    buf_words     32-bit words in each of the two buffers
    sysclk_hz     machine.freq() as the host read it; the board refuses to
                  run at any other

THE PROGRAM. One state machine on PIO_NUM makes the clock with one
side-set pin, which is not optional, so H and L are at most 15. Each clock
is H + L + 4 PIO cycles: high from the `nop` for H + 2 cycles, sampled by
`in` H + 1 cycles after the rising edge, then low for L + 2. Autopush is
off: `push iffull block` runs with the clock low, so a full RX FIFO stalls
the state machine *with the clock low*, and a slow link only lowers the
effective clock. It loses nothing. The two counts come in through the TX
FIFO, so the FIFOs are not joined.

WHICH SAMPLE IS WHICH. Sample k is uo_out in the high phase after rising
edge k, which is trace sample 2k+1 under docs/trace-format.md's one
clock-numbering rule. Edges 0 to reset_clocks - 1 come with rst_n low. The
`irq wait 0` after them holds the clock low while this script releases
reset and reads rst_n back as 1, and only then clears the flag. After
exactly `clocks` edges the program parks with the clock low.

DMA. One channel, paced by the RX DREQ, no IRQs: the main loop polls it,
re-arms it into the other buffer (`trigger=True`) before writing anything,
and only then writes the full buffer out as a `RAW ` chunk. A late re-arm
only stalls the PIO, low. `overruns` counts a buffer armed while still
marked full, and must stay 0. `stalled_buffers` counts buffers during
which FDEBUG.RXSTALL was set; it is information only and is non-zero on
almost every buffer.

The clock pad is checked through IO_BANK0's GPIOn_CTRL before reset is
released and after every buffer: its FUNCSEL must still be this PIO block.
The manual-clock monitor's `clock_project_once()`, for one, takes it to SIO.

OUTPUT, all `TIME` and `RAW ` chunks:

    clocked clocks=N reset_clocks=R pio_div=D cycles_per_clock=C   (first)
    error=...                                       (on any failure)
    samples=S stalled_buffers=B overruns=O status=ok|stopped|error
        sysclk_hz=F elapsed_ms=T                                    (last)

`cycles_per_clock` is H + L + 4, in PIO cycles: the clock is
sysclk / (pio_div * cycles_per_clock). `status=error` always follows an
`error=` chunk. The SDK's loggers are silenced for the whole run, so that
nothing the SDK prints can land inside the chunk stream; a host stop byte
ends the run between buffers with `status=stopped`.

TEARDOWN, in the agreed order: `sm.active(0)`, then `sm.exec("nop()
.side(0)")`, so that a stop mid-clock leaves the clock low -- the other
order lets the running program raise it again in between -- then the
program removed, the IRQ flag cleared, the DMA stopped, the closing
record, the buffers dropped and `kbd_intr(3)` last.
"""

import time

CLOCKS = CFG["clocks"]  # noqa: F821 - CFG is prepended by the host
RESET_CLOCKS = CFG["reset_clocks"]  # noqa: F821
PIO_DIV = CFG["pio_div"]  # noqa: F821
HIGH = CFG["high"]  # noqa: F821
LOW = CFG["low"]  # noqa: F821
BUF_WORDS = CFG["buf_words"]  # noqa: F821

CYCLES = HIGH + LOW + 4
IRQ_ADDR = PIO_BASE + 0x30  # noqa: F821 - the prelude's
CLKDIV_ADDR = PIO_BASE + 0xC8 + 0x18 * SM_NUM  # noqa: F821
#: IO_BANK0 GPIOn_CTRL on RP2350 (the only chip the host sends this to),
#: and the FUNCSEL of this PIO block: PIO0 is 6.
CLK_CTRL = 0x40028000 + 8 * CLK_GPIO + 4  # noqa: F821
FUNCSEL_PIO = 6 + PIO_NUM  # noqa: F821
#: How long the reset edges may take before the handshake is given up, and
#: how long rst_n may take to read 1 after the release.
HANDSHAKE_MS = 1000 + RESET_CLOCKS * PIO_DIV * CYCLES * 1000 // SYSCLK_HZ  # noqa: F821
RST_N_MS = 100


def make_clocked(high, low, count):
    """Assemble the program, its operands closure cells (see the prelude)."""

    @rp2.asm_pio(sideset_init=rp2.PIO.OUT_LOW, in_shiftdir=rp2.PIO.SHIFT_LEFT,
                 autopush=False, push_thresh=32)
    def clocked():
        pull(block).side(0)  # noqa: F821 - PIO assembler names
        out(x, 32).side(0)  # noqa: F821 - x = reset_clocks - 1
        pull(block).side(0)  # noqa: F821
        out(y, 32).side(0)  # noqa: F821 - y = clocks - reset_clocks - 1
        label("reset")  # noqa: F821
        nop().side(1)[high]  # noqa: F821 - clock high; outputs settle
        in_(pins, count).side(1)  # noqa: F821 - sample k, late in the high phase
        push(iffull, block).side(0)[low]  # noqa: F821 - low; a full FIFO stalls here
        jmp(x_dec, "reset").side(0)  # noqa: F821
        irq(block, 0).side(0)  # noqa: F821 - held low while rst_n is released
        label("run")  # noqa: F821
        nop().side(1)[high]  # noqa: F821
        in_(pins, count).side(1)  # noqa: F821
        push(iffull, block).side(0)[low]  # noqa: F821
        jmp(y_dec, "run").side(0)  # noqa: F821
        label("done")  # noqa: F821
        jmp("done").side(0)  # noqa: F821 - exactly `clocks` edges, then low

    return clocked


def write_raw_chunk(out, buf, nbytes, samples):
    """Emit one `RAW ` chunk of `buf`'s first `nbytes`; return its length."""
    written = _write_all(out, b"RAW " + _le32(4 + nbytes) + _le32(samples))  # noqa: F821
    return written + _write_all(out, memoryview(buf)[:nbytes])  # noqa: F821


def pad_ours():
    """Whether the clock pad's FUNCSEL is still this PIO block."""
    return machine.mem32[CLK_CTRL] & 0x1F == FUNCSEL_PIO  # noqa: F821


def quiet_sdk():
    """Raise every SDK logger above ERROR; return what puts them back."""
    try:
        import ttboard.log as sdk_log
    except ImportError:
        return None
    saved = (sdk_log, sdk_log.DefaultLogLevel,
             [(g, g.loglevel) for g in sdk_log.RPLoggers.values()])
    sdk_log.DefaultLogLevel = 99
    for g in sdk_log.RPLoggers.values():
        g.loglevel = 99
    return saved


def loud_sdk(saved):
    """Put back the logger levels `quiet_sdk` changed."""
    if saved:
        saved[0].DefaultLogLevel = saved[1]
        for g, level in saved[2]:
            g.loglevel = level


def wait_ms(done, ms):
    """Poll `done()` for up to `ms` milliseconds; return its last answer."""
    start = time.ticks_ms()
    while not done():
        if time.ticks_diff(time.ticks_ms(), start) > ms:
            return False
        machine.idle()  # noqa: F821
    return True


def main(out):
    """Clock and sample the design `CLOCKS` times; see the docstring."""
    write_time_chunk(out, 0, (  # noqa: F821 - the prelude's
        "clocked clocks=%d reset_clocks=%d pio_div=%d cycles_per_clock=%d"
        % (CLOCKS, RESET_CLOCKS, PIO_DIV, CYCLES)).encode())
    status = "error"
    samples = stalled = overruns = 0
    sm = prog = dma = bufs = poller = stdin = logs = None
    start = time.ticks_ms()
    error = None
    try:
        freq = machine.freq()  # noqa: F821
        if freq != SYSCLK_HZ:  # noqa: F821
            error = "error=sysclk_hz %d is not the %d the clock was worked out for" % (
                freq, SYSCLK_HZ)  # noqa: F821
            return
        if not claim_pio(out):  # noqa: F821 - wrote its own error= chunk
            return
        logs = quiet_sdk()
        micropython.kbd_intr(-1)  # noqa: F821 - restored last, in finally
        stdin = stdin_stream(sys.stdin)  # noqa: F821
        poller = select.poll()  # noqa: F821
        poller.register(stdin, select.POLLIN)  # noqa: F821
        bufs = [bytearray(4 * BUF_WORDS), bytearray(4 * BUF_WORDS)]
        full = [False, False]
        prog = make_clocked(HIGH, LOW, IN_COUNT)  # noqa: F821
        # The prelude's new_state_machine, with the side-set pin: dropping
        # the block's stale programs first. PIO_NUM is never 0 here (the
        # RP2350 profile refuses it), so the stock firmware's are safe.
        try:
            rp2.PIO(PIO_NUM).remove_program()  # noqa: F821
        except Exception:
            pass
        sm = rp2.StateMachine(PIO_NUM * 4 + SM_NUM, prog,  # noqa: F821 - takes the clock pad, low
                              in_base=machine.Pin(IN_BASE),  # noqa: F821
                              sideset_base=machine.Pin(CLK_GPIO))  # noqa: F821
        machine.mem32[CLKDIV_ADDR] = PIO_DIV << 16  # noqa: F821
        machine.mem32[FDEBUG_ADDR] = RXSTALL_BIT  # noqa: F821 - a stale stall
        machine.mem32[IRQ_ADDR] = 1  # noqa: F821 - a stale flag 0 would release reset early
        dma = rp2.DMA()  # noqa: F821
        ctrl = dma.pack_ctrl(size=2, inc_read=False, inc_write=True, treq_sel=DREQ,  # noqa: F821
                              irq_quiet=True)
        left = CLOCKS // 4
        words = min(BUF_WORDS, left)
        dma.config(read=RXF_ADDR, write=bufs[0], count=words, ctrl=ctrl, trigger=True)  # noqa: F821
        sm.put(RESET_CLOCKS - 1)
        sm.put(CLOCKS - RESET_CLOCKS - 1)
        start = time.ticks_ms()
        sm.active(1)
        if not wait_ms(lambda: machine.mem32[IRQ_ADDR] & 1, HANDSHAKE_MS):  # noqa: F821
            error = "error=no reset handshake"
            return
        if not pad_ours():
            error = "error=clock pad is not on PIO%d before reset release" % PIO_NUM  # noqa: F821
            return
        tt.reset_project(False)  # noqa: F821 - tt is the SDK's DemoBoard
        if not wait_ms(lambda: tt.rst_n(), RST_N_MS):  # noqa: F821
            error = "error=rst_n stuck low"
            return
        machine.mem32[IRQ_ADDR] = 1  # noqa: F821 - the clock runs from here
        index = 0
        while True:
            while dma.active():
                if stop_requested(poller, stdin):  # noqa: F821
                    status = "stopped"
                    return
                machine.idle()  # noqa: F821
            full[index] = True
            left -= words
            got = words
            if left:
                words = min(BUF_WORDS, left)
                if full[1 - index]:
                    overruns += 1
                dma.config(write=bufs[1 - index], count=words, trigger=True)
            if machine.mem32[FDEBUG_ADDR] & RXSTALL_BIT:  # noqa: F821
                stalled += 1
                machine.mem32[FDEBUG_ADDR] = RXSTALL_BIT  # noqa: F821
            if not pad_ours():
                error = "error=clock pad was taken from PIO%d mid-run" % PIO_NUM  # noqa: F821
                return
            write_raw_chunk(out, bufs[index], 4 * got, 4 * got)
            samples += 4 * got
            full[index] = False
            if not left:
                status = "ok"
                return
            index = 1 - index
            if stop_requested(poller, stdin):  # noqa: F821
                status = "stopped"
                return
    except KeyboardInterrupt:
        error = "error=interrupted"
    except Exception as e:  # noqa: BLE001 - reported in the stream
        error = "error=%s: %s" % (type(e).__name__, e)
    finally:
        if sm is not None:
            sm.active(0)
            sm.exec("nop() .side(0)")  # after active(0): nothing can raise it again
        try:
            rp2.PIO(PIO_NUM).remove_program(prog)  # noqa: F821
        except Exception:  # noqa: BLE001 - never added
            pass
        machine.mem32[IRQ_ADDR] = 1  # noqa: F821
        stop_dma((dma,))  # noqa: F821
        if error:
            write_time_chunk(out, 0, error.encode())  # noqa: F821
            status = "error"
        write_time_chunk(out, 0, (  # noqa: F821
            "samples=%d stalled_buffers=%d overruns=%d status=%s sysclk_hz=%d elapsed_ms=%d"
            % (samples, stalled, overruns, status, machine.freq(),  # noqa: F821
               time.ticks_diff(time.ticks_ms(), start))).encode())
        if hasattr(out, "flush"):
            out.flush()
        loud_sdk(logs)
        bufs = dma = poller = stdin = None
        gc.collect()  # noqa: F821
        micropython.kbd_intr(3)  # noqa: F821 - last


_OUT = sys.stdout.buffer  # noqa: F821 - the prelude imports sys
_PROBLEM = firmware_problem()  # noqa: F821
if _PROBLEM:
    write_time_chunk(_OUT, 0, _PROBLEM)  # noqa: F821
    write_time_chunk(_OUT, 0, (  # noqa: F821
        "samples=0 stalled_buffers=0 overruns=0 status=error sysclk_hz=%d elapsed_ms=0"
        % machine.freq()).encode())  # noqa: F821
else:
    main(_OUT)
