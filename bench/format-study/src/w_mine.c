// SPDX-License-Identifier: Apache-2.0
// The study's own candidate writers, simulation-thread part: null, planar,
// events and raw. Each takes the same tb.cpp-style input (two raw images a
// clock) and keeps tb.cpp's raw compare as its fast path where it has one.
// Their stored layouts are in the write-up, section "The candidates"; the
// background parts (transforms, compressors, container) are in bg.c.
#include "fs.h"
#include "vgacap/stream.h"   // vgacap_leb128_put_inline
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define NOINLINE __attribute__((noinline))
#define UNLIKELY(x) __builtin_expect(!!(x), 0)

#define STREAM_CAP (16u << 20)    // each stream buffer
#define STREAM_MARGIN (64u << 10) // a block closes once a stream passes CAP - MARGIN

// ---- null: the loop and the loads, nothing recorded ------------------------------------
typedef struct { uint64_t acc; } null_t;
static void *null_create(uint64_t bc, const char *out, fs_block_fn f, void *c) {
    (void)bc; (void)out; (void)f; (void)c;
    return calloc(1, sizeof(null_t));
}
static void null_run(void *st, const uint64_t *raw, uint64_t clocks) {
    null_t *z = st;
    uint64_t acc = z->acc;
    for (uint64_t c = 0; c < clocks; c++) {
        acc += raw[2 * c] ^ raw[2 * c + 1];
        __asm__ volatile("" : "+r"(acc));   // one dependent op a clock, not vectorised away
    }
    z->acc = acc;
}
static void null_finish(void *st) { free(st); }
const fs_writer_t fs_w_null = { "null", null_create, null_run, null_finish };

// ---- shared block state ---------------------------------------------------------------------
typedef struct {
    fs_block_fn on_block;
    void *ctx;
    fs_block_t b;
    uint8_t *p[FS_MAX_STREAMS];   // write pointers
    uint8_t *full_at[FS_MAX_STREAMS];
    uint64_t n;                   // next sample
    uint64_t limit;               // sample at which the block ends (by clocks)
    uint64_t block_samples;
    uint64_t want;                // the last sample's raw image, clk byte cleared
    int started;
    uint64_t clk_errors;
} base_t;

static void base_init(base_t *s, int nstreams, size_t cap, uint64_t block_clocks, fs_block_fn f, void *ctx) {
    memset(s, 0, sizeof *s);
    s->on_block = f; s->ctx = ctx;
    s->b.nstreams = nstreams;
    for (int i = 0; i < nstreams; i++) {
        s->b.buf[i] = malloc(cap);
        if (!s->b.buf[i]) { fprintf(stderr, "fs: out of memory\n"); exit(2); }
        s->p[i] = s->b.buf[i];
        s->full_at[i] = s->b.buf[i] + cap - STREAM_MARGIN;
    }
    s->block_samples = 2 * block_clocks;
    s->want = ~0ull;
}

static void base_emit(base_t *s, uint64_t end) {
    s->b.nsamples = end - s->b.first_sample;
    for (int i = 0; i < s->b.nstreams; i++) s->b.len[i] = (size_t)(s->p[i] - s->b.buf[i]);
    if (s->on_block && s->b.nsamples) s->on_block(s->ctx, &s->b);
    for (int i = 0; i < s->b.nstreams; i++) s->p[i] = s->b.buf[i];
}

static int base_any_full(const base_t *s) {
    int f = 0;
    for (int i = 0; i < s->b.nstreams; i++) f |= s->p[i] > s->full_at[i];
    return f;
}

static void base_free(base_t *s) {
    for (int i = 0; i < s->b.nstreams; i++) free(s->b.buf[i]);
}

// ---- planar: one (value byte, LEB128 run) stream per port --------------------------------------
// Stream k holds port k's runs, in samples, covering the block exactly.
typedef struct {
    base_t s;
    uint64_t start[FS_GROUPS];   // first sample of port k's current run
} planar_t;

static void planar_open(planar_t *w, uint64_t n) {
    w->s.b.first_sample = n;
    w->s.limit = n + w->s.block_samples;
    for (int k = 0; k < FS_GROUPS; k++) w->start[k] = n;
}

