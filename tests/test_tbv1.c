// SPDX-License-Identifier: Apache-2.0
// tb-v1 (src/sample/tbv1.c), the port of tb.cpp:379-557, fed generated
// clk-high uo_out after the shared calibration. python/tests/test_samplers.py
// compares it with the per-clock reference python/vgacap/tbv1.py.
#include "harness.h"
#include "synth.h"
#include "vgacap/sample.h"
#include "vgacap/time.h"
#include <stdint.h>
#include <stdlib.h>

#define HZ 25175000.0
#define RESET 10u            // tb.cpp's reset: rst_n low for clocks 0-9

// ---- generated designs: uo_out of clock c (rst_n is c >= RESET) -----------

typedef struct gen {
    const uint32_t *frame; uint64_t frame_len;   // one frame from (hpos, vpos) = (0, 0)
    uint64_t cpp;                                // whole clocks per design pixel
    uint64_t glitch_v;                           // a clock whose vsync bit is inverted (0: none)
    int kind;                                    // 0: frame, 1: constant 0x88, 2: vsync = hsync
} gen_t;

static uint8_t gen_uo(const gen_t *g, uint64_t c) {
    if (c < RESET) return 0;
    uint8_t uo;
    if (g->kind == 1) uo = 0x88;
    else if (g->kind == 2) uo = (c % 8) >= 2 ? 0x88 : 0;   // 8-clock period, low 2, on both syncs
    else uo = (uint8_t)g->frame[((c - RESET) / g->cpp) % g->frame_len];
    if (g->glitch_v && c == g->glitch_v) uo ^= 0x08;
    return uo;
}

typedef struct got {
    size_t n, cap;
    uint64_t *number, *start;
    uint8_t **rgb6;
    uint16_t width, height;
} got_t;

static void on_frame(void *user, const vgasample_frame_t *f) {
    got_t *g = user;
    if (g->n == g->cap) {
        g->cap = g->cap ? 2 * g->cap : 16;
        g->number = realloc(g->number, g->cap * sizeof *g->number);
        g->start = realloc(g->start, g->cap * sizeof *g->start);
        g->rgb6 = realloc(g->rgb6, g->cap * sizeof *g->rgb6);
        if (!g->number || !g->start || !g->rgb6) abort();
    }
    size_t sz = (size_t)f->width * f->height;
    g->rgb6[g->n] = malloc(sz);
    if (!g->rgb6[g->n]) abort();
    memcpy(g->rgb6[g->n], f->rgb6, sz);
    g->number[g->n] = f->number;
    g->start[g->n] = f->start_clock;
    g->width = f->width; g->height = f->height;
    if (f->missing) abort();
    g->n++;
}

static void got_free(got_t *g) {
    for (size_t i = 0; i < g->n; i++) free(g->rgb6[i]);
    free(g->number); free(g->start); free(g->rgb6);
    memset(g, 0, sizeof *g);
}

typedef struct run {
    vgacal_t cal; vgafc_t fc; tbv1_t *tb;
    vgasample_geometry_t geo;
    uint64_t target, limit;
    got_t got;
} run_t;

// Clocks [0, clocks) to the calibration, then to the frame clock and tb-v1,
// per clock or as maximal runs.
static void feed(const gen_t *g, uint64_t clocks, int per_clock, double seconds, run_t *r) {
    memset(r, 0, sizeof *r);
    vgacal_init(&r->cal, HZ, 0.1);
    uint64_t c = 0;
    while (c < clocks) {
        uint8_t uo = gen_uo(g, c), rst = c >= RESET;
        uint64_t n = 1;
        if (!per_clock)
            while (c + n < clocks && gen_uo(g, c + n) == uo && (c + n >= RESET) == rst) n++;
        uint64_t done = 0;
        if (r->cal.state == VGACAL_RUNNING) done = vgacal_push(&r->cal, uo, rst, c, n);
        if (done < n) {
            if (!r->tb) {
                vgafc_init(&r->fc, &r->cal);
                r->tb = tbv1_create(&r->cal, &r->fc, HZ, seconds, on_frame, &r->got);
            }
            vgafc_push(&r->fc, uo, c + done, n - done);
            tbv1_push(r->tb, uo, c + done, n - done);
        }
        c += n;
    }
    vgacal_end_of_trace(&r->cal, clocks);
    if (!r->tb) {
        vgafc_init(&r->fc, &r->cal);
        r->tb = tbv1_create(&r->cal, &r->fc, HZ, seconds, on_frame, &r->got);
    }
    r->geo = *tbv1_geometry(r->tb);
    r->target = tbv1_target_frames(r->tb);
    r->limit = tbv1_clock_limit(r->tb);
}

