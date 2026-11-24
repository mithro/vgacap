// SPDX-License-Identifier: Apache-2.0
// centre-v1 (src/sample/centrev1.c): libvgaframe's lines and frames, the
// sampler's own mode, cpp, window and mid-pixel pick, frame numbers from the
// shared frame clock, and the window diagnostics. Fed generated clk-high
// uo_out from clock 0, beside the shared calibration, the frame clock and
// (for the numbering tests) tb-v1. python/tests/test_samplers.py compares
// the window and pick with python/vgacap/centre.py and runs the playground
// phase.
#include "harness.h"
#include "synth.h"
#include "vgacap/sample.h"
#include "vgacap/time.h"
#include <math.h>
#include <stdint.h>
#include <stdlib.h>

#define HZ 25175000.0
#define RESET 10u            // tb.cpp's reset: rst_n low for clocks 0-9

// ---- generated designs: uo_out of clock c (rst_n is c >= RESET) -----------

typedef struct gen {
    const uint32_t *frame; uint64_t frame_len;   // one frame of design pixels from (hpos, vpos) = (0, 0)
    uint64_t num, den;                           // num / den clocks per design pixel
    uint64_t glitch_v;                           // a clock whose vsync bit is inverted (0: none)
    int kind;                                    // 0: frame, 1: constant 0x88, 2: vsync = hsync
} gen_t;

// Design pixel p covers clocks [floor(p * cpp), floor((p + 1) * cpp)) from
// the end of reset, so clock t is in the largest p with p * cpp < t + 1.
static uint8_t gen_uo(const gen_t *g, uint64_t c) {
    if (c < RESET) return 0;
    uint8_t uo;
    if (g->kind == 1) uo = 0x88;
    else if (g->kind == 2) uo = (c % 8) >= 2 ? 0x88 : 0;
    else {
        uint64_t t = c - RESET;
        uint64_t p = ((t + 1) * g->den + g->num - 1) / g->num - 1;
        uo = (uint8_t)g->frame[p % g->frame_len];
    }
    if (g->glitch_v && c == g->glitch_v) uo ^= 0x08;
    return uo;
}

static uint8_t rgb6(uint8_t uo) {
    unsigned r = ((uo & 1u) << 1) | ((uo >> 4) & 1u);
    unsigned g = (((uo >> 1) & 1u) << 1) | ((uo >> 5) & 1u);
    unsigned b = (((uo >> 2) & 1u) << 1) | ((uo >> 6) & 1u);
    return (uint8_t)((r << 4) | (g << 2) | b);
}

typedef struct got {
    size_t n, cap;
    uint64_t *number, *start; uint32_t *missing;
    uint8_t **rgb6;
    uint16_t width, height;
} got_t;

static void on_frame(void *user, const vgasample_frame_t *f) {
    got_t *g = user;
    if (g->n == g->cap) {
        g->cap = g->cap ? 2 * g->cap : 16;
        g->number = realloc(g->number, g->cap * sizeof *g->number);
        g->start = realloc(g->start, g->cap * sizeof *g->start);
        g->missing = realloc(g->missing, g->cap * sizeof *g->missing);
        g->rgb6 = realloc(g->rgb6, g->cap * sizeof *g->rgb6);
        if (!g->number || !g->start || !g->missing || !g->rgb6) abort();
    }
    size_t sz = (size_t)f->width * f->height;
    g->rgb6[g->n] = malloc(sz ? sz : 1);
    if (!g->rgb6[g->n]) abort();
    memcpy(g->rgb6[g->n], f->rgb6, sz);
    g->number[g->n] = f->number;
    g->start[g->n] = f->start_clock;
    g->missing[g->n] = f->missing;
    g->width = f->width; g->height = f->height;
    g->n++;
}

static void got_free(got_t *g) {
    for (size_t i = 0; i < g->n; i++) free(g->rgb6[i]);
    free(g->number); free(g->start); free(g->missing); free(g->rgb6);
    memset(g, 0, sizeof *g);
}

