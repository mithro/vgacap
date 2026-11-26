// SPDX-License-Identifier: Apache-2.0
// Test-only helper for python/tests/test_samplers.py: `vgacap-centre-dump
// TRACE RGB6 RAW [DX DY]` runs the shared calibration, the frame clock and
// centre-v1 (vgacap/sample.h) over a tt-um-43 trace (or plain VGCH v2
// stream), with the window override DX, DY (default 0 0). For every emitted
// frame it writes the picked rgb6 to RGB6 and the clock-resolution frame to
// RAW: rows 0 .. lines - 1 of clocks 0 .. line_clocks - 1 of libvgaframe's
// raw frame (6-bit colour | 0x80 written; rows it did not receive are 0).
// It prints
//   capture_after_clock=<n>
//   ok=<0|1> status=<s> mode=<name> cpp=<vgacap_fmt_double> width=<n> height=<n> hback=<n> vback=<n>
//   line_clocks=<n> lines=<n>
//   hs=<found>,<active_low>,<pulse>,<period> vs=<found>,<active_low>,<pulse>,<period>   (the calibration)
//   outside=<n> suggest_dx=<n> suggest_dy=<n> colour_changes=<n> off_grid=<n|-> dx=<n> dy=<n>
//   ends=<n>             (frame-clock ends over the whole trace)
//   frames=<n>
//   frame=<number> <start clock> <missing>   (one line per frame, in order)
// clock_hz is the header's; calib_seconds is META(open)'s, read with strtod.
#include "vgacap/sample.h"
#include "vgacap/time.h"
#include "vgacap/trace.h"
#include <errno.h>
#include <inttypes.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

// META(open)'s value for `key` into out; 0, or -1 if absent or too long.
static int meta_get(const char *meta, const char *key, char *out, size_t outlen) {
    size_t kl = strlen(key);
    for (const char *p = meta; p && *p; ) {
        const char *nl = strchr(p, '\n');
        size_t ll = nl ? (size_t)(nl - p) : strlen(p);
        if (ll > kl && !strncmp(p, key, kl) && p[kl] == '=') {
            size_t vl = ll - kl - 1;
            if (vl >= outlen) return -1;
            memcpy(out, p + kl + 1, vl); out[vl] = 0;
            return 0;
        }
        p = nl ? nl + 1 : NULL;
    }
    return -1;
}

static int parse_double(const char *s, double *out) {
    char *end;
    errno = 0;
    *out = strtod(s, &end);
    return errno || end == s || *end ? -1 : 0;
}

static int parse_int(const char *s, int *out) {
    char *end;
    errno = 0;
    long v = strtol(s, &end, 10);
    if (errno || end == s || *end || v < -100000 || v > 100000) return -1;
    *out = (int)v;
    return 0;
}

typedef struct state {
    vgacal_t cal; vgafc_t fc; centrev1_t *cs;
    int started;
    uint64_t next;    // the next clock expected
    uint64_t L, lines;
    int gap, write_err;
    FILE *out, *raw;
    uint8_t *row0;    // a row of zeros, L bytes
    uint64_t *frame_no, *frame_start; uint32_t *frame_missing; size_t n, cap;
} state_t;

static void on_raw(void *user, const vgaframe_raw_t *r) {
    state_t *st = user;
    for (uint64_t y = 0; y < st->lines; y++) {
        const uint8_t *src = y < r->lines ? r->raw + (size_t)y * r->stride : st->row0;
        if (fwrite(src, 1, (size_t)st->L, st->raw) != (size_t)st->L) st->write_err = 1;
    }
}