static void planar_close(planar_t *w, uint64_t end) {
    const uint64_t cur = w->s.want;
    for (int k = 0; k < FS_GROUPS; k++) {
        uint8_t *p = w->s.p[k];
        *p = (uint8_t)(cur >> (8 * k));
        p += 1 + vgacap_leb128_put_inline(p + 1, end - w->start[k]);
        w->s.p[k] = p;
    }
    base_emit(&w->s, end);
}

// Sample `s` has image v (clk cleared); cur is the previous one.
static inline void planar_sample(planar_t *w, uint64_t s, uint64_t v) {
    uint64_t x = v ^ w->s.want;
    const uint64_t cur = w->s.want;
    while (x) {
        const unsigned k = (unsigned)__builtin_ctzll(x) >> 3;
        uint8_t *p = w->s.p[k];
        *p = (uint8_t)(cur >> (8 * k));
        p += 1 + vgacap_leb128_put_inline(p + 1, s - w->start[k]);
        w->s.p[k] = p;
        w->start[k] = s;
        x &= ~(0xffull << (8 * k));
    }
    w->s.want = v;
}

static NOINLINE void planar_slow(planar_t *w, uint64_t r0, uint64_t r1) {
    const uint64_t n = w->s.n;
    if (!w->s.started) {
        w->s.started = 1;
        w->s.want = r0 & FS_NOCLK_MASK;
        planar_open(w, n);
    } else if (n == w->s.limit) {
        planar_close(w, n);
        planar_open(w, n);
    }
    w->s.clk_errors += (r0 & FS_CLK_RAW) != 0;
    w->s.clk_errors += (r1 & FS_CLK_RAW) == 0;
    planar_sample(w, n, r0 & FS_NOCLK_MASK);
    planar_sample(w, n + 1, r1 & FS_NOCLK_MASK);
    w->s.n = n + 2;
    if (UNLIKELY(base_any_full(&w->s))) {   // a block end by size, at the next clock
        planar_close(w, n + 2);
        planar_open(w, n + 2);
    }
}

static void *planar_create(uint64_t bc, const char *out, fs_block_fn f, void *c) {
    (void)out;
    planar_t *w = calloc(1, sizeof *w);
    base_init(&w->s, FS_GROUPS, STREAM_CAP, bc ? bc : (1ull << 24), f, c);
    return w;
}

static void planar_run(void *st, const uint64_t *raw, uint64_t clocks) {
    planar_t *w = st;
    uint64_t want = w->s.want, n = w->s.n, limit = w->s.limit;
    for (uint64_t c = 0; c < clocks; c++) {
        const uint64_t r0 = raw[2 * c], r1 = raw[2 * c + 1];
        if (UNLIKELY(((r0 ^ want) | (r1 ^ want ^ FS_CLK_RAW)) != 0 || n == limit)) {
            w->s.n = n;
            planar_slow(w, r0, r1);
            want = w->s.want; n = w->s.n; limit = w->s.limit;
        } else {
            n += 2;
        }
    }
    w->s.n = n;
}

static void planar_finish(void *st) {
    planar_t *w = st;
    if (w->s.started) planar_close(w, w->s.n);
    if (w->s.clk_errors) fprintf(stderr, "fs: planar: %llu clock errors\n", (unsigned long long)w->s.clk_errors);
    base_free(&w->s);
    free(w);
}
const fs_writer_t fs_w_planar = { "planar", planar_create, planar_run, planar_finish };

// ---- events: one time-ordered stream of changes ---------------------------------------------------
// Block: the 7 port bytes at its first sample, then per changed sample:
// LEB128 (sample - previous event's sample, or - block start), a mask byte
// (bit k: port k changed), the changed ports' new bytes in port order.
typedef struct {
    base_t s;
    uint64_t last;
} events_t;

static void events_open(events_t *w, uint64_t n) {
    w->s.b.first_sample = n;
    w->s.limit = n + w->s.block_samples;
    w->last = n;
    uint8_t *p = w->s.p[0];
    memcpy(p, &w->s.want, 7);
    w->s.p[0] = p + 7;
}

static inline void events_sample(events_t *w, uint64_t s, uint64_t v) {
    uint64_t x = v ^ w->s.want;
    if (!x) return;
    uint8_t *p = w->s.p[0];
    p += vgacap_leb128_put_inline(p, s - w->last);
    uint8_t *mask = p++;
    unsigned m = 0;
    while (x) {
        const unsigned k = (unsigned)__builtin_ctzll(x) >> 3;
        m |= 1u << k;
        *p++ = (uint8_t)(v >> (8 * k));
        x &= ~(0xffull << (8 * k));
    }
    *mask = (uint8_t)m;
    w->s.p[0] = p;
    w->last = s;
    w->s.want = v;
}

