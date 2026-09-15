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

// Overwrites `width` clocks of hsync, starting `clk` clocks into `line`, with
// the mode's pulse level: a spurious pulse of exactly the shape real hardware
// produces (see the tt08 Tiny Logo capture). Only bit 7 (hsync) is touched,
// so the injected pulse cannot change a single pixel's colour.
static void inject_hsync_glitch(uint32_t *b, const vgaframe_mode_t *m,
                                uint32_t line, uint32_t clk, uint32_t width) {
    uint32_t cpl = (uint32_t)m->h_active + m->h_front + m->h_sync + m->h_back;
    for (uint32_t i = 0; i < width; i++) {
        size_t k = (size_t)line * cpl + clk + i;
        if (m->h_sync_positive) b[k] |= 0x80u; else b[k] &= ~0x80u;
    }
}

// The three glitches the hardware evidence calls for: two inside the active
// area (one shorter and one longer than a sync pulse, both far enough from
// the line start to pass the distance test on their own) and one inside the
// blanking. Returns how many were injected.
static int inject_glitch_set(uint32_t *b, const vgaframe_mode_t *m) {
    uint32_t cpl = (uint32_t)m->h_active + m->h_front + m->h_sync + m->h_back;
    inject_hsync_glitch(b, m, 100, cpl / 2 + 60, 2);
    inject_hsync_glitch(b, m, 200, cpl / 2 + 140, 30);
    inject_hsync_glitch(b, m, 300, cpl - 10, 6);   // front porch: blanking
    return 3;
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

// Feeds one clean frame (so hsync_width and clocks_per_line are learned),
// then `frames` copies of the same frame with glitches injected. Real silicon
// (tt08's tt_um_rejunity_vga_logo) emits hsync pulses of 2 to 30 clocks a few
// times per capture; before the filter they each counted as a line start and
// no frame ever measured the right number of lines.
static void glitchy(const vgaframe_mode_t *m, vgaframe_timing_learner_t *l, int frames, int *injected) {
    size_t n = synth_frame(m, black, NULL, buf, sizeof buf / sizeof buf[0]);
    ASSERT_TRUE(n > 0);
    vgaframe_timing_init(l);
    for (size_t i = 0; i < n; i++) vgaframe_timing_push(l, (uint8_t)((buf[i] >> 7) & 1), (uint8_t)((buf[i] >> 3) & 1), 1);
    *injected = inject_glitch_set(buf, m) * frames;
    for (int f = 0; f < frames; f++)
        for (size_t i = 0; i < n; i++) vgaframe_timing_push(l, (uint8_t)((buf[i] >> 7) & 1), (uint8_t)((buf[i] >> 3) & 1), 1);
}

TEST(ignores_hsync_glitches_640x480) {
    vgaframe_timing_learner_t l; int injected = 0;
    glitchy(vgaframe_mode_match(800, 525), &l, 2, &injected);
    ASSERT_EQ_U(l.t.glitches, (unsigned)injected);
    ASSERT_EQ_U(l.t.clocks_per_line, 800); ASSERT_EQ_U(l.t.lines_per_frame, 525);
    ASSERT_EQ_U(l.t.hsync_width, 96); ASSERT_EQ_U(l.t.locked, 1);
    ASSERT_TRUE(l.t.mode != NULL);
}

TEST(ignores_hsync_glitches_800x600) {
    vgaframe_timing_learner_t l; int injected = 0;
    glitchy(vgaframe_mode_match(1056, 628), &l, 2, &injected);
    ASSERT_EQ_U(l.t.glitches, (unsigned)injected);
    ASSERT_EQ_U(l.t.clocks_per_line, 1056); ASSERT_EQ_U(l.t.lines_per_frame, 628);
    ASSERT_EQ_U(l.t.hsync_width, 128); ASSERT_EQ_U(l.t.locked, 1);
    ASSERT_TRUE(l.t.mode != NULL);
}

TEST(a_pulse_of_the_right_shape_is_never_filtered) {
    // The filter must reject only pulses that fail one of its two tests: a
    // run of otherwise normal lines must still produce one line start each
    // and no glitch at all, whatever the phase the stream started in.
    const vgaframe_mode_t *m = vgaframe_mode_match(800, 525);
    size_t n = synth_frame(m, black, NULL, buf, sizeof buf / sizeof buf[0]);
    for (size_t off = 0; off < 4; off++) {
        vgaframe_timing_learner_t l; vgaframe_timing_init(&l); int lines = 0;
        size_t start = off * 197u;   // arbitrary phases within a line
        for (size_t k = 0; k < 3u * n; k++) {
            size_t i = (start + k) % n;
            if (vgaframe_timing_push(&l, (uint8_t)((buf[i] >> 7) & 1), (uint8_t)((buf[i] >> 3) & 1), 1)) lines++;
        }
        ASSERT_EQ_U(l.t.glitches, 0);
        ASSERT_EQ_U(l.t.clocks_per_line, 800); ASSERT_EQ_U(l.t.locked, 1);
        ASSERT_TRUE(lines >= 3 * 525 - 2);
    }
}

TEST(recovers_from_a_mislearned_pulse_shape) {
    // The filter judges pulses against what it has learned, so a bad first
    // measurement must not be able to wedge it for ever. A capture that
    // begins *inside* an hsync pulse (46 of its 96 clocks are left) and
    // meets a 2-clock spurious pulse before its first clean line learns
    // that 2-clock pulse as the sync width - the only pulse it has ever
    // measured end to end - and would then reject every real pulse from
    // then on, learning nothing at all. After enough consecutive
    // rejections the learner must conclude that it is its own reference
    // that is wrong and bootstrap again.
    const vgaframe_mode_t *m = vgaframe_mode_match(800, 525);
    size_t n = synth_frame(m, black, NULL, buf, sizeof buf / sizeof buf[0]);
    inject_hsync_glitch(buf, m, 0, 400, 2);
    vgaframe_timing_learner_t l; vgaframe_timing_init(&l);
    for (size_t k = 0; k < 4u * n; k++) {
        size_t i = (50u + k) % n;   // 50 clocks into the first hsync pulse
        vgaframe_timing_push(&l, (uint8_t)((buf[i] >> 7) & 1), (uint8_t)((buf[i] >> 3) & 1), 1);
    }
    ASSERT_EQ_U(l.t.clocks_per_line, 800); ASSERT_EQ_U(l.t.lines_per_frame, 525);
    ASSERT_EQ_U(l.t.hsync_width, 96); ASSERT_EQ_U(l.t.locked, 1);
    ASSERT_TRUE(l.t.mode != NULL);
}

int main(void) {
    RUN(learns_640x480_negative_syncs);
    RUN(learns_800x600_positive_syncs);
    RUN(runs_are_equivalent_to_samples);
    RUN(reports_line_and_frame_starts);
    RUN(locks_from_a_stream_that_starts_mid_frame);
    RUN(ignores_hsync_glitches_640x480);
    RUN(ignores_hsync_glitches_800x600);
    RUN(a_pulse_of_the_right_shape_is_never_filtered);
    RUN(recovers_from_a_mislearned_pulse_shape);
    RUN_TESTS_END();
}
