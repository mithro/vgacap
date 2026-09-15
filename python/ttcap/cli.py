# SPDX-License-Identifier: Apache-2.0
"""ttcap command-line entry point.

Subcommands:

* `probe` connects to a board's raw REPL and prints `sys.version` and
  `GPIOMap.all()`, so a board/link can be sanity checked.
* `throughput` measures how fast the board can push bytes over the link,
  which bounds the project clock a capture can keep up with.
* `capture` enables a design, clocks it, and writes a `.vgacap` stream --
  to a file, or, with `--out -`, to stdout as it arrives, so a video
  pipeline can consume the capture live.
* `png` renders a captured stream to PNG images via `vgacap-frames`.

Exit codes: 0 success, 1 a board, link or tool error (including a capture
that ended through `CaptureStats.error`/`timed_out`), 2 a usage error
(argparse's own), 3 a capture that produced no samples at all -- the one
failure worth telling apart, because it means the sampler never saw a clock
edge.
"""

from __future__ import annotations

import argparse
import ast
import contextlib
import itertools
import os
import pathlib
import shutil
import signal
import subprocess
import sys
import threading
from typing import Callable, Iterator, Sequence

from .boards import RP2040_TT06, RP2350_DBV3, profile_from_gpio_map
from .capture import (
    DEFAULT_BUF_WORDS,
    DEFAULT_PIO,
    CaptureError,
    CaptureRequest,
    CaptureStats,
    frames_to_max_bytes,
    run_capture,
    select_project,
    stop_clock,
)
from .repl import LinkClosed, RawRepl, ReplFramingError, ReplLink, SerialLink, WebSocketLink
from .throughput import DEFAULT_BLOCK, DEFAULT_TOTAL, ThroughputResult, measure_throughput

#: `--profile` values that name a board directly; "auto" asks the board.
PROFILES = {"rp2040": RP2040_TT06, "rp2350": RP2350_DBV3}

#: Where `ttcap png` looks for the C renderer, in order.
VGACAP_FRAMES = "vgacap-frames"

#: Everything a subcommand can fail with that is the board's, the link's or
#: the request's fault rather than a bug here. All of them exit 1: a
#: separate code for framing and timeouts was considered and dropped,
#: because 2 is argparse's usage code and overloading it would be worse than
#: one clear message naming the exception type.
#:
#: `SyntaxError` is in the list because `ast.literal_eval()` raises it -- not
#: `ValueError` -- when the board's stdout carries anything besides the
#: `GPIOMap.all()` dict repr the probe and `--profile auto` ask for. A
#: firmware banner or a leftover `print()` is a board problem, not a bug.
CAPTURE_FAILURES = (
    CaptureError,
    ValueError,
    SyntaxError,
    TimeoutError,
    ReplFramingError,
    LinkClosed,
    OSError,
)


def _failed(what: str, exc: BaseException) -> int:
    """Report a subcommand's failure the way `main`'s contract promises."""
    print("%s failed: %s: %s" % (what, type(exc).__name__, exc), file=sys.stderr)
    return 1


def link_from_url(url: str) -> ReplLink:
    """Build a ReplLink from `serial:<port>` or `ws://...` / `wss://...`."""
    if url.startswith("serial:"):
        return SerialLink(url[len("serial:") :])
    if url.startswith("ws://") or url.startswith("wss://"):
        return WebSocketLink(url)
    raise ValueError(f"unsupported link url {url!r}: expected serial:<port> or ws(s)://...")


#: The demo board's `main.py` leaves `tt` in the REPL's globals but not
#: `GPIOMap`, so the probe has to import it itself.
GPIO_MAP_CODE = "from ttboard.pins.gpio_map import GPIOMap; print(GPIOMap.all())"


def _parse_gpio_map(reply: str) -> dict:
    """Turn the board's `GPIOMap.all()` reply into a dict.

    Anything else on the board's stdout -- a firmware banner, a `print()`
    left over from a previous session -- makes `ast.literal_eval()` raise
    `SyntaxError`, which is neither an `OSError` nor a `ValueError` and so
    used to reach the user as a traceback. It is a board problem and gets a
    board problem's message.
    """
    try:
        return ast.literal_eval(reply.strip())
    except (SyntaxError, ValueError) as exc:
        raise CaptureError(
            "board did not reply with a GPIOMap.all() dict (%s); it said "
            "%r. Pass --profile rp2040 or --profile rp2350 instead."
            % (exc, reply.strip()[:200])
        ) from None


