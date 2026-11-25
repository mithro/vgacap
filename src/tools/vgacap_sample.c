// SPDX-License-Identifier: Apache-2.0
// vgacap-sample: a simulation trace to frames, with both samplers, in one pass.
//
//   vgacap-sample TRACE --out DIR --name PREFIX [--sampler tb-v1] [--sampler centre-v1]
//                 [--centre-window DX,DY] [--store-level N] [--png-fd FD]
//
// TRACE is a simulation trace (samples_per_clock 2, a clock bit, META(open)
// with layout=tt-um-43); a board capture is refused (exit 2). One
// vgacap_trace_read over the whole trace feeds, per run of clk-high samples,
// the shared calibration, then (from capture_after_clock) the frame clock,
// then the samplers asked for (default both), in the order vgacap/sample.h
// gives. centre-v1 is fed from clock 0.
//
// Per sampler, in DIR/<sampler>/:
//   PREFIX_<sampler>_frames.rgb6.zst   the distinct frames, first-appearance
//                                      order, one zstd stream (long-distance
//                                      matching, window log 27)
//   frames.txt, frame_hashes.sha256-rgb6.txt, and for tb-v1
//   frame_hashes.fnv1a64-tb2026-rgb888.txt   (docs/frames-format.md)
// --png-fd FD: each new distinct frame is also written to FD as the line
// "<sampler> <distinct> <width> <height>\n" and width * height rgb6 bytes,
// before its store bytes.
//
// clock_hz is the VGCH header's; calib_seconds, seconds and the files' id
// (project) are META(open)'s; status is META(close)'s, or "truncated" when the
// trace has none. A centre-v1 window override that breaks the window's
// bounds (geometry bad-window) is an error: every file is still written
// (centre-v1's with frames: 0), then the tool exits 1 naming the bound.
//
// Exit: 0 when everything asked for was written, no-picture runs included;
// 2 for usage errors and board captures; 1 for I/O and trace errors.
// VGACAP_SAMPLE_DEBUG=1 prints "trace_passes=1 blocks_decoded=<n>" to stderr,
// n being the blocks the pass decompressed (opening the trace decodes its
// last block as well; that is not counted).
#include "vgacap/outputs.h"
#include "vgacap/sample.h"
#include "vgacap/time.h"
#include "vgacap/trace.h"
#include "trace_read_stats.h"

#include <errno.h>
#include <fcntl.h>
#include <inttypes.h>
#include <math.h>
#include <signal.h>
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <unistd.h>

#include <sha256.h>
#include <zstd.h>

#define PROG "vgacap-sample"
#define PATH_LEN 4096
#define TEXT_LEN 1024

enum { S_TB, S_CENTRE, S_N };
static const char *const SAMPLER_NAMES[S_N] = { "tb-v1", "centre-v1" };

typedef struct tool tool_t;

typedef struct samp {
    int on, kind;
    tool_t *tool;
    char dir[PATH_LEN], store_file[PATH_LEN], store_path[PATH_LEN];
    // the store
    FILE *store; ZSTD_CCtx *cctx; uint8_t *zout; size_t zout_cap;
    SHA256_CTX store_sha; uint64_t store_len;
    // distinct frames: SHA-256 (and FNV for tb-v1) of each, in store order
    uint8_t (*sha)[32]; uint64_t *fnv; size_t nd, capd;
    uint32_t *ht; size_t ht_cap;        // open addressing: distinct index + 1, 0 empty
    vgaout_frame_t *frames; size_t nf, capf;
    uint16_t w, h;
    // The last frame's pixels and distinct index: a frame with the same
    // pixels has the same SHA-256, so it is that distinct frame again
    // without hashing (still and repeating pictures are the common case).
    uint8_t *last; int64_t last_d;
} samp_t;

struct tool {
    samp_t s[S_N];
    vgacal_t cal; vgafc_t fc; int fc_on;
    tbv1_t *tb; centrev1_t *cs; int started;
    double clock_hz, seconds;
    int dx, dy;
    uint64_t next;          // the next clock expected
    int gap, failed;
    char why[512];          // the first failure
    int png_fd;
    vgasample_run_t cruns[1024];   // a batch's clock runs (vgacap_trace_read_runs: at most 1,024 runs)
};

