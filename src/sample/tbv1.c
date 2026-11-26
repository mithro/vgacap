// SPDX-License-Identifier: Apache-2.0
// tb-v1: a port of tinytapeout-vga-videos' ttvga/harness/tb.cpp:379-557, the
// testbench's own frame decoding, after the shared calibration (calib.c).
// Its frames are tb.cpp's, so their legacy hashes are the published ones.
//
// tb.cpp's `clock` before a tick is the trace clock c that tick runs (spec
// clock numbering), and every clock tb.cpp's capture loop reads is a
// difference of two such values or a comparison against the same `clock`,
// so c stands in for it throughout. Its capture loop, per tick:
//     while (frames < target_frames && clock < clock_limit) {
//         tick();
//         h_pulse = <hsync in its pulse>; v_pulse = <vsync in its pulse>;
//         if (prev_v && !v_pulse) { if (in_frame) emit_frame(); in_frame = true; y = -1; }
//         if (prev_h && !h_pulse) { line_start = clock; last_px = -1; if (in_frame) y++; }
//         if (in_frame && y >= 0 && !h_pulse) {
//             x_clock = (double)(clock - line_start);
//             pixel = (int)((x_clock + half) / cpp);
//             if (pixel != last_px) { last_px = pixel; <store at (pixel - hback, y - vback)> }
//         }
//         prev_h = h_pulse; prev_v = v_pulse;
//     }
// (The frame and line statistics, audio and ffmpeg are left out: they do
// not change the frames.)
//
// Runs: uo_out is constant within a run, so h_pulse and v_pulse are too, and
// only the run's first clock can see an edge (and so emit a frame or move
// y). The rest of the run is skipped where nothing can change: !in_frame,
// y < 0, h_pulse, or a row outside the picture (which only leaves last_px
// stale, and every line start resets it before it is read again). Everything
// else is evaluated per clock, as tb.cpp does.
#include "vgacap/sample.h"
#include <limits.h>
#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

typedef struct tb_mode {         // tb.cpp:31-36
    const char *name;
    int line_px, lines;          // total pixels per line, total lines per frame
    int hpulse, hback, width;    // pixels
    int vpulse, vback, height;   // lines
} tb_mode_t;

// tb.cpp:39-51, in its order (the match keeps the first of equal errors).
static const tb_mode_t TB_MODES[] = {
    {"640x480@60", 800, 525, 96, 48, 640, 2, 33, 480},
    {"640x480@72", 832, 520, 40, 128, 640, 3, 28, 480},
    {"640x480@75", 840, 500, 64, 120, 640, 3, 16, 480},
    {"800x600@56", 1024, 625, 72, 128, 800, 2, 22, 600},
    {"800x600@60", 1056, 628, 128, 88, 800, 4, 23, 600},
    {"800x600@72", 1040, 666, 120, 64, 800, 6, 23, 600},
    {"800x600@75", 1056, 625, 80, 160, 800, 3, 21, 600},
    {"1024x768@60", 1344, 806, 136, 160, 1024, 6, 29, 768},
    {"1024x768@70", 1328, 806, 136, 144, 1024, 6, 29, 768},
    {"1280x720@60", 1650, 750, 40, 220, 1280, 5, 20, 720},
    {"1280x1024@60", 1688, 1066, 112, 248, 1280, 3, 38, 1024},
};
#define TB_NMODES (sizeof TB_MODES / sizeof TB_MODES[0])

struct tbv1 {
    vgasample_geometry_t geo;
    const vgafc_t *fc;
    vgasample_frame_fn fn; void *user;
    uint8_t h_low, v_low;
    uint64_t from, target_frames, clock_limit;
    double cpp, half;
    int width, height, hback, vback;
    uint8_t *frame;                  // rgb6, width * height
    // tb.cpp:478-482
    uint64_t frames;
    int in_frame, prev_h, prev_v;
    uint64_t line_start, start;      // start: this frame's vsync end
    int y, last_px;
    int stopped;
};

