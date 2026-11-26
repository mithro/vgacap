---
type: how-to
owner: vgacap maintainers
reader: someone whose design reads ui_in to choose what it draws
review: 2027-05-27
---

# How to set a design's inputs during a capture

**You want your design to start one capture with a chosen value on its `ui_in` pins, such as the setting that picks its VGA mode.**

`ttcap capture --ui-in VALUE` sets the inputs while the design is held in reset, so the design starts in the state they select. The board's input mode is put back however the capture ends. Without `--ui-in`, the board's inputs are left as they are.

## What you need

- A capture that works without `--ui-in`, as in [How to capture a stream from a demo board](how-to-capture-stream.md).
- The value your design expects on `ui_in`, from 0 to 255.
- On an FPGA breakout, the design uploaded and enabled first, as in step 4 of [How to capture from a Welland board through the bridge](how-to-capture-welland-bridge.md) with `tt_um_vgacal_modes.bin`.

## Steps

1. In the checkout, run the capture with the value in decimal, `0x` or `0b` form, which drives the inputs and starts the design:

   ```sh
   uv run ttcap capture serial:/dev/ttyACM0 --design tt_um_vgacal_modes --ui-in 1 \
       --mode frame --vga-mode 800x600@60 --clock-hz 40000000 --frames 1 --out modes-ui1.vgacap
   ```

2. In the checkout, run `mkdir -p out && uv run ttcap png modes-ui1.vgacap out/ui1`, which writes the picture the design drew with those inputs.

`ttcap` switches the stock firmware's input mode to `ASIC_RP_CONTROL` for the capture, because only in that mode does the microcontroller drive `ui_in`. The boards on the Welland bench sit in `ASIC_MANUAL_INPUTS`, where setting `tt.ui_in` from the firmware changes nothing. The value is recorded in the capture's description as `ui_in=1`.

## Check

Step 2 shows the picture for that input value. On an FPGA breakout, `tt_um_vgacal_modes` drew its 800x600 mode with `--ui-in 1`, and its 720x400 mode with `--ui-in 2`, each pixel-identical to its reference ([result](https://github.com/mithro/tt-vga-capture/tree/main/docs/results/2026-11-24-frame-capture-fpga4)). The bench run of this page gave the same two modes ([result](https://github.com/mithro/tt-vga-capture/tree/main/docs/results/2026-11-26-docs-bench-ttcap)).

## If it fails

| Step | What you see | Likely cause | Fix |
|---|---|---|---|
| 1 | `ui_in must be 0..255` | the value is outside a byte | give a value from 0 to 255 |
| 1 | `argument --ui-in: invalid` | the value is not a number | write it as `5`, `0x05` or `0b101` |

## Next

- [How to capture whole frames at the design's own clock](how-to-capture-frames.md)
- [ttcap commands and options](reference-commands.md)
