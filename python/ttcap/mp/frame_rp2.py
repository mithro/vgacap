# SPDX-License-Identifier: Apache-2.0
"""Whole-frame and windowed capture at the project's real clock.

A program body: `ttcap.mp.build(cfg, "frame_rp2.py")` puts `prelude.py` in
front of it (imports, shared CFG keys, registers, chunk writers,
`set_gpio_base()`, `init_input_pins()`, cooperative stop) -- read that
docstring first. Keys this body reads on top of the prelude's:

    sample_delay   system clocks to wait after the sampled project-clock
                   edge (`edge`, from the prelude) before reading the pins:
                   moves the sample point into the window where uo_out is
                   stable (see SAMPLE POINT)
    clock_hz       the project clock the host programmed
    cpl            clocks per line of the VGA mode (800 for 640x480@60); a
                   multiple of samples_per_word, so a line is whole words
    region_first   first frame line of the picture region (line 0 is the
                   first line of the vsync pulse, as libvgaframe counts)
    region_lines   lines in the region
    window_lines   most lines one capture may take; 0 = as many as the
                   heap allows. Smaller forces windowed capture.
    hsync_active   level of uo_out[7] during the hsync pulse, 0 or 1;
                   -1 = measure it on the board
    vsync_active   the same for uo_out[3]
    max_buffers    most heap blocks (whole lines each) one capture may use
    max_frames     stop after this many pictures; 0 = until stopped
    max_bytes      stop after this many emitted bytes; 0 = until stopped

Why: streaming every sample over USB tops out near 750 kHz on RP2350
(60 kHz on RP2040), so a design can only be seen slowed down. Here the
project runs at its real clock, the PIO samples one window of one frame
into SRAM, and the window is sent between frames while the frames that
pass meanwhile are skipped. Output is `FRAM` chunks (and `TIME` chunks):

    FRAM  u32 frame_counter, u16 first_line, u16 line_count,
          u32 clocks_per_line, u32 sample_count, then the packed words

`frame_counter` names the *picture*: every window of one sweep over the
region shares it, and libvgaframe's coverage map assembles them, emitting
the picture once every line is covered. With the heap for the whole region
one capture is one picture, all from one frame. With less (an RP2040, or
`window_lines`), a picture comes from consecutive captured frames, which is
exact for a static or frame-periodic design and torn for anything else.

THE TRIGGER, in PIO. The program pulls the number of lines to skip, waits
for the vsync leading edge, counts that many hsync leading edges, and then
runs the same three-instruction sampler as `capture_rp2.py`. So a window
starts at an hsync leading edge, which is where libvgaframe puts x = 0, and
`first_line` is exactly the number of edges counted. Measured from the
RTL: the calibration designs move vsync on the clock of an hsync leading
edge, so after the vsync edge the next hsync leading edge begins line 1.
Starting a sample late (the edge is seen through the input synchroniser
at 5 system clocks a pixel) only shifts the window's first line, and
libvgaframe re-places x at every hsync trailing edge it sees after that --
which is why the default region starts a line above the active area.

SAMPLE POINT. At a real VGA clock there are only 4-5 system clocks per
pixel (the SDK runs the RP2350 at 100 MHz for a 25 MHz project clock), and
the design's uo_out changes a clock-to-output delay after each rising
edge -- through the FPGA, the pads and the input synchroniser -- which on
fpga-4 put the transition right at the falling edge: 1.4% of the bars
picture's pixels came back with stray bits. So the edge and a delay of
whole system clocks after it are CFG. Swept on fpga-4 at 25 MHz (100 MHz
system clock, 4 cycles a pixel, 2026-11-24): falling+0 1.4% wrong,
falling+1 exact, rising+0 exact, rising+1 10% wrong; the host defaults to
rising+0, the middle of that window.

PHASE-LOCKED SAMPLING. The SDK always picks a jitter-free system clock, a
whole multiple of the project clock (measured on fpga-4: 4x at 25 and
31.5 MHz, 2x at 36 and 40 MHz; 28.322 MHz is given as 28.25 = 113 / 4), so
the PWM that makes the project clock is phase-locked to the PIO. The
sampler therefore syncs to one clock edge after the trigger and then runs
free: `in pins [P-1]`, one sample every P system clocks, P = sysclk /
clock. A wait-for-edge loop needs 3 instructions a sample and could not
keep up at 2 clocks a pixel (800x600 at 40 MHz); a delay past 4 - 3 made
it skip samples silently. main() refuses a ratio that is not whole (a
fractional PWM would drift) or outside 2..32 (the delay field's reach;
slower clocks are what stream mode is for).

HEAP. One frame of 640x480 is 420,000 samples, and fpga-1 has ~414 KB free
but in pieces: the largest block measured 142 KB (2026-11-24). So a
capture fills a list of buffers, each a whole number of lines, allocated
largest-first. A DMA channel per buffer ran out of channels first: on
fpga-4, 15 buffers (all 16 channels with the stopper) reached 473 of the
481 lines. So two channels do it with CONTROL BLOCKS: a control channel
copies a 4-word block (READ_ADDR, WRITE_ADDR, TRANS_COUNT, CTRL_TRIG) from
a table into the data channel's alias-0 registers, which starts it; the
data channel chains back to the control channel for the next block. The
capture is one unbroken sample stream, so it is sent as ONE FRAM chunk --
one header, then each buffer's bytes in order (a chunk per buffer would
restart libvgaframe's line position at every buffer).

STOPPING THE SAMPLER. The table's last block turns the data channel into a
"stopper": one word, this state machine's bit, copied to the PIO CTRL
atomic-clear alias, disabling it a few cycles after the last sample. That
block alone raises the channel's IRQ, which is how main() learns the
window is in. A
sampler left running would fill the RX FIFO and set RXSTALL the moment the
DMA finished, and then RXSTALL could no longer mean what it must mean here:
samples were lost *inside* the window, so the picture is wrong. It is
reported per capture (`rxstall` TIME chunk) and in the summary.
"""

