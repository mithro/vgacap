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

static void assert_bars_640x480(void) {
    ASSERT_EQ_U(last.width, 640); ASSERT_EQ_U(last.height, 480); ASSERT_EQ_U(last.partial, 0);
    ASSERT_TRUE(last.timing->mode != NULL);
    for (uint16_t y = 0; y < 480; y++) for (uint16_t x = 0; x < 640; x++) {
        uint8_t c = bars(NULL, x, y); const uint8_t *q = last_rgb + (y * 640 + x) * 3;
        ASSERT_EQ_U(q[0], ((c >> 4) & 3) * 85); ASSERT_EQ_U(q[1], ((c >> 2) & 3) * 85); ASSERT_EQ_U(q[2], (c & 3) * 85);
    }
}

TEST(converges_from_a_mid_frame_start) {
    // A capture has no reason to start aligned to a frame boundary. Feed
    // two full frames' worth of samples, but starting mid-frame (300 lines
    // + 100 clocks in, wrapping around) with no force_mode.
    //
    // The pulse that begins the first (conceptual) frame in this data can
    // never be recognised *as it begins* - its own duration has not been
    // measured yet - but two lines later, when the pulse ends, the learner
    // knows how long it was and therefore that a frame began vsync_lines
    // lines ago. That frame is claimed retroactively, so it is the frame
    // between the first and second pulses that gets emitted, not the one
    // between the second and third: two frame periods are enough, where
    // three used to be needed. The lines before the decision hold nothing
    // but the vsync pulse, which is never inside the active area, so the
    // picture is still complete and pixel-exact.
    const vgaframe_mode_t *m = vgaframe_mode_match(800, 525);
    size_t n = synth_frame(m, bars, NULL, buf, sizeof buf / sizeof buf[0]);
    vgaframe_t f; setup(&f, NULL);
    size_t offset = 300u * 800u + 100u;
    for (size_t k = 0; k < 2u * n; k++) vgaframe_push(&f, buf[(offset + k) % n], 1);
    ASSERT_EQ_U(nframes, 1);
    assert_bars_640x480();
}

TEST(three_mid_frame_periods_yield_two_frames) {
    // The retroactive first frame must not cost a later one: every
    // subsequent pulse still closes exactly one frame.
    const vgaframe_mode_t *m = vgaframe_mode_match(800, 525);
    size_t n = synth_frame(m, bars, NULL, buf, sizeof buf / sizeof buf[0]);
    vgaframe_t f; setup(&f, NULL);
    size_t offset = 300u * 800u + 100u;
    for (size_t k = 0; k < 3u * n; k++) vgaframe_push(&f, buf[(offset + k) % n], 1);
    ASSERT_EQ_U(nframes, 2);
    ASSERT_EQ_U(last.frame_counter, 1);
    assert_bars_640x480();
}

TEST(mode_match_trusted_over_disagreeing_measurement) {
    // The scenario the emit-gate relaxation actually changes: a first
    // measurement that disagrees with the next one (so `locked` can never
    // become true from them) must not block emission forever once a
    // *later* measurement exactly matches a known table mode. One period
    // of an off-by-one timing (cpl=801, matches no table entry) followed
    // by two periods of the real 640x480@60 timing: the boundary between
    // the two real periods measures (800, 525) with `last_cpl/last_lpf`
    // still holding the bad (801, 525) from before, so `locked` stays
    // false there - but `vgaframe_mode_match(800, 525)` succeeds, which
    // must be enough to emit on its own.
    vgaframe_mode_t jittered = *vgaframe_mode_match(800, 525);
    jittered.h_front = (uint16_t)(jittered.h_front + 1); jittered.name = "jittered"; // cpl=801: no table match
    size_t nj = synth_frame(&jittered, bars, NULL, buf, sizeof buf / sizeof buf[0]);
    ASSERT_TRUE(nj > 0);
    static uint32_t buf_j[801 * 525];
    memcpy(buf_j, buf, nj * sizeof buf[0]); // copy out before `buf` is reused below

    const vgaframe_mode_t *m = vgaframe_mode_match(800, 525);
    size_t n = synth_frame(m, bars, NULL, buf, sizeof buf / sizeof buf[0]);

    vgaframe_t f; setup(&f, NULL);
    for (size_t i = 0; i < nj; i++) vgaframe_push(&f, buf_j[i], 1);
    for (int fr = 0; fr < 2; fr++) for (size_t i = 0; i < n; i++) vgaframe_push(&f, buf[i], 1);
    ASSERT_TRUE(nframes >= 1);
    ASSERT_EQ_U(last.width, 640); ASSERT_EQ_U(last.height, 480); ASSERT_EQ_U(last.partial, 0);
    ASSERT_TRUE(last.timing->mode != NULL);
    ASSERT_EQ_U(last.timing->locked, 0); // the bad first measurement means never "locked" (yet)
    for (uint16_t y = 0; y < 480; y++) for (uint16_t x = 0; x < 640; x++) {
        uint8_t c = bars(NULL, x, y); const uint8_t *q = last_rgb + (y * 640 + x) * 3;
        ASSERT_EQ_U(q[0], ((c >> 4) & 3) * 85); ASSERT_EQ_U(q[1], ((c >> 2) & 3) * 85); ASSERT_EQ_U(q[2], (c & 3) * 85);
    }
}

