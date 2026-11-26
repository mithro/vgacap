// SPDX-License-Identifier: Apache-2.0
// The raw-frame callback (vgaframe_config_t.on_raw), the clearing of raw and
// cover at every frame boundary, the retroactive flag, the rgb6 output, and
// emit skipping its conversion when no on_frame callback is set.
#include "harness.h"
#include "vgacap/frame.h"
#include "synth.h"

#define W 1400
#define H 900

static uint32_t buf[6u * 801u * 525u];   // room for six frames of up to 801 x 525 clocks
static uint8_t raw[W * H], rgb[W * H * 3], cover[H], rgb6buf[W * H];

// Tiny VGA: hsync bit 7, vsync bit 3, r1 0, r0 4, g1 1, g0 5, b1 2, b0 6.
static const uint8_t tiny_map[8] = { 7, 3, 0, 4, 1, 5, 2, 6 };

// 640x480 with one clock more front porch: 801 x 525, which no table mode has.
static const vgaframe_mode_t odd801 = { "odd801", 640, 17, 96, 48, 480, 10, 2, 33, 0, 0, 0 };

// ---- callbacks -------------------------------------------------------------

static int nframes;
static vgaframe_output_t last;
static int rgb6_frames, rgb6_bad;   // frames checked by on_frame_rgb6, and pixels that failed

static void on_frame(void *u, const vgaframe_output_t *o) { (void)u; last = *o; nframes++; }

// Every pixel: rgb6 is the 6-bit colour rgb24 shows.
static void on_frame_rgb6(void *u, const vgaframe_output_t *o) {
    (void)u; last = *o; nframes++;
    if (!o->rgb6) { rgb6_bad++; return; }
    for (uint32_t i = 0; i < (uint32_t)o->width * o->height; i++) {
        const uint8_t *p = o->rgb24 + 3 * i;
        uint8_t want = (uint8_t)((p[0] / 85) << 4 | (p[1] / 85) << 2 | (p[2] / 85));
        if (o->rgb6[i] != want) rgb6_bad++;
    }
    rgb6_frames++;
}

typedef struct raw_log {
    int n;                    // on_raw calls so far
    int snap_at;              // the call (1-based) whose raw is copied into snap
    uint32_t lines[16], stride[16];
    uint8_t retro[16], locked[16];
    const vgaframe_mode_t *mode[16];
    int raw_is_buffer[16];
    int nframes_at[16];       // on_frame calls before this on_raw
    uint8_t snap[W * H];
} raw_log_t;
static raw_log_t rl;

static void on_raw(void *u, const vgaframe_raw_t *r) {
    raw_log_t *l = u;
    int i = l->n < 16 ? l->n : 15;
    l->lines[i] = r->lines; l->stride[i] = r->stride; l->retro[i] = r->retroactive;
    l->locked[i] = r->timing ? r->timing->locked : 0xFF;
    l->mode[i] = r->timing ? r->timing->mode : NULL;
    l->raw_is_buffer[i] = r->raw == raw;
    l->nframes_at[i] = nframes;
    l->n++;
    if (l->n == l->snap_at) memcpy(l->snap, r->raw, (size_t)r->stride * H);
}

static void setup(vgaframe_t *f, vgaframe_frame_fn frame_fn, int with_raw, int snap_at) {
    vgaframe_config_t c; memset(&c, 0, sizeof c);
    c.max_clocks_per_line = W; c.max_lines = H;
    memcpy(c.signal_map, tiny_map, 8);
    c.on_frame = frame_fn;
    if (with_raw) { c.on_raw = on_raw; c.raw_user = &rl; }
    memset(&rl, 0, sizeof rl); rl.snap_at = snap_at;
    nframes = 0; rgb6_frames = rgb6_bad = 0;
    memset(&last, 0, sizeof last);
    if (vgaframe_init(f, &c, raw, rgb, cover) != 0) { fprintf(stderr, "  vgaframe_init failed\n"); exit(1); }
}

// ---- streams ---------------------------------------------------------------

static uint8_t bars(void *u, uint16_t x, uint16_t y) { (void)u; (void)y; return (uint8_t)((x / 10) & 0x3F); }
static uint8_t black(void *u, uint16_t x, uint16_t y) { (void)u; (void)x; (void)y; return 0; }
static uint8_t every_colour(void *u, uint16_t x, uint16_t y) { (void)u; return (uint8_t)((x + 7u * y) & 0x3F); }

static uint32_t tiny(uint8_t c, uint32_t hs, uint32_t vs) {
    uint32_t r1 = (c >> 5) & 1, r0 = (c >> 4) & 1, g1 = (c >> 3) & 1;
    uint32_t g0 = (c >> 2) & 1, b1 = (c >> 1) & 1, b0 = c & 1;
    return (hs << 7) | (b0 << 6) | (g0 << 5) | (r0 << 4) | (vs << 3) | (b1 << 2) | (g1 << 1) | r1;
}

