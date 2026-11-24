// SPDX-License-Identifier: Apache-2.0
// Shared by the libvgaframe sources; not installed.
#ifndef VGAFRAME_INTERNAL_H
#define VGAFRAME_INTERNAL_H
#include <stdint.h>
#include "vgacap/frame.h"

// a + b, held at UINT32_MAX instead of wrapping. A phase, line or run that
// long is no sync at all, so a wrapped small number would be worse than the
// ceiling: it would look like a real, short measurement.
static inline uint32_t vgaframe_sat_add32(uint32_t a, uint64_t b) {
    return b > (uint64_t)(UINT32_MAX - a) ? UINT32_MAX : (uint32_t)(a + b);
}

// The no-edge part of a learner push: `run` more clocks at hsync level h.
// The one copy of these additions, shared by vgaframe_timing_push and any
// fast path that already knows the run holds no edge.
static inline void vgaframe_timing_add_run(vgaframe_timing_learner_t *l, uint8_t h, uint64_t run) {
    if (h) l->h_high = vgaframe_sat_add32(l->h_high, run);
    else   l->h_low  = vgaframe_sat_add32(l->h_low, run);
    l->clk_in_line = vgaframe_sat_add32(l->clk_in_line, run);
}

// The learner's no-edge case, which is most pushes: once a sample has been
// seen, a push at the hsync level already held adds `run` clocks
// (vgaframe_timing_add_run) and reports nothing. vsync is not looked at:
// it is sampled only at an accepted pulse's trailing edge, which this is
// not. Returns 1 when it has done the push, else 0 and the caller does the
// full vgaframe_timing_push. vgaframe_timing_push begins with this, and
// vgaframe_push calls it first to skip the rest, so the fast path and the
// slow path's tail are one piece of code.
static inline int vgaframe_timing_push_no_edge(vgaframe_timing_learner_t *l, uint8_t h, uint32_t run) {
    if (!l->have_prev || h != l->prev_h) return 0;
    vgaframe_timing_add_run(l, h, run);
    return 1;
}
#endif
