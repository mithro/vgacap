// SPDX-License-Identifier: Apache-2.0
// The per-clock fast paths of the study's own writers, inline, so that the
// replay (w_mine.c) and the simulation check (sim/tb_study.cpp) run the same
// code: tb.cpp's raw compare against the last sample, as vgch2's glue does,
// and the writer's state in memory, as vgch2's is.
#ifndef FS_W_MINE_H
#define FS_W_MINE_H
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

// The first fields of every study writer's state.
typedef struct {
    uint64_t n;       // next sample
    uint64_t limit;   // sample at which the block ends (by clocks)
    uint64_t want;    // the last sample's raw image, clk byte cleared
    uint64_t *q, *end;   // raw only: the write pointer and the block's end
} fs_head_t;

void fs_planar_slow(void *w, uint64_t r0, uint64_t r1);
void fs_events_slow(void *w, uint64_t r0, uint64_t r1);
void fs_raw_full(void *w);

// clk_raw: the clk byte's bit 0 in the raw image (a constant in the replay,
// RawPorts::clk_raw in the testbench).
static inline void fs_planar_clock(void *w, uint64_t r0, uint64_t r1, uint64_t clk_raw) {
    fs_head_t *h = (fs_head_t *)w;
    if (__builtin_expect(((r0 ^ h->want) | (r1 ^ h->want ^ clk_raw)) != 0 || h->n == h->limit, 0))
        fs_planar_slow(w, r0, r1);
    else
        h->n += 2;
}
static inline void fs_events_clock(void *w, uint64_t r0, uint64_t r1, uint64_t clk_raw) {
    fs_head_t *h = (fs_head_t *)w;
    if (__builtin_expect(((r0 ^ h->want) | (r1 ^ h->want ^ clk_raw)) != 0 || h->n == h->limit, 0))
        fs_events_slow(w, r0, r1);
    else
        h->n += 2;
}
static inline void fs_raw_clock(void *w, uint64_t r0, uint64_t r1) {
    fs_head_t *h = (fs_head_t *)w;
    uint64_t *q = h->q;
    q[0] = r0;
    q[1] = r1;
    q += 2;
    h->q = q;
    if (__builtin_expect(q == h->end, 0)) fs_raw_full(w);
}

// ---- for the simulation check ------------------------------------------------------
// A writer ("events" or "raw") whose blocks go, double-buffered, to a thread
// of its own that transforms and compresses them (as vgch2's does) and writes
// them in the study container to `path`. off: the byte of the raw image
// holding uo_out, uio_out, uio_oe, ui_in, uio_in, rst_n, ena, clk (tb.cpp's
// RawPorts::off). Returns NULL on a bad kind.
void *fs_sim_create(const char *kind, const char *path, const int off[8]);
void fs_sim_finish(void *w);

#ifdef __cplusplus
}
#endif
#endif
