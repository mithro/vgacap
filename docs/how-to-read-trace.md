---
type: how-to
owner: vgacap maintainers
reader: someone writing their own reader for vgacap simulation traces
review: 2027-05-27
---

# How to read a simulation trace without vgacap

**You want your own program to read the samples of a vgacap simulation trace, with a zstd decoder and no vgacap code.**

Every field named here is in [the simulation trace format](trace-format.md). A reader that needs only part of a long trace uses the seek table there instead of step 1.

## What you need

- A trace, such as `design.trace.zst`.
- The `zstd` command-line tool, or a zstd library in your program.

## Steps

1. In a shell, run `zstd -d design.trace.zst -o design.vgch`, which writes the whole uncompressed stream.
2. In your program, read `design.vgch` as chunks: a 4-byte tag, a u32 length, then that many payload bytes, skipping unknown tags.
3. In your program, take `sample_bits`, `samples_per_clock`, `clock_bit`, `clock_first` and `clock_hz` from the `VGCH` chunk.
4. In your program, check that the first `META` chunk has the line `layout=tt-um-43`, so the bits are those of the layout table.
5. In your program, start the sample number `n` at 0, and set it to `first_sample` at each `BLKH` chunk.
6. In your program, expand each `RUNS` pair into `run` samples from `n` on, each with the clock bit [Sample numbers](trace-format.md#sample-numbers) gives it.
7. In your program, take clock `n` ÷ 2 for sample `n`, and its time from the rule in [Clocks and time](trace-format.md#clocks-and-time).

## Check

Step 1 writes a file whose first four bytes are `VGCH`. The number of samples your program counts equals the `total_samples` that `vgacap-dump design.trace.zst` prints, where vgacap is at hand:

```text
total_samples=10080000 crc64=782dfffdda11f725
```

## If it fails

| What you see | Likely cause | Fix |
|---|---|---|
| `zstd: design.trace.zst: unsupported format` at step 1 | The file is a plain uncompressed stream | Skip step 1: a file starting with `VGCH` is already the stream |
| `Read error (39) : premature end` at step 1 | The trace was cut short by a killed run | Read its complete frames only, as [A trace cut short](trace-format.md#a-trace-cut-short) says |
| A count one sample out at a block boundary | `n` was not set at a `BLKH` | Set `n` from each `BLKH`, as in step 5 |

## Next

- [The simulation trace format](trace-format.md)
- [What a simulation trace records](../src/trace/explanation-traces.md)
