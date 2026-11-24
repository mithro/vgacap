#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Put a demo board back to the SDK's start state: Ctrl-B, then Ctrl-D.

Leaves the raw REPL (Ctrl-B) and soft-resets from the friendly REPL
(Ctrl-D), so `main.py` runs again and the SDK re-initialises. Never
`machine.soft_reset()` from the raw REPL: measured on fpga-4, that leaves
"the SDK is not running". On a fpgas.online board the daemon's idle display
then returns to "loaded" about 75 s later.

    uv run python tools/board_restart.py ws://127.0.0.1:18035/serial
"""

from __future__ import annotations

import pathlib
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "python"))

from ttcap.cli import link_from_url  # noqa: E402


def main() -> int:
    link = link_from_url(sys.argv[1])
    try:
        link.write(b"\x02")  # leave the raw REPL, if in it
        time.sleep(0.5)
        link.write(b"\r\n")
        time.sleep(0.5)
        link.write(b"\x04")  # soft reset from the friendly REPL
        deadline = time.monotonic() + 15
        seen = b""
        while time.monotonic() < deadline:
            seen += link.read(0.5)
        text = seen.decode("utf-8", "replace")
        print(text[-1500:])
        return 0 if "soft reboot" in text.lower() else 1
    finally:
        link.close()


if __name__ == "__main__":
    sys.exit(main())
