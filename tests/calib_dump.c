// SPDX-License-Identifier: Apache-2.0
// Test-only helper for python/tests/test_calib.py: `vgacap-calib-dump TRACE`
// runs the shared calibration and the frame clock (vgacap/sample.h) over a
// tt-um-43 trace (or plain VGCH v2 stream) and prints
//   capture_after_clock=<n>
//   hs found=<0|1> active_low=<0|1> pulse=<n> period=<n> phases=<n>
//   vs found=<0|1> active_low=<0|1> pulse=<n> period=<n> phases=<n>
//   ends=<count>
//   end=<clock>        (one line per frame-clock end, in order)
// clock_hz is the header's; calib_seconds is META(open)'s, read with strtod.
#include "vgacap/sample.h"
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

typedef struct state {
    vgacal_t cal; vgafc_t fc; int fc_ready;
    uint64_t next;    // the next clock expected
    int gap;
} state_t;

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
        if (!st->fc_ready) { vgafc_init(&st->fc, &st->cal); st->fc_ready = 1; }
        vgafc_push(&st->fc, uo, c0 + done, n - done);
    }
    return 0;
}

static void print_sync(const char *name, const vgacal_sync_t *s, uint64_t phases) {
    printf("%s found=%u active_low=%u pulse=%" PRIu64 " period=%" PRIu64 " phases=%" PRIu64 "\n",
           name, s->found, s->active_low, s->pulse, s->period, phases);
}

int main(int argc, char **argv) {
    if (argc != 2) { fprintf(stderr, "usage: %s TRACE\n", argv[0]); return 2; }
    char err[256] = "";
    vgacap_trace_reader_t *r = vgacap_trace_open(argv[1], err, sizeof err);
    if (!r) { fprintf(stderr, "vgacap-calib-dump: %s\n", err); return 1; }
    const vgacap_trace_info_t *info = vgacap_trace_info(r);
    const vgacap_header_t *h = &info->header;
    char layout[64], secs[64];
    if (!info->open_meta || meta_get(info->open_meta, "layout", layout, sizeof layout) ||
        strcmp(layout, "tt-um-43") || meta_get(info->open_meta, "calib_seconds", secs, sizeof secs)) {
        fprintf(stderr, "vgacap-calib-dump: no META(open) with layout=tt-um-43 and calib_seconds\n");
        vgacap_trace_close(r); return 1;
    }
    if (h->version != 2 || h->sample_bits != 43 || h->samples_per_clock != 2 ||
        h->clock_bit != 42 || h->clock_first != 0) {
        fprintf(stderr, "vgacap-calib-dump: header disagrees with layout tt-um-43\n");
        vgacap_trace_close(r); return 1;
    }
    char *end;
    errno = 0;
    double calib_seconds = strtod(secs, &end);
    if (errno || *end) {
        fprintf(stderr, "vgacap-calib-dump: bad calib_seconds '%s'\n", secs);
        vgacap_trace_close(r); return 1;
    }

    state_t st;
    memset(&st, 0, sizeof st);
    vgacal_init(&st.cal, (double)h->clock_hz, calib_seconds);
    uint64_t clocks = info->samples / 2;
    if (vgacap_trace_read(r, 0, 2 * clocks, on_run, &st, err, sizeof err)) {
        fprintf(stderr, "vgacap-calib-dump: %s\n", err);
        vgacap_trace_close(r); return 1;
    }
    vgacap_trace_close(r);
    if (st.gap || st.next != clocks) {
        fprintf(stderr, "vgacap-calib-dump: runs do not cover the trace's clocks\n");
        return 1;
    }
    vgacal_end_of_trace(&st.cal, clocks);
    if (!st.fc_ready) vgafc_init(&st.fc, &st.cal);

    printf("capture_after_clock=%" PRIu64 "\n", st.cal.capture_after_clock);
    print_sync("hs", &st.cal.hs, st.cal.h_phases);
    print_sync("vs", &st.cal.vs, st.cal.v_phases);
    printf("ends=%zu\n", st.fc.n);
    for (size_t i = 0; i < st.fc.n; i++) printf("end=%" PRIu64 "\n", st.fc.ends[i]);
    vgacal_free(&st.cal);
    vgafc_free(&st.fc);
    return 0;
}
