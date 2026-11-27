// SPDX-License-Identifier: Apache-2.0
// Existing formats written directly from the simulation thread: FST (libfst,
// vendored in vgacap), LXT2 (GTKWave's lxt2_write) and VCD (text, the study's
// own formatter). Times are picoseconds, as vgacap-export writes them, so the
// file is what a viewer would open. Signals: the seven ports but clk; fst_clk
// adds clk, which changes every sample.
#define _POSIX_C_SOURCE 200809L
#include "fs.h"
#include "fstapi.h"
#include "lxt2_write.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define NOINLINE __attribute__((noinline))
#define UNLIKELY(x) __builtin_expect(!!(x), 0)

static const char *const port_name[FS_PORTS] = { "uo_out", "uio_out", "uio_oe", "ui_in", "uio_in", "rst_n", "ena", "clk" };
static const int port_width[FS_PORTS] = { 8, 8, 8, 8, 8, 1, 1, 1 };

// ---- FST ---------------------------------------------------------------------------
int fs_fst_pack = FST_WR_PT_LZ4;   // the pack type; vgacap-export uses LZ4
int fs_fst_parallel = 1;           // vgacap-export's setting

typedef struct {
    fstWriterContext *ctx;
    fstHandle h[FS_PORTS];
    uint64_t want, n;
    int with_clk, started;
} fst_t;

static void *fst_open(const char *out, int with_clk) {
    fst_t *w = calloc(1, sizeof *w);
    w->ctx = fstWriterCreate(out, 1);
    if (!w->ctx) { fprintf(stderr, "fs: fst: cannot create %s\n", out); exit(2); }
    fstWriterSetPackType(w->ctx, (enum fstWriterPackType)fs_fst_pack);
    fstWriterSetParallelMode(w->ctx, fs_fst_parallel);
    fstWriterSetTimescale(w->ctx, -12);
    fstWriterSetScope(w->ctx, FST_ST_VCD_MODULE, "top", NULL);
    for (int k = 0; k < (with_clk ? FS_PORTS : FS_GROUPS); k++)
        w->h[k] = fstWriterCreateVar(w->ctx, FST_VT_VCD_WIRE, FST_VD_IMPLICIT, (uint32_t)port_width[k], port_name[k], 0);
    fstWriterSetUpscope(w->ctx);
    w->with_clk = with_clk;
    w->want = ~0ull;
    return w;
}
static void *fst_create(uint64_t bc, const char *out, fs_block_fn f, void *c) { (void)bc; (void)f; (void)c; return fst_open(out, 0); }
static void *fst_clk_create(uint64_t bc, const char *out, fs_block_fn f, void *c) { (void)bc; (void)f; (void)c; return fst_open(out, 1); }

static inline void fst_sample(fst_t *w, uint64_t s, uint64_t v, uint64_t prev, int all) {
    uint64_t x = all ? FS_NOCLK_MASK : (v ^ prev);
    if (!x) return;
    fstWriterEmitTimeChange(w->ctx, fs_ps(s));
    while (x) {
        const unsigned k = (unsigned)__builtin_ctzll(x) >> 3;
        fstWriterEmitValueChange32(w->ctx, w->h[k], (uint32_t)port_width[k], (uint32_t)((v >> (8 * k)) & 0xff));
        x &= ~(0xffull << (8 * k));
    }
}

static NOINLINE void fst_slow(fst_t *w, uint64_t r0, uint64_t r1) {
    const uint64_t v0 = r0 & FS_NOCLK_MASK, v1 = r1 & FS_NOCLK_MASK;
    fst_sample(w, w->n, v0, w->want, !w->started);
    w->started = 1;
    fst_sample(w, w->n + 1, v1, v0, 0);
    w->want = v1;
    w->n += 2;
}

static void fst_run(void *st, const uint64_t *raw, uint64_t clocks) {
    fst_t *w = st;
    for (uint64_t c = 0; c < clocks; c++) {
        const uint64_t r0 = raw[2 * c], r1 = raw[2 * c + 1];
        if (UNLIKELY(((r0 ^ w->want) | (r1 ^ w->want ^ FS_CLK_RAW)) != 0)) fst_slow(w, r0, r1);
        else w->n += 2;
    }
}