typedef struct run {
    vgacal_t cal; vgafc_t fc; centrev1_t *cs; tbv1_t *tb;
    vgasample_geometry_t geo;
    centrev1_diag_t diag;
    uint64_t L, lines, vpl;      // the calibration's line, frame and vsync pulse
    got_t got, tgot;             // centre-v1's frames, tb-v1's
} run_t;

static void start_capture(run_t *r) {
    vgafc_init(&r->fc, &r->cal);
    centrev1_calibrated(r->cs, &r->cal, &r->fc);
    r->tb = tbv1_create(&r->cal, &r->fc, HZ, 60.0, on_frame, &r->tgot);
}

// Clocks [0, clocks) as maximal runs: centre-v1 gets every clock; the
// calibration the clocks up to capture_after_clock; the frame clock (first)
// and tb-v1 the rest.
static void feed(const gen_t *g, uint64_t clocks, double calib_seconds, int dx, int dy, run_t *r) {
    memset(r, 0, sizeof *r);
    vgacal_init(&r->cal, HZ, calib_seconds);
    r->cs = centrev1_create(dx, dy, on_frame, &r->got);
    int started = 0;
    uint64_t c = 0;
    while (c < clocks) {
        uint8_t uo = gen_uo(g, c), rst = c >= RESET;
        uint64_t n = 1;
        while (c + n < clocks && gen_uo(g, c + n) == uo && (c + n >= RESET) == rst) n++;
        uint64_t pre = 0;
        if (r->cal.state == VGACAL_RUNNING) pre = vgacal_push(&r->cal, uo, rst, c, n);
        if (pre) centrev1_push(r->cs, uo, c, pre);
        if (r->cal.state == VGACAL_DONE) {
            if (!started) { start_capture(r); started = 1; }
            if (pre < n) {
                vgafc_push(&r->fc, uo, c + pre, n - pre);
                centrev1_push(r->cs, uo, c + pre, n - pre);
                tbv1_push(r->tb, uo, c + pre, n - pre);
            }
        }
        c += n;
    }
    vgacal_end_of_trace(&r->cal, clocks);
    if (!started) start_capture(r);
    centrev1_finish(r->cs, &r->diag);
    r->geo = *centrev1_geometry(r->cs);
    r->L = r->cal.hs.period;
    if (r->L) {
        r->lines = (r->cal.vs.period + r->L / 2) / r->L;
        r->vpl = (r->cal.vs.pulse + r->L / 2) / r->L;
    }
}

static void run_free(run_t *r) {
    centrev1_free(r->cs);
    tbv1_free(r->tb);
    vgacal_free(&r->cal); vgafc_free(&r->fc);
    got_free(&r->got); got_free(&r->tgot);
}

// Pixels of frame i that differ from the clock the rule names:
// x = floor((hsync + hback' + p + 0.5) * cpp) of row vpulse_lines + vback'
// + y, counted from the frame's start clock in whole L-clock lines (every
// generated line is exactly L clocks).
static uint64_t clock_mismatches(const run_t *r, const gen_t *g, size_t i) {
    const double cpp = r->geo.cpp, hsync = (double)r->cal.hs.pulse / cpp;
    uint64_t bad = 0;
    for (uint32_t y = 0; y < r->geo.height; y++) {
        uint64_t row = r->vpl + (uint64_t)((int64_t)r->geo.vback + y);
        for (uint32_t p = 0; p < r->geo.width; p++) {
            uint32_t x = (uint32_t)floor((hsync + r->geo.hback + p + 0.5) * cpp);
            uint8_t want = rgb6(gen_uo(g, r->got.start[i] + row * r->L + x));
            if (r->got.rgb6[i][(size_t)y * r->geo.width + p] != want) bad++;
        }
    }
    return bad;
}

static uint32_t *frame_buf;
static vgaframe_mode_t mode640;

static uint8_t diag(void *user, uint16_t x, uint16_t y) { (void)user; return (uint8_t)((x + 3 * y) & 0x3F); }
// Never black, so the whole active area is lit.
static uint8_t full(void *user, uint16_t x, uint16_t y) { (void)user; return (uint8_t)(1 + (x + 3 * y) % 63); }

