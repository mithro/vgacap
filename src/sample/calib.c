// SPDX-License-Identifier: Apache-2.0
// The shared calibration: an exact port of tinytapeout-vga-videos'
// ttvga/harness/tb.cpp, SyncTracker/median/analyse (tb.cpp:191-230) and the
// calibration loop (tb.cpp:361-378), fed runs of clk-high uo_out.
//
// tb.cpp, per tick, with `clock` = T = c + 1 after the tick of clock c:
//     while (clock < calib_limit) {                   // c < calib_limit
//         tick();                                     // T = c + 1
//         ht.feed(uo & 0x80, clock); vt.feed(uo & 0x08, clock);
//         if ((clock & 0xFFFF) == 0) {
//             hs = analyse(ht); vs = analyse(vt);
//             if (hs.found && vs.found && vt.phases.size() >= 8) break;
//         }
//         if (ht.phases.size() > 4000000) break;
//     }
//     hs = analyse(ht); vs = analyse(vt);
// tb.cpp starts the loop at clock 10, the first clock with rst_n high; here
// the first clock whose clk-high rst_n is 1 (`started`).
//
// Within a run uo_out is constant, so the trackers change at most at the
// run's first clock; the rest of the run cannot change the phases, the
// 4,000,000 test, or any analysis, so one stop check covers the run's first
// multiple of 65536 and the result equals per-clock processing.
#include "vgacap/sample.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define CHECK_MASK 0xFFFFull         // tb.cpp:365, check_every - 1
#define MAX_H_PHASES 4000000ull      // tb.cpp:376

static void *grow(void *p, size_t *cap, size_t need, size_t elem) {
    if (need <= *cap) return p;
    size_t n = *cap ? *cap : 1024;
    while (n < need) n *= 2;
    void *q = realloc(p, n * elem);
    if (!q) { fprintf(stderr, "vgacap_sample: out of memory\n"); abort(); }
    *cap = n;
    return q;
}

// SyncTracker::feed (tb.cpp:196-202). Phase 0 is counted but its length is
// not kept: analyse never reads it (tb.cpp:221).
static void tracker_feed(vgacal_tracker_t *t, uint8_t level, uint64_t clock) {
    if (level == t->last) return;
    if (t->phases) {
        int l = t->last;
        t->len[l] = grow(t->len[l], &t->cap[l], t->n[l] + 1, sizeof *t->len[l]);
        t->len[l][t->n[l]++] = clock - t->phase_start;
    }
    t->phases++;
    t->phase_start = clock;
    t->last = level;
}

// The element at k of v[0..n) sorted (v is reordered): tb.cpp's median takes
// v[size / 2] after std::sort; this selects the same element. Three-way
// partitioning keeps equal lengths, the usual case, linear.
static uint64_t select_k(uint64_t *v, size_t n, size_t k) {
    size_t lo = 0, hi = n;   // k is in [lo, hi)
    for (;;) {
        if (hi - lo == 1) return v[lo];
        uint64_t a = v[lo], b = v[lo + (hi - lo) / 2], c = v[hi - 1];
        uint64_t pivot = a < b ? (b < c ? b : (a < c ? c : a)) : (a < c ? a : (b < c ? c : b));
        size_t lt = lo, i = lo, gt = hi;     // [lo,lt) < pivot, [lt,i) == pivot, [gt,hi) > pivot
        while (i < gt) {
            uint64_t x = v[i];
            if (x < pivot) { v[i] = v[lt]; v[lt] = x; lt++; i++; }
            else if (x > pivot) { gt--; v[i] = v[gt]; v[gt] = x; }
            else i++;
        }
        if (k < lt) hi = lt;
        else if (k >= gt) lo = gt;
        else return pivot;
    }
}

static uint64_t median(vgacal_t *c, const uint64_t *v, size_t n) {   // tb.cpp:205-209
    if (!n) return 0;
    c->scratch = grow(c->scratch, &c->scratch_cap, n, sizeof *c->scratch);
    memcpy(c->scratch, v, n * sizeof *v);
    return select_k(c->scratch, n, n / 2);
}