// Lines [from, lines) of an 801-clock frame of `lines` lines with odd801's
// sync (both pulses negative, starting at line 0 clock 0), every clock
// outside the hsync pulse - blanking included - of colour `c`, the pulse
// black (a line start is reported at the pulse's trailing edge, so a lit
// pulse would land at the end of the line before). Returns the samples
// written.
static size_t odd_frame(uint32_t *out, uint32_t lines, uint32_t from, uint8_t c) {
    size_t k = 0;
    for (uint32_t y = from; y < lines; y++)
        for (uint32_t x = 0; x < 801; x++)
            out[k++] = tiny(x >= odd801.h_sync ? c : 0, x >= odd801.h_sync, y >= odd801.v_sync);
    return k;
}

static void push_all(vgaframe_t *f, const uint32_t *s, size_t n) {
    size_t i = 0;
    while (i < n) {
        size_t j = i;
        while (j < n && s[j] == s[i]) j++;
        vgaframe_push(f, s[i], (uint32_t)(j - i));
        i = j;
    }
}

// `frames` whole synth frames of mode `m`, starting `skip_lines` lines in.
static size_t synth_frames(const vgaframe_mode_t *m, uint8_t (*px)(void *, uint16_t, uint16_t), int frames,
                           uint32_t skip_lines) {
    uint32_t cpl = (uint32_t)m->h_active + m->h_front + m->h_sync + m->h_back;
    size_t n = synth_frame(m, px, NULL, buf, sizeof buf / sizeof buf[0]);
    if (!n) { fprintf(stderr, "  synth_frame failed\n"); exit(1); }
    for (int k = 1; k < frames; k++) memcpy(buf + (size_t)k * n, buf, n * sizeof buf[0]);
    size_t skip = (size_t)skip_lines * cpl;
    memmove(buf, buf + skip, ((size_t)frames * n - skip) * sizeof buf[0]);
    return (size_t)frames * n - skip;
}

// Is any pixel of raw rows [y0, y1) (in a W-wide snapshot) written?
static int any_written(const uint8_t *r, uint32_t y0, uint32_t y1) {
    for (uint32_t y = y0; y < y1; y++)
        for (uint32_t x = 0; x < W; x++)
            if (r[(size_t)y * W + x] & 0x80) return 1;
    return 0;
}

// ---- tests -----------------------------------------------------------------
//
// A stream that starts at a frame's vsync pulse (synth_frame's line 0) cannot
// claim that first frame: the pulse is only known to be the pulse once the
// idle phase after it has ended, which is the next frame's start (report 2).
// So "a stream of N frames" below is N frames after that unclaimed one, and
// the start of the frame after them, whose boundary completes the last.

TEST(raw_fires_without_lock) {
    ASSERT_TRUE(vgaframe_mode_match(801, 525) == NULL);
    ASSERT_TRUE(vgaframe_mode_match_cpl(801) == NULL);
    vgaframe_t f; setup(&f, on_frame, 1, 0);
    // From line 100 of a frame: the next one, claimed retroactively, and
    // the first 10 lines of the one after, whose start completes it. The
    // first measurement of the timing is at that start, so nothing is
    // locked yet.
    size_t n = synth_frames(&odd801, bars, 3, 100) - (525u - 10u) * 801u;
    push_all(&f, buf, n);
    ASSERT_EQ_U(nframes, 0);
    ASSERT_EQ_U(rl.n, 1);
    ASSERT_EQ_U(rl.lines[0], 525);
    ASSERT_EQ_U(rl.stride[0], W);
    ASSERT_TRUE(rl.raw_is_buffer[0]);
    ASSERT_EQ_U(rl.locked[0], 0);
    ASSERT_TRUE(rl.mode[0] == NULL);
    ASSERT_EQ_U(rl.retro[0], 1);
}

TEST(raw_fires_for_black) {
    vgaframe_t f; setup(&f, on_frame, 1, 0);
    push_all(&f, buf, synth_frames(&odd801, black, 5, 0));
    // Frame 1 is never claimed and frame 5 never completed: 2, 3 and 4 are
    // each completed by the next one's start.
    ASSERT_EQ_U(rl.n, 3);
    ASSERT_EQ_U(nframes, 0);
    for (int i = 0; i < 3; i++) ASSERT_EQ_U(rl.lines[i], 525);
}