def probe(url: str) -> str:
    """Connect to `url`, print the board's sys.version and GPIOMap.all()."""
    link = link_from_url(url)
    try:
        repl = RawRepl(link)
        repl.enter()
        try:
            version, err = repl.exec("import sys; print(sys.version)")
            if err:
                raise CaptureError(f"probe could not read sys.version: {err}")
            gpio_map_repr, err = repl.exec(GPIO_MAP_CODE)
            if err:
                raise CaptureError(f"probe could not read GPIOMap.all(): {err}")
        finally:
            repl.exit()
    finally:
        link.close()

    gpio_map = _parse_gpio_map(gpio_map_repr)
    report = f"sys.version: {version.strip()}\nGPIOMap.all(): {gpio_map!r}"
    print(report)
    return report


def throughput(url: str, total: int = DEFAULT_TOTAL, block: int = DEFAULT_BLOCK) -> ThroughputResult:
    """Measure link throughput to `url` and print the one-line summary."""
    link = link_from_url(url)
    try:
        repl = RawRepl(link)
        repl.enter()
        try:
            result = measure_throughput(repl, total=total, block=block)
        finally:
            repl.exit()
    finally:
        link.close()

    print(result.format())
    return result


def resolve_profile(repl: RawRepl, name: str):
    """Return the `BoardProfile` for `--profile`, asking the board for "auto"."""
    if name != "auto":
        return PROFILES[name]
    gpio_map_repr, err = repl.exec(GPIO_MAP_CODE)
    if err:
        raise CaptureError(
            "--profile auto could not read GPIOMap.all(): %s; pass --profile "
            "rp2040 or --profile rp2350 instead" % err
        )
    return profile_from_gpio_map(_parse_gpio_map(gpio_map_repr))


#: `--out` value that means "stream to stdout" rather than "write a file".
#:
#: The `-` convention, and it is deliberately not overridable: a capture is
#: binary, so a stream sent to a file called `-` by accident is unreadable
#: noise in the working directory, while a stream sent to stdout by accident
#: is visible immediately. A real file of that name is still reachable, the
#: way every tool with this convention reaches it: `--out ./-`.
STDOUT_PATH = "-"


class _StdoutStream:
    """`sys.stdout.buffer`, flushed after every write.

    Three jobs, all of them about being the *live* end of a pipeline:

    * flush per write, so a consumer sees each chunk as the board sends it
      rather than when some 8 KB buffer happens to fill -- the difference
      between a video that starts and one that appears to hang;
    * `tell()` from a counter, because `sys.stdout.buffer.tell()` raises
      `OSError` on a pipe, and `capture()` asks how much was written to
      decide whether a failed run left anything behind;
    * never close the underlying buffer, which belongs to the interpreter.
    """

    def __init__(self, buffer) -> None:
        self._buffer = buffer
        self._written = 0

    def write(self, data) -> int:
        written = self._buffer.write(data)
        self._written += len(data)
        self._buffer.flush()
        return written

    def flush(self) -> None:
        self._buffer.flush()

    def tell(self) -> int:
        return self._written


@contextlib.contextmanager
def _open_output(out_path: str) -> Iterator:
    """Yield the binary sink for `--out`: a file, or stdout for `-`."""
    if out_path == STDOUT_PATH:
        stream = _StdoutStream(sys.stdout.buffer)
        try:
            yield stream
        finally:
            # Flushed, not closed: the interpreter owns stdout, and closing
            # it would break the messages still to be written to stderr's
            # sibling on the way out.
            with contextlib.suppress(ValueError, OSError):
                stream.flush()
        return
    with open(out_path, "wb") as fp:
        yield fp


