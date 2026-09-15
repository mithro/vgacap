# SPDX-License-Identifier: Apache-2.0
"""ttcap command-line entry point.

Only the `probe` subcommand exists so far: it connects to a board's raw REPL
and prints `sys.version` and `GPIOMap.all()`, so a board/link can be sanity
checked before the throughput and capture subcommands (added in later
milestone-3 tasks) are used.
"""

from __future__ import annotations

import argparse
import ast
from typing import Sequence

from .repl import RawRepl, ReplLink, SerialLink, WebSocketLink


def link_from_url(url: str) -> ReplLink:
    """Build a ReplLink from `serial:<port>` or `ws://...` / `wss://...`."""
    if url.startswith("serial:"):
        return SerialLink(url[len("serial:") :])
    if url.startswith("ws://") or url.startswith("wss://"):
        return WebSocketLink(url)
    raise ValueError(f"unsupported link url {url!r}: expected serial:<port> or ws(s)://...")


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
            gpio_map_repr, err = repl.exec("print(GPIOMap.all())")
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


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="ttcap")
    subparsers = parser.add_subparsers(dest="command", required=True)

    probe_parser = subparsers.add_parser(
        "probe", help="print the board's sys.version and GPIOMap.all()"
    )
    probe_parser.add_argument(
        "link", help="serial:/dev/ttyACM0 or ws://host:8765/serial"
    )

    args = parser.parse_args(argv)
    if args.command == "probe":
        probe(args.link)
        return 0
    parser.error(f"unknown command {args.command!r}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