static NOINLINE void events_slow(events_t *w, uint64_t r0, uint64_t r1) {
    const uint64_t n = w->s.n;
    if (!w->s.started) {
        w->s.started = 1;
        w->s.want = r0 & FS_NOCLK_MASK;
        events_open(w, n);
    } else if (n == w->s.limit) {
        base_emit(&w->s, n);
        events_open(w, n);
    }
    w->s.clk_errors += (r0 & FS_CLK_RAW) != 0;
    w->s.clk_errors += (r1 & FS_CLK_RAW) == 0;
    events_sample(w, n, r0 & FS_NOCLK_MASK);
    events_sample(w, n + 1, r1 & FS_NOCLK_MASK);
    w->s.n = n + 2;
    if (UNLIKELY(w->s.p[0] > w->s.full_at[0])) {
        base_emit(&w->s, n + 2);
        events_open(w, n + 2);
    }
}

static void *events_create(uint64_t bc, const char *out, fs_block_fn f, void *c) {
    (void)out;
    events_t *w = calloc(1, sizeof *w);
    base_init(&w->s, 1, STREAM_CAP, bc ? bc : (1ull << 24), f, c);
    return w;
}

static void events_run(void *st, const uint64_t *raw, uint64_t clocks) {
    events_t *w = st;
    uint64_t want = w->s.want, n = w->s.n, limit = w->s.limit;
    for (uint64_t c = 0; c < clocks; c++) {
        const uint64_t r0 = raw[2 * c], r1 = raw[2 * c + 1];
        if (UNLIKELY(((r0 ^ want) | (r1 ^ want ^ FS_CLK_RAW)) != 0 || n == limit)) {
            w->s.n = n;
            events_slow(w, r0, r1);
            want = w->s.want; n = w->s.n; limit = w->s.limit;
        } else {
            n += 2;
        }
    }
    w->s.n = n;
}

static void events_finish(void *st) {
    events_t *w = st;
    if (w->s.started) base_emit(&w->s, w->s.n);
    if (w->s.clk_errors) fprintf(stderr, "fs: events: %llu clock errors\n", (unsigned long long)w->s.clk_errors);
    base_free(&w->s);
    free(w);
}
const fs_writer_t fs_w_events = { "events", events_create, events_run, events_finish };

// ---- raw: both raw images of every clock, stored as they are ----------------------------------------
// The simulation thread does two 8-byte stores a clock and nothing else; the
// background part packs, checks the clock bit, transforms and compresses.
typedef struct {
    base_t s;
    uint64_t *q, *end;
} raw_t;

static NOINLINE void raw_full(raw_t *w) {
    uint8_t *const base = w->s.b.buf[0];
    w->s.p[0] = (uint8_t *)w->q;
    const uint64_t end = w->s.b.first_sample + (uint64_t)(w->q - (uint64_t *)(void *)base);
    base_emit(&w->s, end);
    w->s.b.first_sample = end;
    w->q = (uint64_t *)(void *)base;
}

static void *raw_create(uint64_t bc, const char *out, fs_block_fn f, void *c) {
    (void)out;
    raw_t *w = calloc(1, sizeof *w);
    if (!bc) bc = 1ull << 21;
    base_init(&w->s, 1, (size_t)(16 * bc + STREAM_MARGIN), bc, f, c);
    w->q = (uint64_t *)(void *)w->s.b.buf[0];
    w->end = w->q + 2 * bc;
    return w;
}

static void raw_run(void *st, const uint64_t *raw, uint64_t clocks) {
    raw_t *w = st;
    uint64_t *q = w->q, *const end = w->end;
    for (uint64_t c = 0; c < clocks; c++) {
        q[0] = raw[2 * c];
        q[1] = raw[2 * c + 1];
        q += 2;
        if (UNLIKELY(q == end)) {
            w->q = q;
            raw_full(w);
            q = w->q;
        }
    }
    w->q = q;
}

static void raw_finish(void *st) {
    raw_t *w = st;
    raw_full(w);
    base_free(&w->s);
    free(w);
}
const fs_writer_t fs_w_raw = { "raw", raw_create, raw_run, raw_finish };
