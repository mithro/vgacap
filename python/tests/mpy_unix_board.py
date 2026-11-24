# SPDX-License-Identifier: Apache-2.0
"""A demo board with the vgacap module, as MicroPython's unix port plays it.

Not a host module: `build/mpy-unix/micropython` runs this file (see
`mpy_unix_link.py`). It is the board side of the raw REPL -- the subset
`ttcap.repl.RawRepl` speaks -- on stdin/stdout, over the vgacap module's
unix build, whose captures run the firmware's engines on the simulated
board (firmware/sim). What the stock firmware's `tt` does with the project
clock is `vgacap.sim_clock()` here; nothing else of the ttboard SDK exists.

    micropython mpy_unix_board.py UO_FILE [BYTES_PER_S [rxstall]]

Like the board's raw REPL, a capture's stop byte (0x03) reaches the running
code on stdin: the module reads it there, between chunks.
"""

import sys

import vgacap

STDIN = sys.stdin.buffer
OUT = sys.stdout.buffer


class Board:
    """The ttboard calls `ttcap.capture.select_project()` makes."""

    def clock_project_PWM(self, hz, *args, **kwargs):
        vgacap.sim_clock(hz)

    def clock_project_stop(self):
        vgacap.sim_clock(0)

    def reset_project(self, hold):
        pass


def main():
    uo = open(sys.argv[1], "rb").read()
    rate = float(sys.argv[2]) if len(sys.argv) > 2 else 1e6
    vgacap.sim_board(uo, bytes_per_s=rate, rxstall=len(sys.argv) > 3 and sys.argv[3] == "rxstall")
    names = {"tt": Board(), "__name__": "__main__"}
    OUT.write(b"MicroPython unix vgacap board\r\n>>> ")
    raw = False
    code = bytearray()
    while True:
        c = STDIN.read(1)
        if not c:
            return
        if not raw:
            if c == b"\x01":
                raw = True
                code = bytearray()
                OUT.write(b"raw REPL; CTRL-B to exit\r\n>")
            continue
        if c == b"\x02":
            raw = False
            OUT.write(b"\r\n>>> ")
        elif c == b"\x01" or c == b"\x03":
            code = bytearray()
        elif c == b"\x04":
            if not code:
                continue
            text = bytes(code).decode()
            code = bytearray()
            OUT.write(b"OK")
            try:
                exec(text, names)
                OUT.write(b"\x04\x04>")
            except BaseException as e:  # noqa: BLE001 - the board reports everything
                OUT.write(b"\x04")
                sys.print_exception(e, sys.stdout)
                OUT.write(b"\x04>")
        else:
            code += c


main()
