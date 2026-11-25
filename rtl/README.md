# vgacap/rtl: capture in an FPGA

Capture logic to build into your own FPGA design, so the FPGA writes a
[vgacap stream](https://github.com/mithro/tt-vga-capture/blob/main/docs/research/2026-09-15-stream-format.md) itself: small [Migen](https://github.com/m-labs/migen) modules for an FPGA capture harness,
with their generated Verilog committed alongside
([design note](https://github.com/mithro/tt-vga-capture/blob/main/docs/design/2026-11-23-shared-rtl-capture.md)).

On the demo board's FPGA breakout (iCE40UP5K) the framed bytes leave on
`uo_out` + `uio` for the board's RP2350
([results on fpga-4, 250 kHz and 25 MHz](https://github.com/mithro/tt-vga-capture/tree/main/docs/results/2026-11-24-rtl-framer-fpga4)).

The modules import nothing from the rest of vgacap and need none of its
build system: take the `.py` into a [LiteX](https://github.com/enjoy-digital/litex) design, or the `.v` into a
[yosys](https://github.com/YosysHQ/yosys), [Verilator](https://github.com/verilator/verilator) or [Icarus](https://github.com/steveicarus/iverilog) flow. The contract between projects is the
stream format, which vgacap's C and Python readers check.

| module | purpose | code | status |
|---|---|---|---|
| `vgacap_raw_framer` | samples to `RAW` chunks | [`vgacap_raw_framer.py`](vgacap_raw_framer.py) | tested |
| `UoSampler` | `uo_out` registered on the design clock | [tt-harness-handheld-vga](https://github.com/mithro/tt-harness-handheld-vga) | to be contributed |
| RLE chunk writer | RLE chunks for byte-limited links | -- | waits on the [compact-chunk decision](https://github.com/mithro/tt-vga-capture/blob/main/docs/research/2026-11-24-board-rle-throughput.md) |

## vgacap_raw_framer

- Ports: `sample[7:0]` and `sample_valid` in; `out_data[7:0]` and
  `out_valid` out, with `out_ready` in from the sink; `dropped[31:0]` out
  (cumulative); `sys_clk`, `sys_rst`.
- Each chunk holds `samples_per_chunk` samples (default 256), four to a
  word, first sample in bits 31:24, words little-endian. The chunk header
  says `sample_bits=8`, `samples_per_word=4` and flags
  `VGACAP_FLAG_FIRST_SAMPLE_MSB`. The host writes the `VGCH` header in
  front.
- A chunk is admitted at its first sample only if the FIFO (`fifo_words`,
  default 128) has room for all of it. Otherwise it is dropped whole, and a
  `TIME` chunk (message `overrun`) follows the next `RAW` chunk to finish.
  Its count is the cumulative number of samples dropped as of the clock that
  `TIME` chunk starts. Its position in the stream does not mark the gap:
  `RAW` chunks admitted before a drop can come out after the report.
- No flush: if `sample_valid` stops mid-chunk, the stream ends in a
  truncated `RAW` chunk (its header went out when it was admitted), and any
  `TIME` report still due is never sent. Everything before that parses; stop
  a capture on a chunk boundary to end it cleanly.
- The output carries at most one byte a clock. A chunk of 256 samples takes
  272 clocks (12 header bytes and 4 idle FSM clocks), so an always-ready sink
  keeps up with samples on at most 256/272, about 94.1%, of clocks; see the
  [module docstring](vgacap_raw_framer.py).

Regenerate the Verilog after changing the source (a test fails otherwise):

    uv run python rtl/vgacap_raw_framer.py > rtl/vgacap_raw_framer.v

The tests ([`python/tests/test_rtl_raw_framer.py`](../python/tests/test_rtl_raw_framer.py))
run a Migen simulation with an always-ready sink, a slow sink (whole chunks
are dropped and reported), gaps between input samples, runs of ready cycles
and a random sink, checking the valid/ready hold rule on every clock. They
run Icarus on the committed `.v` with
[`tb_vgacap_raw_framer.v`](tb_vgacap_raw_framer.v) (always ready, random
backpressure, and overruns from a slow sink and from full rate, with the
same hold check), and parse every output with
[`vgacap.stream.read_chunks`](../python/vgacap/stream.py). CI runs the
Icarus test in the [`gstreamer` job](../.github/workflows/ci.yml), which installs `iverilog` and fails if
the test is skipped.
