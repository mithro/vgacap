# SPDX-License-Identifier: Apache-2.0
"""Host side of a vgacap capture run.

`capture_cfg()` turns a `BoardProfile` into the `CFG` dict that
`ttcap.mp.with_cfg()` prepends to `ttcap/mp/capture_rp2.py`.
`select_project()` puts the board's design and project clock in the state
the capture needs, and `run_capture()` writes the `VGCH` header the board
never sees and then splices the board's own chunks after it, verbatim.

The split of responsibility is deliberate: the board knows the sample data
and its own overruns, the host knows the signal map, the mode and the
project clock it just programmed, so only the host can write a correct
header. Nothing re-frames the board's chunks -- what the board wrote is
what lands in the file, so a decoding disagreement can never be blamed on
the host's re-serialisation.
"""

from __future__ import annotations

import struct
import time
from dataclasses import dataclass, field

from vgacap.stream import Header, Writer

from . import mp
from .boards import BoardProfile
from .repl import RawRepl, ReplFramingError

EDGES = ("falling", "rising")

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

#: Free heap the board needs before a capture script is worth sending.
#:
#: The stock RP2040 firmware leaves ~80 KB and the minified script still
#: needs room to compile, so anything much below half of that is a run that
#: will fail -- and its failure mode is not always a clean `MemoryError`:
#: twice on tt07 the board printed `FATAL: uncaught exception` and halted,
#: needing a power cycle. Refusing early is strictly kinder.
MIN_FREE_BYTES = 40_000

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
#: buf_words=4096 that is ~82 ms, but a slower clock stretches it linearly.
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