static void on_frame(void *user, const vgasample_frame_t *f) {
    state_t *st = user;
    size_t sz = (size_t)f->width * f->height;
    if (fwrite(f->rgb6, 1, sz, st->out) != sz) st->write_err = 1;
    if (st->n == st->cap) {
        st->cap = st->cap ? 2 * st->cap : 64;
        st->frame_no = realloc(st->frame_no, st->cap * sizeof *st->frame_no);
        st->frame_start = realloc(st->frame_start, st->cap * sizeof *st->frame_start);
        st->frame_missing = realloc(st->frame_missing, st->cap * sizeof *st->frame_missing);
        if (!st->frame_no || !st->frame_start || !st->frame_missing) {
            fprintf(stderr, "vgacap-centre-dump: out of memory\n"); abort();
        }
    }
    st->frame_no[st->n] = f->number;
    st->frame_start[st->n] = f->start_clock;
    st->frame_missing[st->n] = f->missing;
    st->n++;
}

static void start_capture(state_t *st) {
    vgafc_init(&st->fc, &st->cal);
    centrev1_calibrated(st->cs, &st->cal, &st->fc);
    st->L = st->cal.hs.period;
    st->lines = st->L ? (st->cal.vs.period + st->L / 2) / st->L : 0;
    if (st->L > 7596) st->L = 7596;       // the raw buffer's width
    if (st->lines > 1200) st->lines = 1200;
    st->row0 = calloc(st->L ? (size_t)st->L : 1, 1);
    if (!st->row0) { fprintf(stderr, "vgacap-centre-dump: out of memory\n"); abort(); }
    st->started = 1;
}

// A run of samples [s, s + r) holds the clk-high samples 2c + 1 of clocks
// [s >> 1, (s + r) >> 1) (clock_first 0).
static int on_run(void *user, uint64_t s, uint64_t value, uint64_t r) {
    state_t *st = user;
    uint64_t c0 = s >> 1, c1 = (s + r) >> 1;
    if (c1 <= c0) return 0;
    if (c0 != st->next) { st->gap = 1; return 1; }
    st->next = c1;
    uint8_t uo = (uint8_t)(value & 0xFF), rst = (uint8_t)((value >> 40) & 1);
    uint64_t n = c1 - c0, pre = 0;
    if (st->cal.state == VGACAL_RUNNING) pre = vgacal_push(&st->cal, uo, rst, c0, n);
    if (pre) centrev1_push(st->cs, uo, c0, pre);
    if (st->cal.state == VGACAL_DONE) {
        if (!st->started) start_capture(st);
        if (pre < n) {
            vgafc_push(&st->fc, uo, c0 + pre, n - pre);
            centrev1_push(st->cs, uo, c0 + pre, n - pre);
        }
    }
    return 0;
}

