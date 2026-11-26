// SPDX-License-Identifier: Apache-2.0
// Test-only helper for python/tests/test_samplers.py: `vgacap-tbv1-dump TRACE
// OUT` runs the shared calibration, the frame clock and tb-v1
// (vgacap/sample.h) over a tt-um-43 trace (or plain VGCH v2 stream), writes
// every emitted frame's rgb6 to OUT, one after another, and prints
//   capture_after_clock=<n>
//   ok=<0|1> status=<s> mode=<name> cpp=<vgacap_fmt_double> width=<n> height=<n> hback=<n> vback=<n>
//   target_frames=<n> clock_limit=<n>
//   ends=<n>             (frame-clock ends over the whole trace)
//   frames=<n>
//   frame=<number> <start clock>   (one line per frame, in order)
// clock_hz is the header's; calib_seconds and seconds are META(open)'s, read
// with strtod.
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

typedef struct state {
    vgacal_t cal; vgafc_t fc; tbv1_t *tb;
    double clock_hz, seconds;
    uint64_t next;    // the next clock expected
    int gap, write_err;
    FILE *out;
    uint64_t *frame_no, *frame_start; size_t n, cap;
} state_t;

static void on_frame(void *user, const vgasample_frame_t *f) {
    state_t *st = user;
    size_t sz = (size_t)f->width * f->height;
    if (fwrite(f->rgb6, 1, sz, st->out) != sz) st->write_err = 1;
    if (st->n == st->cap) {
        st->cap = st->cap ? 2 * st->cap : 64;
        st->frame_no = realloc(st->frame_no, st->cap * sizeof *st->frame_no);
        st->frame_start = realloc(st->frame_start, st->cap * sizeof *st->frame_start);
        if (!st->frame_no || !st->frame_start) { fprintf(stderr, "vgacap-tbv1-dump: out of memory\n"); abort(); }
    }
    st->frame_no[st->n] = f->number;
    st->frame_start[st->n] = f->start_clock;
    st->n++;
}

static void start_capture(state_t *st) {
    vgafc_init(&st->fc, &st->cal);
    st->tb = tbv1_create(&st->cal, &st->fc, st->clock_hz, st->seconds, on_frame, st);
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
    uint64_t n = c1 - c0, done = 0;
    if (st->cal.state == VGACAL_RUNNING) done = vgacal_push(&st->cal, uo, rst, c0, n);
    if (done < n) {
        if (!st->tb) start_capture(st);
        vgafc_push(&st->fc, uo, c0 + done, n - done);
        tbv1_push(st->tb, uo, c0 + done, n - done);
    }
    return 0;
}

int main(int argc, char **argv) {
    if (argc != 3) { fprintf(stderr, "usage: %s TRACE OUT\n", argv[0]); return 2; }
    char err[256] = "";
    vgacap_trace_reader_t *r = vgacap_trace_open(argv[1], err, sizeof err);
    if (!r) { fprintf(stderr, "vgacap-tbv1-dump: %s\n", err); return 1; }
    const vgacap_trace_info_t *info = vgacap_trace_info(r);
    const vgacap_header_t *h = &info->header;
    char layout[64], csecs[64], secs[64];
    if (!info->open_meta || meta_get(info->open_meta, "layout", layout, sizeof layout) ||
        strcmp(layout, "tt-um-43") || meta_get(info->open_meta, "calib_seconds", csecs, sizeof csecs) ||
        meta_get(info->open_meta, "seconds", secs, sizeof secs)) {
        fprintf(stderr, "vgacap-tbv1-dump: no META(open) with layout=tt-um-43, calib_seconds and seconds\n");
        vgacap_trace_close(r); return 1;
    }
    if (h->version != 2 || h->sample_bits != 43 || h->samples_per_clock != 2 ||
        h->clock_bit != 42 || h->clock_first != 0) {
        fprintf(stderr, "vgacap-tbv1-dump: header disagrees with layout tt-um-43\n");
        vgacap_trace_close(r); return 1;
    }
    state_t st;
    memset(&st, 0, sizeof st);
    double calib_seconds;
    if (parse_double(csecs, &calib_seconds) || parse_double(secs, &st.seconds)) {
        fprintf(stderr, "vgacap-tbv1-dump: bad calib_seconds '%s' or seconds '%s'\n", csecs, secs);
        vgacap_trace_close(r); return 1;
    }
    st.out = fopen(argv[2], "wb");
    if (!st.out) { fprintf(stderr, "vgacap-tbv1-dump: cannot write %s\n", argv[2]); vgacap_trace_close(r); return 1; }
    st.clock_hz = (double)h->clock_hz;
    vgacal_init(&st.cal, st.clock_hz, calib_seconds);
    uint64_t clocks = info->samples / 2;
    if (vgacap_trace_read(r, 0, 2 * clocks, on_run, &st, err, sizeof err)) {
        fprintf(stderr, "vgacap-tbv1-dump: %s\n", err);
        vgacap_trace_close(r); return 1;
    }
    vgacap_trace_close(r);
    if (st.gap || st.next != clocks) {
        fprintf(stderr, "vgacap-tbv1-dump: runs do not cover the trace's clocks\n");
        return 1;
    }
    vgacal_end_of_trace(&st.cal, clocks);
    if (!st.tb) start_capture(&st);
    if (fclose(st.out) || st.write_err) { fprintf(stderr, "vgacap-tbv1-dump: write error on %s\n", argv[2]); return 1; }

    const vgasample_geometry_t *g = tbv1_geometry(st.tb);
    char cpp[64];
    if (vgacap_fmt_double(cpp, sizeof cpp, g->cpp) < 0) { fprintf(stderr, "vgacap-tbv1-dump: cpp format\n"); return 1; }
    printf("capture_after_clock=%" PRIu64 "\n", st.cal.capture_after_clock);
    printf("ok=%d status=%s mode=%s cpp=%s width=%u height=%u hback=%d vback=%d\n",
           g->ok, g->status, g->mode, cpp, g->width, g->height, g->hback, g->vback);
    printf("target_frames=%" PRIu64 " clock_limit=%" PRIu64 "\n",
           tbv1_target_frames(st.tb), tbv1_clock_limit(st.tb));
    printf("ends=%zu\n", st.fc.n);
    printf("frames=%zu\n", st.n);
    for (size_t i = 0; i < st.n; i++)
        printf("frame=%" PRIu64 " %" PRIu64 "\n", st.frame_no[i], st.frame_start[i]);
    tbv1_free(st.tb);
    vgacal_free(&st.cal);
    vgafc_free(&st.fc);
    free(st.frame_no); free(st.frame_start);
    return 0;
}
