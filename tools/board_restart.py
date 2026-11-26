#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Put a demo board back to the SDK's start state: Ctrl-C, Ctrl-B, Ctrl-D.

See `ttcap.repl.restart_sdk`. On a fpgas.online board the daemon's idle
display then returns to "loaded" about 75 s later.

    uv run python tools/board_restart.py ws://127.0.0.1:18035/serial
"""

from __future__ import annotations

import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "python"))

from ttcap.cli import link_from_url  # noqa: E402
from ttcap.repl import restart_sdk  # noqa: E402


def main() -> int:
    link = link_from_url(sys.argv[1])
    try:
        ok, text = restart_sdk(link)
    finally:
        link.close()
    print(text)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
