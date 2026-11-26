---
type: reference
owner: vgacap maintainers
reader: someone looking up a Welland bench board's slug, Pi or bridge address
review: 2027-05-27
---

# Welland bench boards in ttcap

**The Welland bench boards that `ttcap` knows by name, with the Pi and the bridge address each one has.**

The table is built from `WELLAND` in [`boards.py`](boards.py) each time the site is built. It is what `ttcap demo --board` and `ttcap capture --board` accept. Boards move between Pis, so a board is confirmed by its USB serial on its `status.json` before use, as [How to capture from a Welland board through the bridge](how-to-capture-welland-bridge.md) shows.

Slug is the board's name on [tinytapeout.fpgas.online](https://tinytapeout.fpgas.online). Pi is the host name of the Raspberry Pi the board is plugged into. Bridge is the address of that Pi's fpgas.online bridge (`fpgas-tt`), which is on the bench network. Tunnel port is the local port `ttcap` suggests for an SSH tunnel to that bridge. Profile is the demo board pin layout, from [Demo board GPIO maps for capture](reference-gpio-maps.md). Design option is the option that selects the design: `--design` for a bitstream on an FPGA breakout, `--project` for a project on an ASIC breakout.

```{ttcap-welland-boards}
```