CPL = CFG["cpl"]  # noqa: F821 - CFG is prepended by the host
CLOCK_HZ = CFG["clock_hz"]  # noqa: F821
SAMPLE_DELAY = CFG["sample_delay"]  # noqa: F821
REGION_FIRST = CFG["region_first"]  # noqa: F821
REGION_LINES = CFG["region_lines"]  # noqa: F821
WINDOW_LINES = CFG["window_lines"]  # noqa: F821
HSYNC_ACTIVE = CFG["hsync_active"]  # noqa: F821
VSYNC_ACTIVE = CFG["vsync_active"]  # noqa: F821
MAX_BUFFERS = CFG["max_buffers"]  # noqa: F821
MAX_FRAMES = CFG["max_frames"]  # noqa: F821
MAX_BYTES = CFG["max_bytes"]  # noqa: F821

HSYNC_GPIO = UO_GPIOS[7]  # noqa: F821 - prelude
VSYNC_GPIO = UO_GPIOS[3]  # noqa: F821
LINE_WORDS = CPL // SAMPLES_PER_WORD  # noqa: F821
LINE_BYTES = 4 * LINE_WORDS
PIO_CTRL_CLR = PIO_BASE + 0x3000  # noqa: F821 - CTRL (+0x000) atomic clear

#: Set by the stopper block's completion IRQ: the whole window is in.
DONE = [False]


def on_done(_channel):
    DONE[0] = True