@contextlib.contextmanager
def _sigint_stops_the_capture() -> Iterator[Callable[[], bool]]:
    """Turn the first SIGINT into a cooperative stop, for a streamed capture.

    A GStreamer source element ends its child with SIGINT and then waits for
    it (`vgacapttsrc`, M5 Task 4), and a person piping `ttcap capture
    --out -` into `gst-launch` ends it with Ctrl-C. Neither wants the
    default `KeyboardInterrupt`: that aborts mid-stream, so the board's
    closing `TIME` chunk -- the only overrun and RXSTALL report there is --
    never arrives, and the run exits non-zero on a capture that worked.

    So the first signal only sets a flag, which `CaptureRequest.stop`
    consults between chunks: the board finishes its chunk, writes its
    trailer, and the stream ends whole. The cost is one DMA buffer of
    latency (2.2 s at the RP2040's 60 kHz floor), so the handler is put back
    immediately and a *second* Ctrl-C interrupts for real.

    Only installed for `--out -`, and only from the main thread; anywhere
    else the predicate is simply never true and the request's own
    `seconds`/`max_bytes` bound the run as before.
    """
    stopped = threading.Event()
    try:
        previous = signal.getsignal(signal.SIGINT)

        def handler(signum, frame) -> None:
            stopped.set()
            signal.signal(signal.SIGINT, previous)

        signal.signal(signal.SIGINT, handler)
    except (ValueError, OSError, AttributeError):  # not the main thread
        yield stopped.is_set
        return
    try:
        yield stopped.is_set
    finally:
        with contextlib.suppress(ValueError, OSError):
            signal.signal(signal.SIGINT, previous)


def _discard_empty(out_path: str) -> None:
    """Remove a `.vgacap` nothing was ever written to.

    The file is only unlinked while it is still zero bytes, so a capture
    that produced anything at all keeps what it produced -- and a path that
    has already been replaced by something else is left alone.
    """
    try:
        path = pathlib.Path(out_path)
        if path.is_file() and path.stat().st_size == 0:
            path.unlink()
    except OSError:  # pragma: no cover - whatever is there is not ours to fix
        pass


def capture(
    url: str,
    out_path: str,
    *,
    profile: str = "auto",
    project: str | None = None,
    design: str | None = None,
    clock_hz: int,
    seconds: float,
    max_bytes: int = 0,
    frames: int | None = None,
    buf_words: int = DEFAULT_BUF_WORDS,
    edge: str = "falling",
    desc: str = "",
    pio: int = DEFAULT_PIO,
    stop_clock_after: bool = True,
) -> CaptureStats:
    """Run one capture to `out_path` and print what happened.

    The stream file is opened only once the board has accepted every setup
    command, and is removed again if the run fails before a single byte is
    written -- so a failed run leaves neither a header-only nor an empty
    `.vgacap` behind.
    The stats are printed as soon as they exist -- before the clock is
    stopped -- so a failure during teardown cannot swallow the result of a
    capture that already succeeded.

    `out_path` of `-` streams to stdout instead (`STDOUT_PATH`), flushed per
    chunk. Then **every** message this function and its callees print goes
    to stderr, the stats line included: stdout carries the capture and
    nothing else, because the thing reading it is `vgacap_reader`, not a
    person, and one stray line of text desynchronises the framing. That
    mode also accepts a run with no `seconds`/`max-bytes` limit, because
    SIGINT ends it cooperatively (`_sigint_stops_the_capture`).
    """
    to_stdout = out_path == STDOUT_PATH
    # Messages share stdout with the capture only when the capture is not on
    # stdout. There is no third option and no flag: a stream is either
    # parseable or it is not.
    say = sys.stderr if to_stdout else sys.stdout
    link = link_from_url(url)
    try:
        repl = RawRepl(link)
        repl.enter()
        try:
            board = resolve_profile(repl, profile)
            if frames is not None:
                # `buf_words` sizes the chunks, and the byte budget has to
                # allow for each chunk's 12 non-sample bytes.
                max_bytes = frames_to_max_bytes(board, frames, buf_words=buf_words)
            with _sigint_stops_the_capture() if to_stdout else contextlib.nullcontext(
                None
            ) as stop:
                request = CaptureRequest(
                    profile=board,
                    project=project,
                    design=design,
                    clock_hz=clock_hz,
                    seconds=seconds,
                    max_bytes=max_bytes,
                    buf_words=buf_words,
                    edge=edge,
                    desc=desc,
                    pio=pio,
                    stop=stop,
                )
                selection = select_project(repl, request)
                print(
                    "profile=%s project=%s enable=%s"
                    % (board.name, selection["project"], selection["enable"]),
                    file=say,
                )
                # `run_capture()` writes nothing until the board has been
                # cleaned up and has the heap for the script, so a run
                # refused there (or one that dies before the first chunk)
                # must not leave an empty file behind pretending to be a
                # capture. Anything that did get written is a valid, if
                # short, stream and is kept.
                with _open_output(out_path) as fp:
                    try:
                        stats = run_capture(repl, request, fp)
                    except BaseException:
                        if not to_stdout and fp.tell() == 0:
                            _discard_empty(out_path)
                        raise
            _report(stats, "stdout" if to_stdout else out_path, stream=say)
            if stop_clock_after:
                stop_clock(repl)
        finally:
            repl.exit()
    finally:
        link.close()

    return stats


