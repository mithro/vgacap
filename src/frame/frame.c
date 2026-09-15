// SPDX-License-Identifier: Apache-2.0
// Reconstructs RGB24 frames from a continuous stream of (sample, run) pairs,
// and from FRAM partial-frame windows (see vgaframe_frame_begin).
#include "vgacap/frame.h"
#include <string.h>

size_t vgaframe_raw_size(const vgaframe_config_t *c) { return (size_t)c->max_clocks_per_line * c->max_lines; }
size_t vgaframe_rgb_size(const vgaframe_config_t *c) { return vgaframe_raw_size(c) * 3; }

static uint8_t bit(const uint8_t *map, uint32_t s, int sig) {
    uint8_t b = map[sig];
    return b == VGACAP_SIG_ABSENT ? 0 : (uint8_t)((s >> b) & 1);
}

uint8_t vgaframe_colour(const uint8_t *m, uint32_t s) {
    return (uint8_t)((bit(m, s, VGACAP_SIG_R1) << 5) | (bit(m, s, VGACAP_SIG_R0) << 4) |
                     (bit(m, s, VGACAP_SIG_G1) << 3) | (bit(m, s, VGACAP_SIG_G0) << 2) |
                     (bit(m, s, VGACAP_SIG_B1) << 1) | bit(m, s, VGACAP_SIG_B0));
}

int vgaframe_init(vgaframe_t *f, const vgaframe_config_t *cfg, uint8_t *raw, uint8_t *rgb, uint8_t *cover) {
    if (!cfg->max_clocks_per_line || !cfg->max_lines || !raw || !rgb || !cover) return -1;
    memset(f, 0, sizeof *f);
    f->cfg = *cfg; f->raw = raw; f->rgb = rgb; f->cover = cover;
    vgaframe_timing_init(&f->learner);
    memset(raw, 0, vgaframe_raw_size(cfg));
    memset(cover, 0, cfg->max_lines);
    return 0;
}

// Lines expected in a full frame, best information currently available:
// force_mode's table value, else the learner's locked/matched value, else
// (FRAM mode with no mode known yet) the largest window seen so far.
static uint32_t expected_lines(const vgaframe_t *f) {
    if (f->cfg.force_mode)
        return (uint32_t)f->cfg.force_mode->v_active + f->cfg.force_mode->v_front +
               f->cfg.force_mode->v_sync + f->cfg.force_mode->v_back;
    if (f->learner.t.lines_per_frame) return f->learner.t.lines_per_frame;
    return f->fram_max_line;
}

static int covered(const vgaframe_t *f) {
    uint32_t n = expected_lines(f);
    if (!n) return 0;
    for (uint32_t y = 0; y < n && y < f->cfg.max_lines; y++)
        if (!f->cover[y]) return 0;
    return 1;
}

// Emits the accumulated picture. `lines_known` bounds how much of `raw` to
// scan for the auto-crop bounding box and to clear afterwards.
static void emit(vgaframe_t *f, uint32_t lines_known, uint8_t partial_hint) {
    const vgaframe_mode_t *m = f->cfg.force_mode ? f->cfg.force_mode : f->learner.t.mode;
    uint32_t W = f->cfg.max_clocks_per_line, x0, y0, w, h;
    if (m) {
        x0 = (uint32_t)m->h_sync + m->h_back; y0 = (uint32_t)m->v_sync + m->v_back;
        w = m->h_active; h = m->v_active;
    } else { // auto: bounding box of non-black written pixels
        uint32_t minx = W, maxx = 0, miny = f->cfg.max_lines, maxy = 0;
        for (uint32_t y = 0; y < lines_known && y < f->cfg.max_lines; y++)
            for (uint32_t x = 0; x < W; x++) {
                uint8_t p = f->raw[y * W + x];
                if ((p & 0x80) && (p & 0x3F)) {
                    if (x < minx) minx = x;
                    if (x > maxx) maxx = x;
                    if (y < miny) miny = y;
                    if (y > maxy) maxy = y;
                }
            }
        if (minx > maxx) return; // nothing to show
        x0 = minx; y0 = miny; w = maxx - minx + 1; h = maxy - miny + 1;
    }
    if (x0 + w > W) w = W - x0;
    if (y0 + h > f->cfg.max_lines) h = f->cfg.max_lines - y0;
    uint8_t partial = partial_hint;
    for (uint32_t y = 0; y < h; y++)
        for (uint32_t x = 0; x < w; x++) {
            uint8_t p = f->raw[(y0 + y) * W + x0 + x];
            uint8_t *o = f->rgb + (y * w + x) * 3;
            if (!(p & 0x80)) { o[0] = 255; o[1] = 0; o[2] = 255; partial = 1; continue; }
            o[0] = (uint8_t)(((p >> 4) & 3) * 85);
            o[1] = (uint8_t)(((p >> 2) & 3) * 85);
            o[2] = (uint8_t)((p & 3) * 85);
        }
    vgaframe_output_t out;
    out.rgb24 = f->rgb; out.width = (uint16_t)w; out.height = (uint16_t)h; out.timing = &f->learner.t;
    out.active_x0 = (uint16_t)x0; out.active_y0 = (uint16_t)y0; out.partial = partial;
    out.frame_counter = f->fram_mode ? f->fram_counter : f->frames_seen;
    f->frames_seen++;
    if (f->cfg.on_frame) f->cfg.on_frame(f->cfg.user, &out);
    uint32_t clear_lines = lines_known < f->cfg.max_lines ? lines_known + 1 : f->cfg.max_lines;
    memset(f->raw, 0, (size_t)W * clear_lines);
    memset(f->cover, 0, f->cfg.max_lines);
}

