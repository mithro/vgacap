// SPDX-License-Identifier: Apache-2.0
// The study's own candidate writers, simulation-thread part: null, planar,
// events and raw. Each takes the same tb.cpp-style input (two raw images a
// clock) and keeps tb.cpp's raw compare as its fast path where it has one
// (w_mine.h, inline, shared with the simulation check). Their stored layouts
// are in the write-up, section "The candidates"; the background parts
// (transforms, compressors, container) are in bg.c.
#include "fs.h"
#include "w_mine.h"
#include "vgacap/stream.h"   // vgacap_leb128_put_inline
#include <pthread.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define NOINLINE __attribute__((noinline))
#define UNLIKELY(x) __builtin_expect(!!(x), 0)

#define STREAM_CAP (16u << 20)    // each stream buffer
#define STREAM_MARGIN (64u << 10) // a block closes once a stream passes CAP - MARGIN

// The clk byte of the raw image: byte FS_CLK in the replay, wherever
// Verilator put it in the simulation check (fs_sim_create).
uint64_t fs_clk_raw = FS_CLK_RAW, fs_noclk = FS_NOCLK_MASK;
static int clk_byte = FS_CLK;

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
// Blocks go to on_block synchronously (the replay and encode modes), or, with
// `async`, to a thread of the writer's own through two buffer sets, as
// vgch2's compressor thread takes them: the simulation thread waits only if
// the thread still holds the other set.
typedef struct {
    fs_head_t h;                  // first: w_mine.h's inline paths use it
    fs_block_fn on_block;
    void *ctx;
    fs_block_t b;                 // the set being filled
    uint8_t *p[FS_MAX_STREAMS];   // write pointers
    uint8_t *full_at[FS_MAX_STREAMS];
    uint64_t block_samples;
    int started;
    uint64_t clk_errors;
    size_t cap;
    // async
    int async, cur;
    uint8_t *sets[2][FS_MAX_STREAMS];
    fs_block_t handed[2];
    int full[2], done;
    pthread_t thread;
    pthread_mutex_t mu;
    pthread_cond_t cv_full, cv_free;
} base_t;

static void *base_thread(void *arg) {
    base_t *s = arg;
    for (int j = 0;; j ^= 1) {
        pthread_mutex_lock(&s->mu);
        while (!s->full[j] && !s->done) pthread_cond_wait(&s->cv_full, &s->mu);
        if (!s->full[j]) { pthread_mutex_unlock(&s->mu); break; }
        pthread_mutex_unlock(&s->mu);
        s->on_block(s->ctx, &s->handed[j]);
        pthread_mutex_lock(&s->mu);
        s->full[j] = 0;
        pthread_cond_signal(&s->cv_free);
        pthread_mutex_unlock(&s->mu);
    }
    return NULL;
}

static void base_point(base_t *s, int set) {
    for (int i = 0; i < s->b.nstreams; i++) {
        s->b.buf[i] = s->sets[set][i];
        s->p[i] = s->b.buf[i];
        s->full_at[i] = s->b.buf[i] + s->cap - STREAM_MARGIN;
    }
}

static void base_init(base_t *s, int nstreams, size_t cap, uint64_t block_clocks, fs_block_fn f, void *ctx,
                      int async) {
    memset(s, 0, sizeof *s);
    s->on_block = f; s->ctx = ctx;
    s->b.nstreams = nstreams;
    s->cap = cap;
    s->async = async && f;
    for (int set = 0; set < (s->async ? 2 : 1); set++)
        for (int i = 0; i < nstreams; i++) {
            s->sets[set][i] = malloc(cap);
            if (!s->sets[set][i]) { fprintf(stderr, "fs: out of memory\n"); exit(2); }
        }
    base_point(s, 0);
    s->block_samples = 2 * block_clocks;
    s->h.want = ~0ull;
    if (s->async) {
        pthread_mutex_init(&s->mu, NULL);
        pthread_cond_init(&s->cv_full, NULL);
        pthread_cond_init(&s->cv_free, NULL);
        pthread_create(&s->thread, NULL, base_thread, s);
    }
}

static void base_emit(base_t *s, uint64_t end) {
    s->b.nsamples = end - s->b.first_sample;
    for (int i = 0; i < s->b.nstreams; i++) s->b.len[i] = (size_t)(s->p[i] - s->b.buf[i]);
    if (s->async) {
        if (s->b.nsamples) {
            pthread_mutex_lock(&s->mu);
            s->handed[s->cur] = s->b;
            s->full[s->cur] = 1;
            pthread_cond_signal(&s->cv_full);
            s->cur ^= 1;
            while (s->full[s->cur]) pthread_cond_wait(&s->cv_free, &s->mu);
            pthread_mutex_unlock(&s->mu);
            base_point(s, s->cur);
            return;
        }
    } else if (s->on_block && s->b.nsamples) {
        s->on_block(s->ctx, &s->b);
    }
    for (int i = 0; i < s->b.nstreams; i++) s->p[i] = s->b.buf[i];
}

static int base_any_full(const base_t *s) {
    int f = 0;
    for (int i = 0; i < s->b.nstreams; i++) f |= s->p[i] > s->full_at[i];
    return f;
}

