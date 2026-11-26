---
type: how-to
owner: vgacap maintainers
reader: someone with a vgacap checkout and Welland bench access who wants to check vgacap on a real board
review: 2027-05-27
---

# How to run the hardware-in-the-loop check through a tunnel

**You changed vgacap, and you want every check that only a real demo board can settle run from your workstation, with a dated report.**

This page runs [`tools/hil.sh`](hil.sh) against fpga-1, a demo board with an FPGA breakout on the Welland bench, through the fpgas.online bridge (`fpgas-tt`). It does not stop the bridge; running on the board's own Pi is [How to run the hardware-in-the-loop check on the board's Pi](how-to-run-hil-on-the-pi.md). The checks and every option are on [Hardware-in-the-loop checks](reference-hil-checks.md).

## What you need

- vgacap built with its GStreamer plugin, as in [How to build vgacap and run its tests](../docs/development.md) steps 1 and 2.
- A checkout of [tt-vga-testpatterns](https://github.com/mithro/tt-vga-testpatterns) next to the vgacap checkout, for the calibration bitstream and its reference renders.
- SSH access to the Welland bench's gateway, `tweed.welland.mithis.com`.
- Leave to use fpga-1, which is shared: nobody else may be using it while the check runs.

## Steps

1. In a browser, open `https://tinytapeout.fpgas.online/board/fpga-1/status.json` and go on only if it shows `"clients": 0` and `"usb_serial": "4df39a7a6856f86f"`.
2. On your workstation, run `ssh -f -N -L 18034:10.21.2.34:8765 tweed.welland.mithis.com` to tunnel local port 18034 to the board's bridge.
3. In the tt-vga-testpatterns checkout, run `uv run --no-project python tools/upload.py http://127.0.0.1:18034 bitstreams/tt_um_vgacal_bars.bin --enable --clock-hz 500000` to load the calibration design.
4. In the vgacap checkout, run `tools/hil.sh --board fpga-1 --link ws://127.0.0.1:18034/serial --design tt_um_vgacal_bars --clock-hz 500000 tmp/hil/` to run the checks.
5. In the vgacap checkout, run `uv run python tools/board_restart.py ws://127.0.0.1:18034/serial` to put the stock firmware back to its start state.
6. On your workstation, run `pkill -f 'L 18034:10.21.2.34:8765'` to close the tunnel.

## Check

Step 4 prints one `PASS` line for each of the ten checks, then these lines, and exits with status 0. These are the lines of [the 2026-11-24 run on fpga-4](https://github.com/mithro/tt-vga-capture/tree/main/docs/results/2026-11-24-frame-capture-fpga4), with `fpga-1` in the report's name; fpga-1's own result is left to its bench run.

```text
10 check(s): 10 passed, 0 failed, 0 skipped
report: tmp/hil/<date>-hil-fpga-1.md
```

Step 5 prints the board's soft reboot ending in a `>>>` prompt. Then `status.json`, read again until it changes, shows `"state": "loaded"` under `idle_display` again.

## If it fails

| Step | What you see | Likely cause | Fix |
|---|---|---|---|
| 1 | `"clients"` above 0 | someone is using the board | Wait until it shows 0, or use another board |
| 1 | another `usb_serial` | the boards have moved between Pis | Find fpga-1's Pi on its page, and use its octet in steps 2 to 6 |
| 4 | `FAIL probe` with `did not answer within 60s` | the tunnel is down | Run step 2 again |
| 4 | `hil: another hil run already holds ...` and exit 2 | another run is using the board | Wait for it to end |
| 4 | `hil: no .../build/vgacap-frames` and exit 2 | vgacap is not built | Build it, then run step 4 again |
| 4 | `SKIP reference` and `SKIP frame` with `tt-vga-testpatterns is not next door` | tt-vga-testpatterns is elsewhere | Add `--testpatterns <its path>` to step 4 |
| 4 | `SKIP` for `demo-frames` to `stop` | no `gst-launch-1.0` or no plugin in `build/` | Install GStreamer and build the plugin, then run step 4 again |

## Next

- [Hardware-in-the-loop checks](reference-hil-checks.md)
- [How to run the hardware-in-the-loop check on the board's Pi](how-to-run-hil-on-the-pi.md)
- [The test layers of vgacap](../docs/developing/explanation-testing.md)