static void run_free(run_t *r) {
    tbv1_free(r->tb);
    vgacal_free(&r->cal); vgafc_free(&r->fc);
    got_free(&r->got);
}

static uint32_t *frame_buf;
static vgaframe_mode_t mode640;

static uint8_t diag(void *user, uint16_t x, uint16_t y) { (void)user; return (uint8_t)((x + 3 * y) & 0x3F); }

static gen_t frame_gen(const vgaframe_mode_t *m, uint64_t cpp) {
    uint64_t len = (uint64_t)vgaframe_mode_line_px(m) * vgaframe_mode_frame_lines(m);
    free(frame_buf);
    frame_buf = malloc(len * sizeof *frame_buf);
    synth_frame_hvsync(m, diag, NULL, frame_buf, (size_t)len);
    gen_t g = { frame_buf, len, cpp, 0, 0 };
    return g;
}

static int cpp_is(const vgasample_geometry_t *geo, const char *want) {
    char buf[64];
    if (vgacap_fmt_double(buf, sizeof buf, geo->cpp) < 0) return 0;
    if (strcmp(buf, want)) { fprintf(stderr, "  cpp %s, expected %s\n", buf, want); return 0; }
    return 1;
}

// At cpp 2, tb.cpp reads pixel p at x = 2p - 1 clocks after the hsync pulse's
// end plus the back porch: the second clock of the design's pixel p - 1.
// Published behaviour, recorded: tb-v1's p is the design's p - 1 (p >= 1),
// and its column 0 is the design's last blanking pixel (black).
TEST(tbv1_reproduces_half_pixel_early) {
    gen_t g = frame_gen(&mode640, 2);
    run_t r;
    feed(&g, RESET + 8 * 840000ull, 0, 60.0, &r);
    ASSERT_EQ_U(r.geo.ok, 1);
    ASSERT_TRUE(!strcmp(r.geo.status, "ok"));
    ASSERT_TRUE(!strcmp(r.geo.mode, "640x480@60"));
    ASSERT_TRUE(cpp_is(&r.geo, "2"));
    ASSERT_EQ_U(r.geo.width, 640); ASSERT_EQ_U(r.geo.height, 480);
    ASSERT_EQ_U(r.geo.hback, 48); ASSERT_EQ_U(r.geo.vback, 33);
    ASSERT_TRUE(r.got.n >= 2);
    for (size_t i = 0; i < r.got.n; i++) {
        ASSERT_EQ_U(r.got.number[i], i);
        ASSERT_EQ_U(r.got.start[i], r.fc.ends[i]);
        const uint8_t *f = r.got.rgb6[i];
        for (uint16_t y = 0; y < 480; y++) {
            ASSERT_EQ_U(f[(size_t)y * 640], 0);
            for (uint16_t p = 1; p < 640; p++)
                ASSERT_EQ_U(f[(size_t)y * 640 + p], diag(NULL, (uint16_t)(p - 1), y));
        }
    }
    run_free(&r);
}

// Runs (constant uo_out over many clocks) give the same frames as one clock
// at a time.
TEST(tbv1_run_equals_clock) {
    gen_t g = frame_gen(&mode640, 2);
    g.glitch_v = 0;
    run_t a, b;
    feed(&g, RESET + 7 * 840000ull, 1, 60.0, &a);
    feed(&g, RESET + 7 * 840000ull, 0, 60.0, &b);
    ASSERT_TRUE(a.got.n >= 1);
    ASSERT_EQ_U(a.got.n, b.got.n);
    for (size_t i = 0; i < a.got.n; i++) {
        ASSERT_EQ_U(a.got.number[i], b.got.number[i]);
        ASSERT_EQ_U(a.got.start[i], b.got.start[i]);
        ASSERT_EQ_MEM(a.got.rgb6[i], b.got.rgb6[i], (size_t)640 * 480);
    }
    run_free(&a); run_free(&b);
}

