// SPDX-License-Identifier: Apache-2.0
// The replay source: a published trace read through vgacap's own reader, its
// runs expanded into raw images (fs.h). Nothing here is timed.
#define _POSIX_C_SOURCE 200809L
#include "fs.h"
#include "vgacap/trace.h"
#include <stdio.h>
#include <stdlib.h>
#include <time.h>

int fs_off[8] = { 0, 1, 2, 3, 4, 5, 6, 7 };
uint32_t fs_clock_hz = 25175000;

struct fs_source {
    vgacap_trace_reader_t *r;
    uint64_t samples;
};

fs_source_t *fs_source_open(const char *path) {
    char err[512];
    vgacap_trace_reader_t *r = vgacap_trace_open(path, err, sizeof err);
    if (!r) { fprintf(stderr, "fs: %s\n", err); return NULL; }
    const vgacap_trace_info_t *in = vgacap_trace_info(r);
    if (in->header.sample_bits != 43 || in->header.clock_bit != 42 || in->header.samples_per_clock != 2) {
        fprintf(stderr, "fs: %s: not a tt-um-43 trace\n", path);
        vgacap_trace_close(r);
        return NULL;
    }
    fs_source_t *s = calloc(1, sizeof *s);
    s->r = r;
    s->samples = in->samples;
    fs_clock_hz = in->header.clock_hz;
    return s;
}

uint64_t fs_source_clocks(const fs_source_t *s) { return s->samples / 2; }

struct fill { uint64_t from; uint64_t *raw; };

static int on_fill(void *user, const vgacap_trace_run_t *runs, size_t n) {
    struct fill *f = user;
    for (size_t i = 0; i < n; i++) {
        uint64_t raw = fs_raw_from_sample(runs[i].value);
        uint64_t *p = f->raw + (runs[i].first_sample - f->from);
        for (uint64_t k = 0; k < runs[i].run; k++) { p[k] = raw; raw ^= FS_CLK_RAW; }
    }
    return 0;
}

int fs_source_chunk(fs_source_t *s, uint64_t c0, uint64_t clocks, uint64_t *raw) {
    char err[512];
    struct fill f = { 2 * c0, raw };
    if (vgacap_trace_read_runs(s->r, 2 * c0, 2 * (c0 + clocks), on_fill, &f, err, sizeof err)) {
        fprintf(stderr, "fs: %s\n", err);
        return -1;
    }
    return 0;
}

struct fwd { fs_runs_fn fn; void *user; };
static int on_fwd(void *user, const vgacap_trace_run_t *runs, size_t n) {
    struct fwd *w = user;
    return w->fn(w->user, (const fs_run_t *)(const void *)runs, n);
}

int fs_source_runs(fs_source_t *s, uint64_t from, uint64_t to, fs_runs_fn fn, void *user) {
    char err[512];
    struct fwd w = { fn, user };
    if (vgacap_trace_read_runs(s->r, from, to, on_fwd, &w, err, sizeof err)) {
        fprintf(stderr, "fs: %s\n", err);
        return -1;
    }
    return 0;
}

void fs_source_close(fs_source_t *s) {
    if (!s) return;
    vgacap_trace_close(s->r);
    free(s);
}

double fs_thread_ns(void) {
    struct timespec ts;
    clock_gettime(CLOCK_THREAD_CPUTIME_ID, &ts);
    return (double)ts.tv_sec * 1e9 + (double)ts.tv_nsec;
}

double fs_wall_ns(void) {
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return (double)ts.tv_sec * 1e9 + (double)ts.tv_nsec;
}
