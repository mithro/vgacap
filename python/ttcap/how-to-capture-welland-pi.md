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

1. In a browser, open `https://tinytapeout.fpgas.online/board/fpga-4/status.json`, and go on only if it shows `"clients": 0` and the `usb_serial` you expect.
2. On the Pi, run `sudo systemctl stop fpgas-tt`, which stops the bridge and frees `/dev/ttboard`.
3. In the checkout on the Pi, capture from the serial port, which runs with no bridge in between:

   ```sh
   uv run --no-sync ttcap capture serial:/dev/ttboard --clock-hz 750000 --seconds 4 --out bench.vgacap
   ```

4. In the checkout on the Pi, restart the stock firmware's SDK, whether or not step 3 succeeded, which leaves no design of yours enabled:

   ```sh
   uv run --no-sync python tools/board_restart.py serial:/dev/ttboard
   ```

5. On the Pi, run `sudo systemctl start fpgas-tt`, whether or not steps 3 and 4 succeeded, which starts the bridge again.
6. In the browser, reload `status.json` until it shows `"reachable": true` and `idle_display` shows `"state": "loaded"`.

Step 4 comes before step 5 because the bridge checks the board's state when it starts. On an ASIC board, starting the bridge with the capture's project still enabled left it reporting `left:` and that project ([result](https://github.com/mithro/tt-vga-capture/tree/main/docs/results/2026-11-24-direct-serial-fpga4)). `ttcap demo --board fpga-4` run on this Pi picks `serial:/dev/ttboard` itself, and says to stop the bridge first.

## Check

Step 3 ends with `wrote bench.vgacap` and a stats line. On fpga-4 at 750 kHz it was `samples=2834432 ... overruns=0 dropped=0 rxstall=0` ([result](https://github.com/mithro/tt-vga-capture/tree/main/docs/results/2026-11-24-direct-serial-fpga4)). After step 5, `systemctl is-active fpgas-tt` prints `active`, and `sudo fuser /dev/ttboard` shows the bridge's process again.

## If it fails

| Step | What you see | Likely cause | Fix |
|---|---|---|---|
| 1 | `"clients"` above 0 | someone else is using the board | stop here: do not stop the bridge |
| 3 | `capture failed: TimeoutError:` | the bridge still holds the port | check step 2, then run step 3 again |
| 3 | any other failure | see [ttcap exit codes and messages](reference-exit-codes.md) | do steps 4 and 5 before anything else |
| 5 | `systemctl is-active fpgas-tt` prints `failed` | the bridge did not start | run step 5 again and read `journalctl -u fpgas-tt` |
| 6 | `idle_display` stays `left:` and a design name | the SDK restart came after the bridge started | stop the bridge and repeat steps 4 and 5 |

## Next

- [How to capture from a Welland board through the bridge](how-to-capture-welland-bridge.md)
- [How to run ttcap on a Raspberry Pi](how-to-run-on-raspberry-pi.md)
- [Welland bench boards in ttcap](reference-welland-boards.md)
