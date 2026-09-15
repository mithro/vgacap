// SPDX-License-Identifier: Apache-2.0
#include "harness.h"
#include "vgacap/frame.h"
#include "synth.h"

static uint32_t buf[1400 * 900];
static uint8_t black(void *u, uint16_t x, uint16_t y) { (void)u; (void)x; (void)y; return 0; }

static void learn(const vgaframe_mode_t *m, vgaframe_timing_learner_t *l, int frames) {
    size_t n = synth_frame(m, black, NULL, buf, sizeof buf / sizeof buf[0]);
    ASSERT_TRUE(n > 0);
    vgaframe_timing_init(l);
    for (int f = 0; f < frames; f++)
        for (size_t i = 0; i < n; i++) vgaframe_timing_push(l, (uint8_t)((buf[i] >> 7) & 1), (uint8_t)((buf[i] >> 3) & 1), 1);
}

TEST(learns_640x480_negative_syncs) {
    vgaframe_timing_learner_t l; learn(vgaframe_mode_match(800, 525), &l, 3);
    ASSERT_EQ_U(l.t.clocks_per_line, 800); ASSERT_EQ_U(l.t.lines_per_frame, 525);
    ASSERT_EQ_U(l.t.hsync_width, 96); ASSERT_EQ_U(l.t.vsync_lines, 2);
    ASSERT_EQ_U(l.t.hsync_positive, 0); ASSERT_EQ_U(l.t.vsync_positive, 0);
    ASSERT_EQ_U(l.t.locked, 1); ASSERT_TRUE(l.t.mode != NULL); ASSERT_TRUE(strcmp(l.t.mode->name, "640x480@60") == 0);
}

TEST(learns_800x600_positive_syncs) {
    vgaframe_timing_learner_t l; learn(vgaframe_mode_match(1056, 628), &l, 3);
    ASSERT_EQ_U(l.t.clocks_per_line, 1056); ASSERT_EQ_U(l.t.lines_per_frame, 628);
    ASSERT_EQ_U(l.t.hsync_positive, 1); ASSERT_EQ_U(l.t.vsync_positive, 1); ASSERT_EQ_U(l.t.locked, 1);
}

TEST(runs_are_equivalent_to_samples) {
    const vgaframe_mode_t *m = vgaframe_mode_match(800, 525);
    size_t n = synth_frame(m, black, NULL, buf, sizeof buf / sizeof buf[0]);
    vgaframe_timing_learner_t l; vgaframe_timing_init(&l);
    for (int f = 0; f < 3; f++) { size_t i = 0; while (i < n) { size_t j = i; while (j < n && buf[j] == buf[i]) j++;
        vgaframe_timing_push(&l, (uint8_t)((buf[i] >> 7) & 1), (uint8_t)((buf[i] >> 3) & 1), (uint32_t)(j - i)); i = j; } }
    ASSERT_EQ_U(l.t.clocks_per_line, 800); ASSERT_EQ_U(l.t.lines_per_frame, 525); ASSERT_EQ_U(l.t.locked, 1);
}

TEST(reports_line_and_frame_starts) {
    const vgaframe_mode_t *m = vgaframe_mode_match(800, 525);
    size_t n = synth_frame(m, black, NULL, buf, sizeof buf / sizeof buf[0]);
    vgaframe_timing_learner_t l; vgaframe_timing_init(&l); int lines = 0, frames = 0;
    for (int f = 0; f < 3; f++) for (size_t i = 0; i < n; i++) { int r = vgaframe_timing_push(&l, (uint8_t)((buf[i] >> 7) & 1), (uint8_t)((buf[i] >> 3) & 1), 1);
        if (r == 1) lines++;
        if (r == 2) frames++;
    }
    ASSERT_TRUE(frames >= 2); ASSERT_TRUE(lines >= 2 * 525);
}

TEST(locks_from_a_stream_that_starts_mid_frame) {
    // A capture that begins well before the first vsync pulse (the
    // realistic case - nothing guarantees capture starts at a frame
    // boundary) must still converge on the right timing. The first pulse
    // entry after such a start can never be recognised as one (its own
    // duration has never been measured when it begins), which used to
    // leave line_in_frame counting "lines since the process started"
    // instead of "lines since the last frame start", corrupting the very
    // next measurement even though vsync polarity/width were identified
    // correctly.
    const vgaframe_mode_t *m = vgaframe_mode_match(800, 525);
    size_t n = synth_frame(m, black, NULL, buf, sizeof buf / sizeof buf[0]);
    vgaframe_timing_learner_t l; vgaframe_timing_init(&l);
    size_t offset = 300u * 800u; // start 300 lines into a frame
    for (size_t k = 0; k < 3u * n; k++) {
        size_t i = (offset + k) % n;
        vgaframe_timing_push(&l, (uint8_t)((buf[i] >> 7) & 1), (uint8_t)((buf[i] >> 3) & 1), 1);
    }
    ASSERT_EQ_U(l.t.clocks_per_line, 800); ASSERT_EQ_U(l.t.lines_per_frame, 525);
    ASSERT_EQ_U(l.t.vsync_lines, 2); ASSERT_EQ_U(l.t.locked, 1);
    ASSERT_TRUE(l.t.mode != NULL); ASSERT_TRUE(strcmp(l.t.mode->name, "640x480@60") == 0);
}

int main(void) {
    RUN(learns_640x480_negative_syncs);
    RUN(learns_800x600_positive_syncs);
    RUN(runs_are_equivalent_to_samples);
    RUN(reports_line_and_frame_starts);
    RUN(locks_from_a_stream_that_starts_mid_frame);
    RUN_TESTS_END();
}
