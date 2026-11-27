---
type: how-to
owner: vgacap maintainers
reader: someone with a Verilog FPGA design who wants it to write a vgacap stream
review: 2027-05-27
---

# How to add the raw framer's Verilog to an FPGA design

**You want your Verilog FPGA design to frame its `uo_out` samples as vgacap chunks, and the host to save them as a stream.**

The Verilog is generated from the Migen source, so it needs no Python to build. A design in Migen or LiteX uses [How to add the raw framer to a Migen or LiteX design](how-to-add-migen.md) instead. What the ports do is on [The raw framer's ports, parameters and output](reference-raw-framer.md).

## What you need

- An FPGA design in Verilog, with a link that carries bytes to the host.
- A vgacap checkout, for [`vgacap_raw_framer.v`](vgacap_raw_framer.v) and [`framer_to_stream.py`](framer_to_stream.py).
- Python 3 on the host.

## Steps

1. In your design's source list, add `rtl/vgacap_raw_framer.v` from the vgacap checkout, unchanged.
2. In your design, instantiate `vgacap_raw_framer` as shown below, with its clock and active-high reset on your design's clock and reset.
3. In your design, drive `sample` from `uo_out`, and hold `sample_valid` high on each clock that is to be sampled.
4. In your design, send `out_data` to the host on each clock with both `out_valid` and `out_ready` high.
5. In your design, stop `sample_valid` after a whole number of chunks: a multiple of 256 samples.
6. On the host, save every byte the link delivers, in order, as `framer.bin`.
7. On the host, run `python3 rtl/framer_to_stream.py framer.bin out.vgacap --clock-hz HZ`, with the sample rate as `HZ`, to write the stream.

The instance for step 2, with `link_ready` standing for your link's ready signal. `dropped` needs a 32-bit wire: `wire [31:0] dropped;`.

```verilog
vgacap_raw_framer framer (
    .sys_clk(clk), .sys_rst(!rst_n),
    .sample(uo_out), .sample_valid(sample_valid),
    .out_data(link_data), .out_valid(link_valid), .out_ready(link_ready),
    .dropped(dropped)
);
```

## Check

Step 7 prints the chunks it read, with no samples dropped:

```text
wrote out.vgacap: 4 RAW chunks, 1024 samples, 0 dropped
```

`out.vgacap` is then a vgacap stream that every vgacap reader takes, such as [the vgacap command-line tools](../src/tools/README.md).

## If it fails

| What you see | Likely cause | Fix |
|---|---|---|
| `truncated chunk 'RAW '` at step 7 | Sampling stopped in the middle of a chunk | Stop after a multiple of 256 samples, as in step 5 |
| More than `0 dropped` at step 7 | The link took bytes more slowly than samples arrived | Sample less often or use a faster link |
| `non-printable chunk tag` at step 7 | The host lost or added bytes | Check the link and repeat steps 6 and 7 |

## Next

- [The raw framer](explanation-raw-framer.md)
- [The raw framer's ports, parameters and output](reference-raw-framer.md)
- [How to regenerate the raw framer's Verilog and run its tests](how-to-regenerate.md)