static void fail(tool_t *t, const char *fmt, ...) {
    if (t->failed) return;
    t->failed = 1;
    va_list ap; va_start(ap, fmt);
    vsnprintf(t->why, sizeof t->why, fmt, ap);
    va_end(ap);
}

static void *grow(void *p, size_t *cap, size_t need, size_t elem) {
    if (need <= *cap) return p;
    size_t c = *cap ? *cap : 64;
    while (c < need) c *= 2;
    void *q = realloc(p, c * elem);
    if (!q) { fprintf(stderr, PROG ": out of memory\n"); exit(1); }
    *cap = c;
    return q;
}

// ---- the PNG pipe and the store ----------------------------------------------

static int write_all(int fd, const void *buf, size_t len) {
    const uint8_t *p = buf;
    while (len) {
        ssize_t w = write(fd, p, len);
        if (w < 0) { if (errno == EINTR) continue; return -1; }
        p += w; len -= (size_t)w;
    }
    return 0;
}

static int store_open(samp_t *s, int level) {
    s->store = fopen(s->store_path, "wb");
    if (!s->store) return -1;
    s->cctx = ZSTD_createCCtx();
    s->zout_cap = ZSTD_CStreamOutSize();
    s->zout = malloc(s->zout_cap);
    if (!s->cctx || !s->zout) return -1;
    if (ZSTD_isError(ZSTD_CCtx_setParameter(s->cctx, ZSTD_c_compressionLevel, level)) ||
        ZSTD_isError(ZSTD_CCtx_setParameter(s->cctx, ZSTD_c_enableLongDistanceMatching, 1)) ||
        ZSTD_isError(ZSTD_CCtx_setParameter(s->cctx, ZSTD_c_windowLog, 27)))
        return -1;
    sha256_init(&s->store_sha);
    return 0;
}

// Compresses `len` bytes (mode ZSTD_e_continue) or ends the stream (ZSTD_e_end).
static int store_feed(samp_t *s, const uint8_t *p, size_t len, ZSTD_EndDirective mode) {
    ZSTD_inBuffer in = { p, len, 0 };
    for (;;) {
        ZSTD_outBuffer out = { s->zout, s->zout_cap, 0 };
        size_t left = ZSTD_compressStream2(s->cctx, &out, &in, mode);
        if (ZSTD_isError(left)) {
            fail(s->tool, "%s: zstd: %s", s->store_path, ZSTD_getErrorName(left));
            return -1;
        }
        if (out.pos && fwrite(s->zout, 1, out.pos, s->store) != out.pos) {
            fail(s->tool, "%s: write error: %s", s->store_path, strerror(errno));
            return -1;
        }
        if (mode == ZSTD_e_end ? left == 0 : in.pos == in.size) return 0;
    }
}

// ---- frames ------------------------------------------------------------------

static uint64_t key64(const uint8_t sha[32]) {
    uint64_t k; memcpy(&k, sha, 8); return k;
}

// The distinct index of `sha`, or -1 (inserting nothing).
static int64_t lookup(const samp_t *s, const uint8_t sha[32]) {
    if (!s->ht_cap) return -1;
    size_t mask = s->ht_cap - 1;
    for (size_t i = (size_t)key64(sha) & mask;; i = (i + 1) & mask) {
        uint32_t v = s->ht[i];
        if (!v) return -1;
        if (!memcmp(s->sha[v - 1], sha, 32)) return (int64_t)v - 1;
    }
}