int main(int argc, char **argv) {
    if (argc != 4 && argc != 6) { fprintf(stderr, "usage: %s TRACE RGB6 RAW [DX DY]\n", argv[0]); return 2; }
    int dx = 0, dy = 0;
    if (argc == 6 && (parse_int(argv[4], &dx) || parse_int(argv[5], &dy))) {
        fprintf(stderr, "vgacap-centre-dump: bad DX '%s' or DY '%s'\n", argv[4], argv[5]);
        return 2;
    }
    char err[256] = "";
    vgacap_trace_reader_t *r = vgacap_trace_open(argv[1], err, sizeof err);
    if (!r) { fprintf(stderr, "vgacap-centre-dump: %s\n", err); return 1; }
    const vgacap_trace_info_t *info = vgacap_trace_info(r);
    const vgacap_header_t *h = &info->header;
    char layout[64], csecs[64];
    if (!info->open_meta || meta_get(info->open_meta, "layout", layout, sizeof layout) ||
        strcmp(layout, "tt-um-43") || meta_get(info->open_meta, "calib_seconds", csecs, sizeof csecs)) {
        fprintf(stderr, "vgacap-centre-dump: no META(open) with layout=tt-um-43 and calib_seconds\n");
        vgacap_trace_close(r); return 1;
    }
    if (h->version != 2 || h->sample_bits != 43 || h->samples_per_clock != 2 ||
        h->clock_bit != 42 || h->clock_first != 0) {
        fprintf(stderr, "vgacap-centre-dump: header disagrees with layout tt-um-43\n");
        vgacap_trace_close(r); return 1;
    }
    state_t st;
    memset(&st, 0, sizeof st);
    double calib_seconds;
    if (parse_double(csecs, &calib_seconds)) {
        fprintf(stderr, "vgacap-centre-dump: bad calib_seconds '%s'\n", csecs);
        vgacap_trace_close(r); return 1;
    }
    st.out = fopen(argv[2], "wb");
    st.raw = fopen(argv[3], "wb");
    if (!st.out || !st.raw) {
        fprintf(stderr, "vgacap-centre-dump: cannot write %s or %s\n", argv[2], argv[3]);
        vgacap_trace_close(r); return 1;
    }
    vgacal_init(&st.cal, (double)h->clock_hz, calib_seconds);
    st.cs = centrev1_create(dx, dy, on_frame, &st);
    centrev1_set_raw_observer(st.cs, on_raw, &st);
    uint64_t clocks = info->samples / 2;
    if (vgacap_trace_read(r, 0, 2 * clocks, on_run, &st, err, sizeof err)) {
        fprintf(stderr, "vgacap-centre-dump: %s\n", err);
        vgacap_trace_close(r); return 1;
    }
    vgacap_trace_close(r);
    if (st.gap || st.next != clocks) {
        fprintf(stderr, "vgacap-centre-dump: runs do not cover the trace's clocks\n");
        return 1;
    }
    vgacal_end_of_trace(&st.cal, clocks);
    if (!st.started) start_capture(&st);
    centrev1_diag_t d;
    centrev1_finish(st.cs, &d);
    if (fclose(st.out) || fclose(st.raw) || st.write_err) {
        fprintf(stderr, "vgacap-centre-dump: write error on %s or %s\n", argv[2], argv[3]);
        return 1;
    }

    const vgasample_geometry_t *g = centrev1_geometry(st.cs);
    char cpp[64];
    if (vgacap_fmt_double(cpp, sizeof cpp, g->cpp) < 0) { fprintf(stderr, "vgacap-centre-dump: cpp format\n"); return 1; }
    printf("capture_after_clock=%" PRIu64 "\n", st.cal.capture_after_clock);
    printf("ok=%d status=%s mode=%s cpp=%s width=%u height=%u hback=%d vback=%d\n",
           g->ok, g->status, g->mode, cpp, g->width, g->height, g->hback, g->vback);
    printf("line_clocks=%" PRIu64 " lines=%" PRIu64 "\n", st.L, st.lines);
    printf("hs=%d,%d,%" PRIu64 ",%" PRIu64 " vs=%d,%d,%" PRIu64 ",%" PRIu64 "\n",
           st.cal.hs.found, st.cal.hs.active_low, st.cal.hs.pulse, st.cal.hs.period,
           st.cal.vs.found, st.cal.vs.active_low, st.cal.vs.pulse, st.cal.vs.period);
    printf("outside=%" PRIu64 " suggest_dx=%d suggest_dy=%d colour_changes=%" PRIu64,
           d.outside, d.suggest_dx, d.suggest_dy, d.colour_changes);
    if (d.off_grid == UINT64_MAX) printf(" off_grid=-");
    else printf(" off_grid=%" PRIu64, d.off_grid);
    printf(" dx=%d dy=%d\n", d.dx, d.dy);
    printf("ends=%zu\n", st.fc.n);
    printf("frames=%zu\n", st.n);
    for (size_t i = 0; i < st.n; i++)
        printf("frame=%" PRIu64 " %" PRIu64 " %" PRIu32 "\n", st.frame_no[i], st.frame_start[i], st.frame_missing[i]);
    centrev1_free(st.cs);
    vgacal_free(&st.cal);
    vgafc_free(&st.fc);
    free(st.row0);
    free(st.frame_no); free(st.frame_start); free(st.frame_missing);
    return 0;
}