// tb.cpp:397-451. Fills s->geo, and the capture parameters when ok.
static void decide(tbv1_t *s, const vgacal_t *cal, double clock_hz, double seconds) {
    const vgacal_sync_t *hs = &cal->hs, *vs = &cal->vs;
    s->geo.ok = 0; s->geo.mode = "unknown"; s->geo.cpp = 1.0;
    if (!hs->found || !vs->found) { s->geo.status = "no-sync"; return; }
    if (hs->period == 0 || vs->period < 2 * hs->period) { s->geo.status = "bad-timing"; return; }

    const uint64_t line_clocks = hs->period;
    const int lines = (int)((vs->period + line_clocks / 2) / line_clocks);
    const tb_mode_t *mode = NULL;
    double cpp = 1.0;
    double best = 0.03;
    for (size_t i = 0; i < TB_NMODES; i++) {
        const tb_mode_t *m = &TB_MODES[i];
        double k = (double)line_clocks / m->line_px;
        if (k < 0.9 || k > 4.5) continue;
        double ev = fabs((double)lines - m->lines) / m->lines;
        double ep = fabs((double)hs->pulse / (double)line_clocks - (double)m->hpulse / m->line_px);
        double e = ev + ep;
        if (e < best) { best = e; mode = m; cpp = k; }
    }
    const int vpulse_lines = (int)((vs->pulse + line_clocks / 2) / line_clocks);
    int vertical_matched = 0;
    if (mode == NULL) {
        for (size_t i = 0; i < TB_NMODES; i++) {
            const tb_mode_t *m = &TB_MODES[i];
            double k = (double)line_clocks / m->line_px;
            if (k < 0.9 || k > 4.5) continue;
            double eh = fabs(k - round(k * m->line_px) / m->line_px);
            double ep = fabs((double)hs->pulse / (double)line_clocks - (double)m->hpulse / m->line_px);
            if (ep < 0.01 && eh < 0.02 && lines > m->height) { mode = m; cpp = k; break; }
        }
    } else {
        vertical_matched = 1;
    }
    int width, height, hback, vback;
    if (mode) {
        width = mode->width; hback = mode->hback;
        height = vertical_matched ? mode->height : lines - vpulse_lines;
        vback = vertical_matched ? mode->vback : 0;
        if (fabs(cpp - round(cpp)) < 0.01) cpp = round(cpp);
    } else {
        width = (int)(line_clocks - hs->pulse);
        height = lines - vpulse_lines;
        hback = 0; vback = 0;
    }
    s->geo.mode = mode ? mode->name : "unknown";
    s->geo.cpp = cpp;
    if (width > 4096 || height > 4096 || width < 16 || height < 16) { s->geo.status = "bad-timing"; return; }
    const double fps = clock_hz / (double)vs->period;
    s->target_frames = (uint64_t)(seconds * fps + 0.5);
    s->clock_limit = cal->capture_after_clock + (uint64_t)((seconds + 2.5 / fps) * clock_hz);

    s->geo.ok = 1; s->geo.status = "ok";
    s->geo.width = (uint16_t)width; s->geo.height = (uint16_t)height;
    s->geo.hback = hback; s->geo.vback = vback;
    s->cpp = cpp; s->half = cpp / 2.0;     // tb.cpp:483
    s->width = width; s->height = height; s->hback = hback; s->vback = vback;
}

tbv1_t *tbv1_create(const vgacal_t *cal, const vgafc_t *fc, double clock_hz, double seconds,
                    vgasample_frame_fn fn, void *user) {
    if (cal->state != VGACAL_DONE) {
        fprintf(stderr, "tbv1_create: calibration not finished\n");
        abort();
    }
    tbv1_t *s = calloc(1, sizeof *s);
    if (!s) { fprintf(stderr, "vgacap_sample: out of memory\n"); abort(); }
    s->fc = fc; s->fn = fn; s->user = user;
    s->h_low = cal->hs.active_low; s->v_low = cal->vs.active_low;
    s->from = cal->capture_after_clock;
    s->y = -1; s->last_px = -1;
    decide(s, cal, clock_hz, seconds);
    if (s->geo.ok) {
        s->frame = calloc((size_t)s->width * (size_t)s->height, 1);
        if (!s->frame) { fprintf(stderr, "vgacap_sample: out of memory\n"); abort(); }
    }
    return s;
}

const vgasample_geometry_t *tbv1_geometry(const tbv1_t *s) { return &s->geo; }
uint64_t tbv1_target_frames(const tbv1_t *s) { return s->target_frames; }
uint64_t tbv1_clock_limit(const tbv1_t *s) { return s->clock_limit; }
uint64_t tbv1_frames(const tbv1_t *s) { return s->frames; }

