---
type: how-to
owner: vgacap maintainers
reader: someone with Welland bench access who wants to capture on a bench board's own Pi
review: 2027-05-27
---

# How to capture on a Welland board's own Pi

**You want to capture a Welland bench board on the Raspberry Pi it is plugged into, owning its serial port with no bridge in between.**

The fpgas.online bridge (`fpgas-tt`) holds the board's serial port, `/dev/ttboard`, so the capture stops the bridge and starts it again. The board drops off [tinytapeout.fpgas.online](https://tinytapeout.fpgas.online) until the bridge is back. Through the bridge from your own machine is on [How to capture from a Welland board through the bridge](how-to-capture-welland-bridge.md).

## What you need

- SSH access to the board's Pi as `pi`, through the bench gateway `tweed.welland.mithis.com`, and `sudo` on the Pi.
- A vgacap checkout on the Pi, set up as in [How to run ttcap on a Raspberry Pi](how-to-run-on-raspberry-pi.md).
- The board's slug and Pi, from [Welland bench boards in ttcap](reference-welland-boards.md). This page uses fpga-4 on `pi-sw2-p35`.

## Steps

1. In a terminal, log in to the board's Pi through the bench gateway, which gives you a shell on `pi-sw2-p35`:

   ```sh
   ssh -J tweed.welland.mithis.com pi@10.21.2.35
   ```

2. On the Pi, run `curl -s localhost:8765/health`, and go on only if it shows `"clients": 0` and the board's `usb_serial` from [Welland bench boards in ttcap](reference-welland-boards.md#usb-serials).
3. On the Pi, run `sudo systemctl stop fpgas-tt`, which stops the bridge and frees `/dev/ttboard`.
4. In the checkout on the Pi, capture from the serial port, which runs with no bridge in between:

   ```sh
   uv run --no-sync ttcap capture serial:/dev/ttboard --clock-hz 400000 --seconds 4 --out bench.vgacap
   ```

5. In the checkout on the Pi, restart the board to the stock firmware's start state, whether or not step 4 succeeded:

   ```sh
   uv run --no-sync python tools/board_restart.py serial:/dev/ttboard
   ```

6. On the Pi, run `sudo systemctl start fpgas-tt`, whether or not steps 4 and 5 succeeded, which starts the bridge again.
7. On the Pi, run `curl -s localhost:8765/health` again until `idle_display` shows `"state": "loaded"`.

Steps 3 and 6 are those of [How to stop the fpgas-tt daemon and start it again](https://docs.fpgas.online/en/latest/boards/tt-fpga/setup/stop-daemon.html). Step 5 comes before step 6 because the bridge checks the board's state when it starts. On a demo board with an ASIC breakout, starting the bridge with the capture's project still enabled left it reporting `left:` and that project ([result](https://github.com/mithro/tt-vga-capture/tree/main/docs/results/2026-11-24-direct-serial-fpga4)). That report is in `safe_start.state` in the bridge's health output ([result](https://github.com/mithro/tt-vga-capture/tree/main/docs/results/2026-11-26-docs-bench-ttcap)). `ttcap demo --board fpga-4` run on this Pi picks `serial:/dev/ttboard` itself, and says to stop the bridge first.

Step 4 captures the design already enabled. On an idle board that is the idle display, which has no VGA frames, so `ttcap png` prints `frames=0` for it. For pictures, enable a VGA design before step 3, while `/health` shows `"clients": 0`: `curl -s -X POST localhost:8765/designs/tt_um_vga_pattern/enable` did so on fpga-1 ([bench run of PR #56](https://github.com/mithro/tt-vga-capture/tree/main/docs/results/2026-11-27-docs-bench-56), 2026-11-27). Render `bench.vgacap` on a workstation, since a bench Pi has no room or time for Pillow: copy it as step 4 of [How to capture with the vgacap firmware](../../firmware/how-to-capture.md) copies `frame.vgacap`.

## Check

Step 4 prints a stats line, which shows `overruns=0 dropped=0 rxstall=0` when no sample was lost, and ends with `wrote bench.vgacap`. On fpga-1 this step ran at 500 kHz: it was clean twice with the idle display, and overran in both 4-second captures of `tt_um_vga_pattern`, while a 2-second one was clean. That is why it runs at 400 kHz, which was clean with a VGA design ([Capture sources compared](../../docs/reference-capture-sources.md#the-stock-firmware-on-an-rp2350-demo-board)). After step 6, `systemctl is-active fpgas-tt` prints `active`, and `sudo fuser /dev/ttboard` shows the bridge's process again.

On fpga-1, step 7 reached `"state": "loaded"` after 72 s. At about 67 s, one poll showed `"clients": 1`, and the next showed `"clients": 0` with `loaded`, in the same bench run of PR #56. Keep polling through it.

## If it fails

| Step | What you see | Likely cause | Fix |
|---|---|---|---|
| 1 | `Permission denied (publickey)` | your key is not on the gateway or the Pi | ask for bench access |
| 2 | `"clients"` above 0 | someone else is using the board | stop here: do not stop the bridge |
| 2 | a different `usb_serial` | the board has moved to another Pi | find the board by its serial on the site |
| 4 | `capture failed: TimeoutError:` | the bridge still holds the port | check step 3, then run step 4 again |
| 4 | any other failure | see [ttcap exit codes and messages](reference-exit-codes.md) | do steps 5 and 6 before anything else |
| 6 | `systemctl is-active fpgas-tt` prints `failed` | the bridge did not start | run step 6 again and read `journalctl -u fpgas-tt` |
| 7 | `idle_display` `state` stays other than `loaded` | the restart came after the bridge started | stop the bridge and repeat steps 5 and 6 |
| 7 | `safe_start` `state` starts with `left:` on an ASIC breakout | the bridge started before the restart | stop the bridge and repeat steps 5 and 6 |

## Next

- [How to capture from a Welland board through the bridge](how-to-capture-welland-bridge.md)
- [How to run ttcap on a Raspberry Pi](how-to-run-on-raspberry-pi.md)
- [Welland bench boards in ttcap](reference-welland-boards.md)
