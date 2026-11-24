// SPDX-License-Identifier: Apache-2.0
// The fast path through vgaframe_push changes nothing. Every stream here is
// pushed, push by push, into the library's vgaframe_push and into
// ref_vgaframe_push, the a26c942 code kept under tests/fastpath_ref/, each
// from a freshly initialised object (so the first push meets have_prev == 0).
// After every push the two objects' state and the frames each one emitted are
// compared, the frames byte for byte; the raw and cover buffers are compared
// every few thousand pushes and at the end.
#include <stdint.h>
#include "harness.h"
#include "vgacap/frame.h"
#include "synth.h"
#include "fastpath_ref/ref.h"

#define BUF_W 1000
#define BUF_H 700
#define SLOTS 4     // frames one push can emit is at most 2 (a pending FRAM flush, then a frame)

typedef struct capture {
    int n, overflow;
    vgaframe_output_t out[SLOTS];
    vgaframe_timing_t timing[SLOTS];
    uint8_t rgb[SLOTS][BUF_W * BUF_H * 3];
} capture_t;

typedef struct side {
    vgaframe_t f;
    uint8_t raw[BUF_W * BUF_H], rgb[BUF_W * BUF_H * 3], cover[BUF_H];
    capture_t cap;
} side_t;

static side_t lib, ref;   // lib: the library under test; ref: the a26c942 snapshot
static unsigned long pushes, frames_compared, reports[4];   // reports: pushes by last_report

static void on_frame(void *u, const vgaframe_output_t *o) {
    capture_t *c = u;
    if (c->n >= SLOTS) { c->overflow = 1; return; }
    c->out[c->n] = *o;
    c->timing[c->n] = *o->timing;
    memcpy(c->rgb[c->n], o->rgb24, (size_t)o->height * o->stride);
    c->n++;
}

static void pair_init(const uint8_t map[VGACAP_SIG_COUNT]) {
    vgaframe_config_t c; memset(&c, 0, sizeof c);
    c.max_clocks_per_line = BUF_W; c.max_lines = BUF_H;
    memcpy(c.signal_map, map, VGACAP_SIG_COUNT);
    c.on_frame = on_frame;
    memset(&lib, 0, sizeof lib); memset(&ref, 0, sizeof ref);
    c.user = &lib.cap; vgaframe_init(&lib.f, &c, lib.raw, lib.rgb, lib.cover);
    c.user = &ref.cap; ref_vgaframe_init(&ref.f, &c, ref.raw, ref.rgb, ref.cover);
    pushes = frames_compared = 0;
    memset(reports, 0, sizeof reports);
}

static int mismatch(const char *what) {
    fprintf(stderr, "  MISMATCH after push %lu: %s\n", pushes, what);
    return 0;
}
#define SAME(field) do { if ((a)->field != (b)->field) return mismatch(#field); } while (0)

static int same_timing(const vgaframe_timing_t *a, const vgaframe_timing_t *b) {
    SAME(clocks_per_line); SAME(lines_per_frame); SAME(hsync_width); SAME(vsync_lines);
    SAME(hsync_positive); SAME(vsync_positive); SAME(locked); SAME(glitches); SAME(mode);
    return 1;
}

static int same_learner(const vgaframe_timing_learner_t *a, const vgaframe_timing_learner_t *b) {
    if (!same_timing(&a->t, &b->t)) return mismatch("learner.t");
    SAME(prev_h); SAME(prev_v); SAME(have_prev); SAME(clk_in_line); SAME(h_high); SAME(h_low);
    SAME(pulse_open); SAME(pulse_start_clk); SAME(ignored_run); SAME(report_x); SAME(line_in_frame);
    SAME(v_started); SAME(v_high_lines); SAME(v_low_lines); SAME(last_cpl); SAME(last_lpf);
    SAME(h_edge_count);
    return 1;
}

static int same_frames(const capture_t *a, const capture_t *b) {
    if (a->overflow || b->overflow) return mismatch("more frames in one push than SLOTS");
    SAME(n);
    for (int i = 0; i < a->n; i++) {
        SAME(out[i].width); SAME(out[i].height); SAME(out[i].stride);
        SAME(out[i].active_x0); SAME(out[i].active_y0); SAME(out[i].partial); SAME(out[i].frame_counter);
        if (!same_timing(&a->timing[i], &b->timing[i])) return mismatch("frame timing");
        if (memcmp(a->rgb[i], b->rgb[i], (size_t)a->out[i].height * a->out[i].stride)) return mismatch("frame rgb24");
        frames_compared++;
    }
    return 1;
}

