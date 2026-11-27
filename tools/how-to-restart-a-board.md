---
type: how-to
owner: vgacap maintainers
reader: someone with a vgacap checkout who has used a demo board and wants to hand it back as found
review: 2027-05-27
---

# How to restart a demo board to the stock firmware's start state

**You have used a demo board running the stock firmware, and you want it back at the start state it boots into.**

This page restarts the board with [`tools/board_restart.py`](board_restart.py). It sends Ctrl-C twice to stop any running code, Ctrl-B to leave the raw REPL and Ctrl-D to soft-reset. The firmware then starts its default project again. It does not power-cycle the board or touch the bridge.

## What you need

- A link to the board: `serial:<port>`, or the bridge's `ws://.../serial` address through a tunnel.
- Nothing else using the board: a restart stops whatever is running on it.

## Steps

1. In the vgacap checkout, run `uv run python tools/board_restart.py ws://127.0.0.1:18034/serial` to restart the board, which prints what the board printed last.
2. For a bench board, read its `status.json` again until the idle display is back at `"state": "loaded"`, which took 66 to 73 s on fpga-1.

## Check

Step 1 prints the end of the board's boot, from partway through, and exits with status 0. On fpga-1 in [the 2026-11-27 bench run](https://github.com/mithro/tt-vga-capture/tree/main/docs/results/2026-11-27-docs-bench-fpga1) it ended with these lines:

```text
boot done: 2036
tt.sdk_revision=d485c7a9986336024e35d790568fca33be1c5665
tt.sdk_version=3.1.1
MicroPython 970468103d on 2026-09-08; TinyTapeout RP2350B Core with RP2350
Type "help()" for more information.
>>>
```

Step 2 shows `"state": "loaded"` under `idle_display`.

## If it fails

| Step | What you see | Likely cause | Fix |
|---|---|---|---|
| 1 | exit status 1 after 15 s | the board did not reboot to a `>>>` prompt within 15 s | Run step 1 again |
| 1 | a connection error | the tunnel is down or the link is wrong | Open the tunnel again, and check the link |
| 2 | a state other than `loaded` that does not change | the board did not restart cleanly | Run step 1 again |

## Next

- [How to run a MicroPython file on a demo board](how-to-run-a-file-on-a-board.md)
- [How to run the hardware-in-the-loop check through a tunnel](how-to-run-hil-through-a-tunnel.md)