TEST(shorter_second_frame_clean) {
    // A lead-in from line 200 of a 520-line frame, then A (525 lines, claimed
    // retroactively at the end of its vsync pulse) and B (520), every clock
    // lit, then the start of a frame that completes B. A and B do not agree,
    // and 801 clocks match no mode, so nothing is emitted and only the
    // boundary clears raw.
    size_t n = 0;
    n += odd_frame(buf + n, 520, 200, 0x3F);
    n += odd_frame(buf + n, 525, 0, 0x3F);
    n += odd_frame(buf + n, 520, 0, 0x3F);
    n += odd_frame(buf + n, 10, 0, 0x3F);
    vgaframe_t f; setup(&f, on_frame, 1, 2);
    push_all(&f, buf, n);
    ASSERT_EQ_U(nframes, 0);
    ASSERT_EQ_U(rl.n, 2);
    ASSERT_EQ_U(rl.lines[0], 525);
    ASSERT_EQ_U(rl.lines[1], 520);
    ASSERT_TRUE(any_written(rl.snap, 519, 520));
    ASSERT_TRUE(!any_written(rl.snap, 520, 525));
}

TEST(retroactive_first_frame) {
    const vgaframe_mode_t *m = vgaframe_mode_match(800, 525);
    vgaframe_t f; setup(&f, on_frame, 1, 1);
    // Starts mid-frame, at line 100: frame 2 is claimed retroactively at the
    // end of its vsync pulse, frame 3 by its report 2, frame 4 never ends.
    push_all(&f, buf, synth_frames(m, bars, 4, 100));
    ASSERT_EQ_U(rl.n, 2);
    ASSERT_EQ_U(rl.retro[0], 1);
    ASSERT_EQ_U(rl.lines[0], 525);
    ASSERT_TRUE(!any_written(rl.snap, 0, m->v_sync));
    ASSERT_TRUE(any_written(rl.snap, m->v_sync, (uint32_t)m->v_sync + 1));
    ASSERT_EQ_U(rl.retro[1], 0);
}

TEST(rgb6_matches_rgb24) {
    const vgaframe_mode_t *m = vgaframe_mode_match(800, 525);
    vgaframe_t f;
    // Without a buffer there is no rgb6.
    setup(&f, on_frame, 0, 0);
    push_all(&f, buf, synth_frames(m, every_colour, 3, 0));
    ASSERT_TRUE(nframes >= 1);
    ASSERT_TRUE(last.rgb6 == NULL);
    // With one, every pixel of every frame.
    setup(&f, on_frame_rgb6, 0, 0);
    memset(rgb6buf, 0xAB, sizeof rgb6buf);
    vgaframe_set_rgb6_buffer(&f, rgb6buf);
    push_all(&f, buf, synth_frames(m, every_colour, 3, 0));
    ASSERT_TRUE(rgb6_frames >= 1);
    ASSERT_EQ_U(rgb6_bad, 0);
    ASSERT_TRUE(last.rgb6 == rgb6buf);
    ASSERT_EQ_U(last.width, 640); ASSERT_EQ_U(last.height, 480); ASSERT_EQ_U(last.partial, 0);
    // An unwritten pixel is 0 in rgb6 (and magenta in rgb24): a frame
    // flushed 200 lines in has its active area from line 165 on unwritten.
    size_t n = synth_frames(m, every_colour, 3, 0);
    setup(&f, on_frame, 0, 0);
    vgaframe_set_rgb6_buffer(&f, rgb6buf);
    push_all(&f, buf, n - 325u * 800u);
    int before = nframes;
    vgaframe_flush(&f);
    ASSERT_EQ_U(nframes, before + 1);
    ASSERT_EQ_U(last.partial, 1);
    ASSERT_TRUE(last.rgb6 == rgb6buf);
    for (uint32_t y = 0; y < 480; y++)
        for (uint32_t x = 0; x < 640; x++) {
            uint32_t i = y * 640 + x;
            const uint8_t *p = last.rgb24 + 3 * i;
            if (y < 165) {
                ASSERT_EQ_U(last.rgb6[i], every_colour(NULL, (uint16_t)x, (uint16_t)y));
            } else {
                ASSERT_EQ_U(last.rgb6[i], 0);
                ASSERT_EQ_U(p[0], 255); ASSERT_EQ_U(p[1], 0); ASSERT_EQ_U(p[2], 255);
            }
        }
}