void vgaframe_push(vgaframe_t *f, uint32_t sample, uint32_t run) {
    uint8_t h = bit(f->cfg.signal_map, sample, VGACAP_SIG_HSYNC);
    uint8_t v = bit(f->cfg.signal_map, sample, VGACAP_SIG_VSYNC);
    int r = vgaframe_timing_push(&f->learner, h, v, run);
    if (f->fram_mode) {
        if (r == 1) { f->x = 0; f->y++; }
        // r == 2 (learner-detected frame boundary) is ignored: in FRAM mode
        // the window metadata (vgaframe_frame_begin), not the free-running
        // learner, defines line/frame layout.
    } else if (r == 2) {
        if (f->in_frame && (f->learner.t.locked || f->cfg.force_mode)) emit(f, f->y + 1, 0);
        f->y = 0; f->x = 0; f->in_frame = 1;
    } else if (r == 1) {
        f->x = 0;
        if (f->in_frame) f->y++;
    }
    if (!f->in_frame && !f->fram_mode) return;
    uint32_t W = f->cfg.max_clocks_per_line;
    if (f->y < f->cfg.max_lines && f->x < W) {
        uint32_t n = run;
        if (f->x + n > W) n = W - f->x;
        memset(f->raw + f->y * W + f->x, vgaframe_colour(f->cfg.signal_map, sample) | 0x80, n);
        f->cover[f->y] = 1;
    }
    f->x += run;
    if (f->fram_mode) {
        uint32_t dec = run < f->fram_remaining ? run : f->fram_remaining;
        f->fram_remaining -= dec;
        if (f->fram_remaining == 0 && covered(f)) emit(f, expected_lines(f), 0);
    }
}

void vgaframe_flush(vgaframe_t *f) {
    if (f->in_frame || f->fram_mode) emit(f, f->y + 1, 1);
    f->in_frame = 0;
}

void vgaframe_frame_begin(vgaframe_t *f, uint32_t frame_counter, uint16_t first_line,
                          uint16_t line_count, uint32_t clocks_per_line, uint32_t sample_count) {
    if (f->fram_mode && frame_counter != f->fram_counter) {
        int any = 0;
        for (uint32_t y = 0; y < f->cfg.max_lines; y++)
            if (f->cover[y]) { any = 1; break; }
        if (any) emit(f, expected_lines(f) ? expected_lines(f) : f->fram_max_line, 1);
    }
    f->fram_mode = 1;
    f->fram_counter = frame_counter;
    f->fram_first_line = first_line;
    f->fram_line_count = line_count;
    f->fram_cpl = clocks_per_line;
    f->fram_remaining = sample_count;
    f->y = first_line;
    f->x = 0;
    if ((uint32_t)first_line + line_count > f->fram_max_line) f->fram_max_line = (uint32_t)first_line + line_count;
    // The window starts at an hsync leading edge, but is not contiguous with
    // whatever the learner last saw (a different, possibly distant, window).
    // Forget the in-progress hsync phase so the discontinuity is not
    // mistaken for a real edge; vgaframe_timing_push will treat the first
    // sample of this window as a fresh start, exactly like stream start.
    f->learner.clk_in_line = 0;
    f->learner.have_prev = 0;
    f->learner.h_high = 0;
    f->learner.h_low = 0;
    f->learner.v_high_lines = 0;
    f->learner.v_low_lines = 0;
}
