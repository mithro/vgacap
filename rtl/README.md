# vgacap/rtl: capture in an FPGA

Leaf modules for FPGA capture harnesses, in Migen with their generated
Verilog committed alongside (design note: tt-vga-capture
`docs/design/2026-11-23-shared-rtl-capture.md`). No imports from the rest of
vgacap and no build-system coupling: take the `.py` into a LiteX design, or
the `.v` into a yosys/Verilator/Icarus flow. The contract between projects is
the stream format, which vgacap's C and Python readers check.

| module | what | status |
|---|---|---|
| `vgacap_raw_framer` | 8-bit samples in; `RAW ` chunks (fixed size, header first) and overrun `TIME` chunks out, as a valid/ready byte stream | here, tested |
| `UoSampler` | `uo_out` registered on the design clock | in tt-harness-handheld-vga, to be contributed |
| RLE chunk writer | for byte-limited links | waits on the compact-chunk decision |

## vgacap_raw_framer

- Ports: `sample[7:0]`, `sample_valid` in; `out_data[7:0]`, `out_valid`,
  `out_ready` (sink) out; `dropped[31:0]` (cumulative); `sys_clk`, `sys_rst`.
- Chunks are `samples_per_chunk` samples (default 256), four to a word, first
  sample in bits 31:24, words little-endian: header `sample_bits=8`,
  `samples_per_word=4`, flags `VGACAP_FLAG_FIRST_SAMPLE_MSB`. The host writes
  the `VGCH` header in front.
- A chunk is admitted at its first sample only if the FIFO (`fifo_words`,
  default 128) has room for all of it; otherwise it is dropped whole and the
  next chunk out is followed by a `TIME` chunk with the cumulative dropped
  count and the message `overrun`. The stream is always parseable.
- One byte a clock out, so samples on every clock need slightly more than
  the output carries (12 header bytes a chunk); see the module docstring.

Regenerate the Verilog after changing the source (a test fails otherwise):

    uv run python rtl/vgacap_raw_framer.py > rtl/vgacap_raw_framer.v

Tests (`python/tests/test_rtl_raw_framer.py`): Migen simulation with an
always-ready, a slow (drops whole chunks, reports them) and a gappy input;
Icarus on the committed `.v` with `tb_vgacap_raw_framer.v`; every output
parsed by `vgacap.stream.read_chunks`.