static void base_free(base_t *s) {
    if (s->async) {
        pthread_mutex_lock(&s->mu);
        s->done = 1;
        pthread_cond_signal(&s->cv_full);
        pthread_mutex_unlock(&s->mu);
        pthread_join(s->thread, NULL);
    }
    for (int set = 0; set < 2; set++)
        for (int i = 0; i < s->b.nstreams; i++) free(s->sets[set][i]);
}

// ---- planar: one (value byte, LEB128 run) stream per port --------------------------------------
// Stream k holds the runs of raw-image byte k, in samples, covering the block exactly.
typedef struct {
    base_t s;
    uint64_t start[FS_GROUPS];   // first sample of byte k's current run
} planar_t;

static void planar_open(planar_t *w, uint64_t n) {
    w->s.b.first_sample = n;
    w->s.h.limit = n + w->s.block_samples;
    for (int k = 0; k < FS_GROUPS; k++) w->start[k] = n;
}

static void planar_close(planar_t *w, uint64_t end) {
    const uint64_t cur = w->s.h.want;
    for (int k = 0; k < FS_GROUPS; k++) {
        uint8_t *p = w->s.p[k];
        *p = (uint8_t)(cur >> (8 * k));
        p += 1 + vgacap_leb128_put_inline(p + 1, end - w->start[k]);
        w->s.p[k] = p;
    }
    base_emit(&w->s, end);
}

// Sample `s` has image v (clk cleared); want is the previous one.
static inline void planar_sample(planar_t *w, uint64_t s, uint64_t v) {
    uint64_t x = v ^ w->s.h.want;
    const uint64_t cur = w->s.h.want;
    while (x) {
        const unsigned k = (unsigned)__builtin_ctzll(x) >> 3;
        if (s != w->start[k]) {   // a change at the block's first sample ends no run
            uint8_t *p = w->s.p[k];
            *p = (uint8_t)(cur >> (8 * k));
            p += 1 + vgacap_leb128_put_inline(p + 1, s - w->start[k]);
            w->s.p[k] = p;
            w->start[k] = s;
        }
        x &= ~(0xffull << (8 * k));
    }
    w->s.h.want = v;
}

NOINLINE void fs_planar_slow(void *wv, uint64_t r0, uint64_t r1) {
    planar_t *w = wv;
    const uint64_t n = w->s.h.n;
    if (!w->s.started) {
        w->s.started = 1;
        w->s.h.want = r0 & fs_noclk;
        planar_open(w, n);
    } else if (n == w->s.h.limit) {
        planar_close(w, n);
        planar_open(w, n);
    }
    w->s.clk_errors += (r0 & fs_clk_raw) != 0;
    w->s.clk_errors += (r1 & fs_clk_raw) == 0;
    planar_sample(w, n, r0 & fs_noclk);
    planar_sample(w, n + 1, r1 & fs_noclk);
    w->s.h.n = n + 2;
    if (UNLIKELY(base_any_full(&w->s))) {   // a block end by size, at the next clock
        planar_close(w, n + 2);
        planar_open(w, n + 2);
    }
}

static void *planar_create(uint64_t bc, const char *out, fs_block_fn f, void *c) {
    (void)out;
    planar_t *w = calloc(1, sizeof *w);
    base_init(&w->s, FS_GROUPS, STREAM_CAP, bc ? bc : (1ull << 24), f, c, 0);
    return w;
}

static void planar_run(void *st, const uint64_t *raw, uint64_t clocks) {
    for (uint64_t c = 0; c < clocks; c++) fs_planar_clock(st, raw[2 * c], raw[2 * c + 1], FS_CLK_RAW);
}

static void planar_finish(void *st) {
    planar_t *w = st;
    if (w->s.started) planar_close(w, w->s.h.n);
    if (w->s.clk_errors) fprintf(stderr, "fs: planar: %llu clock errors\n", (unsigned long long)w->s.clk_errors);
    base_free(&w->s);
    free(w);
}
const fs_writer_t fs_w_planar = { "planar", planar_create, planar_run, planar_finish };

// ---- events: one time-ordered stream of changes ---------------------------------------------------
// Block: the 7 port bytes at its first sample (the raw image without its clk
// byte, in byte order), then per changed sample: LEB128 (sample - previous
// event's sample, or - block start), a mask byte (bit k: raw byte k
// changed), the changed bytes' new values in byte order.
typedef struct {
    base_t s;
    uint64_t last;
} events_t;

static void events_open(events_t *w, uint64_t n) {
    w->s.b.first_sample = n;
    w->s.h.limit = n + w->s.block_samples;
    w->last = n;
    uint8_t *p = w->s.p[0];
    for (int k = 0; k < 8; k++)
        if (k != clk_byte) *p++ = (uint8_t)(w->s.h.want >> (8 * k));
    w->s.p[0] = p;
}

static inline void events_sample(events_t *w, uint64_t s, uint64_t v) {
    uint64_t x = v ^ w->s.h.want;
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
    w->s.h.want = v;
}