// seconds = 0.1 at 59.94 fps: tb.cpp stops after (uint64_t)(0.1 * fps + 0.5)
// = 6 frames, however long the trace.
TEST(tbv1_frame_target) {
    gen_t g = frame_gen(&mode640, 1);
    run_t r;
    feed(&g, RESET + 14 * 420000ull, 0, 0.1, &r);
    double fps = HZ / (double)r.cal.vs.period;
    ASSERT_EQ_U(r.target, (uint64_t)(0.1 * fps + 0.5));
    ASSERT_EQ_U(r.target, 6);
    ASSERT_EQ_U(r.limit, r.cal.capture_after_clock + (uint64_t)((0.1 + 2.5 / fps) * HZ));
    ASSERT_TRUE(r.fc.n > 7);        // the trace has more frames than the target
    ASSERT_EQ_U(r.got.n, 6);
    ASSERT_EQ_U(tbv1_frames(r.tb), 6);
    for (size_t i = 0; i < 6; i++) ASSERT_EQ_U(r.got.number[i], i);
    run_free(&r);
}

// The tennis game: a 640x480 line with 506 lines matches no mode by line
// count; the fallback keeps every line after the vsync pulse.
TEST(tbv1_tennis_lines) {
    vgaframe_mode_t m = mode640;
    m.v_back = 14;                   // 480 + 10 + 2 + 14 = 506 lines
    gen_t g = frame_gen(&m, 1);
    run_t r;
    feed(&g, RESET + 8 * 506ull * 800, 0, 60.0, &r);
    ASSERT_EQ_U(r.geo.ok, 1);
    ASSERT_TRUE(!strcmp(r.geo.mode, "640x480@60"));
    ASSERT_TRUE(cpp_is(&r.geo, "1"));
    ASSERT_EQ_U(r.geo.width, 640); ASSERT_EQ_U(r.geo.hback, 48);
    ASSERT_EQ_U(r.geo.height, 506 - 2); ASSERT_EQ_U(r.geo.vback, 0);
    ASSERT_TRUE(r.got.n >= 2);
    ASSERT_EQ_U(r.got.height, 504);
    // Row y is the y-th line after the pulse, so the design's active rows
    // start at row v_back = 14. At cpp 1, x + 0.5 truncates to x: column p
    // is the design's own pixel p.
    const uint8_t *f = r.got.rgb6[0];
    for (uint16_t y = 0; y < 14; y++)
        for (uint16_t p = 0; p < 640; p++) ASSERT_EQ_U(f[(size_t)y * 640 + p], 0);
    for (uint16_t y = 0; y < 480; y++)
        for (uint16_t p = 0; p < 640; p++)
            ASSERT_EQ_U(f[(size_t)(y + 14) * 640 + p], diag(NULL, p, y));
    for (uint16_t y = 494; y < 504; y++)
        for (uint16_t p = 0; p < 640; p++) ASSERT_EQ_U(f[(size_t)y * 640 + p], 0);
    run_free(&r);
}

// A 1-clock vsync glitch inside frame 3's pulse: tb.cpp starts a frame at the
// glitch's end too, so frame 3 is the sliver from the glitch to the real end
// (black: no line reaches the picture), and the real frame 3 is numbered 4.
// (13b adds centre-v1's half: there the earlier number has no frame.)
TEST(tbv1_glitch_gap) {
    gen_t g = frame_gen(&mode640, 1);
    run_t a, b;
    uint64_t clocks = RESET + 12 * 420000ull;
    feed(&g, clocks, 0, 60.0, &a);
    ASSERT_TRUE(a.fc.n >= 6);
    uint64_t e3 = a.fc.ends[3];
    g.glitch_v = e3 - 800;           // one clock out of the pulse, mid-pulse
    feed(&g, clocks, 0, 60.0, &b);
    ASSERT_EQ_U(b.fc.n, a.fc.n + 1);
    ASSERT_EQ_U(b.got.n, a.got.n + 1);
    for (size_t i = 0; i < b.got.n; i++) {
        ASSERT_EQ_U(b.got.number[i], i);
        ASSERT_EQ_U(b.got.start[i], b.fc.ends[i]);
    }
    ASSERT_EQ_U(b.got.start[3], e3 - 800);
    ASSERT_EQ_U(b.got.start[4], e3);
    for (size_t i = 0; i < 3; i++) ASSERT_EQ_MEM(b.got.rgb6[i], a.got.rgb6[i], (size_t)640 * 480);
    for (size_t i = 3; i < a.got.n; i++) {
        ASSERT_EQ_U(b.got.start[i + 1], a.got.start[i]);
        ASSERT_EQ_MEM(b.got.rgb6[i + 1], a.got.rgb6[i], (size_t)640 * 480);
    }
    for (size_t p = 0; p < (size_t)640 * 480; p++) ASSERT_EQ_U(b.got.rgb6[3][p], 0);
    run_free(&a); run_free(&b);
}