static void ht_put(samp_t *s, size_t idx) {
    if (2 * (s->nd + 1) > s->ht_cap) {           // keep the load at or under 1/2
        size_t cap = s->ht_cap ? 2 * s->ht_cap : 1024;
        uint32_t *ht = calloc(cap, sizeof *ht);
        if (!ht) { fprintf(stderr, PROG ": out of memory\n"); exit(1); }
        for (size_t j = 0; j < s->ht_cap; j++) {
            uint32_t v = s->ht[j];
            if (!v) continue;
            size_t i = (size_t)key64(s->sha[v - 1]) & (cap - 1);
            while (ht[i]) i = (i + 1) & (cap - 1);
            ht[i] = v;
        }
        free(s->ht); s->ht = ht; s->ht_cap = cap;
    }
    size_t mask = s->ht_cap - 1, i = (size_t)key64(s->sha[idx]) & mask;
    while (s->ht[i]) i = (i + 1) & mask;
    s->ht[i] = (uint32_t)(idx + 1);
}

// Frame f->number is distinct frame d (gaps before it are -1).
static void frame_record(samp_t *s, const vgasample_frame_t *f, int64_t d) {
    s->frames = grow(s->frames, &s->capf, (size_t)f->number + 1, sizeof *s->frames);
    while (s->nf < f->number) {
        vgaout_frame_t gap = { -1, 0, 0 };
        s->frames[s->nf++] = gap;
    }
    vgaout_frame_t rec = { d, f->start_clock, f->missing };
    s->frames[s->nf++] = rec;
}

static void on_frame(void *user, const vgasample_frame_t *f) {
    samp_t *s = user;
    tool_t *t = s->tool;
    if (t->failed) return;
    size_t n = (size_t)f->width * f->height;
    if (s->nf == 0 && s->nd == 0) { s->w = f->width; s->h = f->height; }
    if (f->width != s->w || f->height != s->h) {
        fail(t, "%s: frame %" PRIu64 " is %ux%u, earlier frames %ux%u", SAMPLER_NAMES[s->kind],
             f->number, f->width, f->height, s->w, s->h);
        return;
    }
    if (f->number < s->nf) {
        fail(t, "%s: frame %" PRIu64 " after frame %zu", SAMPLER_NAMES[s->kind], f->number, s->nf - 1);
        return;
    }
    if (s->last && !memcmp(s->last, f->rgb6, n)) {
        frame_record(s, f, s->last_d);
        return;
    }
    uint8_t sha[32];
    vgahash_sha256_rgb6(f->rgb6, n, sha);
    int64_t d = lookup(s, sha);
    if (d < 0) {
        d = (int64_t)s->nd;
        if (s->nd >= UINT32_MAX - 1) { fail(t, "%s: too many distinct frames", SAMPLER_NAMES[s->kind]); return; }
        s->sha = grow(s->sha, &s->capd, s->nd + 1, sizeof *s->sha);
        if (s->kind == S_TB) {
            size_t cap = s->capd;             // fnv tracks sha's capacity
            s->fnv = realloc(s->fnv, cap * sizeof *s->fnv);
            if (!s->fnv) { fprintf(stderr, PROG ": out of memory\n"); exit(1); }
            s->fnv[s->nd] = vgahash_fnv1a64_tb2026_rgb888(f->rgb6, n);
        }
        memcpy(s->sha[s->nd], sha, 32);
        ht_put(s, s->nd);
        s->nd++;
        if (t->png_fd >= 0) {
            char line[96];
            int ll = snprintf(line, sizeof line, "%s %" PRId64 " %u %u\n", SAMPLER_NAMES[s->kind], d,
                              f->width, f->height);
            if (write_all(t->png_fd, line, (size_t)ll) || write_all(t->png_fd, f->rgb6, n)) {
                fail(t, "--png-fd %d: write error: %s", t->png_fd, strerror(errno));
                return;
            }
        }
        if (store_feed(s, f->rgb6, n, ZSTD_e_continue)) return;
        sha256_update(&s->store_sha, f->rgb6, n);
        s->store_len += n;
    }
    if (!s->last && !(s->last = malloc(n))) { fprintf(stderr, PROG ": out of memory\n"); exit(1); }
    memcpy(s->last, f->rgb6, n);
    s->last_d = d;
    frame_record(s, f, d);
}

// ---- the pass ----------------------------------------------------------------

