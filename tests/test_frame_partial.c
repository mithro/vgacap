// SPDX-License-Identifier: Apache-2.0
#include "harness.h"
#include "vgacap/frame.h"
#include "synth.h"

static uint32_t buf[1400 * 900];
static uint8_t raw[1400 * 900], rgb[1400 * 900 * 3], cover[900];
static uint8_t grid(void *u, uint16_t x, uint16_t y) { (void)u; return (uint8_t)(((x % 8) == 0 || (y % 8) == 0) ? 0x3F : 0x10); }
static vgaframe_output_t last; static uint8_t last_rgb[1400 * 900 * 3]; static int nframes, npartial;
static void on_frame(void *u, const vgaframe_output_t *o) { (void)u; last = *o; memcpy(last_rgb, o->rgb24, (size_t)o->width * o->height * 3); nframes++; if (o->partial) npartial++; }

TEST(windows_reassemble_into_one_frame) {
    const vgaframe_mode_t *m = vgaframe_mode_match(800, 525);
    size_t n = synth_frame(m, grid, NULL, buf, sizeof buf / sizeof buf[0]); ASSERT_TRUE(n == 800u * 525u);
    vgaframe_config_t c; memset(&c, 0, sizeof c); c.max_clocks_per_line = 1400; c.max_lines = 900;
    static const uint8_t map[8] = { 7, 3, 0, 4, 1, 5, 2, 6 }; memcpy(c.signal_map, map, 8); c.force_mode = m; c.on_frame = on_frame;
    vgaframe_t f; nframes = npartial = 0; ASSERT_EQ_U(vgaframe_init(&f, &c, raw, rgb, cover), 0);
    // deliver the frame as 21 windows of 25 lines, each tagged with the same frame counter 5, in shuffled order
    static const int order[21] = { 7, 0, 20, 3, 15, 1, 9, 12, 4, 18, 2, 11, 6, 19, 5, 13, 8, 16, 10, 17, 14 };
    for (int k = 0; k < 21; k++) { uint16_t first = (uint16_t)(order[k] * 25);
        vgaframe_frame_begin(&f, 5, first, 25, 800, 25 * 800);
        for (uint32_t i = 0; i < 25u * 800u; i++) vgaframe_push(&f, buf[first * 800u + i], 1);
    }
    ASSERT_EQ_U(nframes, 1); ASSERT_EQ_U(npartial, 0); ASSERT_EQ_U(last.frame_counter, 5);
    ASSERT_EQ_U(last.width, 640); ASSERT_EQ_U(last.height, 480);
    for (uint16_t y = 0; y < 480; y++) for (uint16_t x = 0; x < 640; x++) {
        uint8_t g = grid(NULL, x, y); const uint8_t *q = last_rgb + (y * 640 + x) * 3;
        ASSERT_EQ_U(q[0], ((g >> 4) & 3) * 85); ASSERT_EQ_U(q[2], (g & 3) * 85);
    }
}

TEST(windows_reassemble_without_force_mode) {
    // Same reassembly as windows_reassemble_into_one_frame, but with no
    // force_mode: the learner cannot learn lines_per_frame from windows that
    // never span a vsync transition on both sides, so expected_lines() and
    // the crop must fall back to a table match on the FRAM chunk's
    // clocks_per_line (I2).
    const vgaframe_mode_t *m = vgaframe_mode_match(800, 525);
    size_t n = synth_frame(m, grid, NULL, buf, sizeof buf / sizeof buf[0]); ASSERT_TRUE(n == 800u * 525u);
    vgaframe_config_t c; memset(&c, 0, sizeof c); c.max_clocks_per_line = 1400; c.max_lines = 900;
    static const uint8_t map[8] = { 7, 3, 0, 4, 1, 5, 2, 6 }; memcpy(c.signal_map, map, 8);
    c.force_mode = NULL; c.on_frame = on_frame;
    vgaframe_t f; nframes = npartial = 0; ASSERT_EQ_U(vgaframe_init(&f, &c, raw, rgb, cover), 0);
    static const int order[21] = { 7, 0, 20, 3, 15, 1, 9, 12, 4, 18, 2, 11, 6, 19, 5, 13, 8, 16, 10, 17, 14 };
    for (int k = 0; k < 21; k++) { uint16_t first = (uint16_t)(order[k] * 25);
        vgaframe_frame_begin(&f, 5, first, 25, 800, 25 * 800);
        for (uint32_t i = 0; i < 25u * 800u; i++) vgaframe_push(&f, buf[first * 800u + i], 1);
    }
    ASSERT_EQ_U(nframes, 1); ASSERT_EQ_U(npartial, 0); ASSERT_EQ_U(last.frame_counter, 5);
    ASSERT_EQ_U(last.width, 640); ASSERT_EQ_U(last.height, 480);
    for (uint16_t y = 0; y < 480; y++) for (uint16_t x = 0; x < 640; x++) {
        uint8_t g = grid(NULL, x, y); const uint8_t *q = last_rgb + (y * 640 + x) * 3;
        ASSERT_EQ_U(q[0], ((g >> 4) & 3) * 85); ASSERT_EQ_U(q[2], (g & 3) * 85);
    }
}

