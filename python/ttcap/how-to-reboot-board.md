---
type: how-to
owner: vgacap maintainers
reader: someone who needs a demo board in its USB bootloader, or back on the stock firmware
review: 2027-05-27
---

# How to reboot a demo board

**You want an RP2350 demo board in its USB bootloader to load a UF2 file, or back on the stock firmware after the vgacap firmware.**

`ttcap reboot` restarts the board through its link and returns once the board has taken the command. Its USB device then goes away and comes back as something else. Loading the vgacap firmware onto the bootloader drive is on [the vgacap firmware](../../firmware/README.md).

## What you need

- A vgacap checkout with `uv sync` run in it.
- The board's link: its serial device, which changes name when the board reboots into another firmware.
- No capture running on the board, since the reboot ends it.

## Steps

1. In the checkout, reboot the board into its USB bootloader, which makes it a mass-storage drive that takes a UF2 file:

   ```sh
   uv run ttcap reboot serial:/dev/ttyACM0 --to bootsel
   ```

2. On a board running the vgacap firmware, reboot it to the firmware in flash, which is the stock firmware:

   ```sh
   uv run ttcap reboot serial:/dev/ttyACM0 --firmware picosdk --to flash
   ```

Without `--firmware`, `ttcap` asks the board which firmware runs. It waits two seconds for the vgacap firmware's `hello` reply, and otherwise takes the board for the stock firmware. `--to` is `flash` unless given. The stock firmware reboots through `machine.bootloader()` or `machine.reset()`, and the vgacap firmware through its `bootsel` and `reboot` commands. `--to flash` on the stock firmware has no recorded run on hardware ([#40](https://github.com/mithro/vgacap/issues/40)).

## Check

Step 1 prints `the board runs micropython`, then `rebooting to bootsel`, and exits 0. Step 2 prints `rebooting to flash` and exits 0. After step 1 the board appears as a USB drive, and `lsusb` lists `Raspberry Pi RP2350 Boot`. After step 2 the board's serial device answers `ttcap probe` again ([How to probe a demo board](how-to-probe-board.md)). Both steps were run on the fpga-4 and tt09 boards, each on its own Pi ([result](https://github.com/mithro/tt-vga-capture/tree/main/docs/results/2026-11-24-picosdk-firmware-fpga4)).

## If it fails

| Step | What you see | Likely cause | Fix |
|---|---|---|---|
| 1 | `reboot failed:` with `No such file or directory` | the device name is wrong | check `ls /dev/ttyACM*` |
| 1 | `reboot failed: TimeoutError:` | another program holds the port | close it and run the step again |
| 2 | `reboot failed:` with `No such file or directory` | the vgacap firmware has its own device name | use its `/dev/serial/by-id/usb-vgacap_...` link |

## Next

- [The vgacap firmware](../../firmware/README.md)
- [How to probe a demo board](how-to-probe-board.md)
