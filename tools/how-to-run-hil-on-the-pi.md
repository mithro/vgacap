---
type: how-to
owner: vgacap maintainers
reader: someone with a shell on a Welland bench Pi who wants to check vgacap on its board without the bridge
review: 2027-05-27
---

# How to run the hardware-in-the-loop check on the board's Pi

**You want the hardware-in-the-loop check run on the board's own Raspberry Pi, on its serial port, with the bridge out of the way.**

This page runs [`tools/hil.py`](hil.py) with `--stop-bridge` on fpga-1's Pi. The check stops the fpgas.online bridge (`fpgas-tt`), which holds `/dev/ttboard`, as on [How to stop the fpgas-tt daemon and start it again](https://docs.fpgas.online/en/latest/boards/tt-fpga/setup/stop-daemon.html). It then restarts the board and starts the bridge again. This page ran as written on fpga-1's Pi in [the 2026-11-27 bench run](https://github.com/mithro/tt-vga-capture/tree/main/docs/results/2026-11-27-docs-bench-fpga1). The `reference` and `frame` checks on a Pi are unproven: [vgacap #47](https://github.com/mithro/vgacap/issues/47).

`--no-demo` skips the four GStreamer checks. The `reference` and `frame` checks skip because no tt-vga-testpatterns checkout sits next to vgacap on the Pi. Step 3 runs `hil.py` under `uv run --no-dev`, not `hil.sh`, which would install the dev group: [vgacap #48](https://github.com/mithro/vgacap/issues/48). Running from a workstation is [How to run the hardware-in-the-loop check through a tunnel](how-to-run-hil-through-a-tunnel.md).

## What you need

- A shell on fpga-1's Pi, `pi-sw2-p34`, reached through the Welland gateway as [the gateway's page](https://docs.fpgas.online/en/latest/sites/welland-gateway.html) shows, with `sudo` that asks no password.
- [uv](https://docs.astral.sh/uv/) on the Pi, installed as in step 2 of [How to run ttcap on a Raspberry Pi](../python/ttcap/how-to-run-on-raspberry-pi.md).
- A vgacap checkout on the Pi, and a C compiler. The Pi's package lists have no CMake, so step 2 runs CMake from PyPI through `uvx`. That took 5 s to configure and 3.4 s to build on fpga-1's Pi, and 15 s and 7.8 s on tt09's ([bench run of PR #56](https://github.com/mithro/tt-vga-capture/tree/main/docs/results/2026-11-27-docs-bench-56), 2026-11-27).
- The calibration design `tt_um_vgacal_bars` loaded on the board, as in [the tunnel how-to](how-to-run-hil-through-a-tunnel.md) steps 1 to 3.

## Steps

1. On the Pi, run `curl -s localhost:8765/health` and go on only if it shows `"clients": 0`, since stopping the bridge would cut a visitor off.
2. In the vgacap checkout on the Pi, run `uvx --python /usr/bin/python3 --from cmake cmake -S . -B build && uvx --python /usr/bin/python3 --from cmake cmake --build build -j --target vgacap-frames` to build the frame rebuilder.
3. In the same checkout, run `uv run --no-dev --python /usr/bin/python3 python tools/hil.py --board fpga-1 --link serial:/dev/ttboard --design tt_um_vgacal_bars --clock-hz 500000 --no-demo --stop-bridge --ttcap 'uv run --no-dev --no-sync ttcap' tmp/hil/` to run the checks.
4. On the Pi, run `systemctl is-active fpgas-tt` to confirm that the check started the bridge again.

## Check

Step 3 prints `PASS` for `probe`, `capture`, `reconstruct` and `board-usable`. It prints `SKIP` for the other six with these reasons, where `<parent>` holds the vgacap checkout:

```text
SKIP  reference      tt-vga-testpatterns is not next door: no <parent>/tt-vga-testpatterns/tools/compare_capture.py
SKIP  frame          tt-vga-testpatterns is not next door: no <parent>/tt-vga-testpatterns/tools/compare_capture.py
SKIP  demo-frames    --no-demo: the board checks only
SKIP  video          --no-demo: the board checks only
SKIP  serve          --no-demo: the board checks only
SKIP  stop           --no-demo: the board checks only
```

On fpga-1, `capture` took 4898816 samples in 10.0 s with 0 overruns, and `reconstruct` made 11 frames with 0 glitches. The report, named on the last line, lists under Notes that `fpgas-tt` was stopped and started again, and that the board was restarted first. Step 4 prints `active`, and the board's `status.json` shows `"reachable": true`.

## If it fails

| Step | What you see | Likely cause | Fix |
|---|---|---|---|
| 1 | `"clients"` above 0 | a visitor is using the board | Wait until it shows 0 |
| 3 | `--stop-bridge refused: the bridge at ... has 1 client(s) connected` | a visitor connected after step 1 | Wait, then run steps 1 and 3 again |
| 3 | `` --stop-bridge: `sudo -n systemctl stop fpgas-tt` failed `` | `sudo` asks for a password | Run as a user whose `sudo` needs none |
| 3 | `WARNING: ... fpgas-tt failed` under Notes | the bridge did not start again | Run `sudo systemctl start fpgas-tt` |
| 4 | anything but `active` | the bridge is down | Run `sudo systemctl start fpgas-tt`, then step 4 again |

## Next

- [Hardware-in-the-loop checks](reference-hil-checks.md)
- [How to stop the fpgas-tt daemon and start it again](https://docs.fpgas.online/en/latest/boards/tt-fpga/setup/stop-daemon.html)
