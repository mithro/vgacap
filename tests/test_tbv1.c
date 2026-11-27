// SPDX-License-Identifier: Apache-2.0
// tb-v1 (src/sample/tbv1.c), the port of tb.cpp:379-557, fed generated
// clk-high uo_out after the shared calibration. python/tests/test_samplers.py
// compares it with the per-clock reference python/vgacap/tbv1.py. With
// --speed only tbv1_speed runs (CTest label `perf`, kept out of CI).
#define _POSIX_C_SOURCE 200809L   // clock_gettime
#include "harness.h"
#include "synth.h"
#include "vgacap/sample.h"
#include "vgacap/time.h"
#include <stdint.h>
#include <stdlib.h>
#include <time.h>

#define HZ 25175000.0
#define RESET 10u            // tb.cpp's reset: rst_n low for clocks 0-9

// ---- generated designs: uo_out of clock c (rst_n is c >= RESET) -----------

typedef struct gen {
    const uint32_t *frame; uint64_t frame_len;   // one frame from (hpos, vpos) = (0, 0)
    uint64_t cpp;                                // whole clocks per design pixel
    uint64_t glitch_v;                           // a clock whose vsync bit is inverted (0: none)
    int kind;                                    // 0: frame, 1: constant 0x88, 2: vsync = hsync
    uint64_t num, den;                           // den != 0: num / den clocks per design pixel, not cpp
} gen_t;

// A run spliced into every design: clocks [at, at + len) are uo, and the
// design resumes after them where it left off (len 0: none).
static struct { uint64_t at, len; uint8_t uo; } splice;

