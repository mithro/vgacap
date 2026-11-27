---
type: how-to
owner: vgacap maintainers
reader: someone simulating a Tiny Tapeout VGA design who wants its picture from a byte dump
review: 2027-05-27
---

# How to turn a simulator dump into a vgacap stream

**Your simulation writes `uo_out` as one byte a clock, and you want it as a vgacap stream that the vgacap tools turn into pictures.**

[`vgacap-bin2stream`](bin2stream.py) wraps the bytes in `RAW ` chunks behind a header with the Tiny VGA Pmod's signal map. A dump of every pin on both clock edges is a simulation trace instead: [How to write a simulation trace from a C or C++ testbench](../../src/trace/how-to-write-trace.md).

## What you need

- A simulation of the design that runs for at least two whole frames.
- A vgacap checkout, with [uv](https://docs.astral.sh/uv/).
- vgacap's C tools built with CMake, for the check.

## Steps

1. In your testbench, open `dump.bin` for binary writing once, before the first clock.
2. In your testbench, write `uo_out` to `dump.bin` as one byte once a clock, at the same point of every clock.
3. Run the simulation for at least two whole frames, which fills `dump.bin`.
4. In the vgacap checkout, run `uv run vgacap-bin2stream /path/to/dump.bin out.vgacap --clock-hz 25175000 --mode 0`, with your dump's path and design's clock.

In Icarus Verilog, steps 1 and 2 are these lines:

```verilog
integer fd;
initial fd = $fopen("dump.bin", "wb");
always @(negedge clk) $fwrite(fd, "%c", uo_out);
```

Every option of step 4 is on [the options of vgacap-bin2stream](../../src/tools/reference-options.md#vgacap-bin2stream).

## Check

Step 4 prints the number of bytes it read as samples:

```text
wrote out.vgacap: 1260000 samples
```

`build/vgacap-frames out.vgacap frame`, from the checkout, then writes `frame-0000.ppm` onwards and prints a line for each frame:

```text
frame 0: 640x480 mode=640x480@60 cpl=800 lpf=525 hsync=neg vsync=neg partial=0 glitches=0
```

## If it fails

| What you see | Likely cause | Fix |
|---|---|---|
| `frames=0` and exit status 2 from `vgacap-frames` | The dump holds less than two whole frames | Run the simulation longer, as in step 3 |
| A torn picture | Bytes were not written exactly once a clock | Write one byte at one point of every clock, as in step 2 |

## Next

- [The C tools](../../src/tools/README.md)
- [What a simulation trace records](../../src/trace/explanation-traces.md)
