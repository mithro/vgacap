---
type: how-to
owner: vgacap maintainers
reader: someone with a vgacap checkout who wants to try MicroPython code on a demo board
review: 2027-05-27
---

# How to run a MicroPython file on a demo board

**You want to run a small MicroPython file on a demo board running the stock firmware, and see what it prints.**

This page runs a file with [`tools/board_exec.py`](board_exec.py), over the same link and raw REPL that `ttcap capture` uses. It sends the file as it is, so it suits small experiments such as reading memory, the clock or pin levels. It does not load a design or capture anything.

## What you need

- A link to the board: `serial:<port>` for a board on a USB port of this machine, or the bridge's `ws://.../serial` address through a tunnel.
- For a Welland bench board, the client check and the tunnel of [How to run the hardware-in-the-loop check through a tunnel](how-to-run-hil-through-a-tunnel.md) steps 1 and 2.
- A MicroPython file small enough for the board's memory.

## Steps

1. In the vgacap checkout, write the MicroPython code into a file such as `probe.py`, printing what you want to see.
2. In the vgacap checkout, run `uv run python tools/board_exec.py ws://127.0.0.1:18034/serial probe.py` to run the file, which prints the board's output.
3. In the vgacap checkout, run `uv run python tools/board_restart.py ws://127.0.0.1:18034/serial` to put the stock firmware back to its start state.

## Check

Step 2 prints what the file printed and exits with status 0. A file that raises an error prints `--- board stderr ---` and the traceback, and exits with status 1. Step 3 prints the board's soft reboot ending in a `>>>` prompt.

## If it fails

| Step | What you see | Likely cause | Fix |
|---|---|---|---|
| 2 | `--- board stderr ---` and a `MemoryError` | the file is too big for the board | Make the file smaller |
| 2 | a timeout after 30 s | the code runs longer, or the board is busy | Add `--timeout <seconds>`, or check that nobody else is using the board |
| 2 | a connection error | the tunnel is down or the link is wrong | Open the tunnel again, and check the link |

## Next

- [How to restart a demo board to the stock firmware's start state](how-to-restart-a-board.md)
- [Hardware-in-the-loop checks](reference-hil-checks.md)
