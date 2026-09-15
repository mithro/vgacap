# SPDX-License-Identifier: Apache-2.0
"""A stand-in for `ttcap capture --out -`, for testing `vgacapttsrc`.

The element's whole job is running that command and coping with how it ends,
so the interesting tests are about a child process, not about a board. This
script is that child: it emits a real synthetic vgacap stream on stdout at a
chosen rate, talks on stderr, and can be told to fail or to emit rubbish.

It is deliberately strict about its arguments -- it accepts exactly the flags
`ttcap capture` accepts, and argparse rejects anything else -- so a mistake in
the argv `vgacapttsrc` builds shows up as a failing test rather than as a
board that quietly ignores a setting.

Run it through the element's `ttcap-command` property, with the interpreter
spelled out so no shebang or PATH is involved:

    vgacapttsrc ttcap-command="/path/to/python /path/to/fake_ttcap.py --frames 3"

### Ending

Like the real thing, SIGINT is a *cooperative* stop: the flag is noticed at a
chunk boundary, one more chunk comes out (the board's DMA buffer, already in
flight, which is where the real 2.2 s at 60 kHz goes), then the closing `TIME`
chunk, then exit 0. A consumer that closes the pipe instead gets the same
verdict -- `BrokenPipeError` is a clean end and exit 0 -- but no trailer,
because there is nowhere left to write one.
"""
from __future__ import annotations

import argparse
import errno
import json
import os
import signal
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "python"))

from vgacap.modes import MODES  # noqa: E402
from vgacap.stream import Header, TINYVGA_MAP, Writer  # noqa: E402
from vgacap.synth import frame_samples, grid  # noqa: E402

SAMPLES_PER_CHUNK = 65536

#: Set by SIGINT; read at every chunk boundary.
_stopping = False


def _on_sigint(signum, frame) -> None:  # noqa: ARG001
    global _stopping
    _stopping = True


class Tee:
    """stdout, plus an optional byte-for-byte copy on disk.

    The copy is what lets a test decode the very stream the pipeline saw,
    rather than a second stream generated the same way and hoped to match.
    """

    def __init__(self, path: str | None, deaf: bool = False) -> None:
        self.out = sys.stdout.buffer
        self.copy = open(path, "wb") if path else None
        self.deaf = deaf

    def write(self, data: bytes) -> None:
        if self.copy is not None:
            self.copy.write(data)
            self.copy.flush()
        try:
            self.out.write(data)
            self.out.flush()
        except (BrokenPipeError, OSError):
            if not self.deaf:
                raise
            # --deaf: swallow the closed pipe too, so only a signal that
            # cannot be caught ends this process. That is the last rung of
            # the element's stop ladder, and this is how it gets tested.
            time.sleep(0.05)

    def close(self) -> None:
        if self.copy is not None:
            self.copy.close()


def _silence_stdout() -> None:
    """Retire fd 1 after a broken pipe, so the interpreter's shutdown flush
    does not re-raise it and turn exit 0 into exit 120."""
    try:
        devnull = os.open(os.devnull, os.O_WRONLY)
        os.dup2(devnull, 1)
        os.close(devnull)
    except OSError:
        pass


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="fake_ttcap")
    # How this fake behaves. These come before the subcommand, so they sit in
    # the `ttcap-command` property and the capture arguments follow.
    parser.add_argument("--mode", choices=["normal", "fail", "garbage"], default="normal")
    parser.add_argument("--frames", type=int, default=0,
                        help="frames to emit; 0 runs until stopped")
    parser.add_argument("--chunk-delay", type=float, default=0.0,
                        help="seconds between chunks, to pace the stream")
    parser.add_argument("--stop-latency", type=float, default=0.2,
                        help="seconds a cooperative stop takes, standing in for the "
                             "board's DMA buffer")
    parser.add_argument("--exit-code", type=int, default=2, help="--mode fail exit code")
    parser.add_argument("--argv-file", help="write the whole argv here, as JSON")
    parser.add_argument("--pid-file", help="write this process's pid here")
    parser.add_argument("--copy-to", help="also write the stream to this file")
    parser.add_argument("--startup-delay", type=float, default=0.0)
    parser.add_argument("--ignore-sigint", action="store_true",
                        help="a child whose cooperative stop does not work, so the "
                             "element has to close the pipe instead")
    parser.add_argument("--deaf", action="store_true",
                        help="--ignore-sigint, and swallow the broken pipe as well, "
                             "so only SIGKILL ends it")

    # ...and, from here down, exactly what `ttcap capture` takes.
    parser.add_argument("command", choices=["capture"])
    parser.add_argument("link")
    parser.add_argument("--out", required=True)
    parser.add_argument("--clock-hz", type=int, required=True)
    parser.add_argument("--seconds", type=float, default=5.0)
    parser.add_argument("--project")
    parser.add_argument("--design")
    parser.add_argument("--profile", default="auto")
    parser.add_argument("--pio", type=int)
    parser.add_argument("--buf-words", type=int)
    parser.add_argument("--edge", choices=["falling", "rising"], default="falling")
    parser.add_argument("--desc", default="")
    parser.add_argument("--max-bytes", type=int, default=0)
    return parser.parse_args(argv)