// With clk as a signal there is no fast path: every sample is a time change.
static void fst_clk_run(void *st, const uint64_t *raw, uint64_t clocks) {
    fst_t *w = st;
    for (uint64_t c = 0; c < clocks; c++) {
        const uint64_t r0 = raw[2 * c], r1 = raw[2 * c + 1];
        const uint64_t v0 = r0 & FS_NOCLK_MASK, v1 = r1 & FS_NOCLK_MASK;
        // fst_sample emits the time change when a port changed; else it is emitted here.
        if (w->started && v0 == w->want) fstWriterEmitTimeChange(w->ctx, fs_ps(w->n));
        else fst_sample(w, w->n, v0, w->want, !w->started);
        fstWriterEmitValueChange32(w->ctx, w->h[FS_CLK], 1, (uint32_t)(r0 >> (8 * FS_CLK)) & 1);
        w->started = 1;
        if (v1 == v0) fstWriterEmitTimeChange(w->ctx, fs_ps(w->n + 1));
        else fst_sample(w, w->n + 1, v1, v0, 0);
        fstWriterEmitValueChange32(w->ctx, w->h[FS_CLK], 1, (uint32_t)(r1 >> (8 * FS_CLK)) & 1);
        w->want = v1;
        w->n += 2;
    }
}

static void fst_finish(void *st) {
    fst_t *w = st;
    fstWriterEmitTimeChange(w->ctx, fs_ps(w->n));
    fstWriterClose(w->ctx);
    free(w);
}
const fs_writer_t fs_w_fst = { "fst", fst_create, fst_run, fst_finish };
const fs_writer_t fs_w_fst_clk = { "fst_clk", fst_clk_create, fst_clk_run, fst_finish };

// ---- LXT2 ----------------------------------------------------------------------------
typedef struct {
    struct lxt2_wr_trace *lt;
    struct lxt2_wr_symbol *sym[FS_GROUPS];
    uint64_t want, n;
    int started;
} lxt2_t;

static void *lxt2_create(uint64_t bc, const char *out, fs_block_fn f, void *c) {
    (void)bc; (void)f; (void)c;
    lxt2_t *w = calloc(1, sizeof *w);
    w->lt = lxt2_wr_init(out);
    if (!w->lt) { fprintf(stderr, "fs: lxt2: cannot create %s\n", out); exit(2); }
    lxt2_wr_set_timescale(w->lt, -12);
    char name[32];
    for (int k = 0; k < FS_GROUPS; k++) {
        snprintf(name, sizeof name, "top.%s", port_name[k]);
        w->sym[k] = lxt2_wr_symbol_add(w->lt, name, 0, port_width[k] - 1, 0, LXT2_WR_SYM_F_BITS);
    }
    w->want = ~0ull;
    return w;
}

static inline void lxt2_sample(lxt2_t *w, uint64_t s, uint64_t v, uint64_t prev, int all) {
    uint64_t x = all ? FS_NOCLK_MASK : (v ^ prev);
    if (!x) return;
    lxt2_wr_set_time64(w->lt, fs_ps(s));
    while (x) {
        const unsigned k = (unsigned)__builtin_ctzll(x) >> 3;
        lxt2_wr_emit_value_int(w->lt, w->sym[k], 0, (int)((v >> (8 * k)) & 0xff));
        x &= ~(0xffull << (8 * k));
    }
}

static NOINLINE void lxt2_slow(lxt2_t *w, uint64_t r0, uint64_t r1) {
    const uint64_t v0 = r0 & FS_NOCLK_MASK, v1 = r1 & FS_NOCLK_MASK;
    lxt2_sample(w, w->n, v0, w->want, !w->started);
    w->started = 1;
    lxt2_sample(w, w->n + 1, v1, v0, 0);
    w->want = v1;
    w->n += 2;
}

static void lxt2_run(void *st, const uint64_t *raw, uint64_t clocks) {
    lxt2_t *w = st;
    for (uint64_t c = 0; c < clocks; c++) {
        const uint64_t r0 = raw[2 * c], r1 = raw[2 * c + 1];
        if (UNLIKELY(((r0 ^ w->want) | (r1 ^ w->want ^ FS_CLK_RAW)) != 0)) lxt2_slow(w, r0, r1);
        else w->n += 2;
    }
}

static void lxt2_finish(void *st) {
    lxt2_t *w = st;
    lxt2_wr_set_time64(w->lt, fs_ps(w->n));
    lxt2_wr_close(w->lt);
    free(w);
}
const fs_writer_t fs_w_lxt2 = { "lxt2", lxt2_create, lxt2_run, lxt2_finish };

// ---- VCD -------------------------------------------------------------------------------
// The text a VCD writer would emit, into block buffers that bg.c compresses
// (a .vcd.zst). Vectors as "b" + 8 binary digits, scalars as one digit.
#define VCD_CAP (16u << 20)
#define VCD_MARGIN (64u << 10)