static gen_t frame_gen(const vgaframe_mode_t *m, uint64_t num, uint64_t den,
                       uint8_t (*pix)(void *, uint16_t, uint16_t)) {
    uint64_t len = (uint64_t)vgaframe_mode_line_px(m) * vgaframe_mode_frame_lines(m);
    free(frame_buf);
    frame_buf = malloc(len * sizeof *frame_buf);
    synth_frame_hvsync(m, pix, NULL, frame_buf, (size_t)len);
    gen_t g = { frame_buf, len, num, den, 0, 0 };
    return g;
}

// Clocks for `frames` whole frames of design m at num / den, after reset.
static uint64_t frames_clocks(const vgaframe_mode_t *m, uint64_t num, uint64_t den, double frames) {
    double per = (double)vgaframe_mode_line_px(m) * vgaframe_mode_frame_lines(m) * (double)num / (double)den;
    return RESET + (uint64_t)(frames * per);
}

static int cpp_is(const vgasample_geometry_t *geo, const char *want) {
    char buf[64];
    if (vgacap_fmt_double(buf, sizeof buf, geo->cpp) < 0) return 0;
    if (strcmp(buf, want)) { fprintf(stderr, "  cpp %s, expected %s\n", buf, want); return 0; }
    return 1;
}

// ---- tests -----------------------------------------------------------------

// The clocks-per-pixel rule, step by step, on the measured line.
TEST(centre_cpp_rule) {
    static const struct {
        vgaframe_mode_t m; uint64_t num, den;
        const char *mode, *cpp; int hback, vback;
    } cases[] = {
        // nyan: 832-clock lines, 96-clock hsync, 525 lines: cpp_l 1.04, step 2
        // gives 1; B = 832 - 96 - 640 = 96, hback' = floor(96 * 48 / 64 + 0.5).
        { { "nyan", 640, 24, 96, 72, 480, 10, 2, 33, 0, 0, 0 }, 1, 1, "640x480@60", "1", 72, 33 },
        // rle_vga: 800 with 64, 500 lines: 640x480@75 at 0.9524, step 2 gives
        // 1; B = 96, hback' = floor(96 * 120 / 136 + 0.5) = 85.
        { { "rle", 640, 16, 64, 80, 480, 1, 3, 16, 0, 0, 0 }, 1, 1, "640x480@75", "1", 85, 16 },
        // mandelbrot: 3,584 with 256, 500 lines: 4.2667, step 2 gives 4;
        // B = 896 - 64 - 640 = 192, hback' = floor(192 * 120 / 136 + 0.5).
        { { "mandel", 640, 16, 64, 176, 480, 1, 3, 16, 0, 0, 0 }, 4, 1, "640x480@75", "4", 169, 16 },
        // histefan: 800 with 97, 525 lines: step 1 gives 1; B = 63,
        // hback' = floor(63 * 48 / 64 + 0.5) = 47.
        { { "h97", 640, 16, 97, 47, 480, 10, 2, 33, 0, 0, 0 }, 1, 1, "640x480@60", "1", 47, 33 },
        // 1,525 with 183, 525 lines: |183 - 2 * 96| = 9, so step 3: 1.90625.
        { { "frac", 640, 16, 96, 48, 480, 10, 2, 33, 0, 0, 0 }, 61, 32, "640x480@60", "1.90625", 48, 33 },
    };
    for (size_t i = 0; i < sizeof cases / sizeof cases[0]; i++) {
        gen_t g = frame_gen(&cases[i].m, cases[i].num, cases[i].den, diag);
        run_t r;
        feed(&g, frames_clocks(&cases[i].m, cases[i].num, cases[i].den, 6), 1.0, 0, 0, &r);
        fprintf(stderr, "  case %s: L %llu pulse %llu lines %llu\n", cases[i].m.name,
                (unsigned long long)r.L, (unsigned long long)r.cal.hs.pulse, (unsigned long long)r.lines);
        ASSERT_EQ_U(r.geo.ok, 1);
        ASSERT_TRUE(!strcmp(r.geo.status, "ok"));
        ASSERT_TRUE(!strcmp(r.geo.mode, cases[i].mode));
        ASSERT_TRUE(cpp_is(&r.geo, cases[i].cpp));
        ASSERT_EQ_U(r.geo.width, 640); ASSERT_EQ_U(r.geo.height, 480);
        ASSERT_EQ_U(r.geo.hback, cases[i].hback); ASSERT_EQ_U(r.geo.vback, cases[i].vback);
        run_free(&r);
    }
}