def emit_garbage(sink: Tee, args: argparse.Namespace) -> int:
    """Bytes that are not a vgacap stream at all: no VGCH, chunk tags that are
    not tags. The decoder should resynchronise, find nothing, and reach EOS --
    what must not happen is a hang."""
    rubbish = bytes(range(256)) * 16
    for i in range(64):
        if _stopping:
            break
        sink.write(rubbish[i % 7:] + rubbish[: i % 7])
        if args.chunk_delay:
            time.sleep(args.chunk_delay)
    print("fake ttcap: emitted garbage on purpose", file=sys.stderr)
    return 0


def emit_stream(sink: Tee, args: argparse.Namespace) -> int:
    mode = MODES["640x480@60"]
    samples = frame_samples(mode, grid(mode.h_active, mode.v_active))
    writer = Writer(sink, Header(sample_bits=8, samples_per_word=4,
                                 signal_map=TINYVGA_MAP, mode=3,
                                 clock_hz=args.clock_hz, desc=args.desc or "fake ttcap"))
    print(f"fake ttcap: streaming {mode.name} at {args.clock_hz} Hz from {args.link}",
          file=sys.stderr)

    deadline = time.monotonic() + args.seconds if args.seconds > 0 else None
    frame = 0
    chunks = 0
    while not _stopping:
        if args.frames and frame >= args.frames:
            break
        if deadline is not None and time.monotonic() >= deadline:
            break
        for start in range(0, len(samples), SAMPLES_PER_CHUNK):
            writer.raw(samples[start:start + SAMPLES_PER_CHUNK].tolist())
            chunks += 1
            if args.chunk_delay:
                time.sleep(args.chunk_delay)
            if _stopping:
                break  # a stop is taken at a chunk boundary, never inside one
        frame += 1

    if _stopping:
        # The cooperative stop in full: one more chunk for the buffer the
        # board was already filling, after the latency that costs.
        time.sleep(args.stop_latency)
        writer.raw(samples[:SAMPLES_PER_CHUNK].tolist())
        chunks += 1
    writer.time(time.time_ns(), args.clock_hz, 0, "fake capture complete")
    print(f"fake ttcap: {frame} frames, {chunks} chunks, stopped={_stopping}",
          file=sys.stderr)
    return 0


def main(argv: list[str]) -> int:
    args = parse_args(argv)
    if args.argv_file:
        with open(args.argv_file, "w") as fp:
            json.dump(argv, fp)
    if args.pid_file:
        with open(args.pid_file, "w") as fp:
            fp.write(str(os.getpid()))
    if args.out != "-":
        print(f"fake ttcap: only --out - is implemented, not {args.out!r}", file=sys.stderr)
        return 2
    if args.ignore_sigint or args.deaf:
        signal.signal(signal.SIGINT, signal.SIG_IGN)
    else:
        signal.signal(signal.SIGINT, _on_sigint)
    if args.startup_delay:
        time.sleep(args.startup_delay)

    if args.mode == "fail":
        # A board that never answered: a few lines of context and a non-zero
        # status, which is what has to reach the pipeline as an ERROR.
        print("fake ttcap: opening %s" % args.link, file=sys.stderr)
        print("fake ttcap: the board did not reach the REPL", file=sys.stderr)
        print("fake ttcap: capture failed: no such device", file=sys.stderr)
        return args.exit_code

    sink = Tee(args.copy_to, deaf=args.deaf)
    try:
        if args.mode == "garbage":
            return emit_garbage(sink, args)
        return emit_stream(sink, args)
    except BrokenPipeError:
        _silence_stdout()
        print("fake ttcap: capture ended: the consumer closed the stream", file=sys.stderr)
        return 0
    except OSError as exc:
        if exc.errno != errno.EPIPE:
            raise
        _silence_stdout()
        print("fake ttcap: capture ended: the consumer closed the stream", file=sys.stderr)
        return 0
    finally:
        sink.close()


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
