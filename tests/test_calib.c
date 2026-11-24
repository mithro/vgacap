// SPDX-License-Identifier: Apache-2.0
// The shared calibration (port of tb.cpp:191-230, 361-378) and the frame
// clock (tb.cpp:480, 520), fed generated clk-high uo_out. python/tests/
// test_calib.py runs the same cases through traces, vgacap-calib-dump and
// the Python reference python/vgacap/calib.py.
#include "harness.h"
#include "synth.h"
#include "vgacap/sample.h"
#include <stdint.h>
#include <stdlib.h>

#define HZ 25175000.0
#define RESET 10u            // tb.cpp's reset: rst_n low for clocks 0-9
#define K64 65536u

// ---- generated designs: uo_out of clock c (rst_n is c >= RESET) -----------

typedef struct gen {
    const uint32_t *frame; uint64_t frame_len;   // one frame from (hpos, vpos) = (0, 0), from RESET
    int kind;                                    // 0: frame, 1: hsync toggles every 2, 2: constant, 3: 64k frame
    uint64_t glitch_v;                           // a clock whose vsync bit is inverted (0: none)
} gen_t;

static uint8_t gen_uo(const gen_t *g, uint64_t c) {
    uint8_t uo = 0;
    if (c < RESET) return 0;
    switch (g->kind) {
    case 0: uo = (uint8_t)g->frame[(c - RESET) % g->frame_len]; break;
    case 1: uo = (uint8_t)((((c - RESET) >> 1) & 1) << 7) | 0x08; break;
    case 2: uo = 0x88; break;
    case 3: {   // line 256 clocks, hsync low 32; vsync low in the last 1024 clocks of each 64Ki
        uint8_t hs = (c % 256) >= 32, vs = (c % K64) < K64 - 1024;
        uo = (uint8_t)((hs << 7) | (vs << 3));
        break;
    }
    }
    if (g->glitch_v && c == g->glitch_v) uo ^= 0x08;
    return uo;
}

// Feeds clocks [0, clocks) to calibration then the frame clock, as tb.cpp
// does: per clock (runs of 1) or as maximal runs.
static void feed(const gen_t *g, uint64_t clocks, int per_clock, double calib_seconds,
                 vgacal_t *cal, vgafc_t *fc) {
    vgacal_init(cal, HZ, calib_seconds);
    int fc_ready = 0;
    uint64_t c = 0;
    while (c < clocks) {
        uint8_t uo = gen_uo(g, c), rst = c >= RESET;
        uint64_t n = 1;
        if (!per_clock)
            while (c + n < clocks && gen_uo(g, c + n) == uo && (c + n >= RESET) == rst) n++;
        uint64_t done = 0;
        if (cal->state == VGACAL_RUNNING) done = vgacal_push(cal, uo, rst, c, n);
        if (done < n) {
            if (!fc_ready) { vgafc_init(fc, cal); fc_ready = 1; }
            vgafc_push(fc, uo, c + done, n - done);
        }
        c += n;
    }
    vgacal_end_of_trace(cal, clocks);
    if (!fc_ready) vgafc_init(fc, cal);
}

static uint32_t *frame_buf;
static vgaframe_mode_t mode640;

static uint8_t bars(void *user, uint16_t x, uint16_t y) { (void)user; (void)y; return (uint8_t)((x / 10) & 0x3F); }

static gen_t frame_gen(const vgaframe_mode_t *m) {
    uint64_t len = (uint64_t)vgaframe_mode_line_px(m) * vgaframe_mode_frame_lines(m);
    free(frame_buf);
    frame_buf = malloc(len * sizeof *frame_buf);
    synth_frame_hvsync(m, bars, NULL, frame_buf, (size_t)len);
    gen_t g = { frame_buf, len, 0, 0 };
    return g;
}

static uint64_t roundup64k(uint64_t t) { return (t + K64 - 1) / K64 * K64; }

// hvsync_generator (tests/synth.c) from (0, 0) at clock RESET: the vsync
// pulse starts (ends) on the clock hsync starts on the line before vline
// enters (leaves) the pulse.
static uint64_t v_start(const vgaframe_mode_t *m, uint64_t k) {
    uint64_t cpl = vgaframe_mode_line_px(m), f = cpl * vgaframe_mode_frame_lines(m);
    return RESET + (uint64_t)(m->v_active + m->v_front - 1) * cpl + m->h_active + m->h_front + k * f;
}
static uint64_t v_end(const vgaframe_mode_t *m, uint64_t k) {
    return v_start(m, k) + (uint64_t)m->v_sync * vgaframe_mode_line_px(m);
}

