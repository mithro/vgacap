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
| `sys_rst` | in | 1 | synchronous reset, active high |
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

## RAW chunk

Offset is in bytes from the chunk's start, and every field is little-endian. The sizes are those of the default 256 samples a chunk.

| Offset | Size | Field | Value |
|---|---|---|---|
| 0 | 4 | tag | `RAW ` |
| 4 | 4 | length | 4 + `samples_per_chunk` |
| 8 | 4 | sample count | `samples_per_chunk` |
| 12 | `samples_per_chunk` | samples | four to a 32-bit word, the first sample in bits 31:24 |

## TIME chunk

The framer sends one after it has dropped a chunk. Offset is in bytes from the chunk's start, and every field is little-endian.

| Offset | Size | Field | Value |
|---|---|---|---|
| 0 | 4 | tag | `TIME` |
| 4 | 4 | length | 25 |
| 8 | 8 | `host_time_ns` | 0 |
| 16 | 4 | `clock_hz` | 0 |
| 20 | 4 | `dropped_samples` | `dropped` as the chunk starts: the total since reset |
| 24 | 2 | message length | 7 |
| 26 | 7 | message | `overrun` |

## The header the host writes

The framer sends no `VGCH` header. [`framer_to_stream.py`](framer_to_stream.py) writes this one in front of the chunks. Field is the header field, and Value what the chunks need.

| Field | Value |
|---|---|
| `version` | 1 |
| `sample_bits` | 8 |
| `samples_per_word` | 4 |
| `flags` | `VGACAP_FLAG_FIRST_SAMPLE_MSB` |
| `signal_map` | the Tiny VGA Pmod on `uo_out` |
| `clock_hz` | the rate samples went in, which only the host knows |

## Throughput

Item is what is counted, and Value its size at 256 samples a chunk.

| Item | Value |
|---|---|
| Bytes out on each clock, at most | 1 |
| Clocks to send one `RAW ` chunk | 272: 256 sample bytes, 12 header bytes and 4 idle clocks |
| Share of clocks that can carry a sample, with a sink always ready | 256 ÷ 272: about 94.1% |
