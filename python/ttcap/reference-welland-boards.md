---
type: reference
owner: vgacap maintainers
reader: someone looking up a Welland bench board's slug, Pi or bridge address
review: 2027-05-27
---

# Welland bench boards in ttcap

**The Welland bench boards that `ttcap` knows by name, with the Pi and the bridge address of each.**

The table is built from `WELLAND` in [`boards.py`](boards.py) each time the site is built, and is what `ttcap demo --board` and `ttcap capture --board` accept. The RP2040 rows, tt03p5 to tt08, date from 2026-09-15. The site now names chip boards by USB serial, and these rows are unchecked against them ([#39](https://github.com/mithro/vgacap/issues/39)). Boards move between Pis, so confirm a board by its USB serial on its `status.json` first. [How to capture from a Welland board through the bridge](how-to-capture-welland-bridge.md) shows that check.

Slug is the board's name on [tinytapeout.fpgas.online](https://tinytapeout.fpgas.online), and Pi the host name of the Raspberry Pi it is plugged into. Bridge is the address of that Pi's fpgas.online bridge (`fpgas-tt`), on the bench network. Tunnel port is the local port `ttcap` suggests for an SSH tunnel to that bridge.

Chip is the demo board's microcontroller, whose pins are on [Demo board GPIO maps for capture](reference-gpio-maps.md). A slug that starts `fpga-` carries an FPGA breakout and takes `--design`; the others carry an ASIC breakout and take `--project`.

```{ttcap-welland-boards}
```