TEST(calib_640x480_vectors) {
    gen_t g = frame_gen(&mode640);
    vgacal_t cal; vgafc_t fc;
    feed(&g, 5 * 420000, 0, 0.1, &cal, &fc);
    ASSERT_EQ_U(cal.calib_limit, 2517500);
    ASSERT_EQ_U(cal.state, VGACAL_DONE);
    ASSERT_EQ_U(cal.hs.found, 1); ASSERT_EQ_U(cal.hs.active_low, 1);
    ASSERT_EQ_U(cal.hs.pulse, 96); ASSERT_EQ_U(cal.hs.period, 800);
    ASSERT_EQ_U(cal.vs.found, 1); ASSERT_EQ_U(cal.vs.active_low, 1);
    ASSERT_EQ_U(cal.vs.pulse, 1600); ASSERT_EQ_U(cal.vs.period, 420000);
    // vsync level changes (from the tracker's initial false): high at RESET
    // (active low, idle), then each pulse's start and end. The 8th change
    // completes the 8th phase; tb.cpp sees it at T = c + 1 and stops at the
    // next multiple of 65536.
    uint64_t ch[8] = { RESET, v_start(&mode640, 0), v_end(&mode640, 0), v_start(&mode640, 1),
                       v_end(&mode640, 1), v_start(&mode640, 2), v_end(&mode640, 2), v_start(&mode640, 3) };
    ASSERT_EQ_U(cal.capture_after_clock, roundup64k(ch[7] + 1));
    ASSERT_EQ_U(cal.capture_after_clock, 1703936);
    ASSERT_TRUE(cal.v_phases >= 8);
    vgacal_free(&cal); vgafc_free(&fc);
}

TEST(calib_polarities) {
    for (int p = 0; p < 4; p++) {
        vgaframe_mode_t m = mode640;
        m.h_sync_positive = (uint8_t)(p & 1); m.v_sync_positive = (uint8_t)(p >> 1);
        gen_t g = frame_gen(&m);
        vgacal_t cal; vgafc_t fc;
        feed(&g, 5 * 420000, 0, 0.1, &cal, &fc);
        ASSERT_EQ_U(cal.hs.found, 1); ASSERT_EQ_U(cal.vs.found, 1);
        ASSERT_EQ_U(cal.hs.active_low, !(p & 1));
        ASSERT_EQ_U(cal.vs.active_low, !(p >> 1));
        ASSERT_EQ_U(cal.hs.pulse, 96); ASSERT_EQ_U(cal.vs.pulse, 1600);
        vgacal_free(&cal); vgafc_free(&fc);
    }
}

TEST(calib_no_sync) {
    gen_t g = { NULL, 0, 2, 0 };
    vgacal_t cal; vgafc_t fc;
    feed(&g, 2600000, 0, 0.1, &cal, &fc);
    ASSERT_EQ_U(cal.state, VGACAL_DONE);
    ASSERT_EQ_U(cal.capture_after_clock, 2517500);
    ASSERT_EQ_U(cal.hs.found, 0); ASSERT_EQ_U(cal.vs.found, 0);
    ASSERT_EQ_U(cal.hs.active_low, 1); ASSERT_EQ_U(cal.vs.active_low, 1);   // tb.cpp's defaults
    ASSERT_EQ_U(fc.n, 0);
    vgacal_free(&cal); vgafc_free(&fc);
}

TEST(calib_glitchy_hsync) {
    // hsync changes at clock RESET + 2k (k >= 1): the k-th phase completes
    // at T = RESET + 2k + 1; tb.cpp stops after phase 4,000,001.
    gen_t g = { NULL, 0, 1, 0 };
    vgacal_t cal; vgafc_t fc;
    feed(&g, 8100000, 0, 0.5, &cal, &fc);
    ASSERT_EQ_U(cal.h_phases, 4000001);
    ASSERT_EQ_U(cal.capture_after_clock, RESET + 2 * 4000001ull + 1);
    ASSERT_EQ_U(cal.vs.found, 0);
    vgacal_free(&cal); vgafc_free(&fc);
}

static int same_cal(const vgacal_t *a, const vgacal_t *b) {
    return a->state == b->state && a->started == b->started &&
           a->capture_after_clock == b->capture_after_clock &&
           a->h_phases == b->h_phases && a->v_phases == b->v_phases &&
           !memcmp(&a->hs, &b->hs, sizeof a->hs) && !memcmp(&a->vs, &b->vs, sizeof a->vs);
}

