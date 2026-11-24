// SPDX-License-Identifier: Apache-2.0
// The fast path through vgaframe_push changes nothing. Every stream here is
// pushed, push by push, into the library's vgaframe_push and into
// ref_vgaframe_push, the a26c942 code kept under tests/fastpath_ref/, each
// from a freshly initialised object (so the first push meets have_prev == 0).
// After every push the two objects' state and the frames each one emitted are
// compared, the frames byte for byte; the whole raw, rgb and cover buffers
// are compared every few thousand pushes and at the end. The cases cover
// forced modes (one larger than the buffer), buffers narrower and shorter
// than the line and frame, and a timing no table mode matches, cropped by
// emit's auto bounding box, each under the Tiny VGA and a wide signal map.
// Both sides also have an on_raw callback and an rgb6 buffer (M10 Task 10,
// which made the same deliberate changes to the reference): each raw frame
// handed to on_raw and each rgb6 picture are compared byte for byte too, and
// two cases run with no on_frame at all.
#include <stdint.h>
#include "harness.h"
#include "vgacap/frame.h"
#include "synth.h"
#include "fastpath_ref/ref.h"

#define BUF_W 1000  // the largest buffer any case asks pair_init for
#define BUF_H 700
#define SLOTS 4     // frames one push can emit is at most 2 (a pending FRAM flush, then a frame)

typedef struct capture {
    int n, overflow;
    const vgaframe_t *owner;
    int continuous[SLOTS];   // emitted inside a continuous-mode frame (owner->in_frame at the callback)
    vgaframe_output_t out[SLOTS];
    vgaframe_timing_t timing[SLOTS];
    uint8_t rgb[SLOTS][BUF_W * BUF_H * 3];
    uint8_t rgb6[SLOTS][BUF_W * BUF_H];
    // on_raw calls in the push, likewise.
    int nraw;
    vgaframe_raw_t raw_out[SLOTS];
    vgaframe_timing_t raw_timing[SLOTS];
    int raw_is_own[SLOTS];   // raw_out.raw was the owner's raw buffer
    uint8_t raw[SLOTS][BUF_W * BUF_H];
} capture_t;

typedef struct side {
    vgaframe_t f;
    uint8_t raw[BUF_W * BUF_H], rgb[BUF_W * BUF_H * 3], cover[BUF_H], rgb6[BUF_W * BUF_H];
    capture_t cap;
} side_t;

static side_t lib, ref;   // lib: the library under test; ref: the a26c942 snapshot
static unsigned long pushes, frames_compared, raws_compared, reports[4];   // reports: pushes by last_report
static int with_on_frame = 1;   // 0: both sides run with on_frame NULL
// Continuous-mode frames emitted locked with no mode: the ones cropped by
// emit's auto bounding box at a frame start.
static unsigned long auto_locked_frames;

static void on_frame(void *u, const vgaframe_output_t *o) {
    capture_t *c = u;
    if (c->n >= SLOTS) { c->overflow = 1; return; }
    c->out[c->n] = *o;
    c->continuous[c->n] = c->owner->in_frame;
    c->timing[c->n] = *o->timing;
    memcpy(c->rgb[c->n], o->rgb24, (size_t)o->height * o->stride);
    if (o->rgb6) memcpy(c->rgb6[c->n], o->rgb6, (size_t)o->height * o->width);
    c->n++;
}

static void on_raw(void *u, const vgaframe_raw_t *r) {
    capture_t *c = u;
    if (c->nraw >= SLOTS) { c->overflow = 1; return; }
    c->raw_out[c->nraw] = *r;
    c->raw_timing[c->nraw] = *r->timing;
    c->raw_is_own[c->nraw] = r->raw == c->owner->raw;
    memcpy(c->raw[c->nraw], r->raw, (size_t)r->lines * r->stride);
    c->nraw++;
}