// Standard porches at cpp 1, 2 and 1.90625: every frame is the design's
// picture, nothing missing, nothing lit outside the window.
TEST(centre_pixel_exact) {
    static const struct { uint64_t num, den; const char *cpp; int whole; } cases[] = {
        { 1, 1, "1", 1 }, { 2, 1, "2", 1 }, { 61, 32, "1.90625", 0 },
    };
    for (size_t i = 0; i < sizeof cases / sizeof cases[0]; i++) {
        gen_t g = frame_gen(&mode640, cases[i].num, cases[i].den, diag);
        run_t r;
        feed(&g, frames_clocks(&mode640, cases[i].num, cases[i].den, 9), 1.0, 0, 0, &r);
        ASSERT_EQ_U(r.geo.ok, 1);
        ASSERT_TRUE(!strcmp(r.geo.mode, "640x480@60"));
        ASSERT_TRUE(cpp_is(&r.geo, cases[i].cpp));
        ASSERT_EQ_U(r.geo.width, 640); ASSERT_EQ_U(r.geo.height, 480);
        ASSERT_EQ_U(r.geo.hback, 48); ASSERT_EQ_U(r.geo.vback, 33);
        ASSERT_TRUE(r.got.n >= 2);
        for (size_t k = 0; k < r.got.n; k++) {
            ASSERT_EQ_U(r.got.missing[k], 0);
            for (uint16_t y = 0; y < 480; y++)
                for (uint16_t p = 0; p < 640; p++)
                    ASSERT_EQ_U(r.got.rgb6[k][(size_t)y * 640 + p], diag(NULL, p, y));
            ASSERT_EQ_U(clock_mismatches(&r, &g, k), 0);
        }
        ASSERT_EQ_U(r.diag.outside, 0);
        ASSERT_EQ_U((unsigned)(r.diag.suggest_dx + 100), 100); ASSERT_EQ_U((unsigned)(r.diag.suggest_dy + 100), 100);
        ASSERT_EQ_U((unsigned)(r.diag.dx + 100), 100); ASSERT_EQ_U((unsigned)(r.diag.dy + 100), 100);
        ASSERT_TRUE(r.diag.colour_changes > 0);
        if (cases[i].whole) ASSERT_EQ_U(r.diag.off_grid, 0);
        else ASSERT_EQ_U(r.diag.off_grid, UINT64_MAX);
        run_free(&r);
    }
}

// cpp 0.95 (760-clock lines, a 91-clock hsync: step 3): some design pixels
// get no clock and cannot appear; every output pixel is the clock the rule
// names. (test_samplers.py compares the pick with centre.py's.)
TEST(centre_below_one) {
    gen_t g = frame_gen(&mode640, 19, 20, diag);
    run_t r;
    feed(&g, frames_clocks(&mode640, 19, 20, 9), 1.0, 0, 0, &r);
    ASSERT_EQ_U(r.geo.ok, 1);
    ASSERT_TRUE(!strcmp(r.geo.mode, "640x480@60"));
    ASSERT_EQ_U(r.L, 760); ASSERT_EQ_U(r.cal.hs.pulse, 91);
    ASSERT_TRUE(cpp_is(&r.geo, "0.95"));
    ASSERT_TRUE(r.got.n >= 2);
    for (size_t k = 0; k < r.got.n; k++) {
        ASSERT_EQ_U(r.got.missing[k], 0);
        ASSERT_EQ_U(clock_mismatches(&r, &g, k), 0);
    }
    uint64_t differ = 0;
    for (uint16_t y = 0; y < 480; y++)
        for (uint16_t p = 0; p < 640; p++)
            differ += r.got.rgb6[0][(size_t)y * 640 + p] != diag(NULL, p, y);
    ASSERT_TRUE(differ > 0);
    ASSERT_EQ_U(r.diag.off_grid, UINT64_MAX);
    run_free(&r);
}

