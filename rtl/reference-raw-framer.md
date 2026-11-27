---
type: reference
owner: vgacap maintainers
reader: someone wiring the raw framer into an FPGA design
review: 2027-05-27
---

# The raw framer's ports, parameters and output

**The ports and parameters of `vgacap_raw_framer`, the chunks it sends, and how fast it sends them.**

The values come from the Migen source [`vgacap_raw_framer.py`](vgacap_raw_framer.py), and the generated [`vgacap_raw_framer.v`](vgacap_raw_framer.v) has the same ports. How the framer works is on [The raw framer](explanation-raw-framer.md).

## Ports

Port is the name in the Verilog and in Migen, and Direction is seen from the framer. Width is in bits.

| Port | Direction | Width | Meaning |
|---|---|---|---|
| `sys_clk` | in | 1 | the clock every register runs on |
| `sys_rst` | in | 1 | synchronous reset: active high |
| `sample` | in | 8 | one sample: a Tiny Tapeout `uo_out` |
| `sample_valid` | in | 1 | high on each clock that `sample` is to be taken |
| `out_data` | out | 8 | the next byte of the chunk stream |
| `out_valid` | out | 1 | high while `out_data` holds a byte |
| `out_ready` | in | 1 | high when the sink takes the byte |
| `dropped` | out | 32 | the samples dropped since reset |

A byte moves on each clock with `out_valid` and `out_ready` both high. A byte offered and not taken stays on `out_data`, unchanged, until it is taken. In Migen the clock and reset are the `sys` clock domain's.

## Parameters

Parameter is the argument of `VgacapRawFramer()`, and Default its value in the committed Verilog. Limit is what the constructor refuses outside.

| Parameter | Default | Limit | Meaning |
|---|---|---|---|
| `samples_per_chunk` | 256 | a positive multiple of 4 | samples in every `RAW ` chunk |
| `fifo_words` | 128 | at least `samples_per_chunk` ÷ 4 | 32-bit words the FIFO holds |

## Chunks it sends

The framer sends a `RAW ` chunk for each `samples_per_chunk` samples, and a `TIME` chunk after it has dropped a chunk. Their layout is on [The vgacap stream format](../src/stream/reference-stream-format.md#raw-packed-samples) and [its `TIME` section](../src/stream/reference-stream-format.md#time-time-and-losses). Chunk and Field name a field there, and Value is what the framer puts in it.

| Chunk | Field | Value |
|---|---|---|
| `RAW ` | length | 4 + `samples_per_chunk` |
| `RAW ` | `sample_count` | `samples_per_chunk` |
| `TIME` | `host_time_ns` | 0 |
| `TIME` | `clock_hz` | 0 |
| `TIME` | `dropped_samples` | `dropped` as the chunk starts: the total since reset |
| `TIME` | `msg` | `overrun` |

## The header the host writes

The framer sends no `VGCH` header. [`framer_to_stream.py`](framer_to_stream.py) writes this one in front of the chunks. Field is the header field, and Value what the chunks need.

| Field | Value |
|---|---|
| `version` | 1 |
| `sample_bits` | 8 |
| `samples_per_word` | 4 |
| `mode` | 0: extclk |
| `flags` | `VGACAP_FLAG_FIRST_SAMPLE_MSB` |
| `signal_map` | the Tiny VGA Pmod on `uo_out` |
| `clock_hz` | the sample rate: only the host knows it |

## Throughput

Item is what is counted, and Value its size at 256 samples a chunk.

| Item | Value |
|---|---|
| Most bytes out in one clock | 1 |
| Clocks to send one `RAW ` chunk | 272: 256 sample bytes + 12 header bytes + 4 idle clocks |
| Share of clocks that can carry a sample into an always-ready sink | 256 ÷ 272: about 94.1% |
