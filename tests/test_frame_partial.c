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
    // Regression: emit()'s clocks-per-line table match (used above for the
    // crop) must also be reported through out->timing, not just used
    // internally - the free-running learner alone can never complete this
    // measurement from windows that never span a vsync transition on both
    // sides, so out->timing used to come back with mode==NULL,
    // lines_per_frame==0, and vsync_positive stuck at its zero default.
    ASSERT_TRUE(last.timing->mode != NULL);
    ASSERT_TRUE(strcmp(last.timing->mode->name, "640x480@60") == 0);
    ASSERT_EQ_U(last.timing->lines_per_frame, 525);
    ASSERT_EQ_U(last.timing->vsync_positive, 0);
}

TEST(fram_no_force_reports_resolved_800x600) {
    // Same regression as windows_reassemble_without_force_mode, but for a
    // mode with positive-polarity syncs: the resolved match must supply the
    // true hsync/vsync polarity, not the learner's un-computed zero default
    // (which happens to print as "neg" even when the real polarity is pos).
    const vgaframe_mode_t *m = vgaframe_mode_match(1056, 628);
    size_t n = synth_frame(m, grid, NULL, buf, sizeof buf / sizeof buf[0]); ASSERT_TRUE(n == 1056u * 628u);
    vgaframe_config_t c; memset(&c, 0, sizeof c); c.max_clocks_per_line = 1400; c.max_lines = 900;
    static const uint8_t map[8] = { 7, 3, 0, 4, 1, 5, 2, 6 }; memcpy(c.signal_map, map, 8);
    c.force_mode = NULL; c.on_frame = on_frame;
    vgaframe_t f; nframes = npartial = 0; ASSERT_EQ_U(vgaframe_init(&f, &c, raw, rgb, cover), 0);
    for (uint16_t first = 0; first < 628; first = (uint16_t)(first + 25)) {
        uint16_t count = (uint16_t)(628 - first < 25 ? 628 - first : 25);
        vgaframe_frame_begin(&f, 9, first, count, 1056, (uint32_t)count * 1056);
        for (uint32_t i = 0; i < (uint32_t)count * 1056u; i++) vgaframe_push(&f, buf[(uint32_t)first * 1056u + i], 1);
    }
    ASSERT_EQ_U(nframes, 1); ASSERT_EQ_U(last.frame_counter, 9);
    ASSERT_TRUE(last.timing->mode != NULL);
    ASSERT_TRUE(strcmp(last.timing->mode->name, "800x600@60") == 0);
    ASSERT_EQ_U(last.timing->hsync_positive, 1);
    ASSERT_EQ_U(last.timing->vsync_positive, 1);
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

TEST(continuous_frames_after_fram_still_reconstruct) {
    // FRAM mode used to latch for the lifetime of the object (I3): once any
    // FRAM chunk had been seen, learner-detected frame boundaries were
    // ignored forever and continuous chunks were silently discarded. The
    // firmware is specified to emit both families, so this mixed stream is
    // the expected case. One full frame delivered as FRAM windows, then
    // three frames of ordinary continuous samples.
    const vgaframe_mode_t *m = vgaframe_mode_match(800, 525);
    size_t n = synth_frame(m, grid, NULL, buf, sizeof buf / sizeof buf[0]); ASSERT_TRUE(n == 800u * 525u);
    vgaframe_config_t c; memset(&c, 0, sizeof c); c.max_clocks_per_line = 1400; c.max_lines = 900;
    static const uint8_t map[8] = { 7, 3, 0, 4, 1, 5, 2, 6 }; memcpy(c.signal_map, map, 8);
    c.force_mode = NULL; c.on_frame = on_frame;
    vgaframe_t f; nframes = npartial = 0; ASSERT_EQ_U(vgaframe_init(&f, &c, raw, rgb, cover), 0);

    for (uint16_t first = 0; first < 525; first = (uint16_t)(first + 25)) {
        vgaframe_frame_begin(&f, 1, first, 25, 800, 25 * 800);
        for (uint32_t i = 0; i < 25u * 800u; i++) vgaframe_push(&f, buf[(uint32_t)first * 800u + i], 1);
    }
    ASSERT_EQ_U(nframes, 1); ASSERT_EQ_U(npartial, 0); ASSERT_EQ_U(last.frame_counter, 1);
    ASSERT_EQ_U(last.width, 640); ASSERT_EQ_U(last.height, 480);

    // Continuous samples, no further FRAM metadata. FRAM windows never span
    // a vsync transition on both sides, so the learner comes out of the FRAM
    // phase knowing clocks_per_line but not the frame period: as in
    // converges_from_a_mid_frame_start, the first frame boundary in this
    // data cannot be recognised as one, and a complete picture needs a full
    // period after the first *recognised* boundary. Four frames of data is
    // therefore the smallest input that yields two complete pictures, for
    // any implementation of this design.
    for (int fr = 0; fr < 4; fr++) for (size_t i = 0; i < n; i++) vgaframe_push(&f, buf[i], 1);
    ASSERT_TRUE(nframes >= 3);          // the FRAM frame plus >= 2 continuous ones
    ASSERT_EQ_U(npartial, 0);
    ASSERT_EQ_U(last.width, 640); ASSERT_EQ_U(last.height, 480);
    ASSERT_TRUE(last.timing->mode != NULL);
    // ...and the continuous reconstruction is pixel-exact, not merely present.
    for (uint16_t y = 0; y < 480; y++) for (uint16_t x = 0; x < 640; x++) {
        uint8_t g = grid(NULL, x, y); const uint8_t *q = last_rgb + (y * 640 + x) * 3;
        ASSERT_EQ_U(q[0], ((g >> 4) & 3) * 85); ASSERT_EQ_U(q[1], ((g >> 2) & 3) * 85); ASSERT_EQ_U(q[2], (g & 3) * 85);
    }
}

TEST(incomplete_fram_flushes_at_a_continuous_frame_start) {
    // A FRAM accumulation that never completes must not sit in the buffer
    // forever: the next continuous-mode frame start publishes it as partial
    // and frees the buffer for the continuous reconstruction.
    const vgaframe_mode_t *m = vgaframe_mode_match(800, 525);
    size_t n = synth_frame(m, grid, NULL, buf, sizeof buf / sizeof buf[0]); ASSERT_TRUE(n == 800u * 525u);
    vgaframe_config_t c; memset(&c, 0, sizeof c); c.max_clocks_per_line = 1400; c.max_lines = 900;
    static const uint8_t map[8] = { 7, 3, 0, 4, 1, 5, 2, 6 }; memcpy(c.signal_map, map, 8);
    c.force_mode = m; c.on_frame = on_frame;
    vgaframe_t f; nframes = npartial = 0; ASSERT_EQ_U(vgaframe_init(&f, &c, raw, rgb, cover), 0);
    vgaframe_frame_begin(&f, 3, 100, 25, 800, 25 * 800);   // one window only: never complete
    for (uint32_t i = 0; i < 25u * 800u; i++) vgaframe_push(&f, buf[100u * 800u + i], 1);
    ASSERT_EQ_U(nframes, 0);
    for (int fr = 0; fr < 4; fr++) for (size_t i = 0; i < n; i++) vgaframe_push(&f, buf[i], 1);
    ASSERT_EQ_U(npartial, 1);           // exactly one: the abandoned FRAM frame
    ASSERT_TRUE(nframes >= 3);          // the partial plus >= 2 complete continuous frames
    ASSERT_EQ_U(last.partial, 0);
    for (uint16_t y = 0; y < 480; y++) for (uint16_t x = 0; x < 640; x++) {
        uint8_t g = grid(NULL, x, y); const uint8_t *q = last_rgb + (y * 640 + x) * 3;
        ASSERT_EQ_U(q[0], ((g >> 4) & 3) * 85); ASSERT_EQ_U(q[2], (g & 3) * 85);
    }
}

int main(void) {
    RUN(windows_reassemble_into_one_frame);
    RUN(windows_reassemble_without_force_mode);
    RUN(fram_no_force_reports_resolved_800x600);
    RUN(fram_flush_clears_stale_rows);
    RUN(new_counter_flushes_partial);
    RUN(continuous_frames_after_fram_still_reconstruct);
    RUN(incomplete_fram_flushes_at_a_continuous_frame_start);
    RUN_TESTS_END();
}