// tt-vga-testpatterns' hvsync_generator phase at cpp 1, the active area all
// lit: pixel-exact, nothing outside, no offset suggested.
TEST(centre_testpatterns_phase) {
    gen_t g = frame_gen(&mode640, 1, 1, full);
    run_t r;
    feed(&g, frames_clocks(&mode640, 1, 1, 9), 1.0, 0, 0, &r);
    ASSERT_EQ_U(r.geo.ok, 1);
    ASSERT_TRUE(r.got.n >= 2);
    for (size_t k = 0; k < r.got.n; k++) {
        ASSERT_EQ_U(r.got.missing[k], 0);
        for (uint16_t y = 0; y < 480; y++)
            for (uint16_t p = 0; p < 640; p++)
                ASSERT_EQ_U(r.got.rgb6[k][(size_t)y * 640 + p], full(NULL, p, y));
    }
    ASSERT_EQ_U(r.diag.outside, 0);
    ASSERT_EQ_U((unsigned)(r.diag.suggest_dx + 100), 100); ASSERT_EQ_U((unsigned)(r.diag.suggest_dy + 100), 100);
    ASSERT_EQ_U(r.diag.off_grid, 0);
    run_free(&r);
}

// The override moves the window by dx pixels and dy lines; the suggestion is
// still relative to the window without it.
TEST(centre_override) {
    gen_t g = frame_gen(&mode640, 1, 1, full);
    run_t a, b;
    uint64_t clocks = frames_clocks(&mode640, 1, 1, 9);
    feed(&g, clocks, 1.0, 0, 0, &a);
    feed(&g, clocks, 1.0, 3, -2, &b);
    ASSERT_EQ_U(b.geo.ok, 1);
    ASSERT_EQ_U(b.geo.hback, 48 + 3); ASSERT_EQ_U(b.geo.vback, 33 - 2);
    ASSERT_EQ_U((unsigned)(b.diag.dx + 100), 103); ASSERT_EQ_U((unsigned)(b.diag.dy + 100), 98);
    ASSERT_TRUE(b.diag.outside > 0);
    ASSERT_EQ_U((unsigned)(b.diag.suggest_dx + 100), 100); ASSERT_EQ_U((unsigned)(b.diag.suggest_dy + 100), 100);
    ASSERT_EQ_U(a.got.n, b.got.n);
    ASSERT_TRUE(a.got.n >= 2);
    for (size_t k = 0; k < a.got.n; k++) {
        ASSERT_EQ_U(a.got.number[k], b.got.number[k]);
        ASSERT_EQ_U(a.got.start[k], b.got.start[k]);
        const uint8_t *fa = a.got.rgb6[k], *fb = b.got.rgb6[k];
        for (uint16_t y = 2; y < 480; y++)
            for (uint16_t p = 0; p < 637; p++)
                ASSERT_EQ_U(fb[(size_t)y * 640 + p], fa[(size_t)(y - 2) * 640 + p + 3]);
        // The window's first two rows are the design's blanking.
        for (uint16_t p = 0; p < 640; p++) ASSERT_EQ_U(fb[p], 0);
        ASSERT_EQ_U(clock_mismatches(&b, &g, k), 0);
    }
    run_free(&a); run_free(&b);
}

// Frame k's frame-clock end lies between its start clock and the trailing
// edge of row v (= vpulse_lines here), and the picture tb-v1 numbers k is
// the one centre-v1 numbers k (at cpp 1 both read the same clocks).
TEST(centre_numbers_match_frame_clock) {
    gen_t g = frame_gen(&mode640, 1, 1, diag);
    run_t r;
    feed(&g, frames_clocks(&mode640, 1, 1, 10), 1.0, 0, 0, &r);
    ASSERT_TRUE(r.got.n >= 3);
    size_t both = 0;
    for (size_t i = 0; i < r.got.n; i++) {
        uint64_t k = r.got.number[i];
        if (i) ASSERT_EQ_U(k, r.got.number[i - 1] + 1);
        ASSERT_TRUE(k < r.fc.n);
        ASSERT_TRUE(r.fc.ends[k] >= r.got.start[i]);
        ASSERT_TRUE(r.fc.ends[k] <= r.got.start[i] + r.vpl * r.L + r.cal.hs.pulse);
        // Row 0's leading edge: the design's hsync starts at hpos 656.
        ASSERT_EQ_U((r.got.start[i] - RESET) % 800, 656);
        for (size_t j = 0; j < r.tgot.n; j++) {
            if (r.tgot.number[j] != k) continue;
            ASSERT_EQ_MEM(r.tgot.rgb6[j], r.got.rgb6[i], (size_t)640 * 480);
            both++;
        }
    }
    ASSERT_TRUE(both >= 2);
    ASSERT_EQ_U(r.got.number[0], 0);
    run_free(&r);
}