def make_trigger(clk_gpio, hs_gpio, vs_gpio, hs_act, vs_act, in_count, push, clk_act, delay, period):
    """The trigger + sampler program, operands as closure cells.

    Same rules as `make_sampler()` in the prelude: every operand read in
    the body is a parameter (asm_pio clears the globals while it
    assembles).

    `wait gpio` takes the GPIO **mod 32**. Its index field is 5 bits, and
    MicroPython's assembler ORs the index in unmasked, so GPIO 40 (hsync on
    RP2350) would spill into the source field and wait on the wrong thing.
    The loader stores `(index - gpio_base) & 31` and the hardware adds the
    base back (the relocation measured on fpga-1 with the clock at 16), so
    `40 & 31 = 8` becomes 24 and reads GPIO 40. On RP2040 every pin is
    below 32 and nothing changes.
    """
    clk_gpio &= 31
    hs_gpio &= 31
    vs_gpio &= 31
    hs_idle = 1 - hs_act
    vs_idle = 1 - vs_act
    clk_idle = 1 - clk_act

    @rp2.asm_pio(  # noqa: F821
        in_shiftdir=rp2.PIO.SHIFT_LEFT,  # noqa: F821
        autopush=True,
        push_thresh=push,
    )
    def trigger():
        pull()  # noqa: F821 - lines to skip after the vsync edge
        mov(x, osr)  # noqa: F821
        wait(vs_idle, gpio, vs_gpio)  # noqa: F821
        wait(vs_act, gpio, vs_gpio)  # noqa: F821 - vsync leading edge
        label("skip")  # noqa: F821
        jmp(x_dec, "line")  # noqa: F821 - x lines still to skip?
        jmp("go")  # noqa: F821
        label("line")  # noqa: F821
        wait(hs_idle, gpio, hs_gpio)  # noqa: F821
        wait(hs_act, gpio, hs_gpio)  # noqa: F821 - hsync leading edge
        jmp("skip")  # noqa: F821
        label("go")  # noqa: F821
        wait(clk_idle, gpio, clk_gpio)  # noqa: F821
        wait(clk_act, gpio, clk_gpio)[delay]  # noqa: F821 - the edge, then `delay` cycles
        wrap_target()  # noqa: F821
        in_(pins, in_count)[period - 1]  # noqa: F821 - one sample per project clock
        wrap()  # noqa: F821

    return trigger


def idle_level(pin, reads):
    """The level `pin` reads most often: a sync pulse's *inactive* level.

    A pulse is active for a small part of its period (hsync 96 of 800
    clocks, vsync 2 of 525 lines in 640x480@60), so the majority of
    unsynchronised reads is the idle level whatever the polarity.
    """
    ones = 0
    for _ in range(reads):
        ones += pin.value()
    return 1 if 2 * ones > reads else 0


def plan_buffers(lines_wanted, max_buffers):
    """Allocate up to `max_buffers` buffers of whole lines, largest first.

    Returns a list of [bytearray, lines]. Each buffer is the most lines
    still wanted that one contiguous block holds, found by bisection (each
    probe is freed and collected before the next, so the probes do not
    fragment the heap they are measuring). It stops when not one more line
    fits or the buffer count is reached. The total may be less than wanted
    -- the caller sweeps the rest in later frames.
    """
    bufs = []
    left = lines_wanted
    while left > 0 and len(bufs) < max_buffers:
        lo, hi = 0, left
        while lo < hi:
            mid = (lo + hi + 1) // 2
            try:
                bytearray(mid * LINE_BYTES)
                lo = mid
            except MemoryError:
                hi = mid - 1
            gc.collect()  # noqa: F821
        if not lo:
            break
        bufs.append([bytearray(lo * LINE_BYTES), lo])
        left -= lo
    return bufs


def write_fram_chunk(out, counter, first, lines, parts):
    """Emit one `FRAM` chunk of `lines` lines whose samples are `parts`
    (buffers, in capture order); return the bytes written."""
    size = 0
    for part in parts:
        size += len(part)
    head = (
        b"FRAM"
        + _le32(16 + size)  # noqa: F821 - prelude
        + _le32(counter)  # noqa: F821
        + bytes((first & 0xFF, first >> 8, lines & 0xFF, lines >> 8))
        + _le32(CPL)  # noqa: F821
        + _le32(lines * CPL)  # noqa: F821
    )
    sent = _write_all(out, head)  # noqa: F821
    for part in parts:
        sent += _write_all(out, part)  # noqa: F821
    return sent