TEST(calib_run_equals_clock) {
    gen_t gs[4];
    gs[0] = (gen_t){ NULL, 0, 2, 0 };
    gs[1] = (gen_t){ NULL, 0, 3, 0 };
    gs[2] = (gen_t){ NULL, 0, 1, 0 };
    gs[3] = frame_gen(&mode640);     // last: frame_gen reuses its buffer
    double secs[4] = { 0.1, 0.1, 0.2, 0.1 };
    uint64_t clocks[4] = { 2600000, 600000, 6000000, 4200000 };
    for (int i = 0; i < 4; i++) {
        vgacal_t a, b; vgafc_t fa, fb;
        feed(&gs[i], clocks[i], 1, secs[i], &a, &fa);
        feed(&gs[i], clocks[i], 0, secs[i], &b, &fb);
        ASSERT_TRUE(same_cal(&a, &b));
        ASSERT_EQ_U(fa.n, fb.n);
        ASSERT_TRUE(fa.n == 0 || !memcmp(fa.ends, fb.ends, fa.n * sizeof *fa.ends));
        vgacal_free(&a); vgacal_free(&b); vgafc_free(&fa); vgafc_free(&fb);
    }
}

TEST(fc_counts) {
    gen_t g = frame_gen(&mode640);
    vgacal_t cal; vgafc_t fc;
    feed(&g, 11 * 420000, 0, 0.1, &cal, &fc);
    ASSERT_TRUE(fc.n >= 6);
    uint64_t k = 0;
    while (v_end(&mode640, k) < cal.capture_after_clock + 1) k++;
    ASSERT_EQ_U(fc.ends[0], v_end(&mode640, k));
    for (size_t i = 1; i < fc.n; i++) ASSERT_EQ_U(fc.ends[i] - fc.ends[i - 1], 420000);
    vgacal_free(&cal); vgafc_free(&fc);
}

TEST(fc_end_on_capture_clock_not_counted) {
    // Frames of 64Ki clocks whose vsync pulse ends on multiples of 65536:
    // calibration stops at T = 262144, the very clock that pulse ends, and
    // tb.cpp (prev_v false) does not count it.
    gen_t g = { NULL, 0, 3, 0 };
    vgacal_t cal; vgafc_t fc;
    feed(&g, 7 * K64, 0, 0.1, &cal, &fc);
    ASSERT_EQ_U(cal.capture_after_clock, 4 * K64);
    ASSERT_EQ_U(gen_uo(&g, 4 * K64 - 1) & 0x08, 0);     // in the pulse just before
    ASSERT_EQ_U(gen_uo(&g, 4 * K64) & 0x08, 0x08);      // out of it at capture
    ASSERT_EQ_U(fc.n, 2);
    ASSERT_EQ_U(fc.ends[0], 5 * K64);
    ASSERT_EQ_U(fc.ends[1], 6 * K64);
    vgacal_free(&cal); vgafc_free(&fc);
}

TEST(fc_glitch_adds_end) {
    gen_t g = frame_gen(&mode640);
    vgacal_t cal; vgafc_t fc;
    feed(&g, 9 * 420000, 0, 0.1, &cal, &fc);
    size_t n = fc.n;
    ASSERT_TRUE(n >= 3);
    uint64_t e1 = fc.ends[1], e2 = fc.ends[2];
    vgacal_free(&cal); vgafc_free(&fc);
    g.glitch_v = e1 - 800;           // one clock out of the pulse, mid-pulse
    feed(&g, 9 * 420000, 0, 0.1, &cal, &fc);
    ASSERT_EQ_U(fc.n, n + 1);
    ASSERT_EQ_U(fc.ends[1], e1 - 800);
    ASSERT_EQ_U(fc.ends[2], e1);
    ASSERT_EQ_U(fc.ends[3], e2);
    vgacal_free(&cal); vgafc_free(&fc);
}

// tb.cpp:364 truncates the double product: 0.29 * 25175000 is 7300749.999999999.
TEST(calib_limit_double) {
    vgacal_t c;
    vgacal_init(&c, HZ, 0.29);
    ASSERT_EQ_U(c.calib_limit, 7300749);
    vgacal_init(&c, HZ, 0.1);
    ASSERT_EQ_U(c.calib_limit, 2517500);
    vgacal_free(&c);
}

int main(void) {
    size_t count;
    const vgaframe_mode_t *modes = vgaframe_modes(&count);
    mode640 = modes[0];
    if (vgaframe_mode_line_px(&mode640) != 800 || vgaframe_mode_frame_lines(&mode640) != 525) {
        fprintf(stderr, "mode table entry 0 is not 640x480@60\n");
        return 1;
    }
    RUN(calib_limit_double);
    RUN(calib_640x480_vectors);
    RUN(calib_polarities);
    RUN(calib_no_sync);
    RUN(calib_glitchy_hsync);
    RUN(calib_run_equals_clock);
    RUN(fc_counts);
    RUN(fc_end_on_capture_clock_not_counted);
    RUN(fc_glitch_adds_end);
    free(frame_buf);
    RUN_TESTS_END();
}
