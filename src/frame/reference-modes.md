---
type: reference
owner: vgacap maintainers
reader: someone looking up the VGA timings vgacap recognises
review: 2027-05-27
---

# The VGA mode table

**You want the VGA modes vgacap recognises, with every timing value, as libvgaframe and the samplers use them.**

The table is built from [`src/frame/modes.c`](modes.c) each time these pages are built, so it is the code's own table. How libvgaframe matches a capture against it is on [How libvgaframe rebuilds frames](explanation-rebuilding-frames.md).

In both tables, Mode is the name: width x height @ refresh in Hz. Active, Front porch, Sync and Back porch are the parts of a line, in clocks, or of a frame, in lines. The total is their sum. A positive pulse is high, and a negative pulse low.

Window match says whether a `FRAM` window, which knows only its clocks a line, finds the mode. The modes marked no came from the simulation testbench. Only an exact match on both totals finds them.

## Horizontal: a line

```{vgacap-modes} horizontal
```

## Vertical: a frame

```{vgacap-modes} vertical
```
