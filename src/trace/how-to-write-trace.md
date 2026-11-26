---
type: how-to
owner: vgacap maintainers
reader: someone with a C or C++ simulation testbench, such as a Verilator one, of a Tiny Tapeout design
review: 2027-05-27
---

# How to write a simulation trace from a C or C++ testbench

**You want your testbench to record every pin of a Tiny Tapeout design, on every half clock, as a vgacap simulation trace.**

The writer is the trace library in this directory, declared in [`trace.h`](../../include/vgacap/trace.h). What a trace holds is on [What a simulation trace records](explanation-traces.md), and its bytes on [the simulation trace format](../../docs/trace-format.md).

## What you need

- A vgacap checkout, built with CMake.
- A testbench in C or C++ that steps the design one clock at a time, and can read its pins.
- `cc` or `c++` for the testbench, with POSIX threads.

## Steps

1. In the vgacap checkout, run `cmake -S . -B build && cmake --build build --target vgacap_trace` to build the trace library and the libraries it needs.
2. In your testbench, include `vgacap/trace.h` and create the writer with the header and opening `META` shown below.
3. In your testbench, push each clock's two samples with `vgacap_trace_push2`, packed as shown below, the clock-low sample first.
4. In your testbench, after the last clock, call `vgacap_trace_finish` with the closing `META` shown below.
5. Compile the testbench with `-I` on the checkout's `include`, and link `libvgacap_trace.a`, `libvgacap_stream.a`, `third_party/libvgacap_zstd.a` and `-pthread`.
6. Run the testbench, which writes `design.trace.zst`.

The code for step 2. `project` names the design, and `calib_seconds` and `seconds` are what a sampler of the trace reads ([the `META` keys](../../docs/trace-format.md#meta)):

```c
#include "vgacap/trace.h"

vgacap_header_t hdr;
vgacap_header_init_v2(&hdr, 43, 2, 42, 0);  /* 43 bits, 2 samples a clock, clock bit 42, low first */
hdr.mode = VGACAP_MODE_EVENT;
hdr.clock_hz = 25175000;                    /* the design clock, in whole hertz */
char err[256];
vgacap_trace_writer_t *t = vgacap_trace_create("design.trace.zst", &hdr,
    "meta=open\nproject=tt_um_example\ncalib_seconds=0.05\nseconds=0.1\nlayout=tt-um-43\n",
    3, err, sizeof err);                    /* zstd level 3 */
if (!t) { fprintf(stderr, "%s\n", err); return 1; }
```

The code for step 3, in the `tt-um-43` layout. The clock-low sample holds the outputs from before the clock's rising edge, and the clock-high sample those after it:

```c
static uint64_t pack(uint8_t uo_out, uint8_t uio_out, uint8_t uio_oe, uint8_t ui_in,
                     uint8_t uio_in, int rst_n, int ena, int clk) {
    return (uint64_t)uo_out | (uint64_t)uio_out << 8 | (uint64_t)uio_oe << 16 |
           (uint64_t)ui_in << 24 | (uint64_t)uio_in << 32 |
           (uint64_t)rst_n << 40 | (uint64_t)ena << 41 | (uint64_t)clk << 42;
}

/* each clock: b holds the outputs before the rising edge, a those after it */
if (vgacap_trace_push2(t,
        pack(b.uo_out, b.uio_out, b.uio_oe, ui_in, uio_in, rst_n, 1, 0),
        pack(a.uo_out, a.uio_out, a.uio_oe, ui_in, uio_in, rst_n, 1, 1)))
    break;                                  /* the writer has failed: finish says why */
```

The code for step 4. `status=ok` says the simulation ran to its end:

```c
if (vgacap_trace_finish(t, "meta=close\ncapture_after_clock=0\nstatus=ok\nframes=0\n",
                        err, sizeof err)) {
    fprintf(stderr, "%s\n", err);
    return 1;
}
```

## Check

`build/vgacap-dump design.trace.zst`, from the vgacap checkout, prints a `trace` line with `closed=1`, then the trace's chunks:

```text
trace blocks=1 samples=10080000 table_rebuilt=0 closed=1
VGCH v2 bits=43 spc=2 clock_bit=42 clock_first=0 spw=0 flags=0 clock=25175000 map=7,3,0,4,1,5,2,6 desc=""
```

The [C tools](../tools/README.md) turn the trace into frames and waveforms.

## If it fails

| What you see | Likely cause | Fix |
|---|---|---|
| `vgacap_trace_create` returns NULL at step 2 | The header or the opening `META` is not valid, or the file cannot be created | Read `err`, and compare the `META` with step 2 |
| `vgacap_trace_push2` returns -1 at step 3 | A clock bit out of turn, or a bit at or above bit 43 | Push the clock-low sample first, with only the 43 bits set |
| `undefined reference to pthread_create` at step 5 | POSIX threads are not linked | Add `-pthread`, as in step 5 |
| `closed=0` in the check | The testbench stopped before step 4 | Call `vgacap_trace_finish` on every way out of the testbench |

## Next

- [What a simulation trace records](explanation-traces.md)
- [The simulation trace format](../../docs/trace-format.md)
- [How to write a synthetic simulation trace in Python](../../python/vgacap/how-to-synthetic-trace.md)
