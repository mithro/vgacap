# SPDX-License-Identifier: Apache-2.0
"""ttcap command-line entry point.

Subcommands:

* `probe` connects to a board's raw REPL and prints `sys.version` and
  `GPIOMap.all()`, so a board/link can be sanity checked.
* `throughput` measures how fast the board can push bytes over the link,
  which bounds the project clock a capture can keep up with.
* `capture` enables a design, clocks it, and writes a `.vgacap` stream.
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
import itertools
import os
import pathlib
import shutil
import subprocess
import sys
from typing import Sequence

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

#: Everything a capture can fail with that is the board's, the link's or the
#: request's fault rather than a bug here. All of them exit 1: a separate
#: code for framing and timeouts was considered and dropped, because 2 is
#: argparse's usage code and overloading it would be worse than one clear
#: message naming the exception type.
CAPTURE_FAILURES = (
    CaptureError,
    ValueError,
    TimeoutError,
    ReplFramingError,
    LinkClosed,
    OSError,
)


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


def probe(url: str) -> str:
    """Connect to `url`, print the board's sys.version and GPIOMap.all()."""
    link = link_from_url(url)
    try:
        repl = RawRepl(link)
        repl.enter()
        try:
            version, err = repl.exec("import sys; print(sys.version)")
            if err:
                raise RuntimeError(f"probe failed reading sys.version: {err}")
            gpio_map_repr, err = repl.exec(GPIO_MAP_CODE)
            if err:
                raise RuntimeError(f"probe failed reading GPIOMap.all(): {err}")
        finally:
            repl.exit()
    finally:
        link.close()

    gpio_map = ast.literal_eval(gpio_map_repr.strip())
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
        raise CaptureError(f"--profile auto could not read GPIOMap.all(): {err}")
    return profile_from_gpio_map(ast.literal_eval(gpio_map_repr.strip()))


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
    command, so a failed run does not leave a header-only `.vgacap` behind.
    The stats are printed as soon as they exist -- before the clock is
    stopped -- so a failure during teardown cannot swallow the result of a
    capture that already succeeded.
    """
    link = link_from_url(url)
    try:
        repl = RawRepl(link)
        repl.enter()
        try:
            board = resolve_profile(repl, profile)
            if frames is not None:
                max_bytes = frames_to_max_bytes(board, frames)
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
            )
            selection = select_project(repl, request)
            print(
                "profile=%s project=%s enable=%s"
                % (board.name, selection["project"], selection["enable"])
            )
            with open(out_path, "wb") as fp:
                stats = run_capture(repl, request, fp)
            _report(stats, out_path)
            if stop_clock_after:
                stop_clock(repl)
        finally:
            repl.exit()
    finally:
        link.close()

    return stats


def _report(stats: CaptureStats, out_path: str) -> None:
    print(stats.format())
    # A high-rate capture can log hundreds of consecutive identical
    # "overrun" TIME chunks (e.g. 320 at 1.5 MHz); coalesce runs of the same
    # message into one line with a count instead of flooding the terminal.
    # The final summary line is always its own group of one, since it
    # carries the cumulative totals and is never repeated.
    for message, group in itertools.groupby(stats.messages):
        count = sum(1 for _ in group)
        if count > 1:
            print("  board: %s (x%d)" % (message, count))
        else:
            print("  board: %s" % message)
    print("wrote %s" % out_path)


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
        help="capture duration; 0 means run until --max-bytes/--frames (default 5)",
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
    capture_parser.add_argument("--out", required=True, help="output .vgacap file")
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
        probe(args.link)
        return 0
    if args.command == "throughput":
        result = throughput(args.link, total=args.total, block=args.block)
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
            print("capture failed: %s: %s" % (type(exc).__name__, exc), file=sys.stderr)
            return 1
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
