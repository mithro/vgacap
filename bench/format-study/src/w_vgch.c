// SPDX-License-Identifier: Apache-2.0
// Candidate vgch2: VGCH version 2 as it is (vgacap docs/trace-format.md),
// through vgacap's own trace writer, with tb.cpp's glue copied exactly
// (tinytapeout-vga-videos ttvga/harness/tb.cpp: the raw compare, raw_slow
// and vgacap_trace_repeat2).
#define _POSIX_C_SOURCE 200809L
#define ZSTD_STATIC_LINKING_ONLY
#include "fs.h"
#include "vgch.h"
#include "vgacap/trace.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <zstd.h>

// ---- the hook (vgch_hook.h) ---------------------------------------------------
int fs_hook_skip;                 // 1: no compression (an 8-byte stand-in frame)
fs_block_fn fs_hook_tap;          // each block's uncompressed bytes, or NULL
void *fs_hook_tap_ctx;
static uint64_t hook_first;

size_t fs_hook_ZSTD_compress2(ZSTD_CCtx *c, void *dst, size_t cap, const void *src, size_t n);
size_t fs_hook_ZSTD_compress2(ZSTD_CCtx *c, void *dst, size_t cap, const void *src, size_t n) {
    if (fs_hook_tap) {
        fs_block_t b;
        memset(&b, 0, sizeof b);
        b.first_sample = hook_first;
        b.nstreams = 1;
        b.buf[0] = (uint8_t *)(uintptr_t)src;
        b.len[0] = n;
        fs_hook_tap(fs_hook_tap_ctx, &b);
        hook_first++;   // a block counter only: the tap reads BLKH itself
    }
    if (fs_hook_skip) {
        memset(dst, 0, 8);
        return 8;
    }
    return ZSTD_compress2(c, dst, cap, src, n);
}

// ---- META and header from the source trace ---------------------------------------
static vgacap_header_t src_header;
static char *src_open, *src_close;
static int have_src;

void fs_vgch_set_source(const char *trace_path) {
    char err[512];
    vgacap_trace_reader_t *r = vgacap_trace_open(trace_path, err, sizeof err);
    if (!r) { fprintf(stderr, "fs: %s\n", err); exit(2); }
    const vgacap_trace_info_t *in = vgacap_trace_info(r);
    src_header = in->header;
    free(src_open); free(src_close);
    src_open = strdup(in->open_meta);
    src_close = strdup(in->close_meta ? in->close_meta : "meta=close\ncapture_after_clock=0\nstatus=ok\nframes=0\n");
    have_src = 1;
    vgacap_trace_close(r);
}

// The source's header and META texts, for the lean candidate (w_lean.c).
void fs_vgch_source(const vgacap_header_t **h, const char **open_meta, const char **close_meta);
void fs_vgch_source(const vgacap_header_t **h, const char **open_meta, const char **close_meta) {
    if (!have_src) { fprintf(stderr, "fs: lean needs fs_vgch_set_source\n"); exit(2); }
    *h = &src_header; *open_meta = src_open; *close_meta = src_close;
}

// ---- the candidate ------------------------------------------------------------------
typedef struct {
    vgacap_trace_writer_t *t;
    uint64_t want;
    int failed;
} vg_t;

// tb.cpp's raw_slow.
__attribute__((noinline)) static int vg_slow(vgacap_trace_writer_t *t, uint64_t r0, uint64_t r1, uint64_t *want) {
    *want = r1 & ~FS_CLK_RAW;
    return vgacap_trace_push2(t, fs_pack(r0), fs_pack(r1));
}

static void *vg_create(uint64_t block_clocks, const char *out, fs_block_fn on_block, void *ctx) {
    (void)block_clocks;   // VGACAP_TRACE_BLOCK_CLOCKS: the format fixes it
    if (!have_src) { fprintf(stderr, "fs: vgch2 needs fs_vgch_set_source\n"); exit(2); }
    vg_t *v = calloc(1, sizeof *v);
    char err[512];
    fs_hook_tap = on_block;
    fs_hook_tap_ctx = ctx;
    hook_first = 0;
    v->t = vgacap_trace_create(out, &src_header, src_open, 1, err, sizeof err);
    if (!v->t) { fprintf(stderr, "fs: vgch2: %s\n", err); exit(2); }
    v->want = ~0ull;
    return v;
}

static void vg_run(void *st, const uint64_t *raw, uint64_t clocks) {
    vg_t *v = st;
    vgacap_trace_writer_t *const t = v->t;
    uint64_t want = v->want;
    int bad = 0;
    for (uint64_t c = 0; c < clocks; c++) {
        const uint64_t r0 = raw[2 * c], r1 = raw[2 * c + 1];
        if (__builtin_expect(((r0 ^ want) | (r1 ^ want ^ FS_CLK_RAW)) != 0, 0)) {
            bad |= vg_slow(t, r0, r1, &want);
        } else {
            bad |= vgacap_trace_repeat2(t);
        }
    }
    v->want = want;
    v->failed |= bad;
}

static void vg_finish(void *st) {
    vg_t *v = st;
    char err[512];
    if (vgacap_trace_finish(v->t, src_close, err, sizeof err)) fprintf(stderr, "fs: vgch2 finish: %s\n", err);
    if (v->failed) fprintf(stderr, "fs: vgch2: a push failed\n");
    fs_hook_tap = NULL;
    free(v);
}

const fs_writer_t fs_w_vgch2 = { "vgch2", vg_create, vg_run, vg_finish };