static void start_capture(tool_t *t) {
    vgafc_init(&t->fc, &t->cal);
    t->fc_on = 1;
    if (t->s[S_TB].on)
        t->tb = tbv1_create(&t->cal, &t->fc, t->clock_hz, t->seconds, on_frame, &t->s[S_TB]);
    if (t->cs) centrev1_calibrated(t->cs, &t->cal, &t->fc);
    t->started = 1;
}

// A run of samples [s, s + r) holds the clk-high samples 2c + 1 of clocks
// [s >> 1, (s + r) >> 1) (clock_first 0); a run of only clk-low samples
// holds none.
static inline int on_run(tool_t *t, uint64_t s, uint64_t value, uint64_t r) {
    if (t->failed) return 1;
    uint64_t c0 = s >> 1, c1 = (s + r) >> 1;
    if (c1 <= c0) return 0;
    if (c0 != t->next) { t->gap = 1; return 1; }
    t->next = c1;
    uint8_t uo = (uint8_t)(value & 0xFF), rst = (uint8_t)((value >> 40) & 1);
    uint64_t n = c1 - c0, pre = 0;
    if (t->cal.state == VGACAL_RUNNING) pre = vgacal_push(&t->cal, uo, rst, c0, n);
    if (pre && t->cs) centrev1_push(t->cs, uo, c0, pre);
    if (t->cal.state == VGACAL_DONE) {
        if (!t->started) start_capture(t);
        if (pre < n) {
            vgafc_push(&t->fc, uo, c0 + pre, n - pre);
            if (t->tb) tbv1_push(t->tb, uo, c0 + pre, n - pre);
            if (t->cs) centrev1_push(t->cs, uo, c0 + pre, n - pre);
        }
    }
    return t->failed;
}

// Clock runs after the capture started: all of them to the frame clock,
// then to the samplers, one call a sampler. tb-v1 emits a frame only at a
// frame-clock end, at the run that starts there, and per run it went before
// centre-v1; so the runs are cut at each end, and up to it tb-v1 then
// centre-v1 take them. Every frame (and so the PNG pipe) comes out in the
// order run-at-a-time feeding gives.
static void dispatch(tool_t *t, const vgasample_run_t *r, size_t k) {
    if (!k) return;
    size_t e = t->fc.n, a = 0;
    vgafc_push_runs(&t->fc, r, k);
    if (t->tb && t->cs) {
        for (; e < t->fc.n; e++) {
            const uint64_t c = t->fc.ends[e];
            size_t j = a;
            while (j < k && r[j].first + r[j].clocks <= c) j++;
            if (j > a) {
                tbv1_push_runs(t->tb, r + a, j - a);
                centrev1_push_runs(t->cs, r + a, j - a);
                a = j;
            }
        }
    }
    if (t->tb) tbv1_push_runs(t->tb, r + a, k - a);
    if (t->cs) centrev1_push_runs(t->cs, r + a, k - a);
}

static int on_runs(void *user, const vgacap_trace_run_t *runs, size_t n) {
    tool_t *t = user;
    size_t i = 0;
    for (; i < n && !t->started; i++)          // calibration: run by run
        if (on_run(t, runs[i].first_sample, runs[i].value, runs[i].run)) return 1;
    size_t k = 0;
    int stop = 0;
    for (; i < n; i++) {
        const uint64_t c0 = runs[i].first_sample >> 1, c1 = (runs[i].first_sample + runs[i].run) >> 1;
        if (c1 <= c0) continue;
        if (c0 != t->next) { t->gap = 1; stop = 1; break; }
        t->next = c1;
        t->cruns[k].first = c0; t->cruns[k].clocks = c1 - c0; t->cruns[k].uo_out = (uint8_t)(runs[i].value & 0xFF);
        k++;
    }
    dispatch(t, t->cruns, k);
    return stop || t->failed;
}

// ---- arguments and META ------------------------------------------------------

static int usage(const char *fmt, ...) {
    fprintf(stderr, PROG ": ");
    va_list ap; va_start(ap, fmt);
    vfprintf(stderr, fmt, ap);
    va_end(ap);
    fprintf(stderr, "\nusage: " PROG " TRACE --out DIR --name PREFIX [--sampler tb-v1] [--sampler centre-v1]\n"
                    "       [--centre-window DX,DY] [--store-level N] [--png-fd FD]\n");
    return 2;
}

