---
type: how-to
owner: vgacap maintainers
reader: someone with a vgacap checkout who wants a known trace to test a sampler or a tool with
review: 2027-05-27
---

# How to write a synthetic simulation trace in Python

**You want a simulation trace of a VGA picture you choose, with exact timing and no simulator.**

[`synthtrace.py`](synthtrace.py) writes what a testbench would record from a VGA design that counts pixel positions. It writes a plain, uncompressed trace, which every trace reader takes. A trace from a real testbench is on [How to write a simulation trace from a C or C++ testbench](../../src/trace/how-to-write-trace.md).

## What you need

- A vgacap checkout, with [uv](https://docs.astral.sh/uv/), which installs numpy for the module.
- vgacap's C tools built with CMake, for the check.

## Steps

1. In the checkout, save the script below as `synth_bars.py`, with the mode, length and picture you want.
2. In the checkout, run `uv run python synth_bars.py`, which writes `synth.vgch`.

The script for step 1. It writes twelve frames of 640x480@60 at one pixel a clock, eight vertical bars of six-bit colour:

```python
from vgacap.modes import MODES
from vgacap.synthtrace import build, write_trace

mode = MODES["640x480@60"]
uo, rst_n = build(mode, 12 * mode.cpl * mode.lpf, pixel=lambda x, y: (x // 80) * 9)
write_trace("synth.vgch", uo, rst_n, clock_hz=25175000, project="tt_um_synth",
            calib_seconds="0.05", seconds="0.1")
```

`build` takes these keyword arguments as well:

| Argument | Default | Meaning |
|---|---|---|
| `phase` | `"testpatterns"` | when the syncs change: `"testpatterns"` or `"playground"` |
| `cpp` | 1 | clocks a pixel, which may be fractional |
| `reset_clocks` | 10 | clocks in reset at the start |
| `reset_uo` | 0 | `uo_out` during reset |
| `h_glitches` | none | pairs of clock and length with hsync inverted |
| `v_glitches` | none | pairs of clock and length with vsync inverted |

## Check

`build/vgacap-sample synth.vgch --out sampled --name synth`, from the checkout, finds the picture. `sampled/centre-v1/frames.txt` then starts:

```text
id: tt_um_synth
status: ok
sampler: centre-v1
sampler_status: ok
pixel_format: rgb6
width: 640
height: 480
mode: 640x480@60
```

## If it fails

| What you see | Likely cause | Fix |
|---|---|---|
| `fewer clocks than reset clocks` at step 2 | The length is shorter than the reset | Give more clocks than `reset_clocks` |
| `unknown sync phase` at step 2 | `phase` is misspelt | Give `"testpatterns"` or `"playground"` |
| `sampler_status: no-sync` in the check | The trace ends before the sampler has seen enough frames | Make the trace longer than `calib_seconds` plus `seconds` |

## Next

- [What a simulation trace records](../../src/trace/explanation-traces.md)
- [The simulation trace format](../../docs/trace-format.md)
- [The tb-v1 test vectors](../../tests/vectors/tbv1/reference-tbv1-vectors.md)