static void emit_frame(tbv1_t *s) {   // tb.cpp:485-505, the frame part
    if (s->fc && s->frames < s->fc->n && s->fc->ends[s->frames] != s->start) {
        fprintf(stderr, "tbv1: frame %llu starts at clock %llu, the frame clock's end at %llu\n",
                (unsigned long long)s->frames, (unsigned long long)s->start,
                (unsigned long long)s->fc->ends[s->frames]);
        abort();
    }
    vgasample_frame_t f = { s->frames, s->start, s->frame, s->geo.width, s->geo.height, 0 };
    if (s->fn) s->fn(s->user, &f);
    memset(s->frame, 0, (size_t)s->width * (size_t)s->height);
    s->frames++;
}

// tb.cpp:550-552's colour, as rgb6: R = uo[0], uo[4]; G = uo[1], uo[5];
// B = uo[2], uo[6], first bit most significant (RGB888 / 85).
static uint8_t rgb6(uint8_t uo) {
    unsigned r = ((uo & 1u) << 1) | ((uo >> 4) & 1u);
    unsigned g = (((uo >> 1) & 1u) << 1) | ((uo >> 5) & 1u);
    unsigned b = (((uo >> 2) & 1u) << 1) | ((uo >> 6) & 1u);
    return (uint8_t)((r << 4) | (g << 2) | b);
}

// tb.cpp:541-553 at clock c, with in_frame && y >= 0 && !h_pulse.
static inline void pixel_at(tbv1_t *s, uint64_t c, uint8_t colour) {
    const double x_clock = (double)(c - s->line_start);
    const double q = (x_clock + s->half) / s->cpp;
    // (int) of a double beyond INT_MAX is undefined; x86, which tb.cpp runs
    // on, gives INT_MIN, and INT_MIN - hback is never in the picture.
    const int pixel = q < 2147483648.0 ? (int)q : INT_MIN;
    if (pixel == s->last_px) return;          // not the first clock of a new pixel period
    s->last_px = pixel;
    if (pixel == INT_MIN) return;
    const int px = pixel - s->hback, py = s->y - s->vback;
    if (px >= 0 && px < s->width && py >= 0 && py < s->height)
        s->frame[(size_t)py * (size_t)s->width + (size_t)px] = colour;
}

void tbv1_push(tbv1_t *s, uint8_t uo_out, uint64_t first, uint64_t clocks) {
    if (!s->geo.ok || s->stopped || !clocks) return;
    uint64_t end = first + clocks;
    if (end <= s->from) return;
    if (first < s->from) first = s->from;
    // while (frames < target_frames && clock < clock_limit)
    if (s->frames >= s->target_frames || first >= s->clock_limit) { s->stopped = 1; return; }
    const uint64_t stop = end < s->clock_limit ? end : s->clock_limit;
    const int h_pulse = s->h_low ? !(uo_out & 0x80) : !!(uo_out & 0x80);
    const int v_pulse = s->v_low ? !(uo_out & 0x08) : !!(uo_out & 0x08);
    const uint8_t colour = rgb6(uo_out);

    // The run's first clock, in full.
    if (s->prev_v && !v_pulse) {                  // vsync pulse ended: new frame
        if (s->in_frame) emit_frame(s);
        s->start = first;
        s->in_frame = 1; s->y = -1;
    }
    if (s->prev_h && !h_pulse) {                  // hsync pulse ended: new line
        s->line_start = first;
        s->last_px = -1;
        if (s->in_frame) s->y++;
    }
    if (s->in_frame && s->y >= 0 && !h_pulse) pixel_at(s, first, colour);
    s->prev_h = h_pulse; s->prev_v = v_pulse;
    if (s->frames >= s->target_frames) { s->stopped = 1; return; }

    // The rest: no edges, so frames, y and line_start stay as they are.
    if (!s->in_frame || s->y < 0 || h_pulse) return;
    const int py = s->y - s->vback;
    if (py < 0 || py >= s->height) return;
    for (uint64_t c = first + 1; c < stop; c++) pixel_at(s, c, colour);
    if (stop < end) s->stopped = 1;
}

void tbv1_free(tbv1_t *s) {
    if (!s) return;
    free(s->frame);
    free(s);
}