static int same_buffers(void) {
    if (memcmp(lib.raw, ref.raw, sizeof lib.raw)) return mismatch("raw buffer");
    if (memcmp(lib.cover, ref.cover, sizeof lib.cover)) return mismatch("cover buffer");
    return 1;
}

// Everything vgaframe_push can change, and the frames the push emitted.
static int same_state(void) {
    const vgaframe_t *a = &lib.f, *b = &ref.f;
    SAME(last_report); SAME(x);
    if (a->last_report >= 0 && a->last_report < 4) reports[a->last_report]++;
    SAME(y); SAME(in_frame); SAME(frames_seen);
    SAME(fram_active); SAME(fram_pending); SAME(fram_counter); SAME(fram_first_line);
    SAME(fram_line_count); SAME(fram_cpl); SAME(fram_remaining); SAME(fram_max_line);
    SAME(fram_anchor); SAME(fram_anchor_level); SAME(fram_h_high); SAME(fram_v_high); SAME(fram_clocks);
    if (!same_learner(&a->learner, &b->learner)) return 0;
    if (!same_timing(&a->out_timing, &b->out_timing)) return mismatch("out_timing");
    if (!same_frames(&lib.cap, &ref.cap)) return 0;
    lib.cap.n = ref.cap.n = 0;
    if ((pushes & 4095) == 0 && !same_buffers()) return 0;
    return 1;
}

static int push_both(uint32_t sample, uint32_t run) {
    vgaframe_push(&lib.f, sample, run);
    ref_vgaframe_push(&ref.f, sample, run);
    pushes++;
    return same_state();
}

static int push64_both(uint32_t sample, uint64_t run) {
    vgaframe_push64(&lib.f, sample, run);
    ref_vgaframe_push64(&ref.f, sample, run);
    pushes++;
    return same_state();
}

static int flush_both(void) {
    vgaframe_flush(&lib.f);
    ref_vgaframe_flush(&ref.f);
    return same_state() && same_buffers();
}

static int frame_begin_both(uint32_t counter, uint16_t first, uint16_t count, uint32_t cpl, uint32_t samples) {
    vgaframe_frame_begin(&lib.f, counter, first, count, cpl, samples);
    ref_vgaframe_frame_begin(&ref.f, counter, first, count, cpl, samples);
    return same_state();
}

// Tiny VGA: hsync bit 7, vsync bit 3, r1 0, r0 4, g1 1, g0 5, b1 2, b0 6.
static const uint8_t tiny_map[VGACAP_SIG_COUNT] = { 7, 3, 0, 4, 1, 5, 2, 6 };

// ---- the three bench-runs profiles -------------------------------------

static uint8_t px_busy(void *u, uint16_t x, uint16_t y) { (void)u; return (uint8_t)((x + y) & 0x3F); }
static uint8_t px_bars(void *u, uint16_t x, uint16_t y) { (void)u; (void)y; return (uint8_t)((x / 10) & 0x3F); }
static uint8_t px_static(void *u, uint16_t x, uint16_t y) { (void)u; (void)x; (void)y; return 0x2A; }

static uint32_t frame_buf[800u * 525u];

// As bench-runs does it: one synthesised 640x480 frame merged into runs and
// replayed, here three times, then flushed.
static int bench_profile(uint8_t (*pixel)(void *, uint16_t, uint16_t)) {
    const vgaframe_mode_t *m = vgaframe_mode_match(800, 525);
    size_t n = synth_frame(m, pixel, NULL, frame_buf, sizeof frame_buf / sizeof frame_buf[0]);
    if (n != 800u * 525u) return mismatch("synth_frame");
    pair_init(tiny_map);
    for (int fr = 0; fr < 3; fr++) {
        size_t i = 0;
        while (i < n) {
            size_t j = i;
            while (j < n && frame_buf[j] == frame_buf[i]) j++;
            if (!push_both(frame_buf[i], (uint32_t)(j - i))) return 0;
            i = j;
        }
    }
    return flush_both();
}

// ---- a random, glitchy, sync-shaped stream ------------------------------

static uint64_t rng_state;
static uint32_t rnd(void) {   // xorshift64*
    rng_state ^= rng_state >> 12; rng_state ^= rng_state << 25; rng_state ^= rng_state >> 27;
    return (uint32_t)((rng_state * 2685821657736338717ull) >> 32);
}
static uint32_t rnd_below(uint32_t n) { return rnd() % n; }

#define NFRAMES 4
static uint32_t stream[NFRAMES * 800u * 525u];

