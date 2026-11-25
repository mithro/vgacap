# vgacap/rtl: capture in an FPGA

Capture logic to build into your own FPGA design, so the FPGA writes a
vgacap stream itself: small Migen modules for an FPGA capture harness,
with their generated Verilog committed alongside
([design note](https://github.com/mithro/tt-vga-capture/blob/main/docs/design/2026-11-23-shared-rtl-capture.md)).

On the demo board's FPGA breakout (iCE40UP5K) the framed bytes leave on
`uo_out` + `uio` for the board's RP2350
([results on fpga-4, 250 kHz and 25 MHz](https://github.com/mithro/tt-vga-capture/tree/main/docs/results/2026-11-24-rtl-framer-fpga4)).

The modules import nothing from the rest of vgacap and need none of its
build system: take the `.py` into a LiteX design, or the `.v` into a
yosys, Verilator or Icarus flow. The contract between projects is the
stream format, which vgacap's C and Python readers check.

| module | what | status |
|---|---|---|
| `vgacap_raw_framer` | 8-bit samples in; `RAW` chunks (fixed size, header first) and overrun `TIME` chunks out, as a valid/ready byte stream | here, tested |
| `UoSampler` | `uo_out` registered on the design clock | in [tt-harness-handheld-vga](https://github.com/mithro/tt-harness-handheld-vga), to be contributed |
| RLE chunk writer | for byte-limited links | waits on the [compact-chunk decision](https://github.com/mithro/tt-vga-capture/blob/main/docs/research/2026-11-24-board-rle-throughput.md) |

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
  default 128) has room for all of it. Otherwise it is dropped whole, and
  the next chunk out is followed by a `TIME` chunk with the cumulative
  dropped count and the message `overrun`. The stream is always parseable.
- The output carries one byte a clock, so a sample on every clock needs
  slightly more than the output can carry (each chunk adds 12 header
  bytes); see the module docstring.

Regenerate the Verilog after changing the source (a test fails otherwise):

    uv run python rtl/vgacap_raw_framer.py > rtl/vgacap_raw_framer.v

The tests (`python/tests/test_rtl_raw_framer.py`) run a Migen simulation
with an always-ready sink, a slow sink (whole chunks are dropped and
reported) and gaps between input samples, run Icarus on the committed `.v` with
`tb_vgacap_raw_framer.v`, and parse every output with
`vgacap.stream.read_chunks`.