// Overwrites `width` clocks of hsync, starting `clk` clocks into `line`, with
// the mode's pulse level: the spurious pulses real silicon produces. Only
// bit 7 (hsync) is touched, so no pixel's colour changes and the
// reconstruction stays comparable against the generator.
static void inject_hsync_glitch(uint32_t *b, const vgaframe_mode_t *m,
                                uint32_t line, uint32_t clk, uint32_t width) {
    uint32_t cpl = (uint32_t)m->h_active + m->h_front + m->h_sync + m->h_back;
    for (uint32_t i = 0; i < width; i++) {
        size_t k = (size_t)line * cpl + clk + i;
        if (m->h_sync_positive) b[k] |= 0x80u; else b[k] &= ~0x80u;
    }
}

// Two pulses in mid-active-area (one shorter and one longer than a real sync
// pulse, both far enough into the line to pass the distance test on their
// own, so only the width test can reject them) and one in the blanking.
static uint32_t inject_glitch_set(uint32_t *b, const vgaframe_mode_t *m) {
    uint32_t cpl = (uint32_t)m->h_active + m->h_front + m->h_sync + m->h_back;
    inject_hsync_glitch(b, m, 100, cpl / 2 + 60, 2);
    inject_hsync_glitch(b, m, 200, cpl / 2 + 140, 30);
    inject_hsync_glitch(b, m, 300, cpl - 10, 6);   // front porch: blanking
    return 3;
}

TEST(glitchy_hsync_still_reconstructs_640x480) {
    // One clean frame to learn the timing, then every following frame
    // carries three spurious hsync pulses. Before the glitch filter each of
    // them started a line, the frame came up two lines short, and nothing
    // was emitted at all; now the frames are complete and pixel-exact and
    // the pulses are only counted.
    const vgaframe_mode_t *m = vgaframe_mode_match(800, 525);
    size_t n = synth_frame(m, bars, NULL, buf, sizeof buf / sizeof buf[0]);
    vgaframe_t f; setup(&f, NULL);
    for (size_t i = 0; i < n; i++) vgaframe_push(&f, buf[i], 1);
    uint32_t per_frame = inject_glitch_set(buf, m);
    for (int fr = 0; fr < 2; fr++) for (size_t i = 0; i < n; i++) vgaframe_push(&f, buf[i], 1);
    ASSERT_EQ_U(nframes, 1);
    ASSERT_EQ_U(last.width, 640); ASSERT_EQ_U(last.height, 480); ASSERT_EQ_U(last.partial, 0);
    ASSERT_TRUE(last.timing->mode != NULL);
    ASSERT_EQ_U(last.timing->lines_per_frame, 525);
    ASSERT_EQ_U(last.timing->glitches, per_frame);        // the emitted frame's own
    ASSERT_EQ_U(f.learner.t.glitches, 2 * per_frame);     // both glitchy frames'
    for (uint16_t y = 0; y < 480; y++) for (uint16_t x = 0; x < 640; x++) {
        uint8_t c = bars(NULL, x, y); const uint8_t *q = last_rgb + (y * 640 + x) * 3;
        ASSERT_EQ_U(q[0], ((c >> 4) & 3) * 85); ASSERT_EQ_U(q[1], ((c >> 2) & 3) * 85); ASSERT_EQ_U(q[2], (c & 3) * 85);
    }
}