// The trace of tbv1_glitch_gap: a 1-clock vsync glitch inside frame 3's
// pulse. tb-v1 numbers one extra frame; in centre-v1 the earlier of the two
// numbers (the glitch's) has no frame, and later frames carry the same
// numbers in both.
TEST(centre_glitch_gap) {
    gen_t g = frame_gen(&mode640, 1, 1, diag);
    run_t a, b;
    uint64_t clocks = RESET + 12 * 420000ull;
    feed(&g, clocks, 0.1, 0, 0, &a);
    ASSERT_TRUE(a.fc.n >= 6);
    uint64_t e3 = a.fc.ends[3];
    g.glitch_v = e3 - 800;
    feed(&g, clocks, 0.1, 0, 0, &b);
    ASSERT_EQ_U(b.fc.n, a.fc.n + 1);
    ASSERT_EQ_U(b.tgot.n, a.tgot.n + 1);
    ASSERT_EQ_U(b.got.n, a.got.n);
    ASSERT_TRUE(a.got.n >= 5);
    for (size_t i = 0; i < a.got.n; i++) {
        uint64_t k = a.got.number[i];
        ASSERT_EQ_U(b.got.number[i], k < 3 ? k : k + 1);
        ASSERT_EQ_U(b.got.start[i], a.got.start[i]);
        ASSERT_EQ_MEM(b.got.rgb6[i], a.got.rgb6[i], (size_t)640 * 480);
        ASSERT_TRUE(b.got.number[i] != 3);
        // Same number, same picture, in both samplers.
        for (size_t j = 0; j < b.tgot.n; j++)
            if (b.tgot.number[j] == b.got.number[i])
                ASSERT_EQ_MEM(b.tgot.rgb6[j], b.got.rgb6[i], (size_t)640 * 480);
    }
    ASSERT_EQ_U(b.fc.ends[3], e3 - 800);
    ASSERT_EQ_U(b.fc.ends[4], e3);
    run_free(&a); run_free(&b);
}

// Frames before capture_after_clock are in the trace but not the run's: none
// is numbered, and every frame's numbered end is a counted one.
TEST(centre_ignores_frames_before_capture) {
    gen_t g = frame_gen(&mode640, 1, 1, diag);
    run_t r;
    feed(&g, frames_clocks(&mode640, 1, 1, 9), 1.0, 0, 0, &r);
    ASSERT_TRUE(r.cal.capture_after_clock > 3 * 420000ull);   // three frames before capture
    ASSERT_TRUE(r.got.n >= 2);
    for (size_t i = 0; i < r.got.n; i++) {
        ASSERT_TRUE(r.got.number[i] < r.fc.n);
        ASSERT_TRUE(r.fc.ends[r.got.number[i]] > r.cal.capture_after_clock);
        // Row v's trailing edge is after capture began.
        ASSERT_TRUE(r.got.start[i] + r.vpl * r.L + r.cal.hs.pulse > r.cal.capture_after_clock);
    }
    // The frames are the run's: one per end, less the cut last one.
    ASSERT_EQ_U(r.got.n, r.fc.n - 1);
    for (size_t i = 0; i < r.got.n; i++) ASSERT_EQ_U(r.got.number[i], i);
    run_free(&r);
}