TEST(tbv1_no_sync) {
    gen_t g = { NULL, 0, 1, 0, 1 };
    run_t r;
    feed(&g, 2600000, 0, 60.0, &r);
    ASSERT_EQ_U(r.geo.ok, 0);
    ASSERT_TRUE(!strcmp(r.geo.status, "no-sync"));
    ASSERT_EQ_U(r.target, 0);
    ASSERT_EQ_U(r.got.n, 0);
    run_free(&r);
}

// A small 80-clock line is below every mode's 0.9 clocks per pixel: unknown
// mode, everything after the sync pulses at one clock per pixel
// (tb.cpp:441-444).
TEST(tbv1_unknown_mode) {
    vgaframe_mode_t m = { "small", 64, 4, 8, 4, 30, 2, 3, 5, 0, 0, 0 };
    gen_t g = frame_gen(&m, 1);
    run_t r;
    feed(&g, RESET + 100000, 0, 60.0, &r);
    ASSERT_EQ_U(r.geo.ok, 1);
    ASSERT_TRUE(!strcmp(r.geo.mode, "unknown"));
    ASSERT_TRUE(cpp_is(&r.geo, "1"));
    ASSERT_EQ_U(r.geo.width, 80 - 8); ASSERT_EQ_U(r.geo.height, 40 - 3);
    ASSERT_EQ_U(r.geo.hback, 0); ASSERT_EQ_U(r.geo.vback, 0);
    ASSERT_TRUE(r.got.n >= 5);
    ASSERT_EQ_U(r.got.width, 72); ASSERT_EQ_U(r.got.height, 37);
    run_free(&r);
}

// Bad timing: vs.period < 2 * hs.period (tb.cpp:399), and a picture outside
// 16..4096 (tb.cpp:446; here an 8-clock line, unknown mode, 6 pixels wide).
TEST(tbv1_bad_timing) {
    for (int i = 0; i < 2; i++) {
        vgaframe_mode_t m = { "tiny", 4, 1, 2, 1, 30, 2, 3, 5, 0, 0, 0 };
        gen_t g = i ? frame_gen(&m, 1) : (gen_t){ NULL, 0, 1, 0, 2 };
        run_t r;
        feed(&g, 200000, 0, 60.0, &r);
        ASSERT_EQ_U(r.cal.hs.found, 1); ASSERT_EQ_U(r.cal.vs.found, 1);
        ASSERT_EQ_U(r.cal.state, VGACAL_DONE);
        ASSERT_EQ_U(r.geo.ok, 0);
        ASSERT_TRUE(!strcmp(r.geo.status, "bad-timing"));
        ASSERT_EQ_U(r.got.n, 0);
        run_free(&r);
    }
}

int main(void) {
    size_t count;
    const vgaframe_mode_t *modes = vgaframe_modes(&count);
    mode640 = modes[0];
    if (vgaframe_mode_line_px(&mode640) != 800 || vgaframe_mode_frame_lines(&mode640) != 525) {
        fprintf(stderr, "mode table entry 0 is not 640x480@60\n");
        return 1;
    }
    RUN(tbv1_reproduces_half_pixel_early);
    RUN(tbv1_run_equals_clock);
    RUN(tbv1_frame_target);
    RUN(tbv1_tennis_lines);
    RUN(tbv1_glitch_gap);
    RUN(tbv1_no_sync);
    RUN(tbv1_unknown_mode);
    RUN(tbv1_bad_timing);
    free(frame_buf);
    RUN_TESTS_END();
}