// Fresh objects on both sides with a W x H buffer (at most BUF_W x BUF_H;
// the arrays' unused tails stay equal and are compared too) and force_mode.
static void pair_init(const uint8_t map[VGACAP_SIG_COUNT], uint16_t W, uint16_t H, const vgaframe_mode_t *force) {
    vgaframe_config_t c; memset(&c, 0, sizeof c);
    c.max_clocks_per_line = W; c.max_lines = H; c.force_mode = force;
    memcpy(c.signal_map, map, VGACAP_SIG_COUNT);
    c.on_frame = with_on_frame ? on_frame : NULL;
    c.on_raw = on_raw;
    memset(&lib, 0, sizeof lib); memset(&ref, 0, sizeof ref);
    // rgb and rgb6 start non-zero, so a store past the end of a picture shows.
    memset(lib.rgb, 0xA5, sizeof lib.rgb); memset(ref.rgb, 0xA5, sizeof ref.rgb);
    memset(lib.rgb6, 0xA5, sizeof lib.rgb6); memset(ref.rgb6, 0xA5, sizeof ref.rgb6);
    lib.cap.owner = &lib.f; ref.cap.owner = &ref.f;
    c.user = c.raw_user = &lib.cap; vgaframe_init(&lib.f, &c, lib.raw, lib.rgb, lib.cover);
    c.user = c.raw_user = &ref.cap; ref_vgaframe_init(&ref.f, &c, ref.raw, ref.rgb, ref.cover);
    vgaframe_set_rgb6_buffer(&lib.f, lib.rgb6);
    ref_vgaframe_set_rgb6_buffer(&ref.f, ref.rgb6);
    pushes = frames_compared = raws_compared = auto_locked_frames = 0;
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
        if (!a->out[i].rgb6 || !b->out[i].rgb6) return mismatch("frame rgb6 is NULL");
        if (memcmp(a->rgb6[i], b->rgb6[i], (size_t)a->out[i].height * a->out[i].width)) return mismatch("frame rgb6");
        frames_compared++;
        SAME(continuous[i]);
        if (a->continuous[i] && a->timing[i].locked && !a->timing[i].mode) auto_locked_frames++;
    }
    SAME(nraw);
    for (int i = 0; i < a->nraw; i++) {
        SAME(raw_out[i].stride); SAME(raw_out[i].lines); SAME(raw_out[i].retroactive);
        if (!a->raw_is_own[i] || !b->raw_is_own[i]) return mismatch("raw frame is not the owner's raw buffer");
        if (!same_timing(&a->raw_timing[i], &b->raw_timing[i])) return mismatch("raw frame timing");
        if (memcmp(a->raw[i], b->raw[i], (size_t)a->raw_out[i].lines * a->raw_out[i].stride)) return mismatch("raw frame bytes");
        raws_compared++;
    }
    return 1;
}

static int same_buffers(void) {
    if (memcmp(lib.raw, ref.raw, sizeof lib.raw)) return mismatch("raw buffer");
    if (memcmp(lib.cover, ref.cover, sizeof lib.cover)) return mismatch("cover buffer");
    if (memcmp(lib.rgb, ref.rgb, sizeof lib.rgb)) return mismatch("rgb buffer");
    if (memcmp(lib.rgb6, ref.rgb6, sizeof lib.rgb6)) return mismatch("rgb6 buffer");
    return 1;
}