static int parse_long(const char *s, long lo, long hi, long *out) {
    char *end;
    errno = 0;
    long v = strtol(s, &end, 10);
    if (errno || end == s || *end || v < lo || v > hi) return -1;
    *out = v;
    return 0;
}

static int parse_double(const char *s, double *out) {
    char *end;
    errno = 0;
    *out = strtod(s, &end);
    return errno || end == s || *end ? -1 : 0;
}

// A META seconds value: a number strtod reads whole, finite and >= 0.
static int parse_seconds(const char *s, double *out) {
    return parse_double(s, out) || !isfinite(*out) || *out < 0 ? -1 : 0;
}

// META's value for `key` into out; 0, or -1 if absent, empty or too long.
static int meta_get(const char *meta, const char *key, char *out, size_t outlen) {
    size_t kl = strlen(key);
    for (const char *p = meta; p && *p; ) {
        const char *nl = strchr(p, '\n');
        size_t ll = nl ? (size_t)(nl - p) : strlen(p);
        if (ll > kl + 1 && !strncmp(p, key, kl) && p[kl] == '=') {
            size_t vl = ll - kl - 1;
            if (vl >= outlen) return -1;
            memcpy(out, p + kl + 1, vl); out[vl] = 0;
            return 0;
        }
        p = nl ? nl + 1 : NULL;
    }
    return -1;
}

static int make_dir(const char *path) {
    if (mkdir(path, 0777) == 0 || errno == EEXIST) {
        struct stat st;
        if (stat(path, &st) == 0 && S_ISDIR(st.st_mode)) return 0;
        errno = ENOTDIR;
    }
    return -1;
}

// ---- outputs -----------------------------------------------------------------

static int write_text(tool_t *t, samp_t *s, const char *file, const char *method, const vgaout_t *o) {
    char path[PATH_LEN + 64];
    if ((size_t)snprintf(path, sizeof path, "%s/%s", s->dir, file) >= sizeof path) {
        fail(t, "output path too long"); return -1;
    }
    FILE *f = fopen(path, "w");
    if (!f) { fail(t, "cannot create %s: %s", path, strerror(errno)); return -1; }
    int rc = method ? vgaout_hash_file(f, method, o) : vgaout_frames_txt(f, o);
    if (fclose(f) || rc) { fail(t, "write error on %s", path); return -1; }
    return 0;
}

static int finish_sampler(tool_t *t, samp_t *s, const char *id, const char *status,
                          const vgasample_geometry_t *g, const centrev1_diag_t *d) {
    if (store_feed(s, NULL, 0, ZSTD_e_end)) return -1;
    if (fclose(s->store)) { s->store = NULL; fail(t, "write error on %s: %s", s->store_path, strerror(errno)); return -1; }
    s->store = NULL;
    uint8_t digest[32];
    sha256_final(&s->store_sha, digest);
    char hex[65];
    for (int i = 0; i < 32; i++) snprintf(hex + 2 * i, 3, "%02x", digest[i]);
    vgaout_t o;
    memset(&o, 0, sizeof o);
    o.id = id; o.status = status; o.sampler = SAMPLER_NAMES[s->kind];
    o.sampler_status = g->status; o.mode = g->mode;
    o.width = g->ok ? g->width : 0; o.height = g->ok ? g->height : 0;
    o.clock_hz = (uint32_t)t->clock_hz; o.clocks_per_pixel = g->cpp;
    o.capture_after_clock = t->cal.capture_after_clock;
    o.store = s->store_file; o.store_length = s->store_len; o.store_sha256 = hex;
    if (d) vgaout_set_centre(&o, g, d);
    o.n_distinct = s->nd; o.sha256 = (const uint8_t (*)[32])s->sha; o.fnv = s->fnv;
    o.n_frames = s->nf; o.frames = s->frames;
    if (write_text(t, s, "frames.txt", NULL, &o) ||
        write_text(t, s, "frame_hashes.sha256-rgb6.txt", "sha256-rgb6", &o))
        return -1;
    if (s->kind == S_TB &&
        write_text(t, s, "frame_hashes.fnv1a64-tb2026-rgb888.txt", "fnv1a64-tb2026-rgb888", &o))
        return -1;
    return 0;
}

