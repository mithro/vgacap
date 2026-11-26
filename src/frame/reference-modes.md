---
type: reference
owner: vgacap maintainers
reader: someone looking up the VGA timings vgacap recognises
review: 2027-05-27
---

# The VGA mode table

**You want the VGA modes vgacap recognises, with every timing value, as libvgaframe and the samplers use them.**

The table is built from [`src/frame/modes.c`](modes.c) each time these pages are built, so it is the code's own table. How libvgaframe matches a capture against it is on [How libvgaframe rebuilds frames](explanation-rebuilding-frames.md).

The columns:

- Mode is the name: width x height @ refresh in Hz.
- H active, H front, H sync and H back are the active pixels, front porch, sync pulse and back porch, in clocks; Clocks a line is their sum.
- V active, V front, V sync and V back are the same in lines; Lines a frame is their sum.
- Hsync and Vsync are the level of each sync pulse: negative pulses low, positive pulses high.
- Window match says whether the mode is found from clocks a line alone, as for a `FRAM` window. The modes marked no came from the simulation testbench, and only an exact match on both totals finds them.

```{vgacap-modes}
```