// analyse (tb.cpp:218-230): phases 1.. by level; at least three of each.
static vgacal_sync_t analyse(vgacal_t *c, const vgacal_tracker_t *t) {
    vgacal_sync_t s = { 0, 1, 0, 0 };
    if (t->n[1] < 3 || t->n[0] < 3) return s;
    uint64_t mh = median(c, t->len[1], t->n[1]), ml = median(c, t->len[0], t->n[0]);
    s.active_low = ml < mh;
    s.pulse = s.active_low ? ml : mh;
    s.period = ml + mh;
    s.found = 1;
    return s;
}

// The test at a multiple of 65536: hs.found && vs.found && vt has 8
// phases. A pure function of the phases, so cached by their counts.
static int stop_check(vgacal_t *c) {
    if (c->chk_valid && c->chk_h == c->ht.phases && c->chk_v == c->vt.phases) return c->chk_ok;
    vgacal_sync_t hs = analyse(c, &c->ht), vs = analyse(c, &c->vt);
    c->chk_ok = hs.found && vs.found && c->vt.phases >= 8;
    c->chk_h = c->ht.phases; c->chk_v = c->vt.phases; c->chk_valid = 1;
    return c->chk_ok;
}

static void finish(vgacal_t *c, uint64_t capture_after_clock) {   // after the loop
    c->hs = analyse(c, &c->ht);
    c->vs = analyse(c, &c->vt);
    c->h_phases = c->ht.phases;
    c->v_phases = c->vt.phases;
    c->capture_after_clock = capture_after_clock;
    c->state = VGACAL_DONE;
}

void vgacal_init(vgacal_t *c, double clock_hz, double calib_seconds) {
    memset(c, 0, sizeof *c);
    c->clock_hz = clock_hz;
    c->calib_seconds = calib_seconds;
    c->calib_limit = (uint64_t)(calib_seconds * clock_hz);   // tb.cpp:364
    c->hs.active_low = c->vs.active_low = 1;                  // SyncInfo's defaults
}

uint64_t vgacal_push(vgacal_t *c, uint8_t uo_out, uint8_t rst_n, uint64_t first, uint64_t clocks) {
    if (c->state == VGACAL_DONE || !clocks) return 0;
    if (!c->started) {
        if (!rst_n) return clocks;                    // reset: tb.cpp's first 10 ticks
        c->started = 1;
    }
    const uint64_t end = first + clocks;
    uint64_t cl = first;
    if (cl >= c->calib_limit) { finish(c, cl); return 0; }   // while (clock < calib_limit)
    // The run's first clock: the only one that can change the trackers.
    uint64_t t = cl + 1;
    tracker_feed(&c->ht, (uo_out & 0x80) != 0, t);
    tracker_feed(&c->vt, (uo_out & 0x08) != 0, t);
    c->h_phases = c->ht.phases;
    c->v_phases = c->vt.phases;
    if ((t & CHECK_MASK) == 0 && stop_check(c)) { finish(c, t); return 1; }
    if (c->ht.phases > MAX_H_PHASES) { finish(c, t); return 1; }
    // The rest, clocks (cl, stop), with the phases fixed: only a check at a
    // multiple of 65536 can stop it, and every such check gives the same
    // answer, so the first one decides.
    uint64_t stop = end < c->calib_limit ? end : c->calib_limit;
    uint64_t next_t = (t | CHECK_MASK) + 1;           // the first multiple of 65536 after t
    if (next_t - 1 < stop && stop_check(c)) { finish(c, next_t); return next_t - first; }
    if (stop < end) { finish(c, stop); return stop - first; }   // stop == calib_limit
    return clocks;
}

void vgacal_end_of_trace(vgacal_t *c, uint64_t clocks_in_trace) {
    if (c->state == VGACAL_DONE) return;
    finish(c, clocks_in_trace);
}

void vgacal_free(vgacal_t *c) {
    for (int l = 0; l < 2; l++) { free(c->ht.len[l]); free(c->vt.len[l]); }
    free(c->scratch);
    memset(c, 0, sizeof *c);
}