// Which of the bounds (vgacap/sample.h, centre-v1's window) the override
// breaks, from the window the timing gives (a centre-v1 without override).
static void bad_window_message(tool_t *t, char *msg, size_t len) {
    centrev1_t *base = centrev1_create(0, 0, NULL, NULL);
    centrev1_calibrated(base, &t->cal, &t->fc);
    const vgasample_geometry_t *g = centrev1_geometry(base);
    uint64_t L = t->cal.hs.period;
    uint64_t lines = L ? (t->cal.vs.period + L / 2) / L : 0, vpl = L ? (t->cal.vs.pulse + L / 2) / L : 0;
    int64_t hb = (int64_t)g->hback + t->dx, vb = (int64_t)g->vback + t->dy;
    double hsync = (double)t->cal.hs.pulse / g->cpp;
    double a = (hsync + (double)hb) * g->cpp, b = a + (double)g->width * g->cpp;
    int64_t end = (int64_t)ceil(b), rows_end = (int64_t)vpl + vb + g->height;
    if (!g->ok) snprintf(msg, len, "the timing gives no window (%s)", g->status);
    else if (hb < 0)
        snprintf(msg, len, "the window would start inside the hsync pulse (back porch %d%+d = %" PRId64 " pixels)",
                 g->hback, t->dx, hb);
    else if (vb < 0)
        snprintf(msg, len, "the window would start inside the vsync pulse (back porch %d%+d = %" PRId64 " lines)",
                 g->vback, t->dy, vb);
    else if (end > (int64_t)L)
        snprintf(msg, len, "the window would run past the end of the line (it ends at clock %" PRId64
                 " of a %" PRIu64 "-clock line)", end, L);
    else if (rows_end > (int64_t)lines)
        snprintf(msg, len, "the window would run past the end of the frame (it ends at line %" PRId64
                 " of a %" PRIu64 "-line frame)", rows_end, lines);
    else snprintf(msg, len, "the window breaks its bounds");
    centrev1_free(base);
}

