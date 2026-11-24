# SPDX-License-Identifier: Apache-2.0
"""A `ReplLink` to a board with the vgacap module: MicroPython's unix port.

`build/mpy-unix/micropython` (mpy/build-unix.sh) is MicroPython with the
vgacap module built on the simulated board; it runs `mpy_unix_board.py`,
the board side of the raw REPL, so `ttcap.repl.RawRepl`, `ttcap.module`
and `ttcap capture --firmware mpy-module` drive it exactly as they drive a
board's serial port.
"""

from __future__ import annotations

import os
import pathlib
import select
import subprocess

from ttcap.repl import LinkClosed

ROOT = pathlib.Path(__file__).resolve().parents[2]
MPY_UNIX = ROOT / "build" / "mpy-unix" / "micropython"
BOARD = pathlib.Path(__file__).resolve().parent / "mpy_unix_board.py"


class MpyUnixLink:
    """`write`/`read`/`close` over the unix MicroPython's pipes."""

    def __init__(self, uo_path, bytes_per_s: float = 1e6, rxstall: bool = False,
                 heapsize: str = "4M") -> None:
        argv = [str(MPY_UNIX), "-X", "heapsize=" + heapsize, str(BOARD), str(uo_path), repr(float(bytes_per_s))]
        if rxstall:
            argv.append("rxstall")
        self.proc = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE)
        self.closed = False

    def write(self, data: bytes) -> None:
        try:
            self.proc.stdin.write(data)
            self.proc.stdin.flush()
        except BrokenPipeError as exc:
            raise LinkClosed("micropython exited: %s" % exc) from exc

    def read(self, timeout: float) -> bytes:
        fd = self.proc.stdout.fileno()
        ready, _, _ = select.select([fd], [], [], timeout)
        if not ready:
            return b""
        data = os.read(fd, 1 << 16)
        if not data:
            raise LinkClosed("micropython closed its stdout")
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
