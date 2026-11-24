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
#endif
