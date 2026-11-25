#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Run a MicroPython file on a demo board through ttcap's raw REPL.

For board-side experiments (memory, clock, pin levels) during development:
the same link and raw-REPL framing `ttcap capture` uses, so what works here
works there. Prints the board's stdout, then its stderr if any; exit status
1 when the board reported an error.

    uv run python tools/board_exec.py ws://127.0.0.1:18034/serial probe.py

The file runs in the REPL's own globals, like every ttcap script. It is
sent as-is (no CFG, no minify); keep it small on an RP2040 board.
"""

from __future__ import annotations

import argparse
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "python"))

from ttcap.cli import link_from_url  # noqa: E402
from ttcap.repl import RawRepl  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("link", help="serial:<port> or ws(s)://.../serial")
    parser.add_argument("script", type=pathlib.Path, help="MicroPython file to run")
    parser.add_argument("--timeout", type=float, default=30.0, help="seconds to wait (30)")
    args = parser.parse_args()

    link = link_from_url(args.link)
    repl = RawRepl(link)
    try:
        repl.enter()
        try:
            stdout, stderr = repl.exec(args.script.read_text(encoding="utf-8"), timeout=args.timeout)
        finally:
            repl.exit()
    finally:
        link.close()
    sys.stdout.write(stdout)
    if stderr.strip():
        sys.stdout.write("--- board stderr ---\n" + stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
