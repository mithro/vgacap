# SPDX-License-Identifier: Apache-2.0
"""Host side of a vgacap capture run.

`capture_cfg()` turns a `BoardProfile` into the `CFG` dict that
`ttcap.mp.build()` puts in front of the shared `ttcap/mp/prelude.py` and
the request's program body (`capture_rp2.py` unless it names another).
`select_project()` puts the board's design and project clock in the state
the capture needs, and `run_capture()` writes the `VGCH` header the board
never sees and then splices the board's own chunks after it, verbatim.

The capture itself is a *stream*, not a file writer: `iter_capture()` yields
the header and then every board chunk as bytes, `CaptureSession` wraps that
iterator with the stats, the out-of-band stop and the teardown a
long-running consumer (M5's GStreamer source, `ttcap capture --out -`)
needs, and `run_capture()` is the thin wrapper that pours the same bytes
into a file. There is one implementation of the board protocol and one
implementation of the recovery path; everything else chooses a sink.

The split of responsibility is deliberate: the board knows the sample data
and its own overruns, the host knows the signal map, the mode and the
project clock it just programmed, so only the host can write a correct
header. Nothing re-frames the board's chunks -- what the board wrote is
what lands in the file, so a decoding disagreement can never be blamed on
the host's re-serialisation.
"""

from __future__ import annotations

import io
import re
import struct
import threading
import time
import zlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterator

from vgacap.modes import MODES
from vgacap.stream import Header, Writer

from . import mp
from .boards import BoardProfile, pwm_slice_addr
from .repl import CHUNK_HEADER_LEN, LinkClosed, RawRepl, ReplFramingError

EDGES = ("falling", "rising")
#: Board-side program bodies a `CaptureRequest` may name (`ttcap/mp/`).
CAPTURE_SCRIPTS = ("capture_rp2.py", "frame_rp2.py", "rle_rp2.py", "clocked_rp2.py")
#: `ttcap capture --mode` names: continuous streaming at a reduced clock,
#: or whole frames / windows of frames at the project's real clock, or a
#: stream run-length encoded by the PIO (`RLE ` chunks), or one sample per
#: clock of a bitstream the capture loads and clocks itself (`clocked`).
CAPTURE_MODES = {
    "stream": "capture_rp2.py",
    "frame": "frame_rp2.py",
    "window": "frame_rp2.py",
    "rle": "rle_rp2.py",
    "clocked": "clocked_rp2.py",
}
#: The timing `frame_rp2.py` is told to expect unless asked otherwise.
DEFAULT_VGA_MODE = "640x480@60"
#: The sample point frame capture uses unless told otherwise: the rising
#: edge, read at once. Swept on fpga-4 at 25 MHz, 2026-11-24 (frame_rp2.py,
#: SAMPLE POINT): the middle of a two-point window where uo_out is stable.
FRAME_EDGE = "rising"
#: Lines per window when `--mode window` is asked for without a size.
DEFAULT_WINDOW_LINES = 48
#: Most buffers one frame capture may fill. They cost no DMA channels
#: (control blocks: two channels whatever the count), only 16 bytes of
#: table each. The board plans in chunks of 64, 16, 4 and 1 lines (fast;
#: see frame_rp2.plan_buffers), so a fragmented heap takes many of them.
FRAME_MAX_BUFFERS = 128

#: Informational only, and only a guess: the rp2 port boots RP2040 at
#: 133 MHz but RP2350 at 150 MHz, and the capture never changes the system
#: clock. The authoritative value comes back from the board, which reports
#: `machine.freq()` as `sysclk_hz=` in its closing `TIME` chunk.
DEFAULT_SYSCLK_HZ = 133_000_000

DEFAULT_BUF_WORDS = 4096

#: PIO block the sampler claims by default -- **not** PIO0.
#:
#: The stock demo-board firmware keeps its own program in PIO0 (the FPGA
#: bitstream loader on the RP2350 boards), and on RP2350 that matters twice
#: over: `pio_set_gpio_base_unsafe()` refuses to move a block's 32-pin
#: window while any instruction memory in that block is in use (pico-sdk
#: `pio.c`, returns `PICO_ERROR_INVALID_STATE`), so a capture on PIO0 cannot
#: reach the uo_out pins at GPIO 33+ at all. Measured on fpga-1 (firmware
#: 3.1.0): `rp2.PIO(0).gpio_base(16)` raises `OSError: EINVAL`, while PIO1
#: and PIO2 already sit at base 16 and need no move.
DEFAULT_PIO = 1

#: `VGACAP_MODE_EXTCLK` from include/vgacap/stream.h: every sample is taken
#: on an edge of the project's own clock, which is what the sampler does.
MODE_EXTCLK = 0
#: `VGACAP_MODE_SELFCLK`: the capture made the clock itself, as clocked mode does.
MODE_SELFCLK = 1

#: The board body of clocked capture, which makes the clock with PIO
#: side-set and samples uo_out once per clock (`clocked_rp2.py`).
CLOCKED_SCRIPT = "clocked_rp2.py"
#: Clocks with rst_n low before clock `reset_clocks`, unless asked otherwise:
#: the harness's reference holds reset for clocks 0 to 9.
CLOCKED_RESET_CLOCKS = 10
#: H and L, the program's two delays, are each at most 15: one side-set bit
#: that is not optional leaves 4 of the 5 delay/side-set bits.
CLOCKED_MAX_PHASE = 15
#: PIO cycles of one clock that are not H or L: one each for `nop`, `in`,
#: `push` and `jmp`.
CLOCKED_FIXED_CYCLES = 4
#: The `in` reads uo_out at least this long after the rising edge. Measured
#: on one design and one board -- tt_um_vga_ca on fpga-4, 2026-11-28, batch
#: b2, at 133 MHz and 7 MHz: reads up to 38 ns after the edge got the
#: previous clock's value, 45 and 53 ns were wrong, and from 60 ns on every
#: sample matched the reference. 80 ns is that 60 ns plus a third. A design
#: with a slower clock-to-output path needs a later read: a larger H.
CLOCKED_SAMPLE_NS = 80
#: The largest integer SMn_CLKDIV (its INT field is 16 bits, 0 meaning
#: 65536, which is left alone).
CLOCKED_MAX_DIV = 65535

#: Project clocks in one 640x480@60 frame: 800 x 525 including blanking.
#: `frames_to_max_bytes()` assumes this timing, which is what every Tiny
#: Tapeout VGA design on the shuttle uses; a design at another resolution
#: needs `--max-bytes` worked out by hand.
CLOCKS_PER_FRAME_640X480 = 800 * 525

#: Free heap the script needs for everything that is *not* the two DMA
#: buffers: compiling the minified source and the ~64 module-level names it
#: then leaves in the REPL's globals. Measured on tt07, where the script
#: compiles with 84,208 bytes free and 32 KB of that goes to the buffers.
COMPILE_HEADROOM_BYTES = 24_000


def frame_cfg(
    profile: BoardProfile,
    vga_mode: str = DEFAULT_VGA_MODE,
    window_lines: int = 0,
    max_frames: int = 0,
    max_bytes: int = 0,
    pio: int = DEFAULT_PIO,
    sm: int = 0,
    margin_lines: int = 1,
    hsync_active: int = -1,
    vsync_active: int = -1,
    edge: str = "falling",
    sample_delay: int = 0,
    clock_hz: int = 25_000_000,
) -> dict:
    """Build the `CFG` dict for `frame_rp2.py` (whole-frame/windowed capture).

    The region is the mode's active lines plus `margin_lines` above them:
    a window can start a sample late (see the body's docstring), and the
    margin keeps that off the first line of the picture. Line 0 is the
    first line of the vsync pulse, as libvgaframe counts. `window_lines`
    0 lets the board take as many lines as its heap holds; `hsync_active`
    and `vsync_active` -1 have it measure the sync polarities itself.
    `edge` and `sample_delay` (system clocks after that edge) place the
    sample point; see SAMPLE POINT in frame_rp2.py.
    """
    if not 0 <= sample_delay <= 31:
        raise ValueError(f"sample_delay must be 0..31 system clocks, got {sample_delay}")
    if vga_mode not in MODES:
        raise ValueError(f"unknown VGA mode {vga_mode!r}; known: {', '.join(MODES)}")
    mode = MODES[vga_mode]
    if mode.extended:
        base = [m.name for m in MODES.values() if not m.extended]
        raise ValueError(
            f"{vga_mode}: frame and window capture support only the base modes, which "
            f"libvgaframe recognises by clocks per line alone ({', '.join(base)}); "
            f"streaming capture detects {vga_mode} without --vga-mode"
        )
    if mode.cpl % profile.samples_per_word:
        raise ValueError(
            f"{vga_mode}: {mode.cpl} clocks per line is not a whole number of "
            f"{profile.samples_per_word}-sample words"
        )
    if window_lines < 0 or max_frames < 0:
        raise ValueError("window_lines and max_frames must not be negative")
    if not 0 <= margin_lines <= mode.v_sync + mode.v_back:
        raise ValueError(f"margin_lines must be 0..{mode.v_sync + mode.v_back}")
    for name, value in (("hsync_active", hsync_active), ("vsync_active", vsync_active)):
        if value not in (-1, 0, 1):
            raise ValueError(f"{name} must be -1 (measure), 0 or 1")
    cfg = capture_cfg(profile, max_bytes=max_bytes, edge=edge, pio=pio, sm=sm)
    del cfg["buf_words"]  # capture_rp2.py's; the frame body sizes its own
    cfg.update(
        cpl=mode.cpl,
        region_first=mode.v_sync + mode.v_back - margin_lines,
        region_lines=mode.v_active + margin_lines,
        window_lines=window_lines,
        hsync_active=hsync_active,
        vsync_active=vsync_active,
        max_buffers=FRAME_MAX_BUFFERS,
        max_frames=max_frames,
        sample_delay=sample_delay,
        clock_hz=clock_hz,
        clk_pwm=pwm_slice_addr(profile, profile.clk_gpio),
    )
    return cfg


#: `rle_rp2.py` styles: phase-locked to a whole sysclk/clock ratio, waiting
#: for every clock edge, or whichever is exact for the clock (see the body).
RLE_STYLES = ("auto", "locked", "edge")


def rle_cfg(
    profile: BoardProfile,
    clock_hz: int,
    buf_words: int = DEFAULT_BUF_WORDS,
    max_bytes: int = 0,
    edge: str = "rising",
    sample_delay: int = 0,
    style: str = "auto",
    mask: int = -1,
    oneshot: bool = False,
    pio: int = DEFAULT_PIO,
    sm: int = 0,
) -> dict:
    """Build the `CFG` dict for `rle_rp2.py` (PIO run-length streaming).

    `edge` and `sample_delay` place the sample point as in frame capture
    (the default is frame capture's measured one). `style` and `mask` are
    the body's STYLE and MASK; `mask` -1 lets the board pick (1 on RP2350).
    `buf_words` must be even: a DMA buffer is whole (value, run) pairs.
    `oneshot` fills the two buffers once at full speed and sends them.
    """
    if not 0 <= sample_delay <= 31:
        raise ValueError(f"sample_delay must be 0..31 system clocks, got {sample_delay}")
    if style not in RLE_STYLES:
        raise ValueError(f"style must be one of {RLE_STYLES}, got {style!r}")
    if mask not in (-1, 0, 1):
        raise ValueError(f"mask must be -1 (board decides), 0 or 1, got {mask}")
    if buf_words % 2:
        raise ValueError(f"buf_words must be even (whole pairs), got {buf_words}")
    if clock_hz <= 0:
        raise ValueError(f"clock_hz must be positive, got {clock_hz}")
    cfg = capture_cfg(profile, buf_words=buf_words, max_bytes=max_bytes, edge=edge, pio=pio, sm=sm)
    cfg.update(clock_hz=clock_hz, sample_delay=sample_delay, style=style, mask=mask,
               oneshot=1 if oneshot else 0)
    return cfg


