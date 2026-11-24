// SPDX-License-Identifier: Apache-2.0
// Test-only helper for python/tests/test_roundtrip_c.py: writes a trace with
// the trace writer. `trace-make <path> <clocks> <every>`: a 43-bit tt-um-43
// trace of <clocks> clocks, rst_n and ena high, uo_out counting up every
// <every> clocks (never if 0), changing in the clock-high sample.
#include "vgacap/trace.h"
#include <inttypes.h>
#include <stdio.h>
#include <stdlib.h>

#define OPEN_META "meta=open\nproject=tt_um_test\ncalib_seconds=0.5\nseconds=2\nlayout=tt-um-43\n"
#define CLOSE_META "meta=close\ncapture_after_clock=0\nstatus=ok\nframes=0\n"

int main(int argc, char **argv) {
    if (argc != 4) { fprintf(stderr, "usage: %s <path> <clocks> <every>\n", argv[0]); return 2; }
    uint64_t clocks = strtoull(argv[2], NULL, 10), every = strtoull(argv[3], NULL, 10);
    vgacap_header_t h;
    vgacap_header_init_v2(&h, 43, 2, 42, 0);
    h.mode = VGACAP_MODE_EVENT; h.clock_hz = 25175000;
    char err[256] = "";
    vgacap_trace_writer_t *t = vgacap_trace_create(argv[1], &h, OPEN_META, 3, err, sizeof err);
    if (!t) { fprintf(stderr, "trace-make: %s\n", err); return 1; }
    int bad = 0;
    for (uint64_t s = 0; s < 2 * clocks; s++) {
        uint64_t uo = every ? ((s + 1) / 2 / every) & 0xFF : 0;
        bad |= vgacap_trace_push(t, ((uint64_t)3 << 40) | uo | ((s & 1) ? (uint64_t)1 << 42 : 0));
    }
    if (vgacap_trace_finish(t, CLOSE_META, err, sizeof err) || bad) {
        fprintf(stderr, "trace-make: %s\n", err); return 1;
    }
    return 0;
}