def _report(stats: CaptureStats, out_path: str, stream=None) -> None:
    stream = sys.stdout if stream is None else stream
    print(stats.format(), file=stream)
    # A high-rate capture can log hundreds of consecutive identical
    # "overrun" TIME chunks (e.g. 320 at 1.5 MHz); coalesce runs of the same
    # message into one line with a count instead of flooding the terminal.
    # The final summary line is always its own group of one, since it
    # carries the cumulative totals and is never repeated.
    for message, group in itertools.groupby(stats.messages):
        count = sum(1 for _ in group)
        if count > 1:
            print("  board: %s (x%d)" % (message, count), file=stream)
        else:
            print("  board: %s" % message, file=stream)
    print("wrote %s" % out_path, file=stream)


def find_vgacap_frames(explicit: str | None = None) -> str:
    """Locate the C frame renderer.

    In order: `--vgacap-frames`, the in-tree `build/`, `$VGACAP_FRAMES`,
    then `PATH`.
    """
    if explicit:
        return explicit
    # python/ttcap/cli.py -> the repo root, where CMake puts build/.
    in_tree = pathlib.Path(__file__).resolve().parents[2] / "build" / VGACAP_FRAMES
    if in_tree.exists():
        return str(in_tree)
    from_env = os.environ.get("VGACAP_FRAMES")
    if from_env:
        return from_env
    on_path = shutil.which(VGACAP_FRAMES)
    if on_path:
        return on_path
    raise CaptureError(
        f"cannot find {VGACAP_FRAMES}: build it (cmake --build build), set "
        "$VGACAP_FRAMES, or pass --vgacap-frames"
    )