def frame_note(messages: tuple[str, ...] | list[str], region_lines: int) -> str:
    """What a frame/window capture's pictures are made of, if it matters.

    The board's opening TIME message says how many lines one capture takes
    (`frame window=N ...`). When that is less than the picture region,
    each picture is swept from several *consecutive captured* frames:
    exact for a static or frame-periodic design, torn for anything that
    changes between them (tt_um_vgacal_counter in window mode, measured on
    fpga-4: 1,040 pixels). Returns "" when every picture is one frame.
    """
    for msg in messages:
        if msg.startswith("frame window="):
            window = int(msg.split()[1].split("=")[1])
            if window >= region_lines:
                return ""
            frames = -(-region_lines // window)
            return (
                "note: each picture is assembled from %d captured frames (%d of its "
                "%d lines per frame): exact for a static design, torn for one "
                "that changes from frame to frame" % (frames, window, region_lines)
            )
    return ""


def min_free_bytes(buf_words: int) -> int:
    """Free heap the board needs before this capture is worth sending.

    The stock RP2040 firmware leaves ~80 KB and the script allocates two
    `4 * buf_words`-byte DMA buffers out of it (`capture_rp2.py`'s `bufs`)
    on top of what compiling it costs -- 32 KB at the default 4096 words,
    64 KB at 8192, 128 KB at 16384. A flat floor passed all three, which
    made the check useless at the one job it was added for: `--buf-words`
    is a user-facing flag with no upper bound, and on tt07 (~80-84 KB free)
    the 8192 case cannot possibly fit.

    The failure it is protecting against is not always a clean
    `MemoryError`: twice on tt07 the board printed `FATAL: uncaught
    exception` and halted, needing a power cycle. Refusing early is
    strictly kinder.
    """
    return 8 * buf_words + COMPILE_HEADROOM_BYTES


#: Deletes the names a previous run left in the board's REPL globals, then
#: collects and reports the free heap. `globals()` in the raw REPL *is* that
#: namespace, and `pop(name, None)` tolerates a first run where none of them
#: exist yet.
#:
#: `import gc` comes AFTER the loop, and that ordering is the whole trick:
#: the names being deleted include the script's imports, `gc` among them, so
#: importing first meant the loop popped `gc` and the next line died with
#: `NameError: name 'gc' isn't defined` -- which it did on every run on
#: tt07. Importing afterwards rebinds it whether or not the loop removed it,
#: and costs nothing: the module object is still in `sys.modules`.
_CLEANUP = (
    "for _n in %r:\n"
    "    globals().pop(_n, None)\n"
    "globals().pop('_n', None)\n"
    "import gc\n"
    "gc.collect()\n"
    "print(gc.mem_free())\n"
)

#: Per-read timeout while a capture is streaming. Generous, because the gap
#: between chunks is a whole DMA buffer of project clocks: at 100 kHz with
#: buf_words=4096 that is ~82 ms on an RP2040 (8,192 samples) and ~164 ms
#: on an RP2350 (16,384 samples), and a slower clock stretches it linearly.
DEFAULT_CHUNK_TIMEOUT = 30.0

_TIME_HEAD = struct.Struct("<QIIH")


class CaptureError(Exception):
    """Raised when the board refuses a setup command or reports an error."""


def capture_cfg(
    profile: BoardProfile,
    buf_words: int = DEFAULT_BUF_WORDS,
    max_bytes: int = 0,
    edge: str = "falling",
    pio: int = DEFAULT_PIO,
    sm: int = 0,
) -> dict:
    """Build the `CFG` dict for `capture_rp2.py` from a board profile.

    `buf_words` is the size of each of the two ping-pong DMA buffers in
    32-bit words, `max_bytes` caps the bytes the script emits (0 = run until
    Ctrl-C), `edge` selects which project-clock edge is sampled, and
    `pio`/`sm` pick the state machine -- see `DEFAULT_PIO` for why that is
    not block 0.

    `clk_gpio` and `in_base` go out as *absolute* GPIO numbers, which is
    what MicroPython wants for both `StateMachine(in_base=...)` (it
    subtracts the block's base itself) and `wait ... gpio` (the loader
    relocates the index). The sampled pins must still land inside the PIO's
    32-pin window, which is what the check below enforces -- getting that
    wrong produced no error at all on the board, just constant samples.
    """
    if edge not in EDGES:
        raise ValueError(f"edge must be one of {EDGES}, got {edge!r}")
    if buf_words <= 0:
        raise ValueError(f"buf_words must be positive, got {buf_words}")
    if max_bytes < 0:
        raise ValueError(f"max_bytes must not be negative, got {max_bytes}")
    if not 0 <= pio <= 2:
        raise ValueError(f"pio must be 0..2 (RP2040 has 0..1), got {pio}")
    if pio == 0 and profile.pio_gpio_base != 0:
        # Reaching the uo_out pins on this board means moving the block's
        # 32-pin window, and a block only moves once its instruction memory
        # is empty -- so a capture on PIO0 could only work by wiping the
        # stock firmware's own program (the FPGA bitstream loader), which
        # costs a power cycle to put back. The board script refuses too;
        # this is the half that never lets the request leave the host.
        raise ValueError(
            f"{profile.name}: pio 0 would have to move its pin window to "
            f"GPIO {profile.pio_gpio_base} to reach uo_out, and that means "
            "erasing PIO0's programs -- the stock firmware keeps the FPGA "
            "bitstream loader there. Use --pio 1 or --pio 2."
        )
    if not 0 <= sm <= 3:
        raise ValueError(f"sm must be 0..3, got {sm}")
    in_index = profile.in_base - profile.pio_gpio_base
    if in_index < 0 or in_index + profile.in_count > 32:
        raise ValueError(
            f"{profile.name}: uo_out at GPIO {profile.in_base} is not inside the "
            f"PIO's 32-pin window based at {profile.pio_gpio_base}"
        )

    return {
        "clk_gpio": profile.clk_gpio,
        "in_base": profile.in_base,
        "in_count": profile.in_count,
        # The only pads the board script may configure. The sampled window
        # can span pins the RP2 itself drives -- RP2040 ui_in[0..3] sit at
        # GPIO 9..12, between uo_out[0..3] and uo_out[4..7].
        "uo_gpios": list(profile.uo_gpios),
        "gpio_base": profile.pio_gpio_base,
        "push_thresh": profile.push_thresh,
        "buf_words": buf_words,
        "max_bytes": max_bytes,
        "edge": edge,
        "pio": pio,
        "sm": sm,
        "sysclk_hz": DEFAULT_SYSCLK_HZ,
    }


#: Bytes in each `RAW ` chunk that are not sample data: the 4-byte tag, the
#: 4-byte length and the 4-byte `sample_count` that opens the payload
#: (`capture_rp2.py`'s `write_raw_chunk()`). Both sides count them in the
#: running total they compare against `max_bytes`, so a byte budget that
#: ignores them buys fewer samples than it asked for.
RAW_CHUNK_OVERHEAD = 12


def frames_to_max_bytes(
    profile: BoardProfile, frames: int, buf_words: int = DEFAULT_BUF_WORDS
) -> int:
    """Bytes the board must emit to be sure of `frames` complete frames.

    Assumes 640x480@60 timing (`CLOCKS_PER_FRAME_640X480`), and asks for
    **two** frame periods more than requested. A capture starts mid-frame,
    so the tail of that frame is unusable and the next boundary only tells
    the reconstruction where frames begin; one whole frame after it is the
    first that can be rendered. Measured: a `--frames 1` capture sized with
    a single extra frame gave 837,632 samples on tt07 and `vgacap-frames`
    produced **zero** frames from it.

    The budget is the packed sample words *plus* the 12 non-sample bytes
    each `RAW ` chunk carries (`RAW_CHUNK_OVERHEAD`), because the limit the
    board stops on counts whole chunks, not payloads: asking for exactly
    the sample bytes buys ~0.2% fewer samples than requested (1,257,472
    where 1,260,000 was wanted). `buf_words` is what decides how many
    chunks that is, so it has to match the request's.

    Any `TIME` chunk the board emits is counted in that total too, but only
    an overrun produces one mid-run -- and an overrun has already lost more
    samples than the 26 bytes of its report.
    """
    if frames <= 0:
        raise ValueError(f"frames must be positive, got {frames}")
    if buf_words <= 0:
        raise ValueError(f"buf_words must be positive, got {buf_words}")
    samples = CLOCKS_PER_FRAME_640X480 * (frames + 2)
    words = -(-samples // profile.samples_per_word)
    chunks = -(-words // buf_words)
    return 4 * words + RAW_CHUNK_OVERHEAD * chunks


@dataclass
class CaptureRequest:
    """Everything the host needs to set a board up and capture from it."""

    profile: BoardProfile
    #: `tt.shuttle` macro name for an ASIC board, e.g. "tt_um_rejunity_vga";
    #: None leaves whatever design is already selected alone.
    project: str | None
    #: FPGA boards: the bitstream to enable, through the same `tt.shuttle`
    #: mechanism. Mutually exclusive with `project`.
    design: str | None
    #: Project clock to program with `tt.clock_project_PWM`.
    clock_hz: int
    #: Wall-clock capture duration; 0 means "until max_bytes".
    seconds: float
    #: Cap on the bytes the board emits; 0 means unlimited.
    max_bytes: int
    buf_words: int = DEFAULT_BUF_WORDS
    edge: str = "falling"
    desc: str = ""
    pio: int = DEFAULT_PIO
    sm: int = 0
    #: Consulted between chunks: True ends the capture at the next safe
    #: point, exactly as `seconds`/`max_bytes` do. It is the third way a
    #: capture can be bounded, and the only one a *consumer* controls -- a
    #: GStreamer pipeline saying EOS, a SIGINT handler -- so a request that
    #: supplies it may leave `seconds` and `max_bytes` both 0 and run until
    #: something outside says stop.
    #:
    #: Called from the thread driving the iterator, never from another, and
    #: never while a chunk is half read. Stop latency is therefore one DMA
    #: buffer: with `buf_words=4096`, ~22 ms on an RP2350 at 750 kHz (16,384
    #: samples) but ~137 ms on an RP2040 at its 60 kHz ceiling (8,192
    #: samples). A consumer that cannot wait that long wants
    #: `CaptureSession.request_stop()` instead, which is thread-safe.
    stop: Callable[[], bool] | None = None
    #: The board-side program body `ttcap.mp.build()` puts after the shared
    #: prelude: `capture_rp2.py` streams continuously; Milestone 6 adds the
    #: whole-frame and windowed programs alongside it.
    script: str = "capture_rp2.py"
    #: `frame_rp2.py` only: the VGA timing to expect (a `vgacap.modes`
    #: name), the most lines per capture (0 = what the heap holds), and
    #: how many pictures to take before stopping (0 = no limit).
    vga_mode: str = DEFAULT_VGA_MODE
    window_lines: int = 0
    max_frames: int = 0
    #: `frame_rp2.py` only: system clocks between the sampled edge and the
    #: read (SAMPLE POINT in frame_rp2.py).
    sample_delay: int = 0
    #: The design's `ui_in`, driven by the RP2 (None leaves the board's
    #: inputs alone) -- e.g. tt_um_vgacal_modes takes its mode there.
    ui_in: int | None = None
    #: `--firmware picosdk` on an ASIC board only: the project's index on the
    #: chip's mux (the stock SDK's `tt.shuttle.<name>.count`), selected by the
    #: capture firmware itself (its `asic_select` command). None leaves the
    #: mux alone, which is right on an FPGA board.
    asic_index: int | None = None
    #: `rle_rp2.py` only: its STYLE and MASK (see `rle_cfg`).
    rle_style: str = "auto"
    rle_mask: int = -1
    #: `rle_rp2.py` only: fill both buffers once and send them (see rle_cfg).
    rle_oneshot: bool = False
    #: `clocked_rp2.py` only: the iCE40 bitstream on the host, which the
    #: capture loads with `load_held`; the rising edges to make, a multiple
    #: of 4, one sample each; how many of them come with `rst_n` low; and H,
    #: the high phase's delay, for a sweep (None: `clocked_timing` picks it).
    bitstream: str | None = None
    clocks: int = 0
    reset_clocks: int = CLOCKED_RESET_CLOCKS
    clock_high: int | None = None

    @property
    def is_clocked(self) -> bool:
        """One sample per clock the board makes itself (`clocked_rp2.py`)."""
        return self.script == CLOCKED_SCRIPT

    @property
    def is_frame(self) -> bool:
        """Whole-frame/windowed capture rather than streaming."""
        return self.script == "frame_rp2.py"

    @property
    def is_rle(self) -> bool:
        """Streaming, run-length encoded by the PIO."""
        return self.script == "rle_rp2.py"

    def __post_init__(self) -> None:
        if self.project and self.design:
            raise ValueError(
                "project and design both name a tt.shuttle entry; give only one"
            )
        if self.script not in CAPTURE_SCRIPTS:
            raise ValueError(
                f"script must be one of {CAPTURE_SCRIPTS}, got {self.script!r}"
            )
        if self.asic_index is not None and not 0 <= self.asic_index <= 1023:
            raise ValueError(f"asic_index must be 0..1023, got {self.asic_index}")
        if self.ui_in is not None and not 0 <= self.ui_in <= 255:
            raise ValueError(f"ui_in must be 0..255, got {self.ui_in}")
        if self.edge not in EDGES:
            raise ValueError(f"edge must be one of {EDGES}, got {self.edge!r}")
        if self.clock_hz <= 0:
            raise ValueError(f"clock_hz must be positive, got {self.clock_hz}")
        if self.seconds < 0:
            raise ValueError(f"seconds must not be negative, got {self.seconds}")
        if self.is_clocked:
            self._check_clocked()
        elif self.seconds == 0 and self.max_bytes == 0 and self.stop is None and not (
            (self.is_frame and self.max_frames) or (self.is_rle and self.rle_oneshot)
        ):
            raise ValueError(
                "seconds and max_bytes are both 0 and no stop callable was "
                "given: the capture would never stop"
            )
        # Every check the CFG makes (e.g. sample_delay's range) fails here,
        # before `select_project()` has sent the board a single command.
        self.cfg()

    @property
    def selection(self) -> str | None:
        """The `tt.shuttle` entry to enable, whichever field named it."""
        return self.project or self.design

    def _check_clocked(self) -> None:
        """What a clocked request must be, before the board is touched."""
        if not self.bitstream:
            raise ValueError("a clocked capture needs the bitstream it loads (bitstream=)")
        if self.selection:
            raise ValueError(
                f"a clocked capture loads its own bitstream, but the request names "
                f"{self.selection!r}")
        if self.seconds or self.max_bytes:
            raise ValueError(
                "a clocked capture runs exactly `clocks` clocks: seconds and max_bytes "
                "must be 0")
        if self.profile.chip != "rp2350" or self.profile.clk_gpio != FPGA_CLK_GPIO:
            raise ValueError(
                f"a clocked capture needs the FPGA breakout's RP2350 demo board, not "
                f"profile {self.profile.name}")
        problem = clocked_count_problem(self.clocks, self.reset_clocks, self.buf_words,
                                        self.clock_high)
        if problem:
            raise ValueError(problem)

    def cfg(self) -> dict:
        """The `CFG` dict for the board-side script. A clocked capture's
        lacks the clock (`ClockedTiming.cfg`), which needs the board's
        sysclk."""
        if self.is_clocked:
            return clocked_cfg(self.profile, clocks=self.clocks,
                               reset_clocks=self.reset_clocks, buf_words=self.buf_words,
                               pio=self.pio, sm=self.sm)
        if self.is_frame:
            return frame_cfg(
                self.profile,
                vga_mode=self.vga_mode,
                window_lines=self.window_lines,
                max_frames=self.max_frames,
                max_bytes=self.max_bytes,
                pio=self.pio,
                sm=self.sm,
                edge=self.edge,
                sample_delay=self.sample_delay,
                clock_hz=self.clock_hz,
            )
        if self.is_rle:
            return rle_cfg(
                self.profile,
                clock_hz=self.clock_hz,
                buf_words=self.buf_words,
                max_bytes=self.max_bytes,
                edge=self.edge,
                sample_delay=self.sample_delay,
                style=self.rle_style,
                mask=self.rle_mask,
                oneshot=self.rle_oneshot,
                pio=self.pio,
                sm=self.sm,
            )
        return capture_cfg(
            self.profile,
            buf_words=self.buf_words,
            max_bytes=self.max_bytes,
            edge=self.edge,
            pio=self.pio,
            sm=self.sm,
        )

    def min_free(self) -> int:
        """Free heap the board needs before this capture is worth sending.

        Streaming allocates its two DMA buffers up front (`min_free_bytes`).
        A frame capture sizes its buffers to whatever the heap holds, so it
        needs the compile headroom plus one line -- less than that and it
        could not capture anything at all.
        """
        if self.is_frame:
            line_bytes = MODES[self.vga_mode].cpl // self.profile.samples_per_word * 4
            return COMPILE_HEADROOM_BYTES + line_bytes
        return min_free_bytes(self.buf_words)


@dataclass(frozen=True)
class CaptureStats:
    """What one `run_capture()` produced, for the CLI and for the tests."""

    #: Bytes of board chunks appended after the header (the `VGCH` chunk the
    #: host wrote is not counted, so this matches the board's own `sent`).
    bytes: int
    #: Samples declared by the `RAW ` chunks -- not words and not bytes.
    samples: int
    chunks: int
    #: DMA buffers the board's main loop failed to drain in time, as the
    #: board counted them, not as the host inferred them.
    overruns: int
    seconds: float
    #: PIO `FDEBUG.RXSTALL` at the end: 1 means the RX FIFO overflowed and
    #: samples were lost whatever the overrun count says.
    rxstall: int
    #: stderr the board reported for the capture script (empty when clean).
    stderr: str = ""
    #: Every `TIME` chunk message, in order.
    messages: tuple[str, ...] = field(default_factory=tuple)
    #: True when the board went quiet mid-stream and the host had to
    #: interrupt it. Whatever was captured before that is still valid, but
    #: the run did not end the way it was asked to.
    timed_out: bool = False
    #: Samples the board dropped, taken from the *last* `TIME` chunk. Those
    #: counts are cumulative by convention (see `vgacap.stream.Writer.time`),
    #: so this is the total -- summing the chunks would double-count.
    dropped: int = 0
    #: A host-side failure that ended the run early (a lost framing, a link
    #: that went quiet), or why a clocked run is not clean
    #: (`clocked_problems`). Empty when the board finished on its own
    #: terms. Whatever was written before it is still a valid stream.
    error: str = ""
    #: `gc.mem_free()` the board reported after the pre-run cleanup. Worth
    #: watching: below `min_free_bytes(buf_words)` the script may not
    #: compile at all, or its DMA buffers may not fit.
    mem_free_before: int = 0
    #: The closing record's `status=` ("ok", "stopped", "error"), its
    #: `samples=` (what the board says it sent) and `stalled_buffers=`
    #: (buffers during which the PIO stalled; information only). Clocked
    #: mode's closing record carries them; "" and 0 for a record without.
    status: str = ""
    board_samples: int = 0
    stalled_buffers: int = 0

    @property
    def board_error(self) -> str:
        """What the board said went wrong, or "" if it said nothing.

        The last line of its traceback (e.g. a `MemoryError` allocating the
        DMA buffers) if it raised, else its first `error=` TIME message (a
        setup the script refused) -- either way the reason a run with no
        samples has none, which is not that the sampler saw no clock.
        """
        lines = [line.strip() for line in self.stderr.splitlines() if line.strip()]
        if lines:
            return lines[-1]
        return next((m for m in self.messages if m.startswith("error=")), "")

    @property
    def clean(self) -> bool:
        return not (
            self.overruns
            or self.rxstall
            or self.stderr
            or self.timed_out
            or self.error
        )

    def format(self) -> str:
        line = (
            "bytes=%d samples=%d chunks=%d seconds=%.3f "
            "overruns=%d dropped=%d rxstall=%d"
            % (
                self.bytes,
                self.samples,
                self.chunks,
                self.seconds,
                self.overruns,
                self.dropped,
                self.rxstall,
            )
        )
        if self.seconds > 0:
            line += " samples_per_s=%.0f" % (self.samples / self.seconds)
        if self.mem_free_before:
            line += " mem_free=%d" % self.mem_free_before
        if self.status:
            line += " status=%s board_samples=%d stalled_buffers=%d" % (
                self.status, self.board_samples, self.stalled_buffers)
        if self.timed_out:
            line += " timed_out=yes"
        if self.error:
            line += " error=%r" % self.error
        if self.stderr:
            line += " board_error=%r" % self.stderr.strip()
        return line


def _exec_checked(repl: RawRepl, code: str, timeout: float = 10.0) -> str:
    """Run one setup command, turning the board's traceback into an error."""
    stdout, stderr = repl.exec(code, timeout=timeout)
    if stderr.strip():
        raise CaptureError(f"board rejected {code}:\n{stderr.strip()}")
    return stdout


def select_project(repl: RawRepl, req: CaptureRequest) -> dict:
    """Enable the requested design, set its inputs, and reset it with its
    clock running.

    In order:

    1. enable the design, if the request names one;
    2. stop the clock;
    3. with `ui_in`, switch to ASIC_RP_CONTROL. Every `tt.mode` write
       rebuilds every pin and so releases reset (#57), which is why it
       comes before the reset;
    4. turn off the manual-clock monitor, where the board has one, whose
       timer would otherwise stop the clock mid-run;
    5. with `ui_in`, write it and read it back, and fail if the board does
       not drive the value asked for;
    6. `reset_design`: hold reset and read `rst_n` back as 0, program and
       start the clock, wait on the board for at least `RESET_CLOCKS` clock
       periods and 1 ms, release reset and wait for `rst_n` to read 1 (#58).

    So reset is held, with the inputs already set and the clock running,
    for at least 16 periods of the requested clock, which a synchronous
    reset needs. What steps 3 and 4
    changed is put back by `restore_mode`. A failure after step 2 releases
    reset, stops the clock and puts the mode and monitor back before the
    error is raised.

    Returns what it actually did, which is what the CLI prints: which
    `tt.shuttle` accessor worked also tells you which SDK generation the
    board is running.
    """
    did: dict = {"project": req.selection, "enable": None, "clock_hz": req.clock_hz}
    name = req.selection
    if name:
        # `ProjectMux.__getitem__` exists in SDK 2.x and 3.x alike, so
        # indexing is the portable spelling; attribute access, which goes
        # through `ProjectMux.__getattr__`, is the fallback for a firmware
        # where only that resolves the name.
        try:
            _exec_checked(repl, f"tt.shuttle[{name!r}].enable()")
            did["enable"] = "index"
        except CaptureError:
            try:
                _exec_checked(repl, f"getattr(tt.shuttle, {name!r}).enable()")
                did["enable"] = "getattr"
            except CaptureError as exc:
                # A bitstream uploaded through the fpgas.online daemon
                # (`POST /bitstream`, then `/designs/<name>/enable`) is loaded
                # on the FPGA but is not one of the SDK's named shuttle
                # entries, so neither spelling finds it. Seen on fpga-4,
                # 2026-11-23: `tt.shuttle.enabled` was
                # `<FPGA BitStream tt_um_vgacal_bars>` while `has()` said
                # False. Already enabled is what was asked for. After
                # `load_held` the name is None (its body runs
                # `tt.shuttle.reset()`), so the design it replaced is
                # refused here rather than taken as enabled.
                out = _exec_checked(
                    repl,
                    "print(getattr(getattr(tt.shuttle, 'enabled', None), "
                    f"'name', None) == {name!r})")
                if out.strip().endswith("True"):
                    did["enable"] = "already enabled"
                else:
                    raise CaptureError(
                        f"{exc}\n{name!r} is not a shuttle entry on this board "
                        "and is not the enabled design either; an uploaded FPGA "
                        "bitstream has to be enabled through the board's daemon "
                        "first") from None

    def reset() -> None:
        did["rst_n_wait_ms"] = reset_design(repl, clock_command(req), req.clock_hz)

    _reset_sequence(repl, req, did, reset)
    return did


def _reset_sequence(repl: RawRepl, req: CaptureRequest, did: dict,
                    reset: Callable[[], None]) -> None:
    """Steps 2 to 6 of `select_project`, which `load_held` shares: the one
    reset sequence, with what happens once the inputs are set left to
    `reset`.

    Stops the clock, writes the mode and turns off the monitor, writes
    `ui_in` and reads it back, recording in `did` what `restore_mode`
    puts back, and then calls `reset`. `select_project`'s runs
    `reset_design`; `load_held`'s holds reset and loads the bitstream.
    A failure after the clock stops puts the board back, as
    `select_project` describes, before the error is raised.
    """
    _exec_checked(repl, "tt.clock_project_stop()")
    # Read before the mode write below, which turns the monitor on.
    monitoring = _board_value(_exec_checked(repl, _READ_MONITORING), "monitoring")
    if req.ui_in is not None:
        # The RP2 only drives ui_in in ASIC_RP_CONTROL; the fpgas.online
        # boards sit in ASIC_MANUAL_INPUTS (2), where writing tt.ui_in changes
        # nothing -- measured on fpga-4. The mode it was in is returned so the
        # caller can put it back (`restore_mode`).
        out = _exec_checked(repl, _READ_MODE)
        did["mode_before"] = _board_value(out, "mode")
    try:
        if req.ui_in is not None:
            # Before the reset: every tt.mode write rebuilds every pin, and
            # the rebuild makes rst_n an input, which releases reset (#57).
            _exec_checked(repl, "from ttboard.mode import RPMode\n"
                                "tt.mode = RPMode.ASIC_RP_CONTROL")
        if monitoring >= 0:
            # After the mode write, which turns it on in ASIC_RP_CONTROL.
            did["monitoring_before"] = bool(monitoring)
            _exec_checked(repl, "tt.manual_project_clock.monitoring = False")
        if req.ui_in is not None:
            out = _exec_checked(repl, "tt.ui_in.value = %d\n%s" % (req.ui_in, _READ_UI_IN))
            got = _board_value(out, "ui_in")
            if got != req.ui_in:
                raise CaptureError(
                    "ui_in reads 0x%02x after writing 0x%02x: entering "
                    "ASIC_RP_CONTROL, the stock firmware leaves undriven any "
                    "input that reads high, for fear of contention" % (got, req.ui_in))
            did["ui_in"] = req.ui_in
        reset()
    except BaseException as exc:
        # The caller never sees `did`, and its teardown does not cover a
        # setup that raised, so the board is put back here: the clock
        # stopped, and the mode and monitor as they were. A shared board is
        # not left clocked, in ASIC_RP_CONTROL or without its monitor.
        undo_selection(repl, did, stop=True, error=exc)
        raise


#: How long `reset_design` waits for `rst_n` to read 1 after the release.
#: The release is the board's pull-up against the RP2's internal pull-down
#: (#58). On fpga-4 rst_n read 1 at the first poll (2026-11-27).
RST_N_RELEASE_TIMEOUT_MS = 100

#: Fewest periods of the requested clock `reset_design` holds reset for. The wait is
#: on the board, between the clock starting and the release, so it does
#: not depend on the link's latency.
RESET_CLOCKS = 16

_READ_MODE = "print('ttcap mode=%d' % int(tt.mode))"
_READ_RST_N = "print('ttcap rst_n=%d' % tt.rst_n())"
_READ_UI_IN = "print('ttcap ui_in=%d' % int(tt.ui_in.value))"


def reset_hold_ms(clock_hz: float) -> int:
    """Milliseconds that hold `RESET_CLOCKS` edges at `clock_hz`, at least 1."""
    hz = max(1, int(clock_hz))
    return max(1, -(-RESET_CLOCKS * 1000 // hz))


def release_command(clock_hz: float) -> str:
    """The board command that ends the reset: wait `reset_hold_ms` with the
    clock running, release, then wait up to `RST_N_RELEASE_TIMEOUT_MS` for
    `rst_n` to read 1, timed on the board, and print the time and the level."""
    return "\n".join([
        "import time",
        "time.sleep_ms(%d)" % reset_hold_ms(clock_hz),
        "tt.reset_project(False)",
        "_t = time.ticks_ms()",
        "while not tt.rst_n() and time.ticks_diff(time.ticks_ms(), _t) < %d:"
        % RST_N_RELEASE_TIMEOUT_MS,
        "    time.sleep_ms(1)",
        "print('ttcap rst_n_wait_ms=%d' % time.ticks_diff(time.ticks_ms(), _t))",
        "del _t",
        _READ_RST_N,
    ])


#: Whether the manual-clock monitor is on (1), off (0) or absent (-1): SDK
#: 3.1.1 makes one on TTDBv3.3 and later (`demoboard.py:146-150`), and
#: fpga-4's `tt.manual_project_clock` was None (2026-11-27). Its
#: 3 Hz timer calls `clock_project_once()` after an edge on the manual-clock
#: pin, which stops the PWM (`manual_clock.py:76-87`, `demoboard.py:317-330`).
_READ_MONITORING = (
    "_m = getattr(tt, 'manual_project_clock', None)\n"
    "print('ttcap monitoring=%d' % (-1 if _m is None else int(bool(_m.monitoring))))\n"
    "del _m"
)


def _board_value(stdout: str, key: str) -> int:
    """The integer in a command's `ttcap <key>=N` line.

    The SDK logs at INFO on stdout ("Setting mode to ...", "Changing reset
    to output mode"), so the line is looked for rather than assumed last.
    """
    prefix = "ttcap %s=" % key
    for line in reversed(stdout.splitlines()):
        line = line.strip()
        if line.startswith(prefix):
            try:
                return int(line[len(prefix):])
            except ValueError:
                break
    raise CaptureError(f"board did not report {key}, said {stdout.strip()!r}")


def hold_reset(repl: RawRepl) -> None:
    """Hold the design in reset: `tt.reset_project(True)`, and `rst_n` must
    read 0. The first step of `reset_design`, and the last before the load
    in `load_held`. If the read-back fails, reset is released before the
    error is raised."""
    _exec_checked(repl, "tt.reset_project(True)")
    try:
        if _board_value(_exec_checked(repl, _READ_RST_N), "rst_n") != 0:
            raise CaptureError(
                "rst_n reads 1 while reset is held: tt.reset_project(True) did not drive it low")
    except BaseException as exc:
        _release_reset(repl, exc)
        raise


def _release_reset(repl: RawRepl, exc: BaseException) -> None:
    """Release reset after `exc`, noting on it a release that fails too."""
    try:
        _exec_checked(repl, "tt.reset_project(False)")
    except Exception as again:  # noqa: BLE001 - reported on exc
        exc.add_note(f"releasing reset failed too: {again}")


def reset_design(repl: RawRepl, clock: str, clock_hz: float) -> int:
    """Reset the design with its clock running: the one reset sequence.

    1. `tt.reset_project(True)`, and `rst_n` must read 0;
    2. start the clock with `clock` (a `clock_command()` for `clock_hz`);
    3. in one board command (`release_command`): wait `reset_hold_ms`, so
       reset is held for at least `RESET_CLOCKS` periods of `clock_hz` and 1 ms,
       release, and wait up to `RST_N_RELEASE_TIMEOUT_MS` for `rst_n` to
       read 1.

    Returns the milliseconds `rst_n` took to read 1, as the board timed it.
    A release the pull-up does not win is an error, not a capture of the
    reset state. If any step after the first fails, reset is released
    before the error is raised, so a failed setup does not leave the design
    held in reset with `rst_n` driven low. Nothing may write `tt.mode`
    after this: the write rebuilds the pins and releases reset.
    """
    hold_reset(repl)
    try:
        _exec_checked(repl, clock)
        # The board sleeps reset_hold_ms first (16 s at 1 Hz), and the
        # exec timeout is one deadline for the whole command.
        out = _exec_checked(repl, release_command(clock_hz),
                            timeout=10.0 + reset_hold_ms(clock_hz) / 1000)
        waited = _board_value(out, "rst_n_wait_ms")
        if _board_value(out, "rst_n") != 1:
            raise CaptureError(
                "rst_n stuck low after release: it read 0 for %d ms after "
                "tt.reset_project(False)" % waited)
    except BaseException as exc:
        _release_reset(repl, exc)
        raise
    return waited


def cleanup_names(cfg: dict) -> list[str]:
    """What `prepare_board` deletes: the module-level names of EVERY capture
    program, not just the next one, so switching between stream and frame
    mode does not leave the other program's globals holding the heap."""
    names: set[str] = set()
    for script in CAPTURE_SCRIPTS:
        names.update(mp.module_level_names(mp.build(cfg, script)))
    # `load_held`'s body runs into the same globals and leaves its names
    # (`Pin`, `fabricfoxv2`, `HELD`) for the next capture to clear.
    names.update(mp.module_level_names(mp.build(cfg, LOAD_HELD_SCRIPT)))
    return sorted(names)


#: The RP2350's rated system clock. The SDK's `clock_project_PWM` scans down
#: from `max_rp2040_freq` (133 MHz unless told otherwise) for the first
#: system clock that is an even multiple of the project clock, so 25 MHz
#: gets 100 MHz: 4 system clocks a project clock.
RP2350_SYSCLK_HZ = 150_000_000


def clock_command(req: CaptureRequest) -> str:
    """The `tt.clock_project_PWM` call for this capture.

    PIO run counting in its locked style needs at least 6 system clocks a
    project clock (`rle_rp2.py`), which the SDK's default choice cannot give
    at 25 MHz. On an RP2350 an `rle_rp2.py` capture therefore lets the SDK
    go up to the chip's rated 150 MHz: 25 MHz then gets 150 MHz, 6 to 1.
    Measured on fpga-4, 2026-11-24: prbs exact at 25 MHz with sample points
    rising+0/+1/+4/+5 (offsets 2-3 after the edge are where uo_out changes).
    Every other capture keeps the SDK's default choice, under which its
    sample points were measured.
    """
    hz = int(req.clock_hz)
    if req.is_rle and req.profile.pio_gpio_base == 16:  # the RP2350 profile
        return f"tt.clock_project_PWM({hz}, max_rp2040_freq={RP2350_SYSCLK_HZ})"
    return f"tt.clock_project_PWM({hz})"


def prepare_board(repl: RawRepl, names: list[str], timeout: float = 10.0) -> int:
    """Clear a previous run's leftovers and return the free heap in bytes.

    Deletes `names` (every capture program's module-level names, from
    `cleanup_names`), collects, and reads `gc.mem_free()`. On tt07 that
    recovered about 5 KB (79,296 -> 84,208 free), which was the difference
    between a capture script that compiled and one that did not.
    """
    stdout, stderr = repl.exec(_CLEANUP % (names,), timeout=timeout)
    if stderr.strip():
        raise CaptureError(f"board rejected the cleanup:\n{stderr.strip()}")
    try:
        return int(stdout.strip())
    except ValueError:
        raise CaptureError(
            f"board did not report gc.mem_free(), said {stdout.strip()!r}"
        ) from None


def stop_clock(repl: RawRepl) -> None:
    """Stop the project clock, leaving the board idle between captures."""
    _exec_checked(repl, "tt.clock_project_stop()")


def restore_mode(repl: RawRepl, did: dict) -> None:
    """Put back the RPMode and the manual-clock monitor `select_project`
    changed, as recorded in `did`.

    The mode goes first: its write turns the monitor on in ASIC_RP_CONTROL
    and off in every other mode, whatever it was before.
    """
    if "mode_before" in did:
        _exec_checked(repl, "tt.mode = %d" % int(did["mode_before"]))
    if "monitoring_before" in did:
        _exec_checked(repl, "tt.manual_project_clock.monitoring = %s"
                      % bool(did["monitoring_before"]))


def undo_selection(
    repl: RawRepl, did: dict, stop: bool, error: BaseException | None = None
) -> None:
    """Undo what `select_project` returned in `did`: stop the clock when
    `stop`, and put back the RPMode and the manual-clock monitor it changed.

    Both steps are tried even if one fails. With `error` -- the exception
    a failed capture is already raising -- a failure here becomes a note on
    it rather than replacing it; without one, the first failure is raised.
    """
    steps = []
    if stop:
        steps.append(lambda: stop_clock(repl))
    if "mode_before" in did or "monitoring_before" in did:
        steps.append(lambda: restore_mode(repl, did))
    first = None
    for step in steps:
        try:
            step()
        except Exception as exc:  # noqa: BLE001 - reported below
            if error is not None:
                error.add_note(f"putting the board back failed too: {exc}")
            elif first is None:
                first = exc
    if first is not None:
        raise first


#: The directory `load_held` puts the bitstream in, which `RawRepl.upload()`
#: does not make, and the file.
HELD_DIR = "/_vgacap"
HELD_PATH = HELD_DIR + "/held.bin"

#: Seconds `load_held` gives the board body: the loader feeds the FPGA a
#: byte at a time from MicroPython, so a 104 KB bitstream takes seconds.
LOAD_HELD_TIMEOUT = 60.0

#: The board body `load_held` runs after the prelude.
LOAD_HELD_SCRIPT = "load_held_rp2.py"

#: The project clock's GPIO on the RP2350 demo board v3
#: (`gpio_map_dbv3.py`), the only board with the FPGA breakout's loader.
FPGA_CLK_GPIO = 16

#: The iCE40 synchronisation word. The FPGA ignores what comes before it;
#: icepack writes `FF 00`, its comment lines, `00 FF`, then this word.
SYNC_WORD = b"\x7e\xaa\x99\x7e"

#: The sync word must start in the first SYNC_WINDOW bytes. The tt-vga-testpatterns
#: bitstreams have it at byte 4 (no comment lines). 1024 bytes leaves room
#: for the comment lines other tools write between `FF 00` and `00 FF`, and
#: still rejects a file that holds the word only by chance, deep inside.
SYNC_WINDOW = 1024

#: Failures after which the board may still be running the command, so
#: nothing more may be sent until the caller has `recover()`ed.
_NO_REPLY = (TimeoutError, LinkClosed, KeyboardInterrupt)
#: What a clocked capture also treats as one: a framing loss mid-stream,
#: after which the body may still be running and `recover()` may return ""
#: without proving otherwise (#94, re-review U1).
_CLOCKED_NO_REPLY = _NO_REPLY + (ReplFramingError,)


class LoadHeldTimeout(TimeoutError):
    """`load_held`'s board body did not reply in time.

    `selection` is the dict `load_held` would have returned, without
    `bytes`: after `repl.recover()`, `undo_load_held(repl, selection)`
    puts the board back.
    """

    def __init__(self, message: str, selection: dict) -> None:
        super().__init__(message)
        self.selection = selection

#: Make a directory, an existing one being fine (MicroPython's EEXIST is 17).
_MKDIR = """\
def _ttcap_mkdir(p):
    import os
    try:
        os.mkdir(p)
    except OSError as e:
        if e.errno != 17:
            raise
try:
    _ttcap_mkdir({path!r})
finally:
    del _ttcap_mkdir
"""

#: Delete `HELD_PATH`, then `HELD_DIR` if that leaves it empty (MicroPython's
#: ENOENT is 2 and ENOTEMPTY 39), leaving no name in the REPL's globals. With
#: `missing` true, a file that is not there is fine and the directory is
#: still tried.
_REMOVE_TEMPLATE = """\
def _ttcap_remove(p, d, m):
    import os
    try:
        os.remove(p)
    except OSError as e:
        if not (m and e.errno == 2):
            raise
    try:
        os.rmdir(d)
    except OSError as e:
        if e.errno not in (2, 39):
            raise
try:
    _ttcap_remove({path!r}, {dir!r}, {missing!r})
finally:
    del _ttcap_remove
"""

#: After a good load: the file must be there.
_REMOVE = _REMOVE_TEMPLATE.format(path=HELD_PATH, dir=HELD_DIR, missing=False)

#: After a failure: the file may never have been made, or already be gone.
_REMOVE_IF_THERE = _REMOVE_TEMPLATE.format(path=HELD_PATH, dir=HELD_DIR, missing=True)

#: Whether `tt.shuttle` is the FPGA breakout's mux: `FPGAMux.run` is 'FPGA'
#: (tt-micropython-firmware v3.1.1, `fpga/fpga_mux.py:163`), and an ASIC
#: carrier's `ProjectMux.run` is its shuttle, such as 'tt06'. The GPIO map
#: cannot tell them apart, and on a carrier the loader's GPIO 1 is the TT
#: mux's reset.
_FPGA_CHECK = "print('ttcap fpga=%d' % (getattr(tt.shuttle, 'run', None) == 'FPGA'))"

_LOAD_DONE = re.compile(r"Transmission complete, total bytes: (\d+)")
_LOAD_ERROR = "Error accessing file:"


def load_held_cfg(profile: BoardProfile) -> dict:
    """The `CFG` dict for `load_held_rp2.py`: the prelude's keys (from
    `capture_cfg`) and the bitstream's `path` on the board."""
    return dict(capture_cfg(profile), path=HELD_PATH)


def _check_load(stdout: str, stderr: str, want: int) -> int:
    """The byte count of a held load whose body printed `stdout`, which
    must be `want`, with `rst_n` and the clock pad reading 0 after it.

    The loader catches its own `OSError` and only prints it
    (`fabricfoxv2.py:169-170` in tt-micropython-firmware v3.1.1), so a
    failed load is found in what it printed, not in `stderr`.
    """
    if stderr.strip():
        raise CaptureError(f"the held load failed on the board:\n{stderr.strip()}")
    if not stdout.strip():
        raise CaptureError("the held load said nothing: the board body printed no line")
    lines = [line.strip() for line in stdout.splitlines()]
    errors = [line for line in lines if line.startswith(_LOAD_ERROR)]
    if errors:
        raise CaptureError(f"the loader failed and carried on: {errors[0]!r}")
    counts = [m for m in (_LOAD_DONE.fullmatch(line) for line in lines) if m]
    if not counts:
        raise CaptureError(
            "the loader printed no 'Transmission complete, total bytes: N' "
            f"line, said {stdout.strip()!r}")
    if len(counts) > 1:
        raise CaptureError(
            f"the loader printed {len(counts)} lines of 'Transmission "
            "complete', for one load")
    got = int(counts[0].group(1))
    if got != want:
        raise CaptureError(
            f"the loader fed the FPGA {got} bytes of a {want}-byte bitstream "
            f"({counts[0].group(0)!r})")
    if _board_value(stdout, "rst_n") != 0:
        raise CaptureError("rst_n reads 1 after the load: reset was not held through it")
    if _board_value(stdout, "clk") != 0:
        raise CaptureError(
            "the clock pad reads 1 after the load: it was not held low through it")
    return got


def _check_bitstream(data: bytes) -> None:
    """Refuse a file that cannot configure an iCE40, before the board is
    touched: the loader clears the FPGA first, and its count would match
    an empty or wrong file all the same."""
    if not data:
        raise ValueError("the bitstream is empty: loading it would leave the FPGA blank")
    # find()'s end bounds where the word may end, so the last start it can
    # return is SYNC_WINDOW - 1: the word must start in the window.
    if data.find(SYNC_WORD, 0, SYNC_WINDOW - 1 + len(SYNC_WORD)) < 0:
        raise ValueError(
            "no iCE40 sync word 7E AA 99 7E starting in the bitstream's first %d "
            "bytes: this is not an iCE40 bitstream" % SYNC_WINDOW)


def _delete_held(repl: RawRepl, exc: BaseException) -> None:
    """Delete `HELD_PATH` after `exc`, noting on it a delete that fails too."""
    try:
        _exec_checked(repl, _REMOVE_IF_THERE)
    except Exception as again:  # noqa: BLE001 - reported on exc
        exc.add_note(f"deleting {HELD_PATH} failed too: {again}")


def undo_load_held(repl: RawRepl, did: dict, error: BaseException | None = None) -> None:
    """Put the board back after a `load_held` that failed: delete
    `HELD_PATH`, release reset, stop the clock, and put the mode and
    monitor `did` records back.

    For a load that timed out, `did` is the exception's `selection`, and
    `repl.recover()` comes first. Not for a load that worked: until the
    clocked capture drives the clock pin, nothing may stop the clock or
    write the mode (see `load_held`). Each step is tried even if one
    fails; with `error`, failures become notes on it, as in
    `undo_selection`, and without, the first is raised.
    """
    steps = [
        lambda: _exec_checked(repl, _REMOVE_IF_THERE),
        lambda: _exec_checked(repl, "tt.reset_project(False)"),
    ]
    first = None
    for step in steps:
        try:
            step()
        except Exception as exc:  # noqa: BLE001 - reported below
            if error is not None:
                error.add_note(f"putting the board back failed too: {exc}")
            elif first is None:
                first = exc
    try:
        undo_selection(repl, did, stop=True, error=error)
    except Exception as exc:  # noqa: BLE001 - reported below
        if first is None:
            first = exc
    if first is not None:
        raise first


def load_held(repl: RawRepl, req: CaptureRequest, path_on_host) -> dict:
    """Load the FPGA bitstream at `path_on_host` with the project clock held
    low and `rst_n` held low, so no clock edge reaches the design before
    clock 0.

    Before any board command it refuses (`ValueError`) a request that
    names a design, a profile other than the FPGA breakout's demo board
    (RP2350, clock on GPIO `FPGA_CLK_GPIO`), an empty file, and a file
    without the iCE40 sync word starting in its first `SYNC_WINDOW` bytes.
    Then:

    0. check that `tt.shuttle` is the FPGA mux (`run` is 'FPGA'), else raise
       `CaptureError`: the GPIO layout an ASIC carrier shares cannot tell;
    1. make `HELD_DIR` on the board, an existing one being fine, and
       `RawRepl.upload()` the bitstream to `HELD_PATH`, which checks its
       length and crc32 there and skips the write if they already match;
    2. `select_project`'s reset sequence up to its load: stop the clock,
       set the mode, monitor and `ui_in` as it does, then `hold_reset`.
       The clock is never started;
    3. the board body, `load_held_rp2.py`: `tt.pins.safe_bidir()`,
       `tt.shuttle.reset()`, `tt.clock_project_stop()`, drive the clock pad
       low, then the SDK's `fabricfoxv2.spi_transferPIO(HELD_PATH)`;
    4. check what it printed (`_check_load`): one "Transmission complete,
       total bytes: N" line with N the bitstream's length, no "Error
       accessing file" line, and `rst_n` and the clock pad reading 0;
    5. delete `HELD_PATH`, and `HELD_DIR` if that leaves it empty.

    Returns `select_project`'s keys, with `enable` "held" and one more,
    `bytes`, the loader's byte count. It has no `rst_n_wait_ms`.

    The board afterwards. The design has seen no clock edge since the
    load, so it sits at its power-up value, not its reset value: a design
    with a synchronous reset has not been reset. The clocked capture must
    clock it with `rst_n` still low for its reset cycles before clock 0.
    The clock pad is held low by a `machine.Pin` the SDK does not know of.
    Until that capture drives the clock pin, nothing may call
    `tt.clock_project_stop()`, `stop_clock`, `undo_selection` or
    `restore_mode`, or write `tt.mode`: each un-drives GPIO16, leaving it to
    the RP2's pull-down, and a mode write releases reset too. `mode_before`
    and `monitoring_before` are for putting the board back after that
    capture.

    `tt.shuttle.enabled` is None afterwards, the SDK's own state midway
    through `FPGAMux.enable`, whose `reset_and_clock_mux()` runs the same
    `reset()` (`fpga_mux.py:137-160`). So a later `select_project` naming
    the previous design refuses it rather than taking it as enabled. The
    fpgas.online daemon's idea of the design still names the previous one.

    Errors. An upload that fails, `UploadError` or a board error, deletes
    the file and runs no load; one that gets no reply leaves the file for
    the caller to delete after `repl.recover()`. A reset sequence that fails puts the board back as
    `select_project` does and deletes the file, unless it timed out. A load
    that fails where the board answered (step 4, or a traceback) deletes
    the file, releases reset, stops the clock and puts the mode and monitor
    back before raising `CaptureError`. A body that does not reply sends
    nothing more, since the loader may still be running: a timeout raises
    `LoadHeldTimeout`, and it and any other such exception carry
    `selection`. The caller must `repl.recover()`, as after a timed-out
    `upload()`, then `undo_load_held(repl, exc.selection)`.
    """
    if req.selection:
        raise ValueError(
            f"load_held loads its own bitstream, but the request names {req.selection!r}")
    profile = req.profile
    if profile.chip != "rp2350" or profile.clk_gpio != FPGA_CLK_GPIO:
        raise ValueError(
            f"load_held needs the FPGA breakout's demo board (RP2350, clock on GPIO "
            f"{FPGA_CLK_GPIO}), but profile {profile.name} has the clock on GPIO "
            f"{profile.clk_gpio}")
    data = Path(path_on_host).read_bytes()
    _check_bitstream(data)
    body = mp.build(load_held_cfg(profile), LOAD_HELD_SCRIPT)
    did: dict = {"project": None, "enable": "held", "clock_hz": req.clock_hz}
    if not _board_value(_exec_checked(repl, _FPGA_CHECK), "fpga"):
        raise CaptureError("load_held needs the FPGA breakout: tt.shuttle is not the FPGA mux")
    _exec_checked(repl, _MKDIR.format(path=HELD_DIR))
    try:
        repl.upload(HELD_PATH, data)
    except BaseException as exc:
        if not isinstance(exc, _NO_REPLY):
            _delete_held(repl, exc)
        raise
    try:
        _reset_sequence(repl, req, did, lambda: hold_reset(repl))
    except BaseException as exc:
        if not isinstance(exc, _NO_REPLY):
            _delete_held(repl, exc)
        raise
    try:
        stdout, stderr = repl.exec(body, timeout=LOAD_HELD_TIMEOUT)
    except BaseException as exc:
        note = ("the held load got no reply and may still be running: call "
                "recover() on the REPL, then undo_load_held(repl, exc.selection)")
        if isinstance(exc, TimeoutError):
            timeout = LoadHeldTimeout(str(exc), did)
            timeout.add_note(note)
            raise timeout from exc
        exc.selection = did  # pyright: ignore[reportAttributeAccessIssue]
        exc.add_note(note)
        raise
    try:
        did["bytes"] = _check_load(stdout, stderr, len(data))
        _exec_checked(repl, _REMOVE)
    except BaseException as exc:
        undo_load_held(repl, did, error=exc)
        raise
    return did


# -- clocked capture ----------------------------------------------------------


@dataclass(frozen=True)
class ClockedTiming:
    """The clock a clocked capture makes: `clocked_rp2.py`'s H and L, and
    the state machine's integer divider, at the board's `sysclk_hz`.

    One clock is `cycles_per_clock` = H + L + 4 PIO cycles of `pio_div`
    system clocks each, and the `in` reads uo_out H + 1 PIO cycles after
    the rising edge.
    """

    sysclk_hz: int
    pio_div: int
    high: int
    low: int

    @property
    def cycles_per_clock(self) -> int:
        """PIO cycles in one project clock."""
        return self.high + self.low + CLOCKED_FIXED_CYCLES

    @property
    def clock_hz(self) -> float:
        """The real project clock: sysclk / (pio_div x cycles_per_clock)."""
        return self.sysclk_hz / (self.pio_div * self.cycles_per_clock)

    @property
    def sample_ns(self) -> float:
        """Nanoseconds from the rising edge to the `in` that reads uo_out."""
        return (self.high + 1) * self.pio_div * 1e9 / self.sysclk_hz

    @property
    def below_floor(self) -> bool:
        """The read is under `CLOCKED_SAMPLE_NS` after the edge: only an
        explicit H for a sweep does that, and the run is a sweep point,
        not a reference."""
        return self.high < _least_high(self.sysclk_hz, self.pio_div)

    def cfg(self) -> dict:
        """The keys `clocked_rp2.py` takes from this, `sysclk_hz` the value
        the board checks `machine.freq()` against, `below_floor` what its
        opening record repeats."""
        return {"sysclk_hz": self.sysclk_hz, "pio_div": self.pio_div,
                "high": self.high, "low": self.low, "below_floor": int(self.below_floor)}


#: Words the RX FIFO holds, unjoined: `clocked_rp2.py` joins neither way.
CLOCKED_RX_FIFO_WORDS = 4


def clocked_reset_limit(clocks: int, buf_words: int) -> int:
    """The most reset clocks a clocked capture can make before its `irq`.

    During the reset loop the board only waits for the irq, with buffer 0
    armed for min(buf_words, clocks / 4) words, so every word pushed before
    the irq has to fit there or in the RX FIFO: R samples push floor(R / 4)
    words (`push iffull` at 32 bits), and the rest wait in the ISR. One
    more push and the PIO stalls, low, before the irq: no handshake.
    """
    return 4 * (min(buf_words, clocks // 4) + CLOCKED_RX_FIFO_WORDS) + 3


#: The request fields `clocked_count_problem` names; the CLI passes its
#: options instead.
CLOCKED_COUNT_NAMES = ("clocks", "reset_clocks", "buf_words", "clock_high")


def clocked_count_problem(clocks: int, reset_clocks: int, buf_words: int,
                          clock_high: int | None = None,
                          names: tuple = CLOCKED_COUNT_NAMES) -> str | None:
    """Why these counts make no clocked capture, or None: what needs no
    board, so the CLI refuses it as a usage error and a request as a
    `ValueError`. `names` are what the message calls the four."""
    n_clocks, n_reset, n_buf, n_high = names
    if reset_clocks < 1:
        return f"{n_reset} must be at least 1, got {reset_clocks}"
    if clocks <= 0 or clocks % 4:
        return (f"{n_clocks} must be a positive multiple of 4, so that every word is "
                f"full, got {clocks}")
    if clocks <= reset_clocks:
        return f"{n_clocks} must be more than {n_reset} ({reset_clocks}), got {clocks}"
    if clocks > 1 << 32:
        return f"{n_clocks} must be at most 2**32, got {clocks}"
    limit = clocked_reset_limit(clocks, buf_words)
    if reset_clocks > limit:
        return (f"{n_reset} must be at most {limit} with {n_buf} {buf_words}: more "
                f"fill the DMA buffer and the RX FIFO before reset is released")
    if clock_high is not None and not 0 <= clock_high <= CLOCKED_MAX_PHASE:
        return f"{n_high} must be 0..{CLOCKED_MAX_PHASE}, got {clock_high}"
    return None


def _least_high(sysclk_hz: int, pio_div: int) -> int:
    """The smallest H whose read, H + 1 PIO cycles after the rising edge,
    is at least `CLOCKED_SAMPLE_NS` after it."""
    return max(0, -(-CLOCKED_SAMPLE_NS * sysclk_hz // (1_000_000_000 * pio_div)) - 1)


def _fastest_clock(sysclk_hz: int, high: int | None) -> tuple[int, int] | None:
    """(pio_div, cycles) of the fastest clock at `sysclk_hz`: with H at the
    floor, or `high` if given, and L 0. None if no divider can make one."""
    best = None
    for pio_div in range(1, CLOCKED_MAX_DIV + 1):
        if best is not None and pio_div * CLOCKED_FIXED_CYCLES > best[0] * best[1]:
            break
        h = _least_high(sysclk_hz, pio_div) if high is None else high
        if h > CLOCKED_MAX_PHASE:
            continue  # the 4-bit delay cannot wait that long at this divider
        if best is None or pio_div * (CLOCKED_FIXED_CYCLES + h) < best[0] * best[1]:
            best = (pio_div, CLOCKED_FIXED_CYCLES + h)
    return best


def clocked_timing(sysclk_hz: int, clock_hz: int, high: int | None = None) -> ClockedTiming:
    """The clock nearest `clock_hz` that is not faster, at `sysclk_hz`.

    Every integer `pio_div` is tried with the fewest cycles a clock (H + L +
    4, H and L 0..15) may take at it that is not faster than asked, and the
    fastest result wins, the smaller divider on a tie. 500 kHz at 150 MHz is
    pio_div 10 with 30 cycles, exactly. H is then half the variable cycles,
    rounded up, so the read is late in the high phase, and never less than
    puts the read `CLOCKED_SAMPLE_NS` after the edge, which also bounds how
    few cycles a divider may take.

    `high` sets H instead, for a sweep of the sample point: the clock is the
    nearest not-faster one in which that H leaves L in 0..15, and the floor
    does not apply (`ClockedTiming.below_floor` says when the read is
    earlier). Refused (`ValueError`): a clock faster than the fastest one
    that reads `CLOCKED_SAMPLE_NS` late (at sysclks where H would pass 15 at
    divider 1, a larger divider's), a clock slower than the largest divider
    makes, and an H that fits no clock.
    """
    if sysclk_hz <= 0 or clock_hz <= 0:
        raise ValueError(f"sysclk_hz and clock_hz must be positive, got {sysclk_hz}, {clock_hz}")
    if high is not None and not 0 <= high <= CLOCKED_MAX_PHASE:
        raise ValueError(f"clock_high must be 0..{CLOCKED_MAX_PHASE}, got {high}")
    most = CLOCKED_FIXED_CYCLES + 2 * CLOCKED_MAX_PHASE
    first = max(1, -(-sysclk_hz // (clock_hz * most)))
    if first > CLOCKED_MAX_DIV:
        raise ValueError(
            "%d Hz is below the slowest clocked rate at sysclk %d Hz, %.1f Hz"
            % (clock_hz, sysclk_hz, sysclk_hz / (CLOCKED_MAX_DIV * most)))
    # A rate above the fastest clock there is is refused rather than
    # quietly slowed to it.
    fastest = _fastest_clock(sysclk_hz, high)
    if fastest is None or -(-sysclk_hz // clock_hz) < fastest[0] * fastest[1]:
        raise ValueError(
            "%d Hz is above the fastest clocked rate at sysclk %d Hz, %d Hz, the "
            "fastest that reads uo_out %d ns after the rising edge"
            % (clock_hz, sysclk_hz, sysclk_hz // (fastest[0] * fastest[1]) if fastest else 0,
               CLOCKED_SAMPLE_NS if high is None else
               round((high + 1) * 1e9 / sysclk_hz)))
    best = None  # (pio_div, cycles)
    for pio_div in range(first, CLOCKED_MAX_DIV + 1):
        if best is not None and pio_div * CLOCKED_FIXED_CYCLES > best[0] * best[1]:
            break  # even the fewest cycles at this divider are slower than the best
        cycles = -(-sysclk_hz // (clock_hz * pio_div))
        if high is None:
            least = _least_high(sysclk_hz, pio_div)
            if least > CLOCKED_MAX_PHASE:
                continue  # the floor needs an H the 4-bit delay cannot hold
            cycles = max(cycles, CLOCKED_FIXED_CYCLES + least)
        else:
            cycles = max(cycles, CLOCKED_FIXED_CYCLES + high)
            if cycles - CLOCKED_FIXED_CYCLES - high > CLOCKED_MAX_PHASE:
                continue
        if cycles > most:
            continue
        if best is None or pio_div * cycles < best[0] * best[1]:
            best = (pio_div, cycles)
            if sysclk_hz % (pio_div * cycles) == 0 and sysclk_hz // (pio_div * cycles) == clock_hz:
                break  # exact, at the smallest divider that is
    if best is None:
        raise ValueError(
            f"with H = {high}, no divider makes a clock of {clock_hz} Hz or slower "
            f"with the low phase's L in 0..{CLOCKED_MAX_PHASE}")
    pio_div, cycles = best
    if high is None:
        high = max(_least_high(sysclk_hz, pio_div), (cycles - CLOCKED_FIXED_CYCLES + 1) // 2)
        if high > CLOCKED_MAX_PHASE:
            raise ValueError(
                f"reading uo_out {CLOCKED_SAMPLE_NS} ns late needs H = {high} at sysclk "
                f"{sysclk_hz} Hz, more than {CLOCKED_MAX_PHASE}")
    low = cycles - CLOCKED_FIXED_CYCLES - high
    # The program's delays are 4-bit fields: whatever the search above
    # chose, a value outside them must never reach the board (re-review R1-1).
    if not (0 <= high <= CLOCKED_MAX_PHASE and 0 <= low <= CLOCKED_MAX_PHASE):
        raise ValueError(
            f"no clocked program for {clock_hz} Hz at sysclk {sysclk_hz} Hz: pio_div "
            f"{pio_div} gives H = {high}, L = {low}, and both must be 0..{CLOCKED_MAX_PHASE}")
    return ClockedTiming(sysclk_hz=sysclk_hz, pio_div=pio_div, high=high, low=low)


def clocked_cfg(profile: BoardProfile, clocks: int, reset_clocks: int = CLOCKED_RESET_CLOCKS,
                buf_words: int = DEFAULT_BUF_WORDS, pio: int = DEFAULT_PIO,
                sm: int = 0) -> dict:
    """The `CFG` dict for `clocked_rp2.py`, but for the clock, which
    `ClockedTiming.cfg()` adds once the board's sysclk is known."""
    cfg = capture_cfg(profile, buf_words=buf_words, pio=pio, sm=sm)
    del cfg["max_bytes"]  # the run is `clocks` long
    cfg.update(clocks=clocks, reset_clocks=reset_clocks)
    return cfg


#: The opening record `clocked_rp2.py` writes, its run parameters.
_CLOCKED_OPENING = "clocked clocks=%d reset_clocks=%d pio_div=%d cycles_per_clock=%d"

def clocked_opening(req: CaptureRequest, timing: ClockedTiming) -> str:
    """The opening record a clocked run of `req` at `timing` must write."""
    want = _CLOCKED_OPENING % (req.clocks, req.reset_clocks, timing.pio_div,
                               timing.cycles_per_clock)
    return want + " below_floor=1" if timing.below_floor else want


_READ_SYSCLK = "import machine\nprint('ttcap sysclk=%d' % machine.freq())"


def clocked_problems(req: CaptureRequest, timing: ClockedTiming,
                     stats: CaptureStats) -> list[str]:
    """Why a clocked run is not clean; empty when it is.

    Clean means: the `RAW ` sample counts add up to `clocks` and to the
    closing record's `samples=`; `status=ok`; the opening record matches
    the request; `overruns=0`; and a closing record is there.
    """
    problems = []
    want = clocked_opening(req, timing)
    opening = next((m for m in stats.messages if m.startswith("clocked ")), None)
    if opening != want:
        problems.append(f"the opening record is {opening!r}, not {want!r}")
    if opening is not None:
        # The board reads SMn_CLKDIV back after writing it (review I1).
        div = _keyed_str(opening, "pio_div")
        frac = _keyed_str(opening, "pio_div_frac")
        if div != str(timing.pio_div) or frac is not None:
            problems.append(
                "CLKDIV reads pio_div=%s%s, not the %d worked out"
                % (div, "" if frac is None else " pio_div_frac=" + frac, timing.pio_div))
    if not any(_keyed_str(m, "status") is not None for m in stats.messages):
        problems.append("no closing record")
    elif stats.status != "ok":
        problems.append(f"status={stats.status}")
    if stats.samples != req.clocks:
        problems.append(f"the RAW chunks hold {stats.samples} samples, not clocks={req.clocks}")
    if stats.board_samples != stats.samples:
        problems.append(
            f"the closing record says samples={stats.board_samples}, the RAW chunks "
            f"hold {stats.samples}")
    if stats.overruns:
        problems.append(f"overruns={stats.overruns}")
    return problems


def undo_clocked(repl: RawRepl, did: dict, error: BaseException | None = None) -> None:
    """Put the board back after a clocked capture, whether it ran or not:
    stop the clock, release reset, and put back the mode and monitor `did`
    records (what `load_held` returned).

    Each SDK call is its own command, after the stream, so nothing the SDK
    prints can land in the chunk stream. `tt.clock_project_stop()` makes
    the clock pad the SDK's input with its pull-down, low; the mode write
    rebuilds the pins, so the board ends in the SDK's default pin states.
    Every step is tried even if one fails; with `error`, failures become
    notes on it, and without, the first is raised.
    """
    steps = [lambda: stop_clock(repl), lambda: _exec_checked(repl, "tt.reset_project(False)")]
    if "mode_before" in did or "monitoring_before" in did:
        steps.append(lambda: restore_mode(repl, did))
    first = None
    for step in steps:
        try:
            step()
        except Exception as exc:  # noqa: BLE001 - reported below
            if error is not None:
                error.add_note(f"putting the board back failed too: {exc}")
            elif first is None:
                first = exc
    if first is not None:
        raise first


def recover_clocked(repl: RawRepl, exc: BaseException) -> bool:
    """After a clocked capture that failed without a reply, `recover()` the
    REPL and put the board back; True if `exc` was such a failure.

    Such an exception carries `selection`: from `load_held`, without
    `bytes`, it is put back by `undo_load_held`, and after it by
    `undo_clocked`. A failure here becomes a note on `exc`.
    """
    did = getattr(exc, "selection", None)
    if did is None or not isinstance(exc, _CLOCKED_NO_REPLY):
        return False
    try:
        repl.recover()
        if "bytes" in did:
            undo_clocked(repl, did, error=exc)
        else:
            undo_load_held(repl, did, error=exc)
    except Exception as again:  # noqa: BLE001 - reported on exc
        exc.add_note(f"recovering the board failed too: {again}")
    return True


def clocked_header_bytes(req: CaptureRequest, timing: ClockedTiming) -> bytes:
    """The `VGCH` header of a clocked capture: version 1, mode `SELFCLK`,
    and `clock_hz` the real clock, rounded to the hertz. `desc` names the
    bitstream (its length and crc32, the upload's own check) and the run's
    parameters."""
    data = Path(req.bitstream).read_bytes()
    parts = [req.desc] if req.desc else []
    if req.ui_in is not None:
        parts.append("ui_in=%d" % req.ui_in)
    parts.append(
        "capture=clocked mode=selfclk profile=%s bitstream=%s bitstream_len=%d "
        "bitstream_crc32=%08x "
        "clocks=%d reset_clocks=%d sysclk=%d pio_div=%d high=%d low=%d"
        % (req.profile.name, Path(req.bitstream).name, len(data), zlib.crc32(data),
           req.clocks, req.reset_clocks, timing.sysclk_hz, timing.pio_div, timing.high,
           timing.low))
    if timing.below_floor:
        parts.append("below_floor=1")
    profile = req.profile
    buf = io.BytesIO()
    Writer(buf, Header(version=1, sample_bits=profile.sample_bits, mode=MODE_SELFCLK,
                       clock_hz=round(timing.clock_hz), signal_map=profile.signal_map,
                       samples_per_word=profile.samples_per_word, flags=profile.flags,
                       desc=" ".join(parts)))
    return buf.getvalue()


def _describe(req: CaptureRequest) -> str:
    """The header `desc` line: what the host knows and the board does not.

    The selected macro goes in automatically, so `ttcap capture --project X`
    records which design produced the samples without the caller having to
    repeat it in `--desc`. `mode=extclk` is spelled out even though the
    header's `mode` field says the same thing: `desc` is what a human reads
    out of `vgacap-dump`.
    """
    parts = []
    if req.desc:
        parts.append(req.desc)
    if req.selection:
        parts.append("project=%s" % req.selection)
    if req.ui_in is not None:
        parts.append("ui_in=%d" % req.ui_in)
    parts.append(
        "clock=%d mode=extclk edge=%s profile=%s"
        % (req.clock_hz, req.edge, req.profile.name)
    )
    if req.is_frame:
        parts.append(
            "capture=%s vga_mode=%s sample_delay=%d"
            % ("window" if req.window_lines else "frame", req.vga_mode, req.sample_delay)
        )
    if req.is_rle:
        parts.append("capture=rle sample_delay=%d" % req.sample_delay)
    return " ".join(parts)


def _time_fields(payload: bytes) -> tuple[int, str]:
    """Return `(dropped_samples, message)` from a `TIME` chunk payload."""
    if len(payload) < _TIME_HEAD.size:
        raise CaptureError(f"TIME chunk is only {len(payload)} bytes")
    _host_ns, _clock_hz, dropped, msg_len = _TIME_HEAD.unpack_from(payload, 0)
    msg = payload[_TIME_HEAD.size : _TIME_HEAD.size + msg_len]
    return dropped, msg.decode("utf-8", "replace")


def _keyed_str(msg: str, key: str) -> str | None:
    """Pull `key=<word>` out of a board summary message, or None."""
    prefix = key + "="
    for token in msg.split():
        if token.startswith(prefix):
            return token[len(prefix):]
    return None


def _keyed_int(msg: str, key: str) -> int | None:
    """Pull `key=<int>` out of a board summary message, or None."""
    prefix = key + "="
    for token in msg.split():
        if token.startswith(prefix):
            try:
                return int(token[len(prefix) :])
            except ValueError:
                return None
    return None


#: How long an abandoned capture waits for the board's own trailer after a
#: cooperative stop before falling back to a real Ctrl-C. One DMA buffer at
#: the RP2040's 60 kHz ceiling is ~137 ms at the default `buf_words`, so
#: this is well over the slowest legitimate gap at the default and far
#: below `DEFAULT_CHUNK_TIMEOUT`: this is the
#: unhappy path, and waiting out a 30 s read for a board that is not coming
#: back helps nobody.
STOP_DRAIN_TIMEOUT = 5.0


def _abandon_stream(repl: RawRepl, stream, timeout: float | None = None) -> None:
    """Leave the board at a prompt after a capture that ended early.

    Closing the generator only stops *this* side reading: the script is
    still executing on the board and still writing chunks, so the link has
    to be brought back to a prompt or every later command will read the
    capture's output instead of its own.

    Politely first -- the same cooperative stop byte a normal run sends, so
    the script finishes its chunk and emits its trailer -- and only if that
    does not end the command within `timeout` does it come to a real
    Ctrl-C. Best effort throughout: this runs while another exception is
    propagating, and that exception is the one worth reporting, so nothing
    raised here is allowed to replace it.
    """
    if timeout is None:
        timeout = STOP_DRAIN_TIMEOUT
    try:
        stream.close()
        repl.request_stop()
        if not repl.drain_chunks(timeout):
            repl.recover()
    except Exception:
        pass


def _should_stop(req: CaptureRequest, start: float, total_bytes: int) -> bool:
    """True once the run has reached whichever limit the request set.

    Evaluated *between* chunks, never during one, so all three limits
    overshoot by at most the chunk in flight -- which is the point: a chunk
    cut short would not survive the reader.
    """
    if req.stop is not None and req.stop():
        return True
    if req.seconds and time.monotonic() - start >= req.seconds:
        return True
    return bool(req.max_bytes) and total_bytes >= req.max_bytes


#: Offset of the `sample_count` field in a `FRAM` chunk payload, which opens
#: `<I frame_counter, H first_line, H line_count, I clocks_per_line,
#: I sample_count>` (`vgacap.stream.Writer.frame`, mirroring
#: `vgacap_frame_chunk` in include/vgacap/stream.h). `RAW ` puts its count
#: first, so the two tags need different offsets but are otherwise counted
#: the same way.
FRAM_SAMPLE_COUNT_OFFSET = 12

#: Where each sample-bearing tag keeps its `sample_count`. A tag that is not
#: in here contributes bytes and a chunk to the stats but no samples.
_SAMPLE_COUNT_OFFSETS = {b"RAW ": 0, b"FRAM": FRAM_SAMPLE_COUNT_OFFSET}


def stream_header_bytes(req: CaptureRequest) -> bytes:
    """The `VGCH` header chunk for `req`, as bytes.

    The board never sees this: it knows the sample data and its own
    overruns, while the signal map, the mode and the project clock are
    things only the host that just programmed them can know. Splitting it
    out of the write means a consumer that is not a file -- a pipe, a
    GStreamer buffer -- gets the identical bytes without a file object to
    write them into.
    """
    buf = io.BytesIO()
    profile = req.profile
    Writer(
        buf,
        Header(
            version=1,
            sample_bits=profile.sample_bits,
            mode=MODE_EXTCLK,
            clock_hz=req.clock_hz,
            signal_map=profile.signal_map,
            samples_per_word=profile.samples_per_word,
            flags=profile.flags,
            desc=_describe(req),
        ),
    )
    return buf.getvalue()


class CaptureSession:
    """One capture, as a stream of bytes plus the stats it accumulated.

    `chunks()` is the iterator: the `VGCH` header first, then every chunk
    the board wrote, byte for byte. `stats()` is the `CaptureStats` snapshot
    -- final once the iterator has ended or `close()` has run, and a live
    reading before that. `request_stop()` ends the capture from another
    thread, and `close()` abandons it and puts the board back at a prompt.

    A session runs once: `chunks()` returns the same generator every time,
    and a closed session cannot be restarted.

    **Threading.** Exactly one thread may drive the iterator. `request_stop()`
    is the only method another thread may call, and it is safe because every
    write to the link goes through `RawRepl`'s write lock -- pyserial and
    `websockets.sync` are not safe for concurrent writes, and a stop byte
    interleaved into a command would desynchronise the framing. `close()`
    belongs to the driving thread, because closing a generator another
    thread is suspended inside is not something CPython allows.
    """

    def __init__(
        self,
        repl: RawRepl,
        req: CaptureRequest,
        chunk_timeout: float = DEFAULT_CHUNK_TIMEOUT,
    ) -> None:
        if req.is_clocked and not isinstance(self, ClockedSession):
            raise ValueError("a clocked request runs in a ClockedSession, which loads its bitstream")
        self._repl = repl
        self._req = req
        self._chunk_timeout = chunk_timeout
        self._gen: Iterator[bytes] | None = None
        self._closed = False
        self._finished = False
        #: Guards `_streaming`/`_stop_sent` against `request_stop()` racing
        #: the iterator thread. It is *not* held across any link I/O.
        self._lock = threading.Lock()
        self._stop_requested = threading.Event()
        self._stop_sent = False
        self._streaming = False
        self._start: float | None = None
        self._elapsed = 0.0
        self._stats: CaptureStats | None = None
        self._stderr = ""
        # -- accumulated per chunk ----------------------------------------
        self._total_bytes = 0
        self._samples = 0
        self._chunks = 0
        self._overrun_reports = 0
        self._summary_overruns: int | None = None
        self._rxstall = 0
        self._dropped = 0
        self._messages: list[str] = []
        self._timed_out = False
        self._error = ""
        self._mem_free = 0
        self._status = ""
        self._board_samples = 0
        self._stalled = 0
        #: The built board script (`_prepare()`), sent by `_start_stream()`.
        self._script = ""

    # -- public API -------------------------------------------------------

    @property
    def header_bytes(self) -> bytes:
        """The `VGCH` chunk this session will yield first."""
        return stream_header_bytes(self._req)

    def chunks(self) -> Iterator[bytes]:
        """The stream: the header, then each board chunk verbatim."""
        if self._gen is None:
            self._gen = self._iter()
        return self._gen

    def stats(self) -> CaptureStats:
        """What the capture produced.

        Final once the iterator has ended or `close()` has run. Before that
        it is a live snapshot -- useful for a long-running consumer that
        wants to watch `overruns`/`dropped` climb, but `stderr` and the
        board's closing summary only arrive with the trailer.
        """
        if self._stats is not None:
            return self._stats
        return self._snapshot()

    def request_stop(self) -> None:
        """Ask the board to stop at its next safe point. Thread-safe.

        Sends the same cooperative stop byte a `seconds`/`max_bytes` limit
        sends, so the script finishes the chunk it is writing and then emits
        its closing `TIME` chunk -- the iterator keeps yielding until that
        trailer has been read, and the stream ends with a whole chunk.

        **The stream does not end immediately, and a caller that kills the
        capture too early loses the board's only overrun and RXSTALL
        report.** The byte goes out at once, but the board picks it up
        between chunk writes, so the trailer is up to one DMA buffer away:
        with `buf_words=4096`, ~22 ms on an RP2350 at 750 kHz but **~137 ms**
        on an RP2040 at its 60 kHz ceiling. An element's `stop()` should
        keep pulling
        until the iterator ends -- or wait at least that long -- before it
        escalates to `close()`, and a subprocess driver should allow the
        same before it sends a second signal.

        Idempotent, and safe before the capture has started: the request is
        remembered and the byte goes out as soon as the script is running.
        """
        self._stop_requested.set()
        self._send_stop()

    def close(self) -> None:
        """Abandon the capture and leave the board at a prompt. Idempotent.

        Closing the generator only stops *this* side reading; the script is
        still executing up there. So this runs the same recovery the failure
        paths run -- cooperative stop, drain to the terminator, a real Ctrl-C
        if the board will not come back -- because a session that leaves a
        script streaming corrupts the *next* thing its `RawRepl` does, not
        just this capture.
        """
        if self._closed:
            return
        self._closed = True
        try:
            if self._gen is not None:
                # Raises GeneratorExit inside `_iter`, whose handler does
                # the teardown; a generator that never started, or already
                # ended, takes this as a no-op.
                self._gen.close()
        finally:
            # Whatever the close did, the stats stop moving here: a caller
            # reading them after a failed teardown still gets what the
            # capture produced.
            self._finalise()

    # -- internals --------------------------------------------------------

    def _send_stop(self) -> None:
        """Write the stop byte at most once, and only while a script runs.

        The write happens **under `self._lock`**, not after releasing it.
        Checking `_streaming` and then writing outside the lock leaves a
        window in which another thread's stop has already passed the guard
        but not yet reached the link: `_end_streaming()` would then return
        while that byte was still to come, and it would land somewhere in
        `_abandon_stream()` -- or, worse, after `recover()` had resynchronised
        to the prompt, where `micropython.kbd_intr` is back to its default
        and the byte becomes a `KeyboardInterrupt` in whatever the `RawRepl`
        does next. Holding the lock across the write makes
        `_end_streaming()`'s acquisition a real fence: after it returns, no
        stop byte can still be in flight.

        This cannot deadlock. It is one byte, and `RawRepl._write`'s own lock
        is never held across a read; the iterating thread takes `self._lock`
        only here, in `_end_streaming()` and to set `_streaming`, never
        across any link I/O. Lock order is always session lock then repl
        write lock, and `RawRepl` knows nothing about a session, so there is
        no cycle.
        """
        with self._lock:
            if self._stop_sent or not self._streaming:
                return
            self._stop_sent = True
            self._repl.request_stop()

    def _end_streaming(self) -> None:
        """Close the window in which `request_stop()` may write to the link.

        Called before every teardown, so that a stop arriving from another
        thread while this one is recovering the board cannot put a stray
        Ctrl-C into the middle of that recovery. Because `_send_stop()`
        holds the same lock across its write, this waits for a stop that is
        already in flight rather than racing it. Idempotent.
        """
        with self._lock:
            self._streaming = False

    def _account(self, tag: bytes, payload: bytes) -> None:
        """Fold one chunk into the running totals."""
        self._total_bytes += CHUNK_HEADER_LEN + len(payload)
        self._chunks += 1
        offset = _SAMPLE_COUNT_OFFSETS.get(tag)
        if offset is not None:
            # `FRAM` is counted exactly like `RAW `: a whole-frame capture
            # that reported samples=0 would make the CLI exit 3 ("the
            # sampler never saw a clock edge") on a perfectly good stream.
            self._samples += struct.unpack_from("<I", payload, offset)[0]
        elif tag == b"RLE ":
            # Samples are the runs' sum: u32 pair_count, then (value, run).
            pairs = struct.unpack_from("<I", payload, 0)[0]
            self._samples += sum(struct.unpack_from("<%dI" % (2 * pairs), payload, 4)[1::2])
        elif tag == b"TIME":
            # `dropped_samples` is a running total, so the newest chunk
            # simply replaces the old value -- never `+=`.
            self._dropped, msg = _time_fields(payload)
            self._messages.append(msg)
            # Exactly "overrun": the closing summary starts "overruns="
            # and would otherwise be counted as one more overrun report.
            if msg == "overrun":
                self._overrun_reports += 1
            value = _keyed_int(msg, "overruns")
            if value is not None:
                self._summary_overruns = value
            value = _keyed_int(msg, "rxstall")
            if value is not None:
                self._rxstall = value
            status = _keyed_str(msg, "status")
            if status is not None:
                # The closing record: its counts belong to it alone.
                self._status = status
                self._board_samples = _keyed_int(msg, "samples") or 0
                self._stalled = _keyed_int(msg, "stalled_buffers") or 0

    def _snapshot(self) -> CaptureStats:
        if self._finished:
            elapsed = self._elapsed
        elif self._start is not None:
            elapsed = time.monotonic() - self._start
        else:
            elapsed = 0.0
        return CaptureStats(
            bytes=self._total_bytes,
            samples=self._samples,
            chunks=self._chunks,
            # The closing summary carries the board's own running total, so
            # it wins; the interim "overrun" chunks are increments and are
            # only a fallback for a run whose trailer never arrived.
            overruns=(
                self._summary_overruns
                if self._summary_overruns is not None
                else self._overrun_reports
            ),
            seconds=elapsed,
            rxstall=self._rxstall,
            stderr=self._stderr if self._finished else self._board_stderr(),
            messages=tuple(self._messages),
            timed_out=self._timed_out,
            dropped=self._dropped,
            error=self._error,
            mem_free_before=self._mem_free,
            status=self._status,
            board_samples=self._board_samples,
            stalled_buffers=self._stalled,
        )

    def _board_stderr(self) -> str:
        """The board's stderr, each traceback line given its file and line.

        The board compiled one concatenated script, so it numbers lines in
        that; `mp.annotate()` adds e.g. `[capture_rp2.py:123]` after each.
        """
        return mp.annotate(self._repl.last_stderr, self._req.script)

    def _finalise(self) -> None:
        """Freeze the stats. Idempotent, and must never raise."""
        if self._finished:
            return
        if self._start is not None:
            self._elapsed = time.monotonic() - self._start
        self._stderr = self._board_stderr()
        self._finished = True
        self._stats = self._snapshot()

    def _prepare(self) -> None:
        """Get the board ready, before a byte of the stream is produced.

        The MicroPython board: clear the previous run's names and refuse a
        heap that could not compile the script. A subclass for another
        board protocol overrides this and `_start_stream()`.
        """
        req, repl = self._req, self._repl
        self._script = mp.build(req.cfg(), req.script)
        needed = req.min_free()
        self._mem_free = prepare_board(repl, cleanup_names(req.cfg()))
        if self._mem_free < needed:
            if req.is_frame:
                why = "one line of frame buffer plus %d to compile the script" % (
                    COMPILE_HEADROOM_BYTES
                )
            else:
                why = (
                    "2 x %d bytes of DMA buffer for --buf-words %d, plus %d to "
                    "compile the script" % (4 * req.buf_words, req.buf_words, COMPILE_HEADROOM_BYTES)
                )
            raise CaptureError(
                "board has only %d bytes of free heap after a collect, "
                "need %d (%s); compiling it there can fail without a "
                "clean error (tt07 printed 'FATAL: uncaught exception' "
                "and halted). Reset the board%s, and try again."
                % (
                    self._mem_free,
                    needed,
                    why,
                    "" if req.is_frame else ", or ask for fewer buffer words",
                )
            )

    def _start_stream(self) -> Iterator[tuple[bytes, bytes]]:
        """The board's chunks as `(tag, payload)`, ending with its output.

        `exec_chunks()` is a generator: nothing is sent to the board until
        the first `next()`.
        """
        return self._repl.exec_chunks(self._script, timeout=self._chunk_timeout)

    def _abandon_prepared(self) -> None:
        """Undo `_prepare()` when the stream ends before it started.

        Nothing to do here: the script is only sent by the first read of
        `_start_stream()`'s generator. A protocol whose `_prepare()` starts
        the capture stops it again here.
        """

    #: A board that goes quiet mid-stream is interrupted with `recover()`
    #: and the run ends with `timed_out`. False: the `TimeoutError` is
    #: raised as it is, with nothing more sent, for the caller to recover.
    _recovers_a_quiet_board = True

    def _wind_up(self, error: BaseException | None) -> None:
        """Called once the stream is over, before the stats freeze, with the
        exception the stream is ending with, if any. Nothing to do here; a
        protocol that set the board up in `_prepare()` puts it back here."""

    def _iter(self) -> Iterator[bytes]:
        """The stream itself. See `run_capture` for what the protocol is."""
        error: BaseException | None = None
        try:
            req, repl = self._req, self._repl
            self._prepare()

            # Nothing reaches the consumer until the board has been cleaned
            # up and has the heap to compile the script, so a refused run
            # leaves no header-only file (and no header-only pipe) behind.
            try:
                yield self.header_bytes
            except BaseException:
                self._abandon_prepared()
                raise

            self._start = time.monotonic()
            stream = self._start_stream()
            try:
                for tag, payload in stream:
                    self._account(tag, payload)
                    if not self._streaming:
                        # The first chunk is the proof that the script is
                        # running, and only from here can a stop byte reach
                        # it. `exec_chunks()` is a generator, so it has not
                        # written a byte until this first `next()`: a stop
                        # sent before now would either be swallowed by the
                        # idle raw-REPL prompt or race the script upload.
                        with self._lock:
                            self._streaming = True
                        # A stop that arrived before the script was running
                        # was only remembered; now it can be delivered.
                        if self._stop_requested.is_set():
                            self._send_stop()
                    yield tag + struct.pack("<I", len(payload)) + payload
                    if not self._stop_sent and (
                        self._stop_requested.is_set()
                        or _should_stop(req, self._start, self._total_bytes)
                    ):
                        self._send_stop()
            except TimeoutError as exc:
                if not self._recovers_a_quiet_board:
                    # The protocol's own caller recovers it (ClockedSession):
                    # nothing more is sent from here.
                    self._end_streaming()
                    stream.close()
                    raise
                # The board went quiet mid-stream -- typically the state
                # machine is stuck in its `wait` because the project clock
                # is not running, or the cooperative stop byte never
                # arrived. The script is still executing up there, so
                # interrupt it for real and take its traceback: leaving it
                # would make every later command read its output.
                self._timed_out = True
                self._error = "board went quiet: %s" % exc
                self._end_streaming()
                stream.close()
                repl.recover()
            except ReplFramingError as exc:
                if not self._recovers_a_quiet_board:
                    # As for a timeout: the caller recovers the REPL.
                    self._end_streaming()
                    stream.close()
                    raise
                # The stream desynchronised, so nothing further can be read
                # by length. Same teardown: the alternative is a script that
                # keeps writing into the next command's output.
                self._error = "framing lost: %s" % exc
                self._end_streaming()
                stream.close()
                repl.recover()
            except BaseException:
                # Everything else this loop can raise: a `CaptureError` from
                # a short TIME payload, a `struct.error` from a short RAW
                # one, an operator's `KeyboardInterrupt` -- and, the reason
                # this has to be `BaseException` rather than `Exception`,
                # the `GeneratorExit` a consumer causes by closing the
                # iterator early. A GStreamer element stops consuming at
                # arbitrary moments, and every one of them must still leave
                # the board at a prompt. The exception is the caller's to
                # see, so it is re-raised once the board is quiet.
                self._end_streaming()
                _abandon_stream(repl, stream)
                raise
            finally:
                self._end_streaming()
        except BaseException as exc:
            error = exc
            raise
        finally:
            try:
                self._wind_up(error)
            finally:
                self._finalise()


class ClockedSession(CaptureSession):
    """A clocked capture: `load_held`, then `clocked_rp2.py`, in one REPL
    session, then the board put back.

    `_prepare()`, before a byte of the stream:

    1. read `machine.freq()` and work out the clock (`clocked_timing`),
       which refuses a clock it cannot make before the board is touched;
    2. clear the REPL's names and refuse a heap too small for the buffers;
    3. `load_held` the request's bitstream: reset held, the clock pad
       driven low, the mode, monitor and `ui_in` set;
    4. clear the names again (`load_held`'s), and check the heap again.

    Nothing else runs between the load and the body, which takes the clock
    pad over. The stream is the header (`clocked_header_bytes`), then the
    body's chunks verbatim. Afterwards, however it ended, `undo_clocked`
    stops the clock, releases reset and puts the mode and monitor back --
    unless the failure was one without a reply (a timeout, a closed link,
    an interrupt): then nothing more is sent, the exception gets
    `selection` and a note, and the caller runs `recover_clocked`.

    A run that ended without a host failure but is not clean
    (`clocked_problems`) says why in `CaptureStats.error`.

    `timing` and `selection` (what `load_held` returned) are there once
    `_prepare()` has set them.
    """

    #: A chunk timeout or a framing loss mid-stream is a no-reply failure
    #: like any other: the body may still be running, and `recover()` may
    #: return "" without proving otherwise (#94), so the put-back waits for
    #: the caller's `recover_clocked` (review M3, re-review U1).
    _recovers_a_quiet_board = False

    def __init__(self, repl: RawRepl, req: CaptureRequest,
                 chunk_timeout: float = DEFAULT_CHUNK_TIMEOUT) -> None:
        if not req.is_clocked:
            raise ValueError(f"ClockedSession runs {CLOCKED_SCRIPT}, not {req.script}")
        super().__init__(repl, req, chunk_timeout=chunk_timeout)
        self.timing: ClockedTiming | None = None
        self.selection: dict | None = None
        #: Called with `timing` as soon as it is worked out, before the
        #: board is loaded: the CLI's below-floor warning (re-review R1-2).
        self.on_timing: Callable[[ClockedTiming], None] | None = None

    @property
    def header_bytes(self) -> bytes:
        if self.timing is None:
            raise CaptureError(
                "a clocked capture's header has the real clock, which needs the "
                "board's sysclk: run the session first")
        return clocked_header_bytes(self._req, self.timing)

    def _check_heap(self, free: int) -> None:
        self._mem_free = free
        needed = self._req.min_free()
        if free < needed:
            raise CaptureError(
                "board has only %d bytes of free heap after a collect, need %d (2 x %d "
                "bytes of buffer for --buf-words %d, plus %d to compile the script)"
                % (free, needed, 4 * self._req.buf_words, self._req.buf_words,
                   COMPILE_HEADROOM_BYTES))

    def _prepare(self) -> None:
        req, repl = self._req, self._repl
        sysclk = _board_value(_exec_checked(repl, _READ_SYSCLK), "sysclk")
        self.timing = clocked_timing(sysclk, req.clock_hz, req.clock_high)
        if self.on_timing is not None:
            self.on_timing(self.timing)
        cfg = dict(req.cfg(), **self.timing.cfg())
        self._script = mp.build(cfg, CLOCKED_SCRIPT)
        names = cleanup_names(cfg)
        self._check_heap(prepare_board(repl, names))
        self.selection = load_held(repl, req, req.bitstream)
        self._check_heap(prepare_board(repl, names))

    def _start_stream(self) -> Iterator[tuple[bytes, bytes]]:
        # The longest gap between chunks: the reset clocks and one buffer.
        timing, req = self.timing, self._req
        assert timing is not None
        gap = (4 * req.buf_words + req.reset_clocks) / timing.clock_hz
        return self._repl.exec_chunks(self._script, timeout=self._chunk_timeout + 2 * gap)

    def _wind_up(self, error: BaseException | None) -> None:
        did = self.selection
        if did is None:
            return  # load_held never returned: it put the board back, or says how
        if isinstance(error, _CLOCKED_NO_REPLY):
            error.selection = did  # pyright: ignore[reportAttributeAccessIssue]
            error.add_note(
                "the board did not reply and may still be running a command: call "
                "recover_clocked(repl, exc), which recover()s the REPL and puts the "
                "board back")
            return
        try:
            undo_clocked(self._repl, did, error=error)
        except Exception as exc:  # noqa: BLE001 - the run's outcome
            self._error = self._error or "putting the board back failed: %s" % exc
            return
        if error is None and not self._error and self.timing is not None:
            problems = clocked_problems(self._req, self.timing, self._snapshot())
            if problems:
                self._error = "not clean: " + "; ".join(problems)


def session_for(repl: RawRepl, req: CaptureRequest,
                chunk_timeout: float = DEFAULT_CHUNK_TIMEOUT) -> CaptureSession:
    """The MicroPython session that runs `req`: a `ClockedSession` for a
    clocked request, else a `CaptureSession`."""
    cls = ClockedSession if req.is_clocked else CaptureSession
    return cls(repl, req, chunk_timeout=chunk_timeout)


def iter_capture(
    repl: RawRepl,
    req: CaptureRequest,
    chunk_timeout: float = DEFAULT_CHUNK_TIMEOUT,
) -> Iterator[bytes]:
    """Stream one capture: the `VGCH` header, then each chunk verbatim.

    The spelling for a consumer that wants the bytes and nothing else. Hold
    a `CaptureSession` instead when the stats, an out-of-band stop or an
    explicit teardown matter -- this is that class with the handle dropped.

    Closing the iterator early is supported and is the normal way a
    pipeline ends a capture: the board is stopped cooperatively and drained
    to its prompt before `close()` returns.
    """
    return session_for(repl, req, chunk_timeout=chunk_timeout).chunks()


def run_capture(
    repl: RawRepl,
    req: CaptureRequest,
    out,
    chunk_timeout: float = DEFAULT_CHUNK_TIMEOUT,
) -> CaptureStats:
    """Capture to `out`, a binary file object, and report what happened.

    Writes the `VGCH` header, runs `capture_rp2.py` on the board, and
    appends every chunk the board emits byte for byte. Once `seconds` have
    passed (or `max_bytes` arrived) it sends one stop byte and *keeps
    reading*: the script's `finally` still has a closing `TIME` chunk to
    emit, and dropping it would throw away the only overrun and RXSTALL
    report there is.

    The stop is cooperative. The board has run `micropython.kbd_intr(-1)`,
    so 0x03 is just a byte it picks up between chunk writes -- never one
    that interrupts a write half way and leaves a chunk shorter than its
    declared length. If the board goes quiet anyway, or its framing is
    lost, the run ends through `RawRepl.recover()` (a real Ctrl-C, by then
    re-enabled) and says so in `CaptureStats.error`; everything written
    before that is still a valid stream.

    Any *other* failure -- a malformed chunk, a write that fails, the
    operator's Ctrl-C -- is the caller's to handle and is re-raised, but
    never before the board has been stopped and its command drained to a
    prompt (`_abandon_stream`). A script left streaming would otherwise
    write its chunks into the next command's output.

    The clock is left running: stopping it is `stop_clock()`, so a caller
    can take several captures of one design without restarting it.

    Nothing is written to `out` until the board has been cleaned up and has
    enough free heap to compile the script, so a refused run leaves no
    header-only file behind.

    All of that lives in `CaptureSession`; this is the file sink for it, and
    the only thing it adds is `out.write()`. A write that fails -- a full
    disk, or M5's pipeline going away -- is the caller's to handle, and
    `session.close()` stops the board before it is re-raised, exactly as a
    failure inside the loop would.
    """
    return run_session(session_for(repl, req, chunk_timeout=chunk_timeout), out)


def run_session(session: CaptureSession, out) -> CaptureStats:
    """Pour `session`'s stream into `out`; `run_capture()` for any session.

    The board protocol is the session's (`CaptureSession` for the
    MicroPython raw REPL, `ttcap.firmware.FirmwareSession` for the pico-sdk
    firmware); the file sink and its failure handling are the same.
    """
    try:
        for block in session.chunks():
            out.write(block)
    except BaseException:
        session.close()
        raise
    return session.stats()
