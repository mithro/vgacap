---
type: how-to
owner: vgacap maintainers
reader: someone capturing a project on a demo board with an ASIC breakout through the vgacap firmware
review: 2027-05-27
---

# How to select an ASIC project with the vgacap firmware

**You want to capture a project on a demo board with an ASIC breakout, and the vgacap firmware sees no picture
because the chip's project mux came back with nothing selected.**

The mux's control lines float while the RP2350 is in its bootloader, so an ASIC breakout loses its selected
project when the vgacap firmware is loaded. The firmware's `asic_select` command selects it again, by its index
on the mux, as the stock firmware does. It is never for a demo board with an FPGA breakout: there it erases the
FPGA.

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

The capture's board report shows a full window and sync levels, as on tt09 with `tt_um_rejunity_vga_logo`
([dated result](https://github.com/mithro/tt-vga-capture/tree/main/docs/results/2026-11-24-picosdk-firmware-fpga4)):

```text
profile=rp2350-dbv3 project=None enable=kept (capture firmware)
  board: frame window=481 buffers=1 hsync_active=1 vsync_active=1
  board: frames=2 stalls=0 rxstall=0 sysclk_hz=101000000
wrote frame.vgacap
```

`ttcap png frame.vgacap frame` renders the project's picture.

## If it fails

| What you see | Likely cause | Fix |
|---|---|---|
| Step 1 prints nothing | the macro name or shuttle is wrong | Check both on the project's page at [tinytapeout.com](https://tinytapeout.com) |
| `--asic-index is for ...; ... is an FPGA board` at step 2 | `--board`, the link or this Pi names a demo board with an FPGA breakout | Drop `--asic-index`: an FPGA keeps its design across the switch |
| `--asic-index needs to know which board it is for, and ... disagree` | `--board`, the link and this Pi name different boards | Give the `--board` that the link reaches |
| `board went quiet` and almost nothing written | the wrong index, or no `--asic-index` | Repeat step 1, and give its number |
| `--asic-index is for --firmware picosdk` | `--firmware picosdk` missing | Add it; the stock firmware selects a project with `--project` |

ttcap refuses `--asic-index` only for a board it can name. With a `serial:` link on a machine that is no
Welland bench Pi, nothing names the board, so check the breakout yourself first.

## Next

- [How to capture with the vgacap firmware](how-to-capture.md)
- [The vgacap firmware protocol](PROTOCOL.md)
- [Capture inside the vgacap firmware](explanation-capture.md)