TEST(fram_flush_clears_stale_rows) {
    // A window at lines 400-424, then a window at lines 0-24, then flush: the
    // flush's partial emit must clear every row that was ever written this
    // frame, not just up to the last window's line count (I3), or row 400
    // leaks stale "written" pixels into whatever frame comes next.
    const vgaframe_mode_t *m = vgaframe_mode_match(800, 525);
    size_t n = synth_frame(m, grid, NULL, buf, sizeof buf / sizeof buf[0]); ASSERT_TRUE(n == 800u * 525u);
    vgaframe_config_t c; memset(&c, 0, sizeof c); c.max_clocks_per_line = 1400; c.max_lines = 900;
    static const uint8_t map[8] = { 7, 3, 0, 4, 1, 5, 2, 6 }; memcpy(c.signal_map, map, 8); c.force_mode = m; c.on_frame = on_frame;
    vgaframe_t f; nframes = npartial = 0; ASSERT_EQ_U(vgaframe_init(&f, &c, raw, rgb, cover), 0);
    vgaframe_frame_begin(&f, 1, 400, 25, 800, 25 * 800);
    for (uint32_t i = 0; i < 25u * 800u; i++) vgaframe_push(&f, buf[400u * 800u + i], 1);
    vgaframe_frame_begin(&f, 1, 0, 25, 800, 25 * 800);
    for (uint32_t i = 0; i < 25u * 800u; i++) vgaframe_push(&f, buf[i], 1);
    ASSERT_EQ_U(f.raw[400 * 1400] & 0x80, 0x80); // sanity: row 400 was written
    vgaframe_flush(&f);
    ASSERT_EQ_U(nframes, 1); ASSERT_EQ_U(last.partial, 1);
    ASSERT_EQ_U(f.raw[400 * 1400], 0); // row 400 must be cleared, not left as stale picture
}

TEST(new_counter_flushes_partial) {
    const vgaframe_mode_t *m = vgaframe_mode_match(800, 525);
    size_t n = synth_frame(m, grid, NULL, buf, sizeof buf / sizeof buf[0]); (void)n;
    vgaframe_config_t c; memset(&c, 0, sizeof c); c.max_clocks_per_line = 1400; c.max_lines = 900;
    static const uint8_t map[8] = { 7, 3, 0, 4, 1, 5, 2, 6 }; memcpy(c.signal_map, map, 8); c.force_mode = m; c.on_frame = on_frame;
    vgaframe_t f; nframes = npartial = 0; vgaframe_init(&f, &c, raw, rgb, cover);
    vgaframe_frame_begin(&f, 1, 0, 100, 800, 100 * 800); for (uint32_t i = 0; i < 100u * 800u; i++) vgaframe_push(&f, buf[i], 1);
    vgaframe_frame_begin(&f, 2, 0, 100, 800, 100 * 800);
    ASSERT_EQ_U(nframes, 1); ASSERT_EQ_U(npartial, 1); ASSERT_EQ_U(last.frame_counter, 1);
}

int main(void) {
    RUN(windows_reassemble_into_one_frame);
    RUN(windows_reassemble_without_force_mode);
    RUN(fram_flush_clears_stale_rows);
    RUN(new_counter_flushes_partial);
    RUN_TESTS_END();
}