static uint8_t gen_uo(const gen_t *g, uint64_t c) {
    if (c < RESET) return 0;
    if (splice.len && c >= splice.at) {
        if (c < splice.at + splice.len) return splice.uo;
        c -= splice.len;
    }
    uint8_t uo;
    if (g->kind == 1) uo = 0x88;
    else if (g->kind == 2) uo = (c % 8) >= 2 ? 0x88 : 0;   // 8-clock period, low 2, on both syncs
    else if (g->den) {
        // Design pixel p covers clocks [floor(p * cpp), floor((p + 1) * cpp))
        // from the end of reset: clock t is in the largest p with p * cpp < t + 1.
        uint64_t t = c - RESET;
        uint64_t p = ((t + 1) * g->den + g->num - 1) / g->num - 1;
        uo = (uint8_t)g->frame[p % g->frame_len];
    } else uo = (uint8_t)g->frame[((c - RESET) / g->cpp) % g->frame_len];
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

static uint32_t mix(uint32_t v);

static double feed_calib = 0.1;     // the calibration's seconds

// Clocks [0, clocks) to the calibration, then to the frame clock and tb-v1:
// per_clock 1, one clock a run; 0, as maximal runs; 2, as maximal runs split
// at pseudo-random clocks (pieces of 1 to 37 clocks).
static void feed(const gen_t *g, uint64_t clocks, int per_clock, double seconds, run_t *r) {
    memset(r, 0, sizeof *r);
    vgacal_init(&r->cal, HZ, feed_calib);
    uint64_t c = 0;
    while (c < clocks) {
        uint8_t uo = gen_uo(g, c), rst = c >= RESET;
        uint64_t n = 1;
        if (splice.len && c >= splice.at && c < splice.at + splice.len && per_clock != 1)
            n = splice.at + splice.len - c;          // the spliced run, without scanning it
        const uint64_t most = per_clock == 1 ? 1 : per_clock == 2 ? 1 + mix((uint32_t)c) % 37u : UINT64_MAX;
        while (n < most && c + n < clocks && gen_uo(g, c + n) == uo && (c + n >= RESET) == rst) n++;
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
    gen_t g = { frame_buf, len, cpp, 0, 0, 0, 0 };
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
    gen_t g = { NULL, 0, 1, 0, 1, 0, 0 };
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
        gen_t g = i ? frame_gen(&m, 1) : (gen_t){ NULL, 0, 1, 0, 2, 0, 0 };
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

// A run past INT_MAX clocks of one line (no hsync): tb.cpp's pixel index
// reaches INT_MAX and then is INT_MIN, never in the picture. The frames are
// those a 10,000-clock run of the same colour gives: row 240 from pixel 300
// on in its colour (at cpp 1 tb-v1's pixels are the design's), the rest as
// the design.
TEST(tbv1_run_past_int_max) {
    gen_t g = frame_gen(&mode640, 1);
    const uint64_t at = RESET + 8 * 420000ull + 240 * 800 + 300, lens[2] = { 10000, (1ull << 31) + 5 };
    run_t r[2];
    for (int i = 0; i < 2; i++) {
        splice.at = at; splice.len = lens[i]; splice.uo = 0x80 | 0x08 | 0x11;
        feed(&g, RESET + 12 * 420000ull + lens[i], 0, 200.0, &r[i]);
    }
    splice.len = 0;
    ASSERT_TRUE(r[0].cal.capture_after_clock < at);
    ASSERT_TRUE(r[1].limit > at + lens[1]);
    ASSERT_TRUE(r[0].got.n >= 3);
    ASSERT_EQ_U(r[1].got.n, r[0].got.n);
    size_t hit = 0;
    for (size_t k = 0; k < r[0].got.n; k++) {
        ASSERT_EQ_U(r[1].got.number[k], r[0].got.number[k]);
        const uint64_t shift = r[0].got.start[k] > at ? lens[1] - lens[0] : 0;
        ASSERT_EQ_U(r[1].got.start[k], r[0].got.start[k] + shift);
        ASSERT_EQ_MEM(r[1].got.rgb6[k], r[0].got.rgb6[k], (size_t)640 * 480);
        const uint8_t *row = r[0].got.rgb6[k] + (size_t)240 * 640;
        if (row[299] != 0x30 && row[300] == 0x30 && row[639] == 0x30) hit++;
    }
    ASSERT_EQ_U(hit, 1);
    run_free(&r[0]); run_free(&r[1]);
}

// ---- runs against the per-clock path, on tricky inputs ---------------------
//
// A run of one clock goes through the run's first clock only, which is
// tb.cpp's per-clock loop; so a design fed one clock a run is the reference
// for the same design fed as maximal runs, and as maximal runs split at
// pseudo-random clocks (so that runs also start and end inside a pixel).

#define TRICKY_FRAMES 4u

static uint32_t mix(uint32_t v) {
    v ^= v >> 16; v *= 0x7feb352du; v ^= v >> 15; v *= 0x846ca68bu; v ^= v >> 16;
    return v;
}

// Picture f: rows of runs 1 to 9 pixels long (black among the colours);
// column 0 lit on even rows and black on odd ones, so that the left porch's
// black run continues into the picture; column 639 a one-pixel colour on
// every third row, else black into the right porch.
static uint8_t tricky_px[TRICKY_FRAMES][480][640];
static uint32_t tricky_f;
static uint8_t tricky_pix(void *user, uint16_t x, uint16_t y) { (void)user; return tricky_px[tricky_f][y][x]; }

static void tricky_draw(void) {
    for (uint32_t f = 0; f < TRICKY_FRAMES; f++)
        for (uint32_t y = 0; y < 480; y++) {
            uint32_t x = 0;
            for (uint32_t j = 0; x < 640; j++) {
                uint32_t h = mix(f * 1000003u + y * 1009u + j);
                uint32_t len = 1 + h % 9u;
                uint8_t c = (uint8_t)((h >> 8) % 64u);
                for (uint32_t i = x; i < x + len && i < 640; i++) tricky_px[f][y][i] = c;
                x += len;
            }
            tricky_px[f][y][0] = (y & 1) ? 0 : 0x3F;
            tricky_px[f][y][639] = (y % 3 == 0) ? 0x15 : 0;
            if (y % 3 == 0) tricky_px[f][y][638] = 0;
        }
}

// Frames of mode m showing the pictures in turn: enough for the calibration
// (feed_calib) and two more, then frame hd (= *first_tricky) with rows 199 to 203
// black and no hsync pulse on rows 200 to 202, so one black run crosses four
// lines; frame hd + 2 with no vsync pulse and no hsync pulse on its blanking
// rows, so one black run crosses into the next frame; then two more.
// *frames is how many there are.
static gen_t tricky_gen(const vgaframe_mode_t *m, uint64_t num, uint64_t den, uint64_t *first_tricky,
                        uint64_t *frames) {
    const uint64_t cpl = vgaframe_mode_line_px(m), lpf = vgaframe_mode_frame_lines(m), fl = cpl * lpf;
    const uint64_t hd = (uint64_t)(feed_calib * HZ * (double)den / ((double)fl * (double)num)) + 3, nf = hd + 5;
    free(frame_buf);
    frame_buf = malloc(fl * nf * sizeof *frame_buf);
    for (uint64_t k = 0; k < nf; k++) {
        tricky_f = (uint32_t)(k % TRICKY_FRAMES);
        synth_frame_hvsync(m, tricky_pix, NULL, frame_buf + k * fl, (size_t)fl);
    }
    const uint32_t h_off = m->h_sync_positive ? 0 : 0x80, v_off = m->v_sync_positive ? 0 : 0x08;
    for (uint64_t row = 199; row <= 203; row++)
        for (uint64_t x = 0; x < cpl; x++) {
            uint32_t *u = &frame_buf[hd * fl + row * cpl + x];
            *u &= ~0x77u;                                   // black
            if (row >= 200 && row <= 202) *u = (*u & ~0x80u) | h_off;
        }
    for (uint64_t i = 0; i < fl; i++) {
        uint32_t *u = &frame_buf[(hd + 2) * fl + i];
        *u = (*u & ~0x08u) | v_off;
        if (i / cpl >= m->v_active) *u = (*u & ~0x80u) | h_off;
    }
    *first_tricky = hd; *frames = nf;
    gen_t g = { frame_buf, fl * nf, 1, 0, 0, num, den };
    return g;
}

static void same_frames(const run_t *a, const run_t *b, int *ok) {
    *ok = 0;
    ASSERT_TRUE(a->got.n >= 3);
    ASSERT_EQ_U(a->got.n, b->got.n);
    ASSERT_EQ_U(a->got.width, b->got.width); ASSERT_EQ_U(a->got.height, b->got.height);
    for (size_t i = 0; i < a->got.n; i++) {
        ASSERT_EQ_U(a->got.number[i], b->got.number[i]);
        ASSERT_EQ_U(a->got.start[i], b->got.start[i]);
        ASSERT_EQ_MEM(a->got.rgb6[i], b->got.rgb6[i], (size_t)a->got.width * a->got.height);
    }
    *ok = 1;
}

TEST(tbv1_runs_match_per_clock) {
    tricky_draw();
    static const struct { int mode; uint64_t num, den; const char *cpp; } cases[] = {
        { 0, 1, 1, "1" }, { 0, 2, 1, "2" }, { 0, 3, 1, "3" }, { 0, 4, 1, "4" },
        { 0, 61, 32, "1.90625" }, { 0, 23, 10, "2.3" }, { 0, 3, 2, "1.5" },
        { 0, 19, 20, "0.95" },        // below 1: pixels are skipped
        { 1, 1, 1, "1" },             // unknown mode
    };
    for (size_t i = 0; i < sizeof cases / sizeof cases[0]; i++) {
        vgaframe_mode_t m = cases[i].mode ? (vgaframe_mode_t){ "odd", 600, 20, 40, 40, 400, 5, 3, 20, 0, 0, 0 }
                                          : mode640;
        // Three frames to calibrate in: at cpp 3 and 4, 0.1 s is two or fewer.
        feed_calib = 3.0 * 420000.0 * (double)cases[i].num / (double)cases[i].den / HZ;
        if (feed_calib < 0.1) feed_calib = 0.1;
        uint64_t hd, nf;
        gen_t g = tricky_gen(&m, cases[i].num, cases[i].den, &hd, &nf);
        const double fclocks = (double)g.frame_len / (double)nf * (double)cases[i].num / (double)cases[i].den;
        const uint64_t clocks = RESET + (uint64_t)((double)nf * fclocks);
        run_t a, b, c;
        feed(&g, clocks, 1, 60.0, &a);
        feed(&g, clocks, 0, 60.0, &b);
        feed(&g, clocks, 2, 60.0, &c);
        fprintf(stderr, "  cpp %s, mode %s: %zu frames, %s\n", cases[i].cpp, a.geo.mode, a.got.n, a.geo.status);
        ASSERT_EQ_U(a.geo.ok, 1);
        // The tricky frames come after the calibration, and are emitted.
        ASSERT_TRUE(a.cal.capture_after_clock < RESET + (uint64_t)((double)hd * fclocks));
        ASSERT_TRUE(a.got.n >= 5);
        ASSERT_TRUE(!strcmp(a.geo.mode, cases[i].mode ? "unknown" : "640x480@60"));
        ASSERT_TRUE(cpp_is(&a.geo, cases[i].cpp));
        int ok;
        same_frames(&a, &b, &ok);
        ASSERT_TRUE(ok);
        same_frames(&a, &c, &ok);
        ASSERT_TRUE(ok);
        run_free(&a); run_free(&b); run_free(&c);
    }
    feed_calib = 0.1;
}

// ---- speed (perf label; run by hand, not in CI) -----------------------------
//
// tb-v1's own work (tbv1_push_runs) on a 640x480@60 design at cpp 1 whose
// every frame differs: a 208x96 logo of stripes 1 to 12 pixels long
// bouncing 3 pixels across and 2 down a frame, changing colour at each
// bounce; 96 one-pixel stars drifting left at 1 to 3 pixels a frame; and a
// bottom bar showing the frame number as blocks. Runs are 1 clock (the
// stars, the stripes' edges) up to whole blank lines. Only the tb-v1 calls
// are timed (thread CPU time), not the generator, the calibration, the
// frame clock or the frame callback.

#define SPEED_FRAMES 240u

static uint8_t speed_px[480][640];
static uint8_t speed_pix(void *user, uint16_t x, uint16_t y) { (void)user; return speed_px[y][x]; }


static int64_t bounce(int64_t t, int64_t span) {   // a triangle wave over [0, span]
    int64_t p = t % (2 * span);
    return p <= span ? p : 2 * span - p;
}

static void speed_draw(uint32_t k) {
    memset(speed_px, 0, sizeof speed_px);
    for (uint32_t i = 0; i < 96; i++) {
        uint32_t h = mix(i + 1);
        uint32_t x = (h % 640u + 640u * 4u - k * (1u + i % 3u) % 640u) % 640u;
        uint32_t y = (h >> 12) % 460u;
        speed_px[y][x] = (uint8_t)(0x15 + (i % 3) * 0x15);
    }
    const int64_t lw = 208, lh = 96;
    const int64_t lx = bounce(3 * (int64_t)k, 640 - lw), ly = bounce(2 * (int64_t)k, 460 - lh);
    const int64_t hits = 3 * (int64_t)k / (640 - lw) + 2 * (int64_t)k / (460 - lh);
    static const uint8_t pal[6] = { 0x30, 0x0C, 0x03, 0x3C, 0x33, 0x0F };
    for (int64_t y = 0; y < lh; y++) {
        int64_t x = 0;
        int on = (int)(y & 1);
        for (uint32_t j = 0; x < lw; j++) {
            int64_t len = 1 + (int64_t)(mix((uint32_t)y * 1000u + j) % 12u);
            if (on)
                for (int64_t i = x; i < x + len && i < lw; i++)
                    speed_px[ly + y][lx + i] = (uint8_t)(pal[hits % 6] ^ (y % 24 < 12 ? 0 : 0x15));
            on = !on; x += len;
        }
    }
    for (uint32_t d = 0; d < 20; d++) {             // the frame number, one 32-pixel block a bit
        uint8_t c = (k >> d) & 1u ? 0x3F : 0x2A;
        for (uint32_t y = 464; y < 480; y++) memset(&speed_px[y][d * 32 + 1], c, 30);
    }
}

typedef struct speed_out { uint64_t sum; uint64_t *h; size_t n, distinct; double cb_cpu; } speed_out_t;

static double cpu_s(void);

static void speed_frame(void *user, const vgasample_frame_t *f) {
    speed_out_t *o = user;
    const double t0 = cpu_s();             // the callback is not the sampler's time
    uint64_t h = 0xcbf29ce484222325ull;
    const size_t n = (size_t)f->width * f->height;
    for (size_t i = 0; i < n; i++) { h ^= f->rgb6[i]; h *= 0x100000001b3ull; }
    h ^= f->missing; h *= 0x100000001b3ull;
    h ^= f->number; h *= 0x100000001b3ull;
    if (o->n == 0 || o->h[o->n - 1] != h) o->distinct++;
    o->h[o->n++] = h;
    o->sum = o->sum * 0x100000001b3ull ^ h;
    o->cb_cpu += cpu_s() - t0;
}

static double cpu_s(void) {
    struct timespec ts; clock_gettime(CLOCK_THREAD_CPUTIME_ID, &ts);
    return (double)ts.tv_sec + (double)ts.tv_nsec * 1e-9;
}

TEST(tbv1_speed) {
    const size_t fl = 800 * 525;
    uint32_t *uo = malloc(fl * sizeof *uo);
    vgasample_run_t *runs = malloc(fl * sizeof *runs);
    speed_out_t o = { 0, calloc(SPEED_FRAMES, sizeof(uint64_t)), 0, 0, 0 };
    ASSERT_TRUE(uo && runs && o.h);
    vgacal_t cal; vgafc_t fc;
    vgacal_init(&cal, HZ, 0.1);
    memset(&fc, 0, sizeof fc);
    tbv1_t *tb = NULL;
    uint64_t c = RESET, timed_clocks = 0, nruns = 0;
    double cpu = 0;
    vgacal_push(&cal, 0, 0, 0, RESET);              // reset
    for (uint32_t k = 0; k < SPEED_FRAMES; k++) {
        speed_draw(k);
        synth_frame_hvsync(&mode640, speed_pix, NULL, uo, fl);
        size_t n = 0;
        for (size_t i = 0; i < fl; ) {
            size_t j = i + 1;
            while (j < fl && uo[j] == uo[i]) j++;
            runs[n++] = (vgasample_run_t){ c + i, j - i, (uint8_t)uo[i] };
            i = j;
        }
        c += fl;
        size_t i0 = 0;
        if (!tb) {                                   // run by run until calibration ends
            for (; i0 < n && !tb; i0++) {
                const vgasample_run_t *r = &runs[i0];
                uint64_t pre = vgacal_push(&cal, r->uo_out, 1, r->first, r->clocks);
                if (cal.state == VGACAL_DONE) {
                    vgafc_init(&fc, &cal);
                    tb = tbv1_create(&cal, &fc, HZ, 60.0, speed_frame, &o);
                    if (pre < r->clocks) {
                        vgafc_push(&fc, r->uo_out, r->first + pre, r->clocks - pre);
                        tbv1_push(tb, r->uo_out, r->first + pre, r->clocks - pre);
                    }
                }
            }
            if (!tb) continue;
        }
        vgafc_push_runs(&fc, runs + i0, n - i0);
        double t0 = cpu_s(), cb0 = o.cb_cpu;
        tbv1_push_runs(tb, runs + i0, n - i0);
        cpu += cpu_s() - t0 - (o.cb_cpu - cb0);
        timed_clocks += runs[n - 1].first + runs[n - 1].clocks - runs[i0].first;
        nruns += n - i0;
    }
    ASSERT_TRUE(tb != NULL);
    fprintf(stderr, "  tb-v1: %zu frames (%zu differ from the one before), %.1f clocks a run, "
            "%.3f s CPU, %.3f ms a frame, %.2f ns a clock; frames %016llx\n",
            o.n, o.distinct, (double)timed_clocks / (double)nruns, cpu, cpu * 1e3 / (double)o.n,
            cpu * 1e9 / (double)timed_clocks, (unsigned long long)o.sum);
    ASSERT_TRUE(o.n >= SPEED_FRAMES - 10);
    ASSERT_EQ_U(o.distinct, o.n);
    // The frames as tb-v1 at ebd3b18 gives them.
    ASSERT_EQ_U(o.sum, 0x63038bd2415ccdbcull);
    // On big-storage: 1.4 ms a frame storing every clock of a run, 0.12 ms
    // filling each run's pixels with one memset.
    ASSERT_TRUE(cpu * 1e3 / (double)o.n <= 0.5);
    tbv1_free(tb);
    vgacal_free(&cal); vgafc_free(&fc);
    free(uo); free(runs); free(o.h);
}

int main(int argc, char **argv) {
    size_t count;
    const vgaframe_mode_t *modes = vgaframe_modes(&count);
    mode640 = modes[0];
    if (vgaframe_mode_line_px(&mode640) != 800 || vgaframe_mode_frame_lines(&mode640) != 525) {
        fprintf(stderr, "mode table entry 0 is not 640x480@60\n");
        return 1;
    }
    if (argc > 1 && strcmp(argv[1], "--speed") == 0) {
        RUN(tbv1_speed);
        RUN_TESTS_END();
    }
    RUN(tbv1_reproduces_half_pixel_early);
    RUN(tbv1_run_equals_clock);
    RUN(tbv1_frame_target);
    RUN(tbv1_tennis_lines);
    RUN(tbv1_glitch_gap);
    RUN(tbv1_no_sync);
    RUN(tbv1_unknown_mode);
    RUN(tbv1_bad_timing);
    RUN(tbv1_runs_match_per_clock);
    RUN(tbv1_run_past_int_max);
    free(frame_buf);
    RUN_TESTS_END();
}