def main(out):
    """Capture windows of successive frames until told to stop."""
    # cpl being whole words is checked by the host (frame_cfg).
    sysclk = machine.freq()  # noqa: F821
    period = (sysclk + CLOCK_HZ // 2) // CLOCK_HZ
    if not 2 <= period <= 32 or abs(sysclk - period * CLOCK_HZ) * 200 > sysclk:
        msg = "error=sysclk %d is not 2..32 whole project clocks of %d"
        write_time_chunk(out, 0, (msg % (sysclk, CLOCK_HZ)).encode())  # noqa: F821
        return
    if not claim_pio(out):  # noqa: F821 - prelude
        return

    hs_act = HSYNC_ACTIVE
    if hs_act < 0:
        hs_act = 1 - idle_level(machine.Pin(HSYNC_GPIO), 2000)  # noqa: F821
    vs_act = VSYNC_ACTIVE
    if vs_act < 0:
        vs_act = 1 - idle_level(machine.Pin(VSYNC_GPIO), 2000)  # noqa: F821

    program = make_trigger(
        CLK_WAIT_GPIO, HSYNC_GPIO, VSYNC_GPIO, hs_act, vs_act, IN_COUNT, PUSH_THRESH,  # noqa: F821
        1 if EDGE == "rising" else 0, SAMPLE_DELAY, period,  # noqa: F821
    )
    sm = new_state_machine(program)  # noqa: F821 - prelude

    wanted = REGION_LINES
    if WINDOW_LINES and WINDOW_LINES < wanted:
        wanted = WINDOW_LINES
    bufs = plan_buffers(wanted, MAX_BUFFERS)
    if not bufs:
        write_time_chunk(out, 0, b"error=not one line of heap for a frame buffer")  # noqa: F821
        return
    window = 0
    for item in bufs:
        window += item[1]

    stop_word = bytearray(4)
    stop_word[0] = 1 << SM_NUM  # noqa: F821 - SM_ENABLE bit in PIO CTRL
    table = bytearray(16 * (len(bufs) + 1))
    data = None
    ctl = None
    poller = None
    stdin = None
    frames = 0
    stalls = 0
    sent = 0

    try:
        micropython.kbd_intr(-1)  # noqa: F821 - see the prelude: cooperative stop
        stdin = stdin_stream(sys.stdin)  # noqa: F821
        poller = select.poll()  # noqa: F821
        poller.register(stdin, select.POLLIN)  # noqa: F821

        data = rp2.DMA()  # noqa: F821
        ctl = rp2.DMA()  # noqa: F821
        # A buffer block: paced by the PIO's RX DREQ, then back to ctl.
        # Every field that decides correctness is spelled out rather than
        # left to pack_ctrl's defaults: an IRQ from a fill block would end
        # the capture early, and a paced halt block would never run.
        fill = data.pack_ctrl(
            size=2, inc_read=False, inc_write=True, treq_sel=DREQ,  # noqa: F821
            chain_to=ctl.channel, irq_quiet=True,
        )
        # The last block: unpaced, one word, chains nowhere, raises the IRQ.
        halt = data.pack_ctrl(
            size=2, inc_read=False, inc_write=False, treq_sel=0x3F,
            chain_to=data.channel, irq_quiet=False,
        )
        # ctl: 4 words per trigger into data's alias-0 registers; the write
        # ring (16 bytes) brings it back to READ_ADDR for the next block,
        # and the read address walks on through the table.
        ctl_ctrl = ctl.pack_ctrl(
            size=2, inc_read=True, inc_write=True, ring_size=4, ring_sel=True,
            treq_sel=0x3F, chain_to=ctl.channel, irq_quiet=True,
        )
        data_regs = 0x50000000 + 0x40 * data.channel  # DMA CHn alias 0
        data.irq(on_done, hard=True)

        report = "frame window=%d buffers=%d hsync_active=%d vsync_active=%d"
        report %= (window, len(bufs), hs_act, vs_act)
        sent += write_time_chunk(out, 0, report.encode())  # noqa: F821

        cursor = 0
        picture = 0
        stopping = False
        while not stopping:
            lines_now = window
            if lines_now > REGION_LINES - cursor:
                lines_now = REGION_LINES - cursor
            first = REGION_FIRST + cursor

            # Arm: a clean state machine, the skip count in its TX FIFO,
            # every buffer's channel pointed at its buffer, RXSTALL clear.
            sm.active(0)
            while sm.rx_fifo():
                sm.get()
            sm.restart()
            sm.put(first)
            take = lines_now
            at = 0
            for buf, n in bufs:
                if n > take:
                    n = take
                if n == 0:
                    break
                take -= n
                for word in (RXF_ADDR, uctypes.addressof(buf), n * LINE_WORDS, fill):  # noqa: F821
                    table[at : at + 4] = _le32(word)  # noqa: F821
                    at += 4
            for word in (uctypes.addressof(stop_word), PIO_CTRL_CLR, 1, halt):  # noqa: F821
                table[at : at + 4] = _le32(word)  # noqa: F821
                at += 4
            DONE[0] = False
            MEM[FDEBUG_ADDR] = RXSTALL_BIT  # noqa: F821
            ctl.config(read=table, write=data_regs, count=4, ctrl=ctl_ctrl, trigger=True)
            sm.active(1)

            while not DONE[0]:
                if stop_requested(poller, stdin):  # noqa: F821
                    stopping = True
                    break
                machine.idle()  # noqa: F821
            if stopping:
                break
            sm.active(0)
            if (MEM[FDEBUG_ADDR] >> SM_NUM) & 1:  # noqa: F821
                stalls += 1
                sent += write_time_chunk(out, 0, b"rxstall")  # noqa: F821

            parts = []
            take = lines_now
            for buf, n in bufs:
                if n > take:
                    buf = memoryview(buf)[: take * LINE_BYTES]
                    n = take
                if n == 0:
                    break
                take -= n
                parts.append(buf)
            sent += write_fram_chunk(out, picture, first, lines_now, parts)
            parts = None

            cursor += lines_now
            if cursor >= REGION_LINES:
                cursor = 0
                picture += 1
                frames += 1
            if MAX_FRAMES and frames >= MAX_FRAMES:
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
        rxstall = (MEM[FDEBUG_ADDR] >> SM_NUM) & 1  # noqa: F821
        for dma in (data, ctl):
            if dma is not None:
                dma.irq(None)
        sm.active(0)
        try:
            rp2.PIO(PIO_NUM).remove_program(program)  # noqa: F821
        except Exception:
            pass
        for dma in (ctl, data):
            if dma is not None:
                dma.active(0)
                dma.close()
        summary = "frames=%d stalls=%d rxstall=%d sysclk_hz=%d" % (
            frames, stalls, 1 if (stalls or rxstall) else 0, machine.freq()  # noqa: F821
        )
        write_time_chunk(out, 0, summary.encode())  # noqa: F821
        if hasattr(out, "flush"):
            out.flush()
        del bufs
        data = None
        ctl = None
        poller = None
        stdin = None
        gc.collect()  # noqa: F821
        micropython.kbd_intr(3)  # noqa: F821


MEM = machine.mem32  # noqa: F821 - the prelude imports machine
_OUT = sys.stdout.buffer  # noqa: F821
_PROBLEM = firmware_problem()  # noqa: F821
if _PROBLEM:
    write_time_chunk(_OUT, 0, _PROBLEM)  # noqa: F821
else:
    main(_OUT)