typedef struct {
    fs_block_fn on_block;
    void *ctx;
    fs_block_t b;
    char *p, *full_at;
    uint64_t want, n;
    int started;
} vcd_t;

static char *put_u64(char *p, uint64_t v) {
    char tmp[24];
    int i = 0;
    do { tmp[i++] = (char)('0' + v % 10); v /= 10; } while (v);
    while (i) *p++ = tmp[--i];
    return p;
}

static void vcd_emit(vcd_t *w) {
    w->b.len[0] = (size_t)(w->p - (char *)w->b.buf[0]);
    w->b.nsamples = w->n - w->b.first_sample;
    if (w->on_block && w->b.len[0]) w->on_block(w->ctx, &w->b);
    w->p = (char *)w->b.buf[0];
    w->b.first_sample = w->n;
}

static void *vcd_create(uint64_t bc, const char *out, fs_block_fn f, void *c) {
    (void)bc; (void)out;
    vcd_t *w = calloc(1, sizeof *w);
    w->on_block = f; w->ctx = c;
    w->b.nstreams = 1;
    w->b.buf[0] = malloc(VCD_CAP);
    w->p = (char *)w->b.buf[0];
    w->full_at = w->p + VCD_CAP - VCD_MARGIN;
    w->p += sprintf(w->p, "$timescale 1ps $end\n$scope module top $end\n");
    for (int k = 0; k < FS_GROUPS; k++)
        w->p += sprintf(w->p, "$var wire %d %c %s $end\n", port_width[k], '!' + k, port_name[k]);
    w->p += sprintf(w->p, "$upscope $end\n$enddefinitions $end\n");
    w->want = ~0ull;
    return w;
}

static inline void vcd_sample(vcd_t *w, uint64_t s, uint64_t v, uint64_t prev, int all) {
    uint64_t x = all ? FS_NOCLK_MASK : (v ^ prev);
    if (!x) return;
    char *p = w->p;
    *p++ = '#';
    p = put_u64(p, fs_ps(s));
    *p++ = '\n';
    while (x) {
        const unsigned k = (unsigned)__builtin_ctzll(x) >> 3;
        const unsigned b = (unsigned)(v >> (8 * k)) & 0xff;
        if (port_width[k] == 1) {
            *p++ = (char)('0' + (b & 1));
        } else {
            *p++ = 'b';
            for (int i = 7; i >= 0; i--) *p++ = (char)('0' + ((b >> i) & 1));
            *p++ = ' ';
        }
        *p++ = (char)('!' + k);
        *p++ = '\n';
        x &= ~(0xffull << (8 * k));
    }
    w->p = p;
}

static NOINLINE void vcd_slow(vcd_t *w, uint64_t r0, uint64_t r1) {
    const uint64_t v0 = r0 & FS_NOCLK_MASK, v1 = r1 & FS_NOCLK_MASK;
    vcd_sample(w, w->n, v0, w->want, !w->started);
    w->started = 1;
    vcd_sample(w, w->n + 1, v1, v0, 0);
    w->want = v1;
    w->n += 2;
    if (UNLIKELY(w->p > w->full_at)) vcd_emit(w);
}

static void vcd_run(void *st, const uint64_t *raw, uint64_t clocks) {
    vcd_t *w = st;
    for (uint64_t c = 0; c < clocks; c++) {
        const uint64_t r0 = raw[2 * c], r1 = raw[2 * c + 1];
        if (UNLIKELY(((r0 ^ w->want) | (r1 ^ w->want ^ FS_CLK_RAW)) != 0)) vcd_slow(w, r0, r1);
        else w->n += 2;
    }
}

static void vcd_finish(void *st) {
    vcd_t *w = st;
    w->p += sprintf(w->p, "#");
    w->p = put_u64(w->p, fs_ps(w->n));
    *w->p++ = '\n';
    vcd_emit(w);
    free(w->b.buf[0]);
    free(w);
}
const fs_writer_t fs_w_vcd = { "vcd", vcd_create, vcd_run, vcd_finish };

// ---- the list -----------------------------------------------------------------------------
const fs_writer_t *fs_writer_by_name(const char *name) {
    static const fs_writer_t *const all[] = { &fs_w_null, &fs_w_vgch2, &fs_w_planar, &fs_w_events, &fs_w_runs, &fs_w_clog, &fs_w_raw,
                                              &fs_w_fst, &fs_w_fst_clk, &fs_w_lxt2, &fs_w_vcd };
    for (size_t i = 0; i < sizeof all / sizeof all[0]; i++)
        if (strcmp(all[i]->name, name) == 0) return all[i];
    return NULL;
}
