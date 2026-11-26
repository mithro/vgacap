---
type: how-to
owner: vgacap maintainers
reader: someone with a vgacap checkout who has used a demo board and wants to hand it back as found
review: 2027-05-27
---

# How to restart a demo board to the stock firmware's start state

**You have used a demo board running the stock firmware, and you want it back at the start state it boots into.**

This page restarts the board with [`tools/board_restart.py`](board_restart.py). It sends Ctrl-C twice to stop any running code, Ctrl-B to leave the raw REPL and Ctrl-D to soft-reset, so the firmware starts its default project again. It does not power-cycle the board or touch the bridge.

## What you need

- A link to the board: `serial:<port>`, or the bridge's `ws://.../serial` address through a tunnel.
- Nothing else using the board: a restart stops whatever is running on it.

## Steps

1. In the vgacap checkout, run `uv run python tools/board_restart.py ws://127.0.0.1:18034/serial` to restart the board, which prints what the board printed last.
2. For a bench board, wait about 75 s, then open its `status.json` to see the idle display back at `"state": "loaded"`.

## Check

Step 1 prints the board's `soft reboot` and the start of its default project, ending in a `>>>` prompt, and exits with status 0. Step 2 shows `"state": "loaded"` under `idle_display`.

## If it fails

| Step | What you see | Likely cause | Fix |
|---|---|---|---|
| 1 | exit status 1 after 15 s | no soft reboot came back | Run step 1 again |
| 1 | a connection error | the tunnel is down or the link is wrong | Open the tunnel again, and check the link |
| 2 | a state other than `loaded` after two minutes | the board did not restart cleanly | Run step 1 again |

## Next

- [How to run a MicroPython file on a demo board](how-to-run-a-file-on-a-board.md)
- [How to run the hardware-in-the-loop check through a tunnel](how-to-run-hil-through-a-tunnel.md)
