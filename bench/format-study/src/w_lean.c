// SPDX-License-Identifier: Apache-2.0
// Candidate lean: VGCH version 2 through the lean writer
// (trace_write_lean.c: vgacap's writer with push2's slow path replaced),
// with tb.cpp's glue as the vgch2 candidate has it (w_vgch.c). Also the
// lean writer's entry points for the simulation check (lean_api.h).
#define _POSIX_C_SOURCE 200809L
#include "lean.h"
#include "lean_api.h"
#include "fs.h"
#include "vgch.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

void fs_vgch_source(const vgacap_header_t **h, const char **open_meta, const char **close_meta);
extern fs_block_fn fs_hook_tap;

typedef struct {
    vgacap_trace_writer_t *t;
    uint64_t want;
    int failed;
} ln_t;

// tb.cpp's raw_slow.
__attribute__((noinline)) static int ln_slow(vgacap_trace_writer_t *t, uint64_t r0, uint64_t r1, uint64_t *want) {
    *want = r1 & ~FS_CLK_RAW;
    return vgacap_trace_push2(t, fs_pack(r0), fs_pack(r1));
}

static void *ln_create(uint64_t block_clocks, const char *out, fs_block_fn on_block, void *ctx) {
    (void)block_clocks; (void)on_block; (void)ctx;
    const vgacap_header_t *h;
    const char *om, *cm;
    fs_vgch_source(&h, &om, &cm);
    ln_t *v = calloc(1, sizeof *v);
    char err[512];
    v->t = vgacap_trace_create(out, h, om, 1, err, sizeof err);
    if (!v->t) { fprintf(stderr, "fs: lean: %s\n", err); exit(2); }
    v->want = ~0ull;
    return v;
}

static void ln_run(void *st, const uint64_t *raw, uint64_t clocks) {
    ln_t *v = st;
    vgacap_trace_writer_t *const t = v->t;
    uint64_t want = v->want;
    int bad = 0;
    for (uint64_t c = 0; c < clocks; c++) {
        const uint64_t r0 = raw[2 * c], r1 = raw[2 * c + 1];
        if (__builtin_expect(((r0 ^ want) | (r1 ^ want ^ FS_CLK_RAW)) != 0, 0)) {
            bad |= ln_slow(t, r0, r1, &want);
        } else {
            bad |= vgacap_trace_repeat2(t);
        }
    }
    v->want = want;
    v->failed |= bad;
}

static void ln_finish(void *st) {
    ln_t *v = st;
    const vgacap_header_t *h;
    const char *om, *cm;
    fs_vgch_source(&h, &om, &cm);
    char err[512];
    if (vgacap_trace_finish(v->t, cm, err, sizeof err)) fprintf(stderr, "fs: lean finish: %s\n", err);
    if (v->failed) fprintf(stderr, "fs: lean: a push failed\n");
    free(v);
}

const fs_writer_t fs_w_lean = { "lean", ln_create, ln_run, ln_finish };

// ---- lean_api.h ------------------------------------------------------------------
vgacap_trace_writer_t *fs_lean_create(const char *path, const vgacap_header_t *h, const char *open_meta,
                                      int zstd_level, char *err, size_t errlen) {
    return vgacap_trace_create(path, h, open_meta, zstd_level, err, errlen);
}
int fs_lean_finish(vgacap_trace_writer_t *t, const char *close_meta, char *err, size_t errlen) {
    return vgacap_trace_finish(t, close_meta, err, errlen);
}
static inline uint64_t pack_off(uint64_t raw, const int *o) {
    uint8_t b[8];
    memcpy(b, &raw, 8);
    return (uint64_t)b[o[0]] | (uint64_t)b[o[1]] << 8 | (uint64_t)b[o[2]] << 16 | (uint64_t)b[o[3]] << 24 |
           (uint64_t)b[o[4]] << 32 | (uint64_t)(b[o[5]] & 1) << 40 | (uint64_t)(b[o[6]] & 1) << 41 |
           (uint64_t)(b[o[7]] & 1) << 42;
}
__attribute__((noinline)) int fs_lean_raw_slow(vgacap_trace_writer_t *t, const int off[8], uint64_t clk_raw,
                                               uint64_t r0, uint64_t r1, uint64_t *want_raw) {
    *want_raw = r1 & ~clk_raw;
    return vgacap_trace_push2(t, pack_off(r0, off), pack_off(r1, off));
}
int fs_lean_repeat2_slow(vgacap_trace_writer_t *t) { return vgacap_trace_repeat2_slow(t); }