TEST(no_on_frame_skips_conversion) {
    const vgaframe_mode_t *m = vgaframe_mode_match(800, 525);
    vgaframe_t f; setup(&f, NULL, 1, 0);
    vgaframe_set_rgb6_buffer(&f, rgb6buf);
    memset(rgb, 0xAB, sizeof rgb);
    memset(rgb6buf, 0xAB, sizeof rgb6buf);
    // Five frames, the first unclaimed, and the start of a sixth: frames 2
    // to 5 are completed.
    push_all(&f, buf, synth_frames(m, bars, 6, 0) - (525u - 10u) * 800u);
    ASSERT_EQ_U(rl.n, 4);
    // emit's bookkeeping still ran: frames 2 to 5 had a table mode at their
    // boundaries (frame 1 was never claimed, so frame 2's start ends nothing).
    ASSERT_EQ_U(f.frames_seen, 4);
    for (size_t i = 0; i < sizeof rgb; i++) if (rgb[i] != 0xAB) { ASSERT_EQ_U(rgb[i], 0xAB); }
    for (size_t i = 0; i < sizeof rgb6buf; i++) if (rgb6buf[i] != 0xAB) { ASSERT_EQ_U(rgb6buf[i], 0xAB); }
}

TEST(boundary_clears_after_early_return) {
    // A lead-in from line 200, then F1 (claimed retroactively) and F2: 525
    // lines of black, which lock the learner (they agree) with no mode (801
    // clocks), so F2's boundary calls emit and emit's auto-crop finds nothing
    // to show and returns early. F3 is 520 lines, lit, and F4's start
    // completes it.
    size_t n = 0;
    n += odd_frame(buf + n, 525, 200, 0);
    n += odd_frame(buf + n, 525, 0, 0);
    n += odd_frame(buf + n, 525, 0, 0);
    n += odd_frame(buf + n, 520, 0, 0x2A);
    n += odd_frame(buf + n, 10, 0, 0x2A);
    vgaframe_t f; setup(&f, on_frame, 1, 3);
    push_all(&f, buf, n);
    ASSERT_EQ_U(rl.n, 3);
    ASSERT_EQ_U(rl.locked[1], 1);          // F2's boundary: locked, so emit ran
    ASSERT_TRUE(rl.mode[1] == NULL);       // with no mode: the auto-crop
    ASSERT_EQ_U(rl.nframes_at[2], 0);      // ... which returned early
    ASSERT_EQ_U(rl.lines[2], 520);
    ASSERT_EQ_U(rl.snap[(size_t)519 * W + 800], 0x80 | 0x2A);
    ASSERT_TRUE(!any_written(rl.snap, 520, 525));
}

TEST(fram_early_return_then_continuous_is_clean) {
    // A FRAM window of black lines 0..9 at 801 clocks a line (no table mode,
    // nothing lit) completes its 10-line picture, whose emit returns early
    // with nothing to show, so it stays pending. A continuous 801 x 525
    // stream from line 100 then claims a frame retroactively, which flushes
    // the pending picture - and that emit returns early too. The claimed
    // frame's rows 0 .. vsync_lines - 1 are never received, so they must be
    // unwritten in its on_raw, not the FRAM window's pixels.
    vgaframe_config_t c; memset(&c, 0, sizeof c);
    c.max_clocks_per_line = 1000; c.max_lines = 600;
    memcpy(c.signal_map, tiny_map, 8);
    c.on_frame = on_frame; c.on_raw = on_raw; c.raw_user = &rl;
    memset(&rl, 0, sizeof rl); rl.snap_at = 1; nframes = 0;
    vgaframe_t f; ASSERT_EQ_U(vgaframe_init(&f, &c, raw, rgb, cover), 0);
    // Ten black lines from mid-frame (vsync idle throughout), as the
    // window's lines 0..9.
    size_t n = odd_frame(buf, 110, 100, 0);
    vgaframe_frame_begin(&f, 1, 0, 10, 801, (uint32_t)n);
    push_all(&f, buf, n);
    ASSERT_EQ_U(nframes, 0);
    n = 0;
    n += odd_frame(buf + n, 525, 100, 0x2A);
    n += odd_frame(buf + n, 525, 0, 0x2A);
    n += odd_frame(buf + n, 10, 0, 0x2A);
    push_all(&f, buf, n);
    ASSERT_EQ_U(rl.n, 1);
    ASSERT_EQ_U(rl.retro[0], 1);
    ASSERT_EQ_U(rl.stride[0], 1000);
    uint32_t written = 0;
    for (uint32_t i = 0; i < (uint32_t)odd801.v_sync * 1000u; i++) written += (rl.snap[i] & 0x80) != 0;
    ASSERT_EQ_U(written, 0);
    ASSERT_EQ_U(rl.snap[(size_t)odd801.v_sync * 1000u + 800u], 0x80 | 0x2A);   // its own first row is there
}

int main(void) {
    RUN(raw_fires_without_lock);
    RUN(raw_fires_for_black);
    RUN(shorter_second_frame_clean);
    RUN(retroactive_first_frame);
    RUN(rgb6_matches_rgb24);
    RUN(no_on_frame_skips_conversion);
    RUN(boundary_clears_after_early_return);
    RUN(fram_early_return_then_continuous_is_clean);
    RUN_TESTS_END();
}
