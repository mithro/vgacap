# SPDX-License-Identifier: Apache-2.0
"""A `ReplLink` to `vgacap-fw-sim`: the capture firmware on a simulated board.

`build/vgacap-fw-sim` runs the firmware's portable core (firmware/core) on
firmware/sim/hal_sim.c and speaks firmware/PROTOCOL.md on stdin/stdout, so
`ttcap.firmware.FirmwareLink` drives it exactly as it drives a board's USB
serial port.
"""

from __future__ import annotations

import os
import pathlib
import select
import subprocess

from ttcap.repl import LinkClosed

ROOT = pathlib.Path(__file__).resolve().parents[2]
FW_SIM = ROOT / "build" / "vgacap-fw-sim"
FRAMES = ROOT / "build" / "vgacap-frames"


class SimLink:
    """`write`/`read`/`close` over the simulator's pipes."""

    def __init__(self, uo_path, *extra: str) -> None:
        self.proc = subprocess.Popen(
            [str(FW_SIM), "--uo", str(uo_path), *extra],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
        )
        self.closed = False

    def write(self, data: bytes) -> None:
        try:
            self.proc.stdin.write(data)
            self.proc.stdin.flush()
        except BrokenPipeError as exc:
            raise LinkClosed("simulator exited: %s" % exc) from exc

    def read(self, timeout: float) -> bytes:
        fd = self.proc.stdout.fileno()
        ready, _, _ = select.select([fd], [], [], timeout)
        if not ready:
            return b""
        data = os.read(fd, 1 << 16)
        if not data:
            raise LinkClosed("simulator closed its stdout")
        return data

    def close(self) -> None:
        if self.closed:
            return
        self.closed = True
        try:
            self.proc.stdin.close()
        except BrokenPipeError:
            pass
        try:
            self.proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            self.proc.kill()
            self.proc.wait()
        self.proc.stdout.close()