int main(int argc, char **argv) {
    const char *trace = NULL, *out = NULL, *name = NULL;
    int want[S_N] = { 0, 0 }, any = 0;
    long level = 3, png_fd = -1, dx = 0, dy = 0;
    for (int i = 1; i < argc; i++) {
        const char *a = argv[i];
        if (a[0] != '-' || !a[1]) {
            if (trace) return usage("one TRACE only ('%s')", a);
            trace = a; continue;
        }
        if (i + 1 >= argc) return usage("%s needs a value", a);
        const char *v = argv[++i];
        if (!strcmp(a, "--out")) out = v;
        else if (!strcmp(a, "--name")) name = v;
        else if (!strcmp(a, "--sampler")) {
            int k = !strcmp(v, "tb-v1") ? S_TB : !strcmp(v, "centre-v1") ? S_CENTRE : -1;
            if (k < 0) return usage("unknown sampler '%s' (tb-v1 or centre-v1)", v);
            want[k] = 1; any = 1;
        } else if (!strcmp(a, "--centre-window")) {
            char buf[64];
            const char *comma = strchr(v, ',');
            if (!comma || (size_t)(comma - v) >= sizeof buf) return usage("--centre-window wants DX,DY, not '%s'", v);
            memcpy(buf, v, (size_t)(comma - v)); buf[comma - v] = 0;
            if (parse_long(buf, -100000, 100000, &dx) || parse_long(comma + 1, -100000, 100000, &dy))
                return usage("--centre-window wants DX,DY, not '%s'", v);
        } else if (!strcmp(a, "--store-level")) {
            if (parse_long(v, ZSTD_minCLevel(), ZSTD_maxCLevel(), &level))
                return usage("--store-level wants a zstd level %d..%d, not '%s'", ZSTD_minCLevel(), ZSTD_maxCLevel(), v);
        } else if (!strcmp(a, "--png-fd")) {
            if (parse_long(v, 0, 1 << 20, &png_fd) || fcntl((int)png_fd, F_GETFD) < 0)
                return usage("--png-fd wants an open file descriptor, not '%s'", v);
        } else return usage("unknown option '%s'", a);
    }
    if (!trace || !out || !name) return usage("TRACE, --out and --name are required");
    if (!*name || strpbrk(name, "/\\ \t\n\r")) return usage("--name wants a bare file-name prefix, not '%s'", name);
    if (!any) want[S_TB] = want[S_CENTRE] = 1;

    char err[256] = "";
    vgacap_trace_reader_t *r = vgacap_trace_open(trace, err, sizeof err);
    if (!r) { fprintf(stderr, PROG ": %s\n", err); return 1; }
    const vgacap_trace_info_t *info = vgacap_trace_info(r);
    const vgacap_header_t *h = &info->header;
    char layout[64], project[TEXT_LEN], csecs[64], secs[64], status[TEXT_LEN];
    if (h->version != 2 || h->samples_per_clock != 2 || !info->open_meta ||
        meta_get(info->open_meta, "layout", layout, sizeof layout) || strcmp(layout, "tt-um-43")) {
        fprintf(stderr, PROG ": %s is not a simulation trace (needs samples_per_clock 2 and layout=tt-um-43); "
                "board captures are decoded with vgacap-frames\n", trace);
        vgacap_trace_close(r); return 2;
    }
    if (h->sample_bits != 43 || h->clock_bit != 42 || h->clock_first != 0) {
        fprintf(stderr, PROG ": %s: header disagrees with layout tt-um-43 (bits %u, clock bit %u, clock first %u)\n",
                trace, h->sample_bits, h->clock_bit, h->clock_first);
        vgacap_trace_close(r); return 1;
    }
    tool_t *t = calloc(1, sizeof *t);
    if (!t) { fprintf(stderr, PROG ": out of memory\n"); return 1; }
    t->png_fd = (int)png_fd; t->dx = (int)dx; t->dy = (int)dy;
    t->clock_hz = (double)h->clock_hz;
    double calib_seconds = 0;
    if (meta_get(info->open_meta, "project", project, sizeof project) ||
        meta_get(info->open_meta, "calib_seconds", csecs, sizeof csecs) ||
        meta_get(info->open_meta, "seconds", secs, sizeof secs)) {
        fprintf(stderr, PROG ": %s: META(open) needs project, calib_seconds and seconds\n", trace);
        vgacap_trace_close(r); return 1;
    }
    // Checked before the library casts them to uint64 (calib_limit, the
    // frame target): a negative or non-finite value is undefined there.
    const char *bad_key = parse_seconds(csecs, &calib_seconds) ? "calib_seconds"
                        : parse_seconds(secs, &t->seconds) ? "seconds" : NULL;
    if (bad_key) {
        fprintf(stderr, PROG ": %s: META(open) %s '%s' is not a finite number >= 0\n", trace, bad_key,
                bad_key[0] == 'c' ? csecs : secs);
        vgacap_trace_close(r); return 1;
    }
    if (!info->close_meta) snprintf(status, sizeof status, "truncated");
    else if (meta_get(info->close_meta, "status", status, sizeof status)) {
        fprintf(stderr, PROG ": %s: META(close) has no status\n", trace);
        vgacap_trace_close(r); return 1;
    }
    if (t->png_fd >= 0) signal(SIGPIPE, SIG_IGN);   // a closed pipe is a write error, reported

    if (make_dir(out)) { fprintf(stderr, PROG ": cannot create %s: %s\n", out, strerror(errno)); return 1; }
    for (int k = 0; k < S_N; k++) {
        samp_t *s = &t->s[k];
        s->kind = k; s->tool = t; s->on = want[k];
        if (!s->on) continue;
        if ((size_t)snprintf(s->dir, sizeof s->dir, "%s/%s", out, SAMPLER_NAMES[k]) >= sizeof s->dir ||
            (size_t)snprintf(s->store_file, sizeof s->store_file, "%s_%s_frames.rgb6.zst", name,
                             SAMPLER_NAMES[k]) >= sizeof s->store_file ||
            (size_t)snprintf(s->store_path, sizeof s->store_path, "%s/%s/%s_%s_frames.rgb6.zst", out, SAMPLER_NAMES[k],
                             name, SAMPLER_NAMES[k]) >= sizeof s->store_path) {
            fprintf(stderr, PROG ": output path too long\n"); return 1;
        }
        if (make_dir(s->dir)) { fprintf(stderr, PROG ": cannot create %s: %s\n", s->dir, strerror(errno)); return 1; }
        if (store_open(s, (int)level)) {
            fprintf(stderr, PROG ": cannot create %s: %s\n", s->store_path, errno ? strerror(errno) : "zstd setup failed");
            return 1;
        }
    }

    vgacal_init(&t->cal, t->clock_hz, calib_seconds);
    if (want[S_CENTRE]) t->cs = centrev1_create(t->dx, t->dy, on_frame, &t->s[S_CENTRE]);
    uint64_t clocks = info->samples / 2;     // a last clk-low sample alone is no clock
    // blocks_decoded counts the pass alone: opening decodes the last block too.
    vgacap_trace_read_stats_t before, after;
    vgacap_trace_read_stats(r, &before);
    int passes = 1;
    int rc = vgacap_trace_read_runs(r, 0, info->samples, on_runs, t, err, sizeof err);
    if (rc) { fprintf(stderr, PROG ": %s: %s\n", trace, err); return 1; }
    if (t->failed) { fprintf(stderr, PROG ": %s\n", t->why); return 1; }
    if (t->gap || t->next != clocks) {
        fprintf(stderr, PROG ": %s: the runs do not cover the trace's %" PRIu64 " clocks\n", trace, clocks);
        return 1;
    }
    vgacap_trace_read_stats(r, &after);
    if (getenv("VGACAP_SAMPLE_DEBUG") && !strcmp(getenv("VGACAP_SAMPLE_DEBUG"), "1"))
        fprintf(stderr, "trace_passes=%d blocks_decoded=%" PRIu64 "\n", passes, after.blocks_decoded - before.blocks_decoded);
    vgacal_end_of_trace(&t->cal, clocks);
    if (!t->started) start_capture(t);

    // tb-v1's files first, then centre-v1's (written on every path).
    if (t->s[S_TB].on && finish_sampler(t, &t->s[S_TB], project, status, tbv1_geometry(t->tb), NULL)) {
        fprintf(stderr, PROG ": %s\n", t->why); return 1;
    }
    int bad_window = 0;
    char bw[512] = "";
    if (t->cs) {
        centrev1_diag_t d;
        centrev1_finish(t->cs, &d);
        const vgasample_geometry_t *g = centrev1_geometry(t->cs);
        if (finish_sampler(t, &t->s[S_CENTRE], project, status, g, &d)) {
            fprintf(stderr, PROG ": %s\n", t->why); return 1;
        }
        if (!strcmp(g->status, "bad-window")) { bad_window = 1; bad_window_message(t, bw, sizeof bw); }
    }
    if (t->png_fd >= 0 && close(t->png_fd)) {
        fprintf(stderr, PROG ": --png-fd %d: %s\n", t->png_fd, strerror(errno)); return 1;
    }
    if (bad_window) {
        fprintf(stderr, PROG ": --centre-window %d,%d cannot be used: %s; centre-v1 wrote no frames\n",
                t->dx, t->dy, bw);
        return 1;
    }
    for (int k = 0; k < S_N; k++) {
        samp_t *s = &t->s[k];
        ZSTD_freeCCtx(s->cctx); free(s->zout); free(s->sha); free(s->fnv); free(s->ht); free(s->frames); free(s->last);
    }
    if (t->tb) tbv1_free(t->tb);
    if (t->cs) centrev1_free(t->cs);
    vgacal_free(&t->cal);
    if (t->fc_on) vgafc_free(&t->fc);
    vgacap_trace_close(r);
    free(t);
    return 0;
}
