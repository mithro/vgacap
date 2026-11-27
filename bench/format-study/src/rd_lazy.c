// SPDX-License-Identifier: Apache-2.0
// fsbench read --kind vgch2lazy: the lazy-BLKH copy of vgacap's reader
// (trace_read_lazy.c), behind the same interface rd.c gives vgch2.
#include "lazy.h"
#include "fs.h"
#include <stdio.h>
#include <stdlib.h>

void *fs_lazy_open(const char *path);
int fs_lazy_read(void *ctx, uint64_t from, uint64_t to, fs_runs_fn fn, void *user);
uint64_t fs_lazy_samples(void *ctx);
void fs_lazy_close(void *ctx);

void *fs_lazy_open(const char *path) {
    char err[512];
    vgacap_trace_reader_t *r = vgacap_trace_open(path, err, sizeof err);
    if (!r) fprintf(stderr, "fs: %s\n", err);
    return r;
}
struct fwd { fs_runs_fn fn; void *user; };
static int fwd_fn(void *user, const vgacap_trace_run_t *runs, size_t n) {
    struct fwd *f = user;
    return f->fn(f->user, (const fs_run_t *)(const void *)runs, n);
}
int fs_lazy_read(void *ctx, uint64_t from, uint64_t to, fs_runs_fn fn, void *user) {
    char err[512];
    struct fwd f = { fn, user };
    if (vgacap_trace_read_runs(ctx, from, to, fwd_fn, &f, err, sizeof err)) { fprintf(stderr, "fs: %s\n", err); return -1; }
    return 0;
}
uint64_t fs_lazy_samples(void *ctx) { return vgacap_trace_info(ctx)->samples; }
void fs_lazy_close(void *ctx) { vgacap_trace_close(ctx); }