NOINLINE void fs_events_slow(void *wv, uint64_t r0, uint64_t r1) {
    events_t *w = wv;
    const uint64_t n = w->s.h.n;
    if (!w->s.started) {
        w->s.started = 1;
        w->s.h.want = r0 & fs_noclk;
        events_open(w, n);
    } else if (n == w->s.h.limit) {
        base_emit(&w->s, n);
        events_open(w, n);
    }
    w->s.clk_errors += (r0 & fs_clk_raw) != 0;
    w->s.clk_errors += (r1 & fs_clk_raw) == 0;
    events_sample(w, n, r0 & fs_noclk);
    events_sample(w, n + 1, r1 & fs_noclk);
    w->s.h.n = n + 2;
    if (UNLIKELY(w->s.p[0] > w->s.full_at[0])) {
        base_emit(&w->s, n + 2);
        events_open(w, n + 2);
    }
}

static void *events_new(uint64_t bc, fs_block_fn f, void *c, int async) {
    events_t *w = calloc(1, sizeof *w);
    base_init(&w->s, 1, STREAM_CAP, bc ? bc : (1ull << 24), f, c, async);
    return w;
}
static void *events_create(uint64_t bc, const char *out, fs_block_fn f, void *c) {
    (void)out;
    return events_new(bc, f, c, 0);
}

static void events_run(void *st, const uint64_t *raw, uint64_t clocks) {
    for (uint64_t c = 0; c < clocks; c++) fs_events_clock(st, raw[2 * c], raw[2 * c + 1], FS_CLK_RAW);
}

static void events_finish(void *st) {
    events_t *w = st;
    if (w->s.started) base_emit(&w->s, w->s.h.n);
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
    uint64_t block_clocks;
} raw_t;

NOINLINE void fs_raw_full(void *wv) {
    raw_t *w = wv;
    uint8_t *const base = w->s.b.buf[0];
    w->s.p[0] = (uint8_t *)w->s.h.q;
    const uint64_t end = w->s.b.first_sample + (uint64_t)(w->s.h.q - (uint64_t *)(void *)base);
    base_emit(&w->s, end);
    w->s.b.first_sample = end;
    w->s.h.q = (uint64_t *)(void *)w->s.b.buf[0];   // the other set, when async
    w->s.h.end = w->s.h.q + 2 * w->block_clocks;
}

static void *raw_new(uint64_t bc, fs_block_fn f, void *c, int async) {
    raw_t *w = calloc(1, sizeof *w);
    if (!bc) bc = 1ull << 21;
    w->block_clocks = bc;
    base_init(&w->s, 1, (size_t)(16 * bc + STREAM_MARGIN), bc, f, c, async);
    w->s.h.q = (uint64_t *)(void *)w->s.b.buf[0];
    w->s.h.end = w->s.h.q + 2 * bc;
    return w;
}
static void *raw_create(uint64_t bc, const char *out, fs_block_fn f, void *c) {
    (void)out;
    return raw_new(bc, f, c, 0);
}

static void raw_run(void *st, const uint64_t *raw, uint64_t clocks) {
    for (uint64_t c = 0; c < clocks; c++) fs_raw_clock(st, raw[2 * c], raw[2 * c + 1]);
}

static void raw_finish(void *st) {
    raw_t *w = st;
    fs_raw_full(w);
    base_free(&w->s);
    free(w);
}
const fs_writer_t fs_w_raw = { "raw", raw_create, raw_run, raw_finish };

// ---- the simulation check's writers (w_mine.h) -------------------------------------------------------
void *fs_bg_new(const char *writer, const char *transform, const char *comp, const char *pfile);
fs_block_fn fs_bg_fn(void);
void fs_bg_close(void *bg);

typedef struct { void *w, *bg; int raw; } sim_t;
static sim_t *sim_of[64];   // writer -> its bg, for fs_sim_finish
static int nsim;

void *fs_sim_create(const char *kind, const char *path, const int off[8]) {
    for (int k = 0; k < 8; k++) fs_off[k] = off[k];
    clk_byte = off[7];
    fs_clk_raw = 1ull << (8 * clk_byte);
    fs_noclk = ~(0xffull << (8 * clk_byte));
    const int raw = !strcmp(kind, "raw");
    if (!raw && strcmp(kind, "events")) return NULL;
    void *bg = fs_bg_new(kind, raw ? "xor6" : "identity", "zstd1", path);
    void *w = raw ? raw_new(0, fs_bg_fn(), bg, 1) : events_new(0, fs_bg_fn(), bg, 1);
    sim_t *s = calloc(1, sizeof *s);
    s->w = w; s->bg = bg; s->raw = raw;
    if (nsim < 64) sim_of[nsim++] = s;
    return w;
}

void fs_sim_finish(void *w) {
    for (int i = 0; i < nsim; i++) {
        if (sim_of[i] && sim_of[i]->w == w) {
            if (sim_of[i]->raw) raw_finish(w);
            else events_finish(w);
            fs_bg_close(sim_of[i]->bg);
            free(sim_of[i]);
            sim_of[i] = NULL;
            return;
        }
    }
}
