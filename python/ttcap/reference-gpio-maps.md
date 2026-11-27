---
type: reference
owner: vgacap maintainers
reader: someone looking up which demo board pins a capture samples
review: 2027-05-27
---

# Demo board GPIO maps for capture

**The microcontroller pins a capture reads on the RP2040 and the RP2350 demo boards, and the VGA signal on each.**

Both tables are built from the two board profiles in [`boards.py`](boards.py) each time the site is built. `ttcap capture --profile auto` picks the profile whose project clock and `uo_out` pins match the board's `GPIOMap.all()`, which `ttcap probe` prints. How the pins are read is on [Capture on the demo board](explanation-demo-board-capture.md).

## Pins

Signal is the Tiny Tapeout signal, and VGA signal the [Tiny VGA Pmod](https://github.com/mole99/tiny-vga) signal it carries. GPIO is the microcontroller pin on each profile. Sample bit is the bit of a sample that holds the signal, counted from the first sampled GPIO in the next table.

```{ttcap-gpio-map} pins
```

## Sampled window

Profile is the name `ttcap` gives the layout, and Chip the demo board's microcontroller. First sampled GPIO and Bits sampled give the window of pins one PIO `in` instruction reads. Samples per word is how many samples fill one 32-bit word. PIO GPIO base is the first GPIO the sampling PIO block reaches.

```{ttcap-gpio-map} window
```
