// SPDX-License-Identifier: Apache-2.0
// The frame clock: tb.cpp's capture loop's frame starts (tb.cpp:480, 520).
//     bool prev_v = false;                                 // tb.cpp:480
//     while (...) { tick(); v_pulse = vs.active_low ? !(uo & 0x08) : !!(uo & 0x08);
//                   if (prev_v && !v_pulse) <frame start>; ...; prev_v = v_pulse; }
// Capture's first tick is clock capture_after_clock, so an end is a clock
// e >= capture_after_clock + 1 out of the pulse whose clock e - 1 was in it.
// tb.cpp records `clock` = e + 1 there; this records e (spec clock numbering).
#include "vgacap/sample.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

void vgafc_init(vgafc_t *fc, const vgacal_t *c) {
    memset(fc, 0, sizeof *fc);
    fc->v_active_low = c->vs.active_low;
    fc->prev_v = 0;
    fc->from = c->capture_after_clock;
}

static inline void fc_push(vgafc_t *fc, uint8_t uo_out, uint64_t first, uint64_t clocks) {
    uint64_t end = first + clocks;
    if (end <= fc->from) return;
    if (first < fc->from) first = fc->from;
    uint8_t v = (uint8_t)(fc->v_active_low ? !(uo_out & 0x08) : !!(uo_out & 0x08));
    if (fc->prev_v && !v) {
        if (fc->n == fc->cap) {
            size_t cap = fc->cap ? 2 * fc->cap : 64;
            uint64_t *e = realloc(fc->ends, cap * sizeof *e);
            if (!e) { fprintf(stderr, "vgacap_sample: out of memory\n"); abort(); }
            fc->ends = e; fc->cap = cap;
        }
        fc->ends[fc->n++] = first;
    }
    fc->prev_v = v;
}

void vgafc_push(vgafc_t *fc, uint8_t uo_out, uint64_t first, uint64_t clocks) {
    fc_push(fc, uo_out, first, clocks);
}

void vgafc_push_runs(vgafc_t *fc, const vgasample_run_t *runs, size_t n) {
    for (size_t i = 0; i < n; i++) fc_push(fc, runs[i].uo_out, runs[i].first, runs[i].clocks);
}

void vgafc_free(vgafc_t *fc) {
    free(fc->ends);
    memset(fc, 0, sizeof *fc);
}
