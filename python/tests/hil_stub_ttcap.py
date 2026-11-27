# SPDX-License-Identifier: Apache-2.0
"""A stand-in for `ttcap` with no board behind it, for testing `tools/hil.py`.

`hil.py` drives three `ttcap` subcommands, and this implements all three
without hardware:

* `probe` answers with a plausible board banner and GPIO map;
* `capture` writes a real synthetic `vgacap` stream to `--out` by running
  `gst/tests/fake_ttcap.py`, and prints the same `bytes=... samples=...`
  summary line `ttcap capture` prints, with the sample total read back from
  the stream by `build/vgacap-dump` so the number in the report is the real
  one;
* `demo` is the **real** `ttcap demo`, with `--ttcap-command` pointed at
  `fake_ttcap.py`. That is the point: the pipeline, the decoder, the video
  and the MJPEG server are all the real thing, and only the board is fake.

`HIL_STUB` selects a failure to simulate, which is how the no-board paths get
tested:

    ok            (default) everything answers
    probe-fails   `probe` exits 1 with a message, as an unreachable board does
    probe-hangs   `probe` never answers, so hil.py's timeout has to fire
    overruns      `capture` reports overruns=3 and exits 1, as `ttcap capture`
                  does for a board that cannot keep up
    demo-fails    `demo` exits 1 at once, before touching its outdir, as a
                  demo that dies on its arguments does
    demo-overruns `demo` runs, but its capture reports overruns and exits 1,
                  as `ttcap capture` does for a board that cannot keep up
"""
from __future__ import annotations

import os
import pathlib
import re
import subprocess
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parents[2]
FAKE_TTCAP = ROOT / "gst" / "tests" / "fake_ttcap.py"
VGACAP_DUMP = ROOT / "build" / "vgacap-dump"

BANNER = "3.4.0; MicroPython v1.24.1 on 2024-11-29; " \
         "Tiny Tapeout demo board v3 with RP2350"
GPIO_MAP = "{'rp_projclk': 24, 'uo_out0': 0, 'uo_out7': 7}"


def mode() -> str:
    return os.environ.get("HIL_STUB", "ok")


def take(argv: list[str], flag: str) -> str | None:
    """The value of `--flag`, or None. Unknown flags are left alone: this
    stub is deliberately lax where `fake_ttcap.py` is strict, because what
    it is standing in for here is a board, not an argument parser."""
    if flag in argv:
        index = argv.index(flag)
        if index + 1 < len(argv):
            return argv[index + 1]
    return None


def probe() -> int:
    if mode() == "probe-hangs":
        time.sleep(3600)
    if mode() == "probe-fails":
        print("ttcap: probe failed: ConnectionRefusedError: [Errno 111]",
              file=sys.stderr)
        return 1
    print(BANNER)
    print(GPIO_MAP)
    return 0


def capture(argv: list[str]) -> int:
    out = take(argv, "--out")
    if out is None:
        print("stub ttcap: capture needs --out", file=sys.stderr)
        return 2
    clock_hz = int(take(argv, "--clock-hz") or 500000)
    seconds = float(take(argv, "--seconds") or 5)
    # One 640x480@60 frame is 800x525 clocks, so this is what a capture of
    # this length at this clock would really contain -- which is what makes
    # hil.py's frame-count expectation a genuine test rather than a tautology.
    frames = max(1, int(seconds * clock_hz / (800 * 525)))
    started = time.monotonic()
    with open(out, "wb") as sink:
        child = subprocess.run(
            [sys.executable, str(FAKE_TTCAP), "capture", "stub://board",
             "--out", "-", "--clock-hz", str(clock_hz), "--seconds", "0",
             "--frames", str(frames)],
            stdout=sink, stderr=subprocess.PIPE, text=True, check=False)
    elapsed = time.monotonic() - started
    if child.returncode != 0:
        sys.stderr.write(child.stderr)
        return child.returncode
    chunks = re.search(r"(\d+) chunks", child.stderr)
    samples = 0
    if VGACAP_DUMP.exists():
        dump = subprocess.run([str(VGACAP_DUMP), out], capture_output=True,
                              text=True, check=False)
        found = re.search(r"total_samples=(\d+)", dump.stdout)
        samples = int(found.group(1)) if found else 0
    overruns = 3 if mode() == "overruns" else 0
    print("bytes=%d samples=%d chunks=%s seconds=%.3f overruns=%d dropped=0 "
          "rxstall=0" % (pathlib.Path(out).stat().st_size, samples,
                         chunks.group(1) if chunks else "0", elapsed, overruns))
    print("  board: fake capture complete")
    print("wrote %s" % out)
    if overruns:
        # `ttcap capture` exits 1 on a lossy capture (issue #59).
        print("overruns: %d buffers torn" % overruns, file=sys.stderr)
        return 1
    return 0


def demo(argv: list[str]) -> int:
    """The real demo, in this process, with the board replaced by
    `fake_ttcap.py`.

    In *this* process on purpose: `hil.py`'s cooperative-stop check signals
    the command it spawned, and that signal has to land on the thing that
    installed the demo's handlers. A wrapper subprocess in between would test
    the wrapper.

    `--chunk-delay` paces the fake: without it it emits as fast as the pipe
    will take it, and a `--seconds 0` run would spend its whole life in the
    encoder rather than waiting to be stopped.
    """
    if mode() == "demo-fails":
        print("stub ttcap: demo failed before starting", file=sys.stderr)
        return 1
    sys.path.insert(0, str(ROOT / "python"))
    from ttcap.cli import main as ttcap_main

    fake = "--fake-mode overruns " if mode() == "demo-overruns" else ""
    return ttcap_main(["demo", *argv, "--ttcap-command",
                       "%s %s %s--chunk-delay 0.02" % (sys.executable, FAKE_TTCAP, fake)])


def main(argv: list[str]) -> int:
    if not argv:
        print("stub ttcap: no subcommand", file=sys.stderr)
        return 2
    command, rest = argv[0], argv[1:]
    if command == "probe":
        return probe()
    if command == "capture":
        return capture(rest)
    if command == "demo":
        return demo(rest)
    print("stub ttcap: %s is not implemented" % command, file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
