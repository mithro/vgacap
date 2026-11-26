---
type: landing
owner: vgacap maintainers
reader: someone who simulates a Tiny Tapeout design and wants its picture
review: 2027-05-27
---

# Simulation traces

- [What a simulation trace records](explanation-traces.md): every pin on every half clock, as runs in compressed blocks.
- [How to write a simulation trace from a C or C++ testbench](how-to-write-trace.md): the trace library in a testbench.
- [How to write a synthetic simulation trace in Python](../../python/vgacap/how-to-synthetic-trace.md): a known picture with exact timing, without a simulator.
- [How to turn a simulator dump into a vgacap stream](../../python/vgacap/how-to-bin2stream.md): `uo_out` as one byte a clock.
- [How to read a simulation trace without vgacap](../../docs/how-to-read-trace.md): a reader of your own, with a zstd decoder.
- [The simulation trace format](../../docs/trace-format.md): the chunks, the bit layout and the container.
- [The tb-v1 test vectors](../../tests/vectors/tbv1/reference-tbv1-vectors.md): the traces and outputs tb-v1 must reproduce.