def frames_to_max_bytes(profile: BoardProfile, frames: int) -> int:
    """Bytes the board must emit to be sure of `frames` complete frames.

    Assumes 640x480@60 timing (`CLOCKS_PER_FRAME_640X480`), and asks for
    **two** frame periods more than requested. A capture starts mid-frame,
    so the tail of that frame is unusable and the next boundary only tells
    the reconstruction where frames begin; one whole frame after it is the
    first that can be rendered. Measured: a `--frames 1` capture sized with
    a single extra frame gave 837,632 samples on tt07 and `vgacap-frames`
    produced **zero** frames from it.

    Only the packed sample words are counted. Chunk headers add 8 bytes per
    ~16 KB buffer, which the margin covers many times over.
    """
    if frames <= 0:
        raise ValueError(f"frames must be positive, got {frames}")
    samples = CLOCKS_PER_FRAME_640X480 * (frames + 2)
    words = -(-samples // profile.samples_per_word)
    return 4 * words


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

    def __post_init__(self) -> None:
        if self.project and self.design:
            raise ValueError(
                "project and design both name a tt.shuttle entry; give only one"
            )
        if self.edge not in EDGES:
            raise ValueError(f"edge must be one of {EDGES}, got {self.edge!r}")
        if self.clock_hz <= 0:
            raise ValueError(f"clock_hz must be positive, got {self.clock_hz}")
        if self.seconds < 0:
            raise ValueError(f"seconds must not be negative, got {self.seconds}")
        if self.seconds == 0 and self.max_bytes == 0:
            raise ValueError(
                "seconds and max_bytes are both 0: the capture would never stop"
            )

    @property
    def selection(self) -> str | None:
        """The `tt.shuttle` entry to enable, whichever field named it."""
        return self.project or self.design

    def cfg(self) -> dict:
        """The `CFG` dict for the board-side script."""
        return capture_cfg(
            self.profile,
            buf_words=self.buf_words,
            max_bytes=self.max_bytes,
            edge=self.edge,
            pio=self.pio,
            sm=self.sm,
        )


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
    #: watching: below `MIN_FREE_BYTES` the script may not compile at all.
    mem_free_before: int = 0

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
    """Enable the requested design and start its clock, in a safe order.

    The clock is stopped and the project held in reset *before* the new
    frequency is programmed, so the design never sees a half-reprogrammed
    clock, and reset is released last -- the capture then starts from the
    design's own frame 0 rather than part way through one.

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
            _exec_checked(repl, f"getattr(tt.shuttle, {name!r}).enable()")
            did["enable"] = "getattr"
    _exec_checked(repl, "tt.clock_project_stop()")
    _exec_checked(repl, "tt.reset_project(True)")
    # `clock_project_PWM(freqHz, duty_u16=0xffff/2, quiet=False,
    # max_rp2040_freq=...)` in both SDK 2.x and 3.x; only the
    # `max_rp2040_freq` default differs between them, and this passes
    # neither it nor any other optional argument.
    _exec_checked(repl, f"tt.clock_project_PWM({int(req.clock_hz)})")
    _exec_checked(repl, "tt.reset_project(False)")
    return did


def prepare_board(repl: RawRepl, names: list[str], timeout: float = 10.0) -> int:
    """Clear a previous run's leftovers and return the free heap in bytes.

    Deletes every module-level name the script is about to define, collects,
    and reads `gc.mem_free()`. On tt07 that recovered about 5 KB (79,296 ->
    84,208 free), which was the difference between a capture script that
    compiled and one that did not.
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
    parts.append(
        "clock=%d mode=extclk edge=%s profile=%s"
        % (req.clock_hz, req.edge, req.profile.name)
    )
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


def _should_stop(req: CaptureRequest, start: float, total_bytes: int) -> bool:
    """True once the run has reached whichever limit the request set."""
    if req.seconds and time.monotonic() - start >= req.seconds:
        return True
    return bool(req.max_bytes) and total_bytes >= req.max_bytes


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

    The clock is left running: stopping it is `stop_clock()`, so a caller
    can take several captures of one design without restarting it.

    Nothing is written to `out` until the board has been cleaned up and has
    enough free heap to compile the script, so a refused run leaves no
    header-only file behind.
    """
    profile = req.profile
    script = mp.with_cfg(mp.minify(mp.load("capture_rp2.py")), req.cfg())

    mem_free = prepare_board(repl, mp.module_level_names(script))
    if mem_free < MIN_FREE_BYTES:
        raise CaptureError(
            "board has only %d bytes of free heap after a collect, need %d; "
            "compiling the capture script there can fail without a clean "
            "error (tt07 printed 'FATAL: uncaught exception' and halted). "
            "Reset the board and try again." % (mem_free, MIN_FREE_BYTES)
        )

    Writer(
        out,
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

    total_bytes = 0
    samples = 0
    chunks = 0
    overrun_reports = 0
    summary_overruns: int | None = None
    rxstall = 0
    dropped = 0
    messages: list[str] = []
    interrupted = False
    timed_out = False
    error = ""

    start = time.monotonic()
    try:
        for tag, payload in repl.exec_chunks(script, timeout=chunk_timeout):
            out.write(tag + struct.pack("<I", len(payload)) + payload)
            total_bytes += 8 + len(payload)
            chunks += 1
            if tag == b"RAW ":
                samples += struct.unpack_from("<I", payload, 0)[0]
            elif tag == b"TIME":
                # `dropped_samples` is a running total, so the newest chunk
                # simply replaces the old value -- never `+=`.
                dropped, msg = _time_fields(payload)
                messages.append(msg)
                # Exactly "overrun": the closing summary starts "overruns="
                # and would otherwise be counted as one more overrun report.
                if msg == "overrun":
                    overrun_reports += 1
                value = _keyed_int(msg, "overruns")
                if value is not None:
                    summary_overruns = value
                value = _keyed_int(msg, "rxstall")
                if value is not None:
                    rxstall = value
            if not interrupted and _should_stop(req, start, total_bytes):
                repl.request_stop()
                interrupted = True
    except TimeoutError as exc:
        # The board went quiet mid-stream -- typically the state machine is
        # stuck in its `wait` because the project clock is not running, or
        # the cooperative stop byte never arrived. The script is still
        # executing up there, so interrupt it for real and take its
        # traceback: leaving it would make every later command read its
        # output instead of its own.
        timed_out = True
        error = "board went quiet: %s" % exc
        repl.recover()
    except ReplFramingError as exc:
        # The stream desynchronised, so nothing further can be read by
        # length. Same teardown: the alternative is a script that keeps
        # writing into the next command's output.
        error = "framing lost: %s" % exc
        repl.recover()
    elapsed = time.monotonic() - start

    return CaptureStats(
        bytes=total_bytes,
        samples=samples,
        chunks=chunks,
        # The closing summary carries the board's own running total, so it
        # wins; the interim "overrun" chunks are increments and are only a
        # fallback for a run whose trailer never arrived.
        overruns=summary_overruns if summary_overruns is not None else overrun_reports,
        seconds=elapsed,
        rxstall=rxstall,
        stderr=repl.last_stderr,
        messages=tuple(messages),
        timed_out=timed_out,
        dropped=dropped,
        error=error,
        mem_free_before=mem_free,
    )