// Everything vgaframe_push can change, and the frames the push emitted.
static int same_state(void) {
    const vgaframe_t *a = &lib.f, *b = &ref.f;
    SAME(last_report); SAME(x);
    if (a->last_report >= 0 && a->last_report < 4) reports[a->last_report]++;
    SAME(y); SAME(in_frame); SAME(frames_seen); SAME(retro_frame);
    SAME(fram_active); SAME(fram_pending); SAME(fram_counter); SAME(fram_first_line);
    SAME(fram_line_count); SAME(fram_cpl); SAME(fram_remaining); SAME(fram_max_line);
    SAME(fram_anchor); SAME(fram_anchor_level); SAME(fram_h_high); SAME(fram_v_high); SAME(fram_clocks);
    if (!same_learner(&a->learner, &b->learner)) return 0;
    if (!same_timing(&a->out_timing, &b->out_timing)) return mismatch("out_timing");
    if (!same_frames(&lib.cap, &ref.cap)) return 0;
    lib.cap.n = ref.cap.n = 0;
    lib.cap.nraw = ref.cap.nraw = 0;
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

static int frame_begin_both(uint32_t counter, uint16_t first, uint16_t count, uint32_t line_clocks, uint32_t samples) {
    vgaframe_frame_begin(&lib.f, counter, first, count, line_clocks, samples);
    ref_vgaframe_frame_begin(&ref.f, counter, first, count, line_clocks, samples);
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
    pair_init(tiny_map, BUF_W, BUF_H, NULL);
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
static uint32_t stream[NFRAMES * 800u * 525u];   // room for four frames of up to 800 x 525 clocks
static const vgaframe_mode_t *tm;                 // the stream's timing
static uint32_t cpl, lpf;                         // its clocks per line and lines per frame
static unsigned glitch_frames = 0x5;              // bit k: frame k carries hsync glitches

// The stream in Tiny VGA layout (`tiny_map`) with random noise in bits 8-31
// on a quarter of the frames. Timing `m` (640x480@60 unless a case says
// otherwise), frames starting at the vsync pulse as synth_frame's do. The active area takes a new random colour
// on about a third of its clocks. A tenth of the lines of frames 0 and 2
// (`glitch_frames`) carry an hsync glitch of 2 to 30 clocks at a random place
// (frames 1 and 3 have none, so they match the mode and are emitted). About one line in 1000
// carries a vsync glitch, which leaves hsync alone and so has to pass through
// the no-edge path untouched.
static void make_stream(const vgaframe_mode_t *m) {
    tm = m;
    cpl = (uint32_t)m->h_active + m->h_front + m->h_sync + m->h_back;
    lpf = (uint32_t)m->v_active + m->v_front + m->v_sync + m->v_back;
    uint32_t i = 0, colour = 0;
    for (uint32_t fr = 0; fr < NFRAMES; fr++)
        for (uint32_t y = 0; y < lpf; y++) {
            uint32_t hglitch_at = (((glitch_frames >> fr) & 1) && rnd_below(10) == 0) ? rnd_below(cpl) : UINT32_MAX;
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

// One case: a fresh pair with a W x H buffer and force_mode `force`, fed the
// random stream with timing `m` under `map`.
static int random_case(uint64_t seed, const uint8_t map[VGACAP_SIG_COUNT], int is_tiny,
                       const vgaframe_mode_t *m, uint16_t W, uint16_t H, const vgaframe_mode_t *force) {
    rng_state = seed;
    make_stream(m);
    size_t n = (size_t)NFRAMES * cpl * lpf;
    uint32_t a0 = (uint32_t)m->v_sync + m->v_back;   // first active line
    pair_init(map, W, H, force);   // fresh: the first push meets have_prev == 0
    // Start anywhere, mid-line and mid-frame, so the learner bootstraps from
    // an arbitrary phase and the first frame is claimed retroactively.
    size_t start = rnd_below((uint32_t)(n / 2));
    if (!push_stream(start, n, map, is_tiny)) return 0;
    // Runs longer than a buffer line, and longer than 32 bits.
    uint32_t idle = is_tiny ? stream[n - 1] : remap(stream[n - 1], map);
    if (!push_both(idle, 5000)) return 0;
    if (!push64_both(idle, 5000000000ull)) return 0;
    if (!push_stream(0, n / 2, map, is_tiny)) return 0;
    // A frame 75 lines too long (frame 1 with one line repeated 75 times),
    // which matches no mode and so is not emitted, then frame 1 as it is,
    // which is: that emit clears only its own lines, and the 75 below keep
    // the long frame's pixels until the FRAM picture below clears every line.
    size_t f1 = (size_t)cpl * lpf, lrep = f1 + (size_t)(lpf * 4 / 7) * cpl;
    if (!push_stream(f1, lrep, map, is_tiny)) return 0;
    for (int k = 0; k < 75; k++)
        if (!push_stream(lrep, lrep + cpl, map, is_tiny)) return 0;
    if (!push_stream(lrep, 2 * f1, map, is_tiny)) return 0;
    if (!push_stream(f1, 2 * f1, map, is_tiny)) return 0;
    if (!push_stream(3 * f1, 3 * f1 + 10u * cpl, map, is_tiny)) return 0;   // its end: the frame start that emits it
    // FRAM windows of 25 lines each, two pictures' worth of a few windows,
    // some cut short, each starting at a line's hsync leading edge.
    for (uint32_t k = 0; k < 8; k++) {
        uint16_t first = (uint16_t)(a0 + 25 * rnd_below(m->v_active / 25u));
        uint32_t samples = 25u * cpl - (rnd_below(3) == 0 ? rnd_below(cpl) : 0);
        size_t at = f1 + (size_t)first * cpl;
        if (!frame_begin_both(k / 4, first, 25, cpl, samples)) return 0;
        if (!push_stream(at, at + samples, map, is_tiny)) return 0;
    }
    // A FRAM picture whose windows cover every active line, so it is
    // emitted complete-looking (partial_hint 0), but whose window at active
    // line 250 stops 300 clocks short, leaving the end of that line's active
    // area unwritten: only the pixels themselves can mark it partial.
    for (uint32_t first = a0; first < a0 + m->v_active; first += 25) {
        uint16_t count = (uint16_t)(first + 25 > a0 + m->v_active ? a0 + m->v_active - first : 25);
        uint32_t samples = count * cpl - (first == a0 + 250 ? 300u : 0u);
        size_t at = f1 + (size_t)first * cpl;
        if (!frame_begin_both(9, (uint16_t)first, count, cpl, samples)) return 0;
        if (!push_stream(at, at + samples, map, is_tiny)) return 0;
    }
    // Back to a continuous stream, long enough for the learner to settle
    // again and emit frames.
    for (int pass = 0; pass < 2; pass++)
        if (!push_stream(0, n, map, is_tiny)) return 0;
    if (!flush_both()) return 0;
    fprintf(stderr, "  seed %llu, %s %ux%u%s%s%s: %lu pushes (reports 0/1/2/3: %lu/%lu/%lu/%lu), %lu frames compared, %lu locked auto-cropped, %lu raw frames compared\n",
            (unsigned long long)seed, m->name, W, H, force ? ", forced " : "", force ? force->name : "",
            with_on_frame ? "" : ", no on_frame",
            pushes, reports[0], reports[1], reports[2], reports[3], frames_compared, auto_locked_frames, raws_compared);
    return with_on_frame ? frames_compared > 0 : raws_compared > 0;
}

static int random_stream(uint64_t seed, const uint8_t map[VGACAP_SIG_COUNT], int is_tiny) {
    return random_case(seed, map, is_tiny, vgaframe_mode_match(800, 525), BUF_W, BUF_H, NULL);
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

// ---- force_mode, buffers smaller than the line, and no table mode --------

// Colour bits above 7: the per-sample colour has to be computed.
static const uint8_t wide_map[VGACAP_SIG_COUNT] = { 30, 12, 8, 17, 22, 9, 31, 25 };

// 700 x 500 clocks, which no table mode has (checked below), so frames are
// emitted only once the learner locks, and cropped by the auto bounding box.
static const vgaframe_mode_t odd_timing = { "odd700x500", 560, 20, 60, 60, 440, 10, 2, 48, 0, 0, 0 };

// Both maps; with `need_auto`, each run must also emit at least one locked
// continuous frame through the auto-crop.
static int both_maps(uint64_t seed, const vgaframe_mode_t *m, uint16_t W, uint16_t H, const vgaframe_mode_t *force,
                     int need_auto) {
    // A frame is locked only when it agrees with the one before, so for those
    // runs only frame 0 is glitchy: frames 1 to 3 are clean, and 2 and 3 lock.
    glitch_frames = need_auto ? 0x1 : 0x5;
    int ok = random_case(seed, tiny_map, 1, m, W, H, force) && (!need_auto || auto_locked_frames);
    ok = ok && random_case(seed + 1, wide_map, 0, m, W, H, force) && (!need_auto || auto_locked_frames);
    glitch_frames = 0x5;
    return ok;
}

TEST(fastpath_equivalent_forced_640x480) {
    const vgaframe_mode_t *vga = vgaframe_mode_match(800, 525);
    ASSERT_TRUE(both_maps(10, vga, BUF_W, BUF_H, vga, 0));
}

TEST(fastpath_equivalent_forced_larger_mode_clamped) {
    // 1024x768's active area (from x 296, y 35) runs off a 1000 x 700
    // buffer, so emit's w = W - x0 and h = max_lines - y0 clamps apply.
    const vgaframe_mode_t *xga = vgaframe_mode_match(1344, 806);
    ASSERT_TRUE(xga != NULL);
    ASSERT_TRUE(both_maps(20, vgaframe_mode_match(800, 525), BUF_W, BUF_H, xga, 0));
}

TEST(fastpath_equivalent_buffer_narrower_than_line) {
    // 700 x 500 against 800 x 525 timing: every line is clipped at W (so the
    // dirty box reaches W and the clear is one memset), lines past 500 are
    // dropped, and the detected mode's crop is clamped on both axes.
    ASSERT_TRUE(both_maps(30, vgaframe_mode_match(800, 525), 700, 500, NULL, 0));
}

TEST(fastpath_equivalent_tiny_buffer_forced) {
    ASSERT_TRUE(both_maps(40, vgaframe_mode_match(800, 525), 300, 200, vgaframe_mode_match(800, 525), 0));
}

TEST(fastpath_equivalent_no_table_mode_auto_crop) {
    ASSERT_TRUE(vgaframe_mode_match_cpl(700) == NULL);
    ASSERT_TRUE(vgaframe_mode_match(700, 500) == NULL);
    ASSERT_TRUE(both_maps(50, &odd_timing, BUF_W, BUF_H, NULL, 1));
}

TEST(fastpath_equivalent_no_table_mode_auto_crop_small_buffer) {
    ASSERT_TRUE(both_maps(60, &odd_timing, 640, 450, NULL, 1));
}

TEST(fastpath_equivalent_random_tiny_no_on_frame) {
    // Task 10: with no on_frame, emit only clears and counts.
    with_on_frame = 0;
    int ok = random_stream(6, tiny_map, 1) && frames_compared == 0;
    ok = ok && both_maps(80, &odd_timing, BUF_W, BUF_H, NULL, 0) && frames_compared == 0;
    with_on_frame = 1;
    ASSERT_TRUE(ok);
}

TEST(fastpath_equivalent_no_table_mode_forced_800x600_56) {
    const vgaframe_mode_t *svga56 = vgaframe_mode_match(1024, 625);
    ASSERT_TRUE(svga56 != NULL);
    ASSERT_TRUE(both_maps(70, &odd_timing, BUF_W, BUF_H, svga56, 0));
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
    RUN(fastpath_equivalent_forced_640x480);
    RUN(fastpath_equivalent_forced_larger_mode_clamped);
    RUN(fastpath_equivalent_buffer_narrower_than_line);
    RUN(fastpath_equivalent_tiny_buffer_forced);
    RUN(fastpath_equivalent_no_table_mode_auto_crop);
    RUN(fastpath_equivalent_no_table_mode_auto_crop_small_buffer);
    RUN(fastpath_equivalent_no_table_mode_forced_800x600_56);
    RUN(fastpath_equivalent_random_tiny_no_on_frame);
    RUN_TESTS_END();
}