// The stream in Tiny VGA layout (`tiny_map`) with random noise in bits 8-31
// on a quarter of the frames. 640x480@60 timing, frames starting at the
// vsync pulse as synth_frame's do. The active area takes a new random colour
// on about a third of its clocks. A tenth of the lines of frames 0 and 2
// carry an hsync glitch of 2 to 30 clocks at a random place (frames 1 and 3
// have none, so they match the mode and are emitted). About one line in 1000
// carries a vsync glitch, which leaves hsync alone and so has to pass through
// the no-edge path untouched.
static void make_stream(void) {
    const vgaframe_mode_t *m = vgaframe_mode_match(800, 525);
    uint32_t cpl = 800, lpf = 525, i = 0, colour = 0;
    for (uint32_t fr = 0; fr < NFRAMES; fr++)
        for (uint32_t y = 0; y < lpf; y++) {
            uint32_t hglitch_at = (fr % 2 == 0 && rnd_below(10) == 0) ? rnd_below(cpl) : UINT32_MAX;
            uint32_t hglitch_len = 2 + rnd_below(29);
            uint32_t vglitch_at = rnd_below(1000) == 0 ? rnd_below(cpl) : UINT32_MAX;
            uint32_t vglitch_len = 1 + rnd_below(40);
            int vpulse = y < m->v_sync;
            int yactive = y >= (uint32_t)m->v_sync + m->v_back && y < (uint32_t)m->v_sync + m->v_back + m->v_active;
            for (uint32_t x = 0; x < cpl; x++, i++) {
                int hpulse = x < m->h_sync;
                int xactive = x >= (uint32_t)m->h_sync + m->h_back && x < (uint32_t)m->h_sync + m->h_back + m->h_active;
                uint32_t hs = (uint32_t)(hpulse ? m->h_sync_positive : !m->h_sync_positive);
                uint32_t vs = (uint32_t)(vpulse ? m->v_sync_positive : !m->v_sync_positive);
                if (x >= hglitch_at && x < hglitch_at + hglitch_len) hs ^= 1;
                if (x >= vglitch_at && x < vglitch_at + vglitch_len) vs ^= 1;
                if (xactive && yactive) { if (rnd_below(3) == 0) colour = rnd() & 0x77; }
                else colour = rnd_below(400) == 0 ? (rnd() & 0x77) : 0;   // the odd blanking pixel
                uint32_t s = colour | (hs << 7) | (vs << 3);
                if (fr == 2) s |= rnd() & 0xFFFFFF00u;
                stream[i] = s;
            }
        }
}

// Moves each signal's bit from its Tiny VGA position to `map`'s, and fills
// every bit `map` does not use with noise.
static uint32_t remap(uint32_t s, const uint8_t map[VGACAP_SIG_COUNT]) {
    uint32_t out = 0, used = 0;
    for (int sig = 0; sig < VGACAP_SIG_COUNT; sig++) {
        if (map[sig] == VGACAP_SIG_ABSENT) continue;
        used |= 1u << map[sig];
        if ((s >> tiny_map[sig]) & 1) out |= 1u << map[sig];
    }
    return out | (rnd() & ~used);
}

// Pushes stream[from, to) as runs: equal neighbours merged, a quarter of the
// runs split again at random (consecutive pushes of one sample), now and then
// a zero-length push, and every sample put through `map`.
static int push_stream(size_t from, size_t to, const uint8_t map[VGACAP_SIG_COUNT], int is_tiny) {
    size_t i = from;
    while (i < to) {
        size_t j = i;
        while (j < to && stream[j] == stream[i]) j++;
        uint32_t s = is_tiny ? stream[i] : remap(stream[i], map);
        uint32_t left = (uint32_t)(j - i);
        if (rnd_below(64) == 0 && !push_both(s, 0)) return 0;
        if (rnd_below(1u << 15) == 0 && !push_both(rnd(), 0)) return 0;   // a rare zero-length stray sample
        while (left) {
            uint32_t piece = (rnd_below(4) == 0 && left > 1) ? 1 + rnd_below(left) : left;
            if (!push_both(s, piece)) return 0;
            left -= piece;
        }
        i = j;
    }
    return 1;
}

