---
type: how-to
owner: vgacap maintainers
reader: someone with a Migen or LiteX FPGA design who wants it to write a vgacap stream
review: 2027-05-27
---

# How to add the raw framer to a Migen or LiteX design

**You want your Migen or LiteX design to frame its `uo_out` samples as vgacap chunks, and the host to save them as a stream.**

The module imports Migen and nothing else from vgacap. A design in plain Verilog uses [How to add the raw framer's Verilog to an FPGA design](how-to-add-verilog.md) instead. What the ports do is on [The raw framer's ports, parameters and output](reference-raw-framer.md).

## What you need

- An FPGA design in [Migen](https://github.com/m-labs/migen) or [LiteX](https://github.com/enjoy-digital/litex), with a link that carries bytes to the host.
- A vgacap checkout, for [`vgacap_raw_framer.py`](vgacap_raw_framer.py) and [`framer_to_stream.py`](framer_to_stream.py).
- Python 3 on the host.

## Steps

1. In your design's Python sources, add `rtl/vgacap_raw_framer.py` from the vgacap checkout, unchanged, so that `from vgacap_raw_framer import VgacapRawFramer` imports it.
2. In your module, add `self.submodules.framer = framer = VgacapRawFramer(samples_per_chunk=256, fifo_words=128)`, which runs on the `sys` clock domain.
3. In your module, connect the ports as shown below, with `uo_out`, `take` and the `link_` signals standing for your own.
4. In your design, stop `sample_valid` after a whole number of chunks: a multiple of 256 samples.
5. On the host, save every byte the link delivers, in order, as `framer.bin`.
6. On the host, run `python3 rtl/framer_to_stream.py framer.bin out.vgacap --clock-hz HZ`, with the sample rate as `HZ`, to write the stream.

The connections for step 3:

```python
self.comb += [
    framer.sample.eq(uo_out),
    framer.sample_valid.eq(take),
    framer.out_ready.eq(link_ready),
    link_data.eq(framer.out_data),
    link_valid.eq(framer.out_valid),
]
```

A design whose sample clock is another domain wraps the module in Migen's `ClockDomainsRenamer`.

## Check

Step 6 prints the chunks it read, with no samples dropped:

```text
wrote out.vgacap: 4 RAW chunks, 1024 samples, 0 dropped
```

`out.vgacap` is then a vgacap stream that every vgacap reader takes, such as [the vgacap command-line tools](../src/tools/README.md).

## If it fails

| Step | What you see | Likely cause | Fix |
|---|---|---|---|
| 2 | `samples_per_chunk must be a positive multiple of 4` | The chunk size cannot be packed into words | Give a multiple of 4 |
| 2 | `fifo_words must hold at least one chunk` | The FIFO is smaller than a chunk | Give at least `samples_per_chunk` ÷ 4 words |
| 6 | `truncated chunk 'RAW '` | Sampling stopped in the middle of a chunk | Stop after a multiple of 256 samples, as in step 4 |
| 6 | More than `0 dropped` | The link took bytes more slowly than samples arrived | Sample less often or use a faster link |

## Next

- [The raw framer](explanation-raw-framer.md)
- [The raw framer's ports, parameters and output](reference-raw-framer.md)
- [How to regenerate the raw framer's Verilog and run its tests](how-to-regenerate.md)
