// SPDX-License-Identifier: Apache-2.0
// The timing learner at simulation timings (762- and 3,584-clock lines) and at
// the exact phase of tt-vga-testpatterns' hvsync_generator.
#include "harness.h"
#include "vgacap/frame.h"
#include "synth.h"

static uint32_t buf[3584u * 525u];
static uint8_t raw[7596u * 1200u], rgb[7596u * 1200u * 3u], cover[1200];

static const uint8_t tiny_map[8] = { 7, 3, 0, 4, 1, 5, 2, 6 };

static uint8_t grad(void *u, uint16_t x, uint16_t y) { (void)u; return (uint8_t)((x * 3u + y * 5u) & 0x3F); }

static void setup(vgaframe_t *f, uint16_t maxc, uint16_t maxl, vgaframe_raw_fn on_raw, void *raw_user) {
    vgaframe_config_t c; memset(&c, 0, sizeof c);
    c.max_clocks_per_line = maxc; c.max_lines = maxl;
    memcpy(c.signal_map, tiny_map, 8);
    c.on_raw = on_raw; c.raw_user = raw_user;
    if (vgaframe_init(f, &c, raw, rgb, cover) != 0) { fprintf(stderr, "  vgaframe_init failed\n"); exit(1); }
}

TEST(learner_762) {
    static const vgaframe_mode_t m = { "sim762", 610, 15, 91, 46, 480, 10, 2, 33, 0, 0 };
    size_t n = synth_frame(&m, grad, NULL, buf, sizeof buf / sizeof buf[0]);
    ASSERT_EQ_U(n, 762u * 525u);
    vgaframe_t f; setup(&f, 1400, 900, NULL, NULL);
    for (int fr = 0; fr < 3; fr++) for (size_t i = 0; i < n; i++) vgaframe_push(&f, buf[i], 1);
    const vgaframe_timing_t *t = &f.learner.t;
    ASSERT_EQ_U(t->clocks_per_line, 762);
    ASSERT_EQ_U(t->hsync_width, 91);
    ASSERT_EQ_U(t->lines_per_frame, 525);
    ASSERT_EQ_U(t->locked, 1);
}

TEST(learner_3584) {
    static const vgaframe_mode_t m = { "sim3584", 2560, 64, 256, 704, 480, 10, 2, 33, 0, 0 };
    size_t n = synth_frame(&m, grad, NULL, buf, sizeof buf / sizeof buf[0]);
    ASSERT_EQ_U(n, 3584u * 525u);
    vgaframe_t f; setup(&f, 7596, 1200, NULL, NULL);
    for (int fr = 0; fr < 3; fr++) for (size_t i = 0; i < n; i++) vgaframe_push(&f, buf[i], 1);
    const vgaframe_timing_t *t = &f.learner.t;
    ASSERT_EQ_U(t->clocks_per_line, 3584);
    ASSERT_EQ_U(t->hsync_width, 256);
    ASSERT_EQ_U(t->lines_per_frame, 525);
    ASSERT_EQ_U(t->locked, 1);
}

// hvsync_row0: every raw frame, row v_sync + v_back holds vpos = 0's pixels from
// x = h_sync + h_back, and the rows after it hold vpos = 1.. in turn.
typedef struct row0_log { int frames, bad_rows, bad_px, locked_frames, retro_frames; } row0_log_t;

static void on_raw_row0(void *u, const vgaframe_raw_t *r) {
    row0_log_t *l = u;
    l->frames++;
    if (r->timing->locked) l->locked_frames++;
    if (r->retroactive) l->retro_frames++;
    if (r->lines != 525) { l->bad_rows++; return; }
    for (uint32_t y = 0; y < 480; y++) {
        const uint8_t *row = r->raw + (size_t)(2 + 33 + y) * r->stride;
        for (uint32_t x = 0; x < 640; x++)
            if (row[96 + 48 + x] != (uint8_t)(grad(NULL, (uint16_t)x, (uint16_t)y) | 0x80)) l->bad_px++;
    }
}

TEST(hvsync_row0) {
    const vgaframe_mode_t *m = vgaframe_mode_match(800, 525);
    ASSERT_TRUE(m != NULL);
    size_t n = synth_frame_hvsync(m, grad, NULL, buf, sizeof buf / sizeof buf[0]);
    ASSERT_EQ_U(n, 800u * 525u);
    row0_log_t log; memset(&log, 0, sizeof log);
    vgaframe_t f; setup(&f, 1400, 900, on_raw_row0, &log);
    for (int fr = 0; fr < 4; fr++) for (size_t i = 0; i < n; i++) vgaframe_push(&f, buf[i], 1);
    // The stream starts at hpos = vpos = 0, so the first vsync is the retroactive
    // frame start; each later vsync closes one frame.
    ASSERT_TRUE(log.frames >= 3);
    ASSERT_EQ_U(log.bad_rows, 0);
    ASSERT_EQ_U(log.bad_px, 0);
    ASSERT_EQ_U(f.learner.t.hsync_width, 96);
    ASSERT_EQ_U(f.learner.t.locked, 1);
}

int main(void) {
    RUN(learner_762);
    RUN(learner_3584);
    RUN(hvsync_row0);
    RUN_TESTS_END();
}