static int random_stream(uint64_t seed, const uint8_t map[VGACAP_SIG_COUNT], int is_tiny) {
    rng_state = seed;
    make_stream();
    size_t n = sizeof stream / sizeof stream[0];
    pair_init(map);   // fresh: the first push meets have_prev == 0
    // Start anywhere, mid-line and mid-frame, so the learner bootstraps from
    // an arbitrary phase and the first frame is claimed retroactively.
    size_t start = rnd_below((uint32_t)(n / 2));
    if (!push_stream(start, n, map, is_tiny)) return 0;
    // Runs longer than a buffer line, and longer than 32 bits.
    uint32_t idle = is_tiny ? stream[n - 1] : remap(stream[n - 1], map);
    if (!push_both(idle, 5000)) return 0;
    if (!push64_both(idle, 5000000000ull)) return 0;
    if (!push_stream(0, n / 2, map, is_tiny)) return 0;
    // A 600-line frame (frame 1 with line 300 repeated 75 times), which
    // matches no mode and so is not emitted, then frame 1 as it is, which is:
    // that emit clears only its own 526 lines, and lines 526 to 599 keep the
    // long frame's pixels until the FRAM picture below clears every line.
    size_t f1 = 800u * 525u, l300 = f1 + 300u * 800u;
    if (!push_stream(f1, l300, map, is_tiny)) return 0;
    for (int k = 0; k < 75; k++)
        if (!push_stream(l300, l300 + 800u, map, is_tiny)) return 0;
    if (!push_stream(l300, f1 + 525u * 800u, map, is_tiny)) return 0;
    if (!push_stream(f1, f1 + 525u * 800u, map, is_tiny)) return 0;
    if (!push_stream(3u * f1, 3u * f1 + 10u * 800u, map, is_tiny)) return 0;   // its end: the frame start that emits it
    // FRAM windows of 25 lines each, two pictures' worth of a few windows,
    // some cut short, each starting at a line's hsync leading edge.
    for (uint32_t k = 0; k < 8; k++) {
        uint16_t first = (uint16_t)(35 + 25 * rnd_below(19));
        uint32_t samples = 25u * 800u - (rnd_below(3) == 0 ? rnd_below(800) : 0);
        size_t at = 800u * 525u + (size_t)first * 800u;
        if (!frame_begin_both(k / 4, first, 25, 800, samples)) return 0;
        if (!push_stream(at, at + samples, map, is_tiny)) return 0;
    }
    // Back to a continuous stream, long enough for the learner to settle
    // again and emit frames.
    for (int pass = 0; pass < 2; pass++)
        if (!push_stream(0, n, map, is_tiny)) return 0;
    if (!flush_both()) return 0;
    fprintf(stderr, "  seed %llu: %lu pushes (reports 0/1/2/3: %lu/%lu/%lu/%lu), %lu frames compared\n",
            (unsigned long long)seed, pushes, reports[0], reports[1], reports[2], reports[3], frames_compared);
    return frames_compared > 0;
}

TEST(fastpath_equivalent_busy) { ASSERT_TRUE(bench_profile(px_busy)); ASSERT_TRUE(frames_compared >= 2); }
TEST(fastpath_equivalent_bars) { ASSERT_TRUE(bench_profile(px_bars)); ASSERT_TRUE(frames_compared >= 2); }
TEST(fastpath_equivalent_static) { ASSERT_TRUE(bench_profile(px_static)); ASSERT_TRUE(frames_compared >= 2); }

TEST(fastpath_equivalent_random_tiny) {
    ASSERT_TRUE(random_stream(1, tiny_map, 1));
    ASSERT_TRUE(random_stream(0x9E3779B97F4A7C15ull, tiny_map, 1));
}

TEST(fastpath_equivalent_random_absent_colours) {
    // Every map bit below 8, with two colour signals absent.
    static const uint8_t map[VGACAP_SIG_COUNT] = { 7, 3, VGACAP_SIG_ABSENT, 4, 1, 5, 2, VGACAP_SIG_ABSENT };
    ASSERT_TRUE(random_stream(2, map, 0));
}

TEST(fastpath_equivalent_random_wide) {
    // Colour bits above 7, so the per-sample colour has to be computed.
    static const uint8_t map[VGACAP_SIG_COUNT] = { 30, 12, 8, 17, 22, 9, 31, 25 };
    ASSERT_TRUE(random_stream(3, map, 0));
}

TEST(fastpath_equivalent_random_mixed) {
    // One colour bit above 7, the rest below: still not table-sized.
    static const uint8_t map[VGACAP_SIG_COUNT] = { 7, 3, 0, 4, 1, 5, 2, 14 };
    ASSERT_TRUE(random_stream(4, map, 0));
}

TEST(fastpath_equivalent_random_no_hsync) {
    // No hsync at all: every push after the first is the no-edge case.
    static const uint8_t map[VGACAP_SIG_COUNT] = { VGACAP_SIG_ABSENT, 3, 0, 4, 1, 5, 2, 6 };
    ASSERT_TRUE(random_stream(5, map, 0));
}

int main(void) {
    RUN(fastpath_equivalent_busy);
    RUN(fastpath_equivalent_bars);
    RUN(fastpath_equivalent_static);
    RUN(fastpath_equivalent_random_tiny);
    RUN(fastpath_equivalent_random_absent_colours);
    RUN(fastpath_equivalent_random_wide);
    RUN(fastpath_equivalent_random_mixed);
    RUN(fastpath_equivalent_random_no_hsync);
    RUN_TESTS_END();
}