def png(stream_path: str, out_prefix: str, vgacap_frames: str | None = None) -> list[str]:
    """Render `stream_path` to PNGs named `<out_prefix>-NNNN.png`.

    `vgacap-frames` writes binary PPMs, which are exact and trivially
    parsed but are not what anyone wants to look at; Pillow does the last
    step. The PPMs are left in place -- they are the tool's own output, and
    deleting a file the caller did not ask about would be a surprise.
    """
    tool = find_vgacap_frames(vgacap_frames)
    result = subprocess.run(
        [tool, stream_path, out_prefix], capture_output=True, text=True, check=False
    )
    if result.stdout:
        print(result.stdout, end="")
    if result.returncode != 0:
        raise CaptureError(
            "%s exited %d: %s"
            % (tool, result.returncode, result.stderr.strip() or "no frame decoded")
        )

    try:
        from PIL import Image
    except ImportError as exc:  # pragma: no cover - depends on the environment
        raise CaptureError(
            "Pillow is needed to write PNGs: uv sync --extra synth"
        ) from exc

    prefix = pathlib.Path(out_prefix)
    written = []
    for ppm in sorted(prefix.parent.glob(prefix.name + "-*.ppm")):
        target = ppm.with_suffix(".png")
        with Image.open(ppm) as image:
            image.save(target)
        written.append(str(target))
    print("wrote %d png(s)" % len(written))
    return written


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="ttcap")
    subparsers = parser.add_subparsers(dest="command", required=True)

    probe_parser = subparsers.add_parser(
        "probe", help="print the board's sys.version and GPIOMap.all()"
    )
    probe_parser.add_argument(
        "link", help="serial:/dev/ttyACM0 or ws://host:8765/serial"
    )

    throughput_parser = subparsers.add_parser(
        "throughput", help="measure how fast the board can push bytes over the link"
    )
    throughput_parser.add_argument(
        "link", help="serial:/dev/ttyACM0 or ws://host:8765/serial"
    )
    throughput_parser.add_argument(
        "--bytes",
        dest="total",
        type=int,
        default=DEFAULT_TOTAL,
        help=f"payload bytes to send (default {DEFAULT_TOTAL})",
    )
    throughput_parser.add_argument(
        "--block",
        type=int,
        default=DEFAULT_BLOCK,
        help=f"board-side write size in bytes (default {DEFAULT_BLOCK})",
    )

    capture_parser = subparsers.add_parser(
        "capture", help="capture a design's VGA output to a .vgacap stream"
    )
    capture_parser.add_argument(
        "link", help="serial:/dev/ttyACM0 or ws://host:8765/serial"
    )
    capture_parser.add_argument(
        "--profile",
        choices=[*PROFILES, "auto"],
        default="auto",
        help="board profile; auto asks the board for its GPIOMap (default)",
    )
    capture_parser.add_argument(
        "--project", help="tt.shuttle macro to enable, e.g. tt_um_rejunity_vga"
    )
    capture_parser.add_argument(
        "--design", help="FPGA boards: bitstream to enable, via the same tt.shuttle"
    )
    capture_parser.add_argument(
        "--clock-hz", type=int, required=True, help="project clock to program"
    )
    capture_parser.add_argument(
        "--seconds",
        type=float,
        default=5.0,
        help="capture duration; 0 means run until --max-bytes/--frames, or "
        "until SIGINT when --out - is streaming (default 5)",
    )
    capture_limit = capture_parser.add_mutually_exclusive_group()
    capture_limit.add_argument(
        "--max-bytes",
        type=int,
        default=0,
        help="stop once the board has emitted this many bytes (0 = no limit)",
    )
    capture_limit.add_argument(
        "--frames",
        type=int,
        help="stop after enough bytes for N frames, assuming 640x480@60 "
        "timing (800x525 clocks) plus one frame of margin",
    )
    capture_parser.add_argument(
        "--out",
        required=True,
        help="output .vgacap file, or - to stream the capture to stdout "
        "(every message then goes to stderr); for a file literally named "
        "'-', pass ./-",
    )
    capture_parser.add_argument(
        "--buf-words",
        type=int,
        default=DEFAULT_BUF_WORDS,
        help=f"words per DMA buffer, two are allocated (default {DEFAULT_BUF_WORDS})",
    )
    capture_parser.add_argument(
        "--edge", choices=["falling", "rising"], default="falling",
        help="project-clock edge to sample (default falling: outputs have settled)",
    )
    capture_parser.add_argument("--desc", default="", help="note to store in the header")
    capture_parser.add_argument(
        "--pio",
        type=int,
        default=DEFAULT_PIO,
        help=f"PIO block for the sampler (default {DEFAULT_PIO}; the stock "
        "firmware keeps its own program in block 0)",
    )
    capture_parser.add_argument(
        "--no-stop-clock",
        dest="stop_clock",
        action="store_false",
        help="leave the project clock running after the capture",
    )

    png_parser = subparsers.add_parser(
        "png", help="render a .vgacap stream to PNG images"
    )
    png_parser.add_argument("stream", help="the .vgacap file to render")
    png_parser.add_argument("prefix", help="output prefix; files are <prefix>-NNNN.png")
    png_parser.add_argument(
        "--vgacap-frames", help="path to the vgacap-frames binary"
    )

    args = parser.parse_args(argv)
    if args.command == "probe":
        # Wrapped like `capture`: `ttcap probe serial:/dev/nope` is the
        # first command anyone runs, and a missing device, a board
        # traceback or a dropped WebSocket is not a bug to report.
        try:
            probe(args.link)
        except CAPTURE_FAILURES as exc:
            return _failed("probe", exc)
        return 0
    if args.command == "throughput":
        try:
            result = throughput(args.link, total=args.total, block=args.block)
        except CAPTURE_FAILURES as exc:
            return _failed("throughput", exc)
        return 1 if (result.corrupt or result.short) else 0
    if args.command == "capture":
        try:
            stats = capture(
                args.link,
                args.out,
                profile=args.profile,
                project=args.project,
                design=args.design,
                clock_hz=args.clock_hz,
                seconds=args.seconds,
                max_bytes=args.max_bytes,
                frames=args.frames,
                buf_words=args.buf_words,
                edge=args.edge,
                desc=args.desc,
                pio=args.pio,
                stop_clock_after=args.stop_clock,
            )
        except CAPTURE_FAILURES as exc:
            # `capture()` prints the stats before it tears anything down, so
            # whatever was gathered is already on stdout by now; all that is
            # left to say is why it stopped.
            return _failed("capture", exc)
        if stats.error or stats.timed_out:
            return 1
        if stats.samples == 0:
            print(
                "no samples captured: the sampler never saw a clock edge",
                file=sys.stderr,
            )
            return 3
        return 0
    if args.command == "png":
        try:
            png(args.stream, args.prefix, args.vgacap_frames)
        except CaptureError as exc:
            print("png failed: %s" % exc, file=sys.stderr)
            return 1
        return 0
    parser.error(f"unknown command {args.command!r}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
