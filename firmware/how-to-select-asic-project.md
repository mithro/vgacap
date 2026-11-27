---
type: how-to
owner: vgacap maintainers
reader: someone capturing a project on a demo board with an ASIC breakout through the vgacap firmware
review: 2027-05-27
---

# How to select an ASIC project with the vgacap firmware

**You want to capture a project on a demo board with an ASIC breakout through the vgacap firmware, which sees
no picture.**

The project mux's control lines float while the RP2350 is in its bootloader. So an ASIC breakout comes back with
no project selected once the vgacap firmware is loaded. The firmware's `asic_select` command selects the project
again by its index on the mux, as the stock firmware does. It is never for a demo board with an FPGA breakout,
where [GPIO 1](reference-board.md#gpios) going low erases the FPGA.

## What you need

- A demo board with an ASIC breakout, running the vgacap firmware, and `FW`, its serial port, both from
  [How to run the vgacap firmware from RAM and go back to stock](how-to-load.md).
- The project's macro name, such as `tt_um_rejunity_vga_logo`, and its shuttle, such as `tt09`.
- `curl` and [jq](https://jqlang.org/).

## Steps

1. In a shell, read the project's mux index from the shuttle's index, which prints a number such as `806`.

   ```console
   $ curl -s "https://index.tinytapeout.com/tt09.json?fields=address" | jq '.projects[] | select(.macro == "tt_um_rejunity_vga_logo") | .address'
   ```

2. At the checkout's root, capture with `--asic-index` set to that number, which sends `asic_select index=806` before the clock starts.

   ```console
   $ uv run --no-sync ttcap capture --firmware picosdk serial:$FW --asic-index 806 --clock-hz 25175000 --mode frame --frames 2 --out frame.vgacap
   ```

3. For every later capture of the same project, give the same `--asic-index`, which selects the project again each time.

## Check

The capture's board report shows a full window and sync levels, as on the tt09 board with `tt_um_rejunity_vga_logo`
([dated result](https://github.com/mithro/tt-vga-capture/tree/main/docs/results/2026-11-24-picosdk-firmware-fpga4)):

```text
profile=rp2350-dbv3 project=None enable=kept (capture firmware)
bytes=769803 samples=769600 chunks=4 seconds=4.039 overruns=0 dropped=0 rxstall=0 samples_per_s=190533
  board: frame window=481 buffers=1 hsync_active=1 vsync_active=1
  board: frames=2 stalls=0 rxstall=0 sysclk_hz=101000000
wrote frame.vgacap
```

`ttcap png frame.vgacap frame` renders the project's picture. On the Welland bench, copy the capture to a
workstation and render it there, as in step 4 of [How to capture with the vgacap firmware](how-to-capture.md):
a bench Pi has no room or time for Pillow. On the tt09 board that gave 2 frames with positive syncs and 0 glitches ([bench result](https://github.com/mithro/tt-vga-capture/tree/main/docs/results/2026-11-27-docs-bench-fpga1)).

## If it fails

| Step | What you see | Likely cause | Fix |
|---|---|---|---|
| 1 | It prints nothing | the macro name or shuttle is wrong | Check both on the project's page at [tinytapeout.com](https://tinytapeout.com) |
| 2 | `--asic-index is for ...; ... is an FPGA board, where asic_select pulls GPIO 1 -- the iCE40's CRESET_B -- low and erases the FPGA` | `--board`, the link or this Pi names a demo board with an FPGA breakout | Drop `--asic-index`: an FPGA keeps its design across the switch |
| 2 | `--asic-index needs to know which board it is for, and ... disagree` | `--board`, the link and this Pi name different boards | Give the `--board` that the link reaches |
| 2 | `--asic-index needs to know which board ws://... is, and '...' does not resolve (...)` | the host in a `ws://` link has no address | Check the link's host name |
| 2 | `board went quiet` and almost nothing written | the wrong index, or no `--asic-index` | Repeat step 1, and give its number |
| 2 | `--asic-index is for --firmware picosdk` | `--firmware picosdk` missing | Add it; the stock firmware selects a project with `--project` |

ttcap refuses `--asic-index` only for a board it can name. With a `serial:` link on a machine that is no
Welland bench Pi, nothing names the board, so check the breakout yourself first.

## Next

- [How to capture with the vgacap firmware](how-to-capture.md)
- [The vgacap firmware protocol](PROTOCOL.md)
- [Capture inside the vgacap firmware](explanation-capture.md)
