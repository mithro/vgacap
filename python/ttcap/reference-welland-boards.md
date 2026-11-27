---
type: reference
owner: vgacap maintainers
reader: someone looking up a Welland bench board's slug, Pi or bridge address
review: 2027-05-27
---

# Welland bench boards in ttcap

**The Welland bench boards that `ttcap` knows by name, with the Pi and the bridge address of each.**

The first table is built from `WELLAND` in [`boards.py`](boards.py) each time the site is built, and is what `ttcap demo --board` and `ttcap capture --board` accept. Some of its rows name boards the bench no longer has ([#39](https://github.com/mithro/vgacap/issues/39)).

Boards move between Pis, so confirm a board by its USB serial on its `status.json` first. [How to capture from a Welland board through the bridge](how-to-capture-welland-bridge.md) shows that check.

## Boards by name

Slug is the name `--board` takes, and Pi the host name of the Raspberry Pi it is plugged into. Bridge is the address of that Pi's fpgas.online bridge (`fpgas-tt`), on the bench network. Tunnel port is the local port `ttcap` suggests for an SSH tunnel to that bridge.

Chip is the demo board's microcontroller, whose pins are on [Demo board GPIO maps for capture](reference-gpio-maps.md). A slug that starts `fpga-` is a demo board with an FPGA breakout and takes `--design`. The others are demo boards with an ASIC breakout and take `--project`.

```{ttcap-welland-boards}
```

## USB serials

Board is the name the pages use, and Site slug the board's name on [tinytapeout.fpgas.online](https://tinytapeout.fpgas.online). USB serial is the `usb_serial` its `status.json` shows, and Source where that was read. `--board` says whether `ttcap --board` accepts the board's name.

| Board | Site slug | Breakout | `--board` | USB serial | Source |
|---|---|---|---|---|---|
| fpga-1 | `fpga-1` | FPGA | yes | `4df39a7a6856f86f` | [2026-11-26](https://github.com/mithro/tt-vga-capture/tree/main/docs/results/2026-11-26-docs-bench-ttcap) |
| fpga-3 | `fpga-3` | FPGA | yes | `8c46329b33590ecb` | [`status.json`](https://tinytapeout.fpgas.online/board/fpga-3/status.json) |
| fpga-4 | `fpga-4` | FPGA | yes | `a2961e5cac65b25f` | [2026-11-26](https://github.com/mithro/tt-vga-capture/tree/main/docs/results/2026-11-26-docs-bench-ttcap) |
| tt07 | `tt-de641070db746f27` | ASIC | yes | `de641070db746f27` | [2026-11-26](https://github.com/mithro/tt-vga-capture/tree/main/docs/results/2026-11-26-docs-bench-ttcap) |
| tt09 | `tt-3b0880b48ee5c388` | ASIC | no ([#39](https://github.com/mithro/vgacap/issues/39)) | `3b0880b48ee5c388` | [2026-11-26](https://github.com/mithro/tt-vga-capture/tree/main/docs/results/2026-11-26-docs-bench-ttcap) |
