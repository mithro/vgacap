---
type: how-to
owner: vgacap maintainers
reader: someone checking that ttcap can reach a demo board before capturing
review: 2027-05-27
---

# How to probe a demo board

**You want to know that `ttcap` reaches your demo board, and which firmware and pin map it has, before you capture.**

`ttcap probe` enters the stock firmware's raw REPL, prints its `sys.version` and its `GPIOMap.all()`, and leaves. It changes nothing on the board. The pin maps it can show are on [Demo board GPIO maps for capture](reference-gpio-maps.md).

## What you need

- A vgacap checkout with `uv sync` run in it.
- The board's link: `serial:` and its device, or the `ws://` address of an fpgas.online bridge.

## Steps

1. In the checkout, probe the board through its link, which prints its version and pin map:

   ```sh
   uv run ttcap probe serial:/dev/ttyACM0
   ```

2. In the output, read `rp_projclk` and `uo_out0`: GPIO 0 and 5 mean an RP2040 demo board, 16 and 33 an RP2350.

## Check

The probe prints two lines and exits 0. From an RP2350 demo board running the stock firmware 3.1.1 ([result](https://github.com/mithro/tt-vga-capture/tree/main/docs/results/2026-11-26-docs-bench-ttcap)):

```text
sys.version: 3.4.0; MicroPython 970468103d on 2026-09-08
GPIOMap.all(): {'mng00': 3, 'mng01': 4, ... 'rp_projclk': 16, 'uo_out0': 33, 'uo_out1': 34, ...}
```

## If it fails

| Step | What you see | Likely cause | Fix |
|---|---|---|---|
| 1 | `probe failed: SerialException:` ending `No such file or directory` | the device name is wrong | check `ls /dev/ttyACM*` and use that name |
| 1 | `probe failed: SerialException:` ending `Permission denied` | your user cannot open the port | join the `dialout` group and log in again |
| 1 | `probe failed: TimeoutError: timed out waiting for` | no raw REPL answered: another firmware runs or another program reads the port | close the other program, or reboot to the stock firmware |
| 1 | `probe could not read GPIOMap.all()` | the firmware is not the stock firmware | reboot it to the stock firmware |

## Next

- [How to measure a demo board's link throughput](how-to-measure-throughput.md)
- [How to capture a stream from a demo board](how-to-capture-stream.md)
- [How to reboot a demo board](how-to-reboot-board.md)
