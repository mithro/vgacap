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
import struct
import threading
import time
from dataclasses import dataclass, field
from typing import Callable, Iterator

from vgacap.modes import MODES
from vgacap.stream import Header, Writer

from . import mp
from .boards import BoardProfile, pwm_slice_addr
from .repl import CHUNK_HEADER_LEN, RawRepl, ReplFramingError

EDGES = ("falling", "rising")
#: Board-side program bodies a `CaptureRequest` may name (`ttcap/mp/`).
CAPTURE_SCRIPTS = ("capture_rp2.py", "frame_rp2.py", "rle_rp2.py")
#: `ttcap capture --mode` names: continuous streaming at a reduced clock,
#: or whole frames / windows of frames at the project's real clock, or a
#: stream run-length encoded by the PIO (`RLE ` chunks).
CAPTURE_MODES = {
    "stream": "capture_rp2.py",
    "frame": "frame_rp2.py",
    "window": "frame_rp2.py",
    "rle": "rle_rp2.py",
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
        if self.seconds == 0 and self.max_bytes == 0 and self.stop is None and not (
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

    def cfg(self) -> dict:
        """The `CFG` dict for the board-side script."""
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
    #: that went quiet). Empty when the board finished on its own terms.
    #: Whatever was written before it is still a valid stream.
    error: str = ""
    #: `gc.mem_free()` the board reported after the pre-run cleanup. Worth
    #: watching: below `min_free_bytes(buf_words)` the script may not
    #: compile at all, or its DMA buffers may not fit.
    mem_free_before: int = 0

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
       start the clock, release reset and wait for `rst_n` to read 1 (#58).

    A design with a synchronous reset so sees its clock while reset is held,
    with its inputs already set, and the capture starts from the design's
    own frame 0. What step 3 and 4 changed is put back by `restore_mode`;
    on a failure here that is done before the error is raised.

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
                # False. Already enabled is what was asked for.
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
    _exec_checked(repl, "tt.clock_project_stop()")
    # Read before the mode write below, which turns the monitor on.
    monitoring = _board_value(_exec_checked(repl, _READ_MONITORING), "monitoring")
    if req.ui_in is not None:
        # The RP2 only drives ui_in in ASIC_RP_CONTROL; the fpgas.online
        # boards sit in ASIC_MANUAL_INPUTS (2), where writing tt.ui_in changes
        # nothing -- measured on fpga-4. The mode it was in is returned so the
        # caller can put it back (`restore_mode`).
        out = _exec_checked(repl, "print(int(tt.mode))")
        did["mode_before"] = int(out.strip().splitlines()[-1])
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
        reset_design(repl, clock_command(req))
    except BaseException as exc:
        # The caller never sees `did`, so it cannot put the board back: a
        # shared board must not be left in ASIC_RP_CONTROL, or without its
        # manual-clock monitor, by a failed setup.
        if "mode_before" in did or "monitoring_before" in did:
            undo_selection(repl, did, stop=True, error=exc)
        raise
    return did


#: How long `reset_design` waits for `rst_n` to read 1 after the release.
#: The release is the board's pull-up against the RP2's internal pull-down
#: (#58); 100 ms is far longer than any RC on that line needs.
RST_N_RELEASE_TIMEOUT_MS = 100

_READ_RST_N = "print('ttcap rst_n=%d' % tt.rst_n())"
_READ_UI_IN = "print('ttcap ui_in=%d' % int(tt.ui_in.value))"
#: Waits on the board itself, so the link's latency is not in the timeout.
_AWAIT_RST_N = "\n".join([
    "import time",
    "_t = time.ticks_ms()",
    "while not tt.rst_n() and time.ticks_diff(time.ticks_ms(), _t) < %d:" % RST_N_RELEASE_TIMEOUT_MS,
    "    time.sleep_ms(1)",
    "del _t",
    _READ_RST_N,
])
#: Whether the manual-clock monitor is on (1), off (0) or absent (-1): it
#: is there from TTDBv3.3 on, and SDK 2.x and fpga-4's 3.1.1 have none. Its
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


def reset_design(repl: RawRepl, clock: str) -> None:
    """Reset the design with its clock running: the one reset sequence.

    Holds reset, checks `rst_n` reads 0, starts the clock with `clock`
    (a `clock_command()`), releases reset and waits up to
    `RST_N_RELEASE_TIMEOUT_MS` for `rst_n` to read 1. A design with a
    synchronous reset so sees clock edges while `rst_n` is low, and a
    release the pull-up does not win is an error, not a capture of the
    reset state. Nothing may write `tt.mode` after this: the write rebuilds
    the pins and releases reset.
    """
    _exec_checked(repl, "tt.reset_project(True)")
    if _board_value(_exec_checked(repl, _READ_RST_N), "rst_n") != 0:
        raise CaptureError(
            "rst_n reads 1 while reset is held: tt.reset_project(True) did not drive it low")
    _exec_checked(repl, clock)
    _exec_checked(repl, "tt.reset_project(False)")
    out = _exec_checked(repl, _AWAIT_RST_N)
    if _board_value(out, "rst_n") != 1:
        raise CaptureError(
            "rst_n stuck low after release: it read 0 for %d ms after "
            "tt.reset_project(False)" % RST_N_RELEASE_TIMEOUT_MS)


def cleanup_names(cfg: dict) -> list[str]:
    """What `prepare_board` deletes: the module-level names of EVERY capture
    program, not just the next one, so switching between stream and frame
    mode does not leave the other program's globals holding the heap."""
    names: set[str] = set()
    for script in CAPTURE_SCRIPTS:
        names.update(mp.module_level_names(mp.build(cfg, script)))
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

    def _iter(self) -> Iterator[bytes]:
        """The stream itself. See `run_capture` for what the protocol is."""
        try:
            req, repl = self._req, self._repl
            self._prepare()

            # Nothing reaches the consumer until the board has been cleaned
            # up and has the heap to compile the script, so a refused run
            # leaves no header-only file (and no header-only pipe) behind.
            try:
                yield stream_header_bytes(req)
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
        finally:
            self._finalise()


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
    return CaptureSession(repl, req, chunk_timeout=chunk_timeout).chunks()


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
    return run_session(CaptureSession(repl, req, chunk_timeout=chunk_timeout), out)


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
