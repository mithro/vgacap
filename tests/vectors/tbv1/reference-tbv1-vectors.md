---
type: reference
owner: vgacap maintainers
reader: someone changing the tb-v1 sampler, or checking what its test vectors cover
review: 2027-05-27
---

# The tb-v1 test vectors

**The six traces in this directory, the designs they come from, and what the tb-v1 sampler must find in each.**

Each case directory holds `trace.vgch`, the trace decompressed, and the `frames.txt` and `frame_hashes.fnv1a64-tb2026-rgb888.txt` the testbench pipeline wrote for it. [`test_tbv1_vectors.py`](../../../python/tests/test_tbv1_vectors.py) runs `vgacap-sample --sampler tb-v1` on each trace, and both files must come out byte for byte. A difference is fixed in the sampler, never in the vectors. What the trace holds is on [What a simulation trace records](../../../src/trace/explanation-traces.md).

## Designs

Case is the directory, and Design what its test design drives on `uo_out`. The designs are in `tests/sim/designs/` of [tinytapeout-vga-videos](https://github.com/mithro/tinytapeout-vga-videos/tree/25ee61ae98325223a261ceabb3383913da3cd982/tests/sim/designs), at the generator commit below.

| Case | Design |
|---|---|
| `bars640` | 640x480@60 at one pixel a clock: eight bars that rotate by one bar each frame |
| `bars_cpp2` | the bars of `bars640` with each pixel held for 2 clocks |
| `frac` | 640x480@60 timing with 1,525-clock lines: 1.90625 clocks a pixel |
| `glitch` | `bars640` with a one-clock break in the vsync pulse of every fourth frame |
| `nosync` | a constant `uo_out`: no sync signal at all |
| `playground` | the VGA Playground's sync timing, with a pattern that moves one pixel a frame |

## Results

Case is the directory. The other columns are lines of its `frames.txt`, read when the site is built: Status is the run's, and Sampler status tb-v1's own.

```{vgacap-tbv1-vectors} results
```

## Where they come from

Repository is the repository at the commit that made the vectors, read from [`MANIFEST.txt`](MANIFEST.txt). The manifest also holds each file's SHA-256, which the test checks.

```{vgacap-tbv1-vectors} provenance
```
