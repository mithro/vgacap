// SPDX-License-Identifier: Apache-2.0
#include "harness.h"
#include "vgacap/frame.h"
#include "synth.h"

static uint32_t buf[1400 * 900];
static uint8_t raw[1400 * 900], rgb[1400 * 900 * 3], cover[900];
static uint8_t bars(void *u, uint16_t x, uint16_t y) { (void)u; (void)y; return (uint8_t)((x / 10) & 0x3F); }
static vgaframe_output_t last; static uint8_t last_rgb[1400 * 900 * 3]; static int nframes;
static void on_frame(void *u, const vgaframe_output_t *o) { (void)u; last = *o; memcpy(last_rgb, o->rgb24, (size_t)o->width * o->height * 3); nframes++; }

static void setup(vgaframe_t *f, const vgaframe_mode_t *force) {
    vgaframe_config_t c; memset(&c, 0, sizeof c); c.max_clocks_per_line = 1400; c.max_lines = 900;
    static const uint8_t map[8] = { 7, 3, 0, 4, 1, 5, 2, 6 }; memcpy(c.signal_map, map, 8);
    c.force_mode = force; c.on_frame = on_frame; nframes = 0;
    ASSERT_EQ_U(vgaframe_init(f, &c, raw, rgb, cover), 0);
}

TEST(colour_helper) {
    static const uint8_t map[8] = { 7, 3, 0, 4, 1, 5, 2, 6 };
    ASSERT_EQ_U(vgaframe_colour(map, 0x77), 0x3F);        // all colour bits set, syncs clear
    ASSERT_EQ_U(vgaframe_colour(map, 0x11), 0x30);        // r1 (bit0) + r0 (bit4) => rr=3
}

TEST(reconstructs_640x480_bars) {
    const vgaframe_mode_t *m = vgaframe_mode_match(800, 525);
    size_t n = synth_frame(m, bars, NULL, buf, sizeof buf / sizeof buf[0]);
    vgaframe_t f; setup(&f, NULL);
    for (int fr = 0; fr < 3; fr++) for (size_t i = 0; i < n; i++) vgaframe_push(&f, buf[i], 1);
    ASSERT_TRUE(nframes >= 1);
    ASSERT_EQ_U(last.width, 640); ASSERT_EQ_U(last.height, 480); ASSERT_EQ_U(last.partial, 0);
    ASSERT_TRUE(last.timing->mode != NULL);
    // pixel (25, 100): bar index 2 => colour 0x02 => B=2*85
    const uint8_t *p = last_rgb + (100 * 640 + 25) * 3;
    ASSERT_EQ_U(p[0], 0); ASSERT_EQ_U(p[1], 0); ASSERT_EQ_U(p[2], 170);
    // pixel (639, 479): bar 63 => 0x3F => white
    p = last_rgb + (479 * 640 + 639) * 3; ASSERT_EQ_U(p[0], 255); ASSERT_EQ_U(p[1], 255); ASSERT_EQ_U(p[2], 255);
    // full-image check against the generator
    for (uint16_t y = 0; y < 480; y++) for (uint16_t x = 0; x < 640; x++) {
        uint8_t c = bars(NULL, x, y); const uint8_t *q = last_rgb + (y * 640 + x) * 3;
        ASSERT_EQ_U(q[0], ((c >> 4) & 3) * 85); ASSERT_EQ_U(q[1], ((c >> 2) & 3) * 85); ASSERT_EQ_U(q[2], (c & 3) * 85);
    }
}

TEST(reconstructs_with_runs_and_800x600) {
    const vgaframe_mode_t *m = vgaframe_mode_match(1056, 628);
    size_t n = synth_frame(m, bars, NULL, buf, sizeof buf / sizeof buf[0]);
    vgaframe_t f; setup(&f, NULL);
    for (int fr = 0; fr < 3; fr++) { size_t i = 0; while (i < n) { size_t j = i; while (j < n && buf[j] == buf[i]) j++; vgaframe_push(&f, buf[i], (uint32_t)(j - i)); i = j; } }
    ASSERT_TRUE(nframes >= 1); ASSERT_EQ_U(last.width, 800); ASSERT_EQ_U(last.height, 600); ASSERT_EQ_U(last.partial, 0);
    const uint8_t *p = last_rgb + (10 * 800 + 799) * 3; uint8_t c = bars(NULL, 799, 10);
    ASSERT_EQ_U(p[0], ((c >> 4) & 3) * 85);
}