TEST(glitchy_hsync_still_reconstructs_800x600) {
    // The same with positive syncs (so the spurious pulses are high, not
    // low) and run-coalesced input, which is how a real RLE stream arrives.
    const vgaframe_mode_t *m = vgaframe_mode_match(1056, 628);
    size_t n = synth_frame(m, bars, NULL, buf, sizeof buf / sizeof buf[0]);
    vgaframe_t f; setup(&f, NULL);
    size_t i = 0;
    while (i < n) { size_t j = i; while (j < n && buf[j] == buf[i]) j++; vgaframe_push(&f, buf[i], (uint32_t)(j - i)); i = j; }
    uint32_t per_frame = inject_glitch_set(buf, m);
    for (int fr = 0; fr < 2; fr++) { i = 0; while (i < n) { size_t j = i; while (j < n && buf[j] == buf[i]) j++;
        vgaframe_push(&f, buf[i], (uint32_t)(j - i)); i = j; } }
    ASSERT_EQ_U(nframes, 1);
    ASSERT_EQ_U(last.width, 800); ASSERT_EQ_U(last.height, 600); ASSERT_EQ_U(last.partial, 0);
    ASSERT_EQ_U(last.timing->lines_per_frame, 628);
    ASSERT_EQ_U(last.timing->glitches, per_frame);
    ASSERT_EQ_U(f.learner.t.glitches, 2 * per_frame);
    for (uint16_t y = 0; y < 600; y++) for (uint16_t x = 0; x < 800; x++) {
        uint8_t c = bars(NULL, x, y); const uint8_t *q = last_rgb + (y * 800 + x) * 3;
        ASSERT_EQ_U(q[0], ((c >> 4) & 3) * 85); ASSERT_EQ_U(q[1], ((c >> 2) & 3) * 85); ASSERT_EQ_U(q[2], (c & 3) * 85);
    }
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

TEST(reset_restores_the_post_init_state) {
    // vgaframe_reset must leave the object indistinguishable from a fresh
    // vgaframe_init on the same buffers (I4): a GStreamer flush/seek or a
    // reconnect goes through it, and it is the way to abandon a half-built
    // picture or a pending FRAM accumulation.
    const vgaframe_mode_t *m = vgaframe_mode_match(800, 525);
    size_t n = synth_frame(m, bars, NULL, buf, sizeof buf / sizeof buf[0]);

    // Reference run: a fresh init, three frames, every pixel recorded.
    vgaframe_t a; setup(&a, NULL);
    for (int fr = 0; fr < 3; fr++) for (size_t i = 0; i < n; i++) vgaframe_push(&a, buf[i], 1);
    ASSERT_TRUE(nframes >= 1);
    int ref_frames = nframes; uint16_t ref_w = last.width, ref_h = last.height;
    uint32_t ref_counter = last.frame_counter;
    static uint8_t ref_rgb[1400 * 900 * 3];
    memcpy(ref_rgb, last_rgb, (size_t)ref_w * ref_h * 3);

    // Same object, dirtied with a partial frame and an open FRAM window,
    // then reset and driven with the same stream.
    for (size_t i = 0; i < n / 2; i++) vgaframe_push(&a, buf[i], 1);
    vgaframe_frame_begin(&a, 77, 300, 25, 800, 25 * 800);
    for (uint32_t i = 0; i < 10u * 800u; i++) vgaframe_push(&a, buf[300u * 800u + i], 1);
    vgaframe_reset(&a);
    ASSERT_EQ_U(a.frames_seen, 0); ASSERT_EQ_U(a.in_frame, 0);
    ASSERT_EQ_U(a.fram_active, 0); ASSERT_EQ_U(a.fram_pending, 0);
    ASSERT_TRUE(a.learner.t.mode == NULL);
    ASSERT_EQ_U(a.raw[300 * 1400], 0);   // the abandoned window left nothing behind

    nframes = 0;
    for (int fr = 0; fr < 3; fr++) for (size_t i = 0; i < n; i++) vgaframe_push(&a, buf[i], 1);
    ASSERT_EQ_U(nframes, ref_frames);
    ASSERT_EQ_U(last.width, ref_w); ASSERT_EQ_U(last.height, ref_h);
    ASSERT_EQ_U(last.frame_counter, ref_counter);   // the running count restarted too
    ASSERT_EQ_U(last.stride, (unsigned)ref_w * 3);
    ASSERT_EQ_MEM(last_rgb, ref_rgb, (size_t)ref_w * ref_h * 3);
}

int main(void) {
    RUN(colour_helper);
    RUN(reconstructs_640x480_bars);
    RUN(reconstructs_with_runs_and_800x600);
    RUN(table_match_wins_over_odd_porches);
    RUN(oversized_run_does_not_overflow);
    RUN(force_mode_wider_than_buffer_emits_nothing);
    RUN(converges_from_a_mid_frame_start);
    RUN(three_mid_frame_periods_yield_two_frames);
    RUN(mode_match_trusted_over_disagreeing_measurement);
    RUN(glitchy_hsync_still_reconstructs_640x480);
    RUN(glitchy_hsync_still_reconstructs_800x600);
    RUN(flush_emits_partial);
    RUN(reset_restores_the_post_init_state);
    RUN_TESTS_END();
}