// A frame cut off by the end of the trace is not emitted.
TEST(centre_drops_cut_last_frame) {
    gen_t g = frame_gen(&mode640, 1, 1, diag);
    run_t r;
    uint64_t clocks = frames_clocks(&mode640, 1, 1, 9.5);
    feed(&g, clocks, 1.0, 0, 0, &r);
    ASSERT_TRUE(r.got.n >= 2);
    uint64_t last = r.got.start[r.got.n - 1];
    ASSERT_TRUE(last + 420000 + r.cal.hs.pulse <= clocks);     // its successor began
    ASSERT_TRUE(last + 2 * 420000ull > clocks);                // the next one was cut
    for (size_t k = 0; k < r.got.n; k++) ASSERT_EQ_U(r.got.missing[k], 0);
    run_free(&r);
}

// No sync: mode "unknown", cpp 1, no picture, no frames.
TEST(centre_no_sync) {
    gen_t g = { NULL, 0, 1, 1, 0, 1 };
    run_t r;
    feed(&g, 2600000, 0.1, 0, 0, &r);
    ASSERT_EQ_U(r.geo.ok, 0);
    ASSERT_TRUE(!strcmp(r.geo.status, "no-sync"));
    ASSERT_TRUE(!strcmp(r.geo.mode, "unknown"));
    ASSERT_TRUE(cpp_is(&r.geo, "1"));
    ASSERT_EQ_U(r.geo.width, 0); ASSERT_EQ_U(r.geo.height, 0);
    ASSERT_EQ_U(r.got.n, 0);
    run_free(&r);
}

// Bad timing by period (vsync = hsync): "unknown", cpp 1. By size (1,300
// lines of a 640x480@60 line, matched by the line-count fallback): the
// matched mode and cpp, width and height 0.
TEST(centre_bad_timing) {
    gen_t g = { NULL, 0, 1, 1, 0, 2 };
    run_t r;
    feed(&g, 200000, 0.1, 0, 0, &r);
    ASSERT_EQ_U(r.cal.hs.found, 1); ASSERT_EQ_U(r.cal.vs.found, 1);
    ASSERT_EQ_U(r.geo.ok, 0);
    ASSERT_TRUE(!strcmp(r.geo.status, "bad-timing"));
    ASSERT_TRUE(!strcmp(r.geo.mode, "unknown"));
    ASSERT_TRUE(cpp_is(&r.geo, "1"));
    ASSERT_EQ_U(r.geo.width, 0); ASSERT_EQ_U(r.geo.height, 0);
    ASSERT_EQ_U(r.got.n, 0);
    run_free(&r);

    vgaframe_mode_t tall = mode640;
    tall.v_back = 1300 - 480 - 10 - 2;
    g = frame_gen(&tall, 1, 1, diag);
    feed(&g, frames_clocks(&tall, 1, 1, 6), 1.0, 0, 0, &r);
    ASSERT_EQ_U(r.lines, 1300);
    ASSERT_EQ_U(r.geo.ok, 0);
    ASSERT_TRUE(!strcmp(r.geo.status, "bad-timing"));
    ASSERT_TRUE(!strcmp(r.geo.mode, "640x480@60"));
    ASSERT_TRUE(cpp_is(&r.geo, "1"));
    ASSERT_EQ_U(r.geo.width, 0); ASSERT_EQ_U(r.geo.height, 0);
    ASSERT_EQ_U(r.got.n, 0);
    run_free(&r);
}

int main(void) {
    size_t count;
    const vgaframe_mode_t *modes = vgaframe_modes(&count);
    mode640 = modes[0];
    if (vgaframe_mode_line_px(&mode640) != 800 || vgaframe_mode_frame_lines(&mode640) != 525) {
        fprintf(stderr, "mode table entry 0 is not 640x480@60\n");
        return 1;
    }
    RUN(centre_cpp_rule);
    RUN(centre_pixel_exact);
    RUN(centre_below_one);
    RUN(centre_testpatterns_phase);
    RUN(centre_override);
    RUN(centre_numbers_match_frame_clock);
    RUN(centre_glitch_gap);
    RUN(centre_ignores_frames_before_capture);
    RUN(centre_drops_cut_last_frame);
    RUN(centre_no_sync);
    RUN(centre_bad_timing);
    free(frame_buf);
    RUN_TESTS_END();
}
