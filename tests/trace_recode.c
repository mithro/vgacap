// SPDX-License-Identifier: Apache-2.0
// Test-only helper for python/tests/test_vgacap_sample.py: `trace-recode IN
// OUT` rewrites a trace or plain VGCH v2 stream IN (e.g. one written by
// vgacap.synthtrace) as a zstd seekable trace OUT through the trace writer,
// sample by sample, so it gets the writer's block boundaries. The header and
// META(open) are IN's; META(close) is IN's too, or, when IN has none, OUT is
// abandoned without one.
#include "vgacap/trace.h"
#include <stdio.h>
#include <stdlib.h>

typedef struct st { vgacap_trace_writer_t *w; uint64_t clk; int bad; } st_t;

static int on_run(void *user, uint64_t first, uint64_t value, uint64_t run) {
    (void)first;
    st_t *s = user;
    for (uint64_t i = 0; i < run; i++) {
        uint64_t v = s->clk && (i & 1) ? value ^ s->clk : value;
        s->bad |= vgacap_trace_push(s->w, v);
    }
    return s->bad;
}

int main(int argc, char **argv) {
    if (argc != 3) { fprintf(stderr, "usage: %s IN OUT\n", argv[0]); return 2; }
    char err[256] = "";
    vgacap_trace_reader_t *r = vgacap_trace_open(argv[1], err, sizeof err);
    if (!r) { fprintf(stderr, "trace-recode: %s\n", err); return 1; }
    const vgacap_trace_info_t *info = vgacap_trace_info(r);
    if (!info->open_meta) { fprintf(stderr, "trace-recode: %s has no META(open)\n", argv[1]); return 1; }
    st_t s = { 0, 0, 0 };
    if (info->header.version == 2 && info->header.clock_bit < 64) s.clk = (uint64_t)1 << info->header.clock_bit;
    s.w = vgacap_trace_create(argv[2], &info->header, info->open_meta, 3, err, sizeof err);
    if (!s.w) { fprintf(stderr, "trace-recode: %s\n", err); return 1; }
    if (vgacap_trace_read(r, 0, info->samples, on_run, &s, err, sizeof err) || s.bad) {
        fprintf(stderr, "trace-recode: read %s or push failed: %s\n", argv[1], err);
        vgacap_trace_abandon(s.w); return 1;
    }
    if (!info->close_meta) { vgacap_trace_abandon(s.w); vgacap_trace_close(r); return 0; }
    int rc = vgacap_trace_finish(s.w, info->close_meta, err, sizeof err);
    vgacap_trace_close(r);
    if (rc) { fprintf(stderr, "trace-recode: %s\n", err); return 1; }
    return 0;
}