TEST(table_match_wins_over_odd_porches) {
    // 640x480 timing but with an odd back porch: (cpl, lpf) is still (800, 525),
    // which the table matches on length alone, so the crop is the table's
    // mode rather than an auto bounding box of the (shifted) active area.
    vgaframe_mode_t odd = *vgaframe_mode_match(800, 525); odd.h_back = 50; odd.h_front = 14; // still 800 clocks
    odd.v_back = 30; odd.v_front = 13; odd.name = "odd";
    size_t n = synth_frame(&odd, bars, NULL, buf, sizeof buf / sizeof buf[0]);
    vgaframe_t f; setup(&f, NULL);
    for (int fr = 0; fr < 3; fr++) for (size_t i = 0; i < n; i++) vgaframe_push(&f, buf[i], 1);
    ASSERT_TRUE(nframes >= 1);
    // table still matches on 800x525 (mode chosen by lengths) so the crop is the table's; verify the shift shows up as expected
    ASSERT_EQ_U(last.width, 640); ASSERT_EQ_U(last.height, 480);
    ASSERT_TRUE(last.timing->mode != NULL);
}

TEST(oversized_run_does_not_overflow) {
    // A run length decoded from an untrusted stream can be an arbitrary
    // uint32_t. f->x + run must not be allowed to wrap and defeat the clip
    // to the buffer width (C1); it must clip exactly at the buffer edge.
    const vgaframe_mode_t *m = vgaframe_mode_match(800, 525);
    size_t n = synth_frame(m, bars, NULL, buf, sizeof buf / sizeof buf[0]);
    vgaframe_t f; setup(&f, m);
    for (int fr = 0; fr < 2; fr++) for (size_t k = 0; k < n; k++) vgaframe_push(&f, buf[k], 1); // get in_frame == 1
    size_t i = 0;
    for (; i < 100; i++) vgaframe_push(&f, buf[i], 1); // f.x == 100, W - f.x == 1300
    vgaframe_push(&f, buf[i], 0xFFFFFFF0u);            // f.x + run wraps to 84 (< W) if unclipped
    ASSERT_EQ_U(f.cover[f.y], 1);
    ASSERT_TRUE((f.raw[f.y * f.cfg.max_clocks_per_line + (f.cfg.max_clocks_per_line - 1)] & 0x80) != 0);
}

TEST(force_mode_wider_than_buffer_emits_nothing) {
    // A force_mode whose active area starts at or past the buffer edge
    // (h_sync + h_back >= max_clocks_per_line) must not underflow the crop
    // clamp (I1); emit() should just skip the frame rather than crash.
    vgaframe_mode_t narrow; memset(&narrow, 0, sizeof narrow);
    narrow.name = "narrow";
    narrow.h_active = 10; narrow.h_front = 5; narrow.h_sync = 5; narrow.h_back = 400; // h_sync+h_back = 405
    narrow.v_active = 5;  narrow.v_front = 2; narrow.v_sync = 2; narrow.v_back = 3;
    size_t n = synth_frame(&narrow, bars, NULL, buf, sizeof buf / sizeof buf[0]);
    ASSERT_TRUE(n > 0);
    vgaframe_config_t c; memset(&c, 0, sizeof c);
    c.max_clocks_per_line = 300; c.max_lines = 50; // narrower than h_sync+h_back
    static const uint8_t map[8] = { 7, 3, 0, 4, 1, 5, 2, 6 }; memcpy(c.signal_map, map, 8);
    c.force_mode = &narrow; c.on_frame = on_frame; nframes = 0;
    vgaframe_t f; ASSERT_EQ_U(vgaframe_init(&f, &c, raw, rgb, cover), 0);
    for (int fr = 0; fr < 3; fr++) for (size_t i = 0; i < n; i++) vgaframe_push(&f, buf[i], 1);
    ASSERT_EQ_U(nframes, 0);
}

TEST(flush_emits_partial) {
    const vgaframe_mode_t *m = vgaframe_mode_match(800, 525);
    size_t n = synth_frame(m, bars, NULL, buf, sizeof buf / sizeof buf[0]);
    vgaframe_t f; setup(&f, m);
    for (int fr = 0; fr < 2; fr++) for (size_t i = 0; i < n; i++) vgaframe_push(&f, buf[i], 1);
    for (size_t i = 0; i < n / 2; i++) vgaframe_push(&f, buf[i], 1);
    int before = nframes; vgaframe_flush(&f);
    ASSERT_EQ_U(nframes, before + 1); ASSERT_EQ_U(last.partial, 1);
}

int main(void) {
    RUN(colour_helper);
    RUN(reconstructs_640x480_bars);
    RUN(reconstructs_with_runs_and_800x600);
    RUN(table_match_wins_over_odd_porches);
    RUN(oversized_run_does_not_overflow);
    RUN(force_mode_wider_than_buffer_emits_nothing);
    RUN(flush_emits_partial);
    RUN_TESTS_END();
}
