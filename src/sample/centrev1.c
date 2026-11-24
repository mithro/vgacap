// SPDX-License-Identifier: Apache-2.0
// centre-v1: libvgaframe's timing learner supplies the lines and frames at
// clock resolution (its raw frame callback); this picks every pixel itself,
// mid-pixel, from the raw frame, in a window fixed once per run from the
// shared calibration. The definition is the spec's (raw-frames-and-hash-
// methods design, Layer 2's centre-v1 and Layer 3); sample.h summarises it
// and python/vgacap/centre.py is the reference for the window and the pick.
//
// Lines and frames. libvgaframe reports a line at its hsync pulse's trailing
// edge, on the first clock of the push that leaves the pulse, and dates it
// from the leading edge: x = report_x (the pulse's width) at that clock. So
// a row's leading edge is that push's first clock less report_x. Report 2
// starts a frame at row 0, the first line whose trailing edge sees vsync in
// its pulse; report 3 claims, at the trailing edge of the first line out of
// the first pulse, a frame that began vsync_lines lines earlier. The raw
// callback for a frame fires inside the push that brings the next report 2,
// before that push's report is acted on here, so the frame's own start and
// row-v clock are still the ones recorded for it.
#include "vgacap/sample.h"
#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define CV_MAX_CPL 7596u     // 4.5 clocks a pixel times the widest table line (1,688)
#define CV_MAX_LINES 1200u   // above which a frame is bad timing

struct centrev1 {
    vgaframe_t vf;
    uint8_t *raw, *rgb, *cover;
    vgasample_frame_fn fn; void *user;
    void (*raw_fn)(void *user, const vgaframe_raw_t *r); void *raw_user;
    int dx, dy;
    uint64_t next;                  // the next clock expected
    // Fixed at centrev1_calibrated.
    int calibrated;
    const vgafc_t *fc;
    vgasample_geometry_t geo;
    uint64_t L, lines, vpl;         // clocks a line, lines a frame, vsync pulse lines
    double cpp, hsync;              // hsync: the pulse in pixels, hs.pulse / cpp
    int hb0, vb0;                   // hback', vback' without the override
    int64_t *xs;                    // xs[p]: pixel p's clock, -1 when outside the line
    uint8_t *frame;                 // rgb6, width * height
    uint8_t *map;                   // L * lines: 1 where a clock was ever lit
    uint64_t colour_changes, off_grid;
    // The frame in progress.
    int have_start, v_found;
    uint64_t start, v_clock;        // row 0's leading edge; row v's trailing edge
};

static void *xcalloc(size_t n, size_t size) {
    void *p = calloc(n ? n : 1, size);
    if (!p) { fprintf(stderr, "vgacap_sample: out of memory\n"); abort(); }
    return p;
}

// The diagnostics' boundary before pixel p of a window with back porch hb:
// the clock edge nearest (hsync + hb + p) * cpp, floor(that + 0.5). At whole
// cpp the product is a whole number of clocks and this is it exactly; at
// fractional cpp the design's own boundaries are quantised to clocks too, by
// up to one clock either way, and the nearest edge keeps a window's last
// pixel inside it (1.90625: pixel 639 of 640x480@60 ends at 1494.5 clocks).
static int64_t window_clock(const centrev1_t *s, int hb, int64_t p) {
    return (int64_t)floor((s->hsync + (double)hb + (double)p) * s->cpp + 0.5);
}

static void on_raw(void *user, const vgaframe_raw_t *r);

centrev1_t *centrev1_create(int dx, int dy, vgasample_frame_fn fn, void *user) {
    static const uint8_t tiny_map[VGACAP_SIG_COUNT] = { 7, 3, 0, 4, 1, 5, 2, 6 };
    centrev1_t *s = xcalloc(1, sizeof *s);
    s->fn = fn; s->user = user; s->dx = dx; s->dy = dy;
    s->geo.ok = 0; s->geo.status = "no-sync"; s->geo.mode = "unknown"; s->geo.cpp = 1.0;
    vgaframe_config_t c;
    memset(&c, 0, sizeof c);
    c.max_clocks_per_line = CV_MAX_CPL;
    c.max_lines = CV_MAX_LINES;
    memcpy(c.signal_map, tiny_map, sizeof tiny_map);
    c.on_frame = NULL;              // emit keeps only its bookkeeping
    c.on_raw = on_raw; c.raw_user = s;
    s->raw = xcalloc(vgaframe_raw_size(&c), 1);
    s->rgb = xcalloc(vgaframe_rgb_size(&c), 1);
    s->cover = xcalloc(c.max_lines, 1);
    if (vgaframe_init(&s->vf, &c, s->raw, s->rgb, s->cover)) {
        fprintf(stderr, "centrev1_create: vgaframe_init failed\n");
        abort();
    }
    return s;
}

void centrev1_set_raw_observer(centrev1_t *s, void (*fn)(void *user, const vgaframe_raw_t *r), void *user) {
    s->raw_fn = fn; s->raw_user = user;
}

const vgasample_geometry_t *centrev1_geometry(const centrev1_t *s) { return &s->geo; }

// The mode, cpp and window (sample.h; tb.cpp:397-446 for the mode).
static void decide(centrev1_t *s, const vgacal_t *cal) {
    const vgacal_sync_t *hs = &cal->hs, *vs = &cal->vs;
    vgasample_geometry_t *g = &s->geo;
    g->ok = 0; g->mode = "unknown"; g->cpp = 1.0;
    g->width = g->height = 0; g->hback = g->vback = 0;
    if (!hs->found || !vs->found) { g->status = "no-sync"; return; }
    if (hs->period == 0 || vs->period < 2 * hs->period) { g->status = "bad-timing"; return; }

    const uint64_t L = hs->period;
    const uint64_t lines = (vs->period + L / 2) / L;
    size_t nm;
    const vgaframe_mode_t *modes = vgaframe_modes(&nm);
    const vgaframe_mode_t *mode = NULL;
    double cpp = 1.0, best = 0.03;
    for (size_t i = 0; i < nm; i++) {
        const vgaframe_mode_t *m = &modes[i];
        const double lp = (double)vgaframe_mode_line_px(m), fl = (double)vgaframe_mode_frame_lines(m);
        double k = (double)L / lp;
        if (k < 0.9 || k > 4.5) continue;
        double ev = fabs((double)lines - fl) / fl;
        double ep = fabs((double)hs->pulse / (double)L - (double)m->h_sync / lp);
        double e = ev + ep;
        if (e < best) { best = e; mode = m; cpp = k; }
    }
    const uint64_t vpl = (vs->pulse + L / 2) / L;
    int vertical_matched = 0;
    if (mode == NULL) {
        for (size_t i = 0; i < nm; i++) {
            const vgaframe_mode_t *m = &modes[i];
            const double lp = (double)vgaframe_mode_line_px(m);
            double k = (double)L / lp;
            if (k < 0.9 || k > 4.5) continue;
            double eh = fabs(k - round(k * lp) / lp);
            double ep = fabs((double)hs->pulse / (double)L - (double)m->h_sync / lp);
            if (ep < 0.01 && eh < 0.02 && lines > m->v_active) { mode = m; cpp = k; break; }
        }
    } else {
        vertical_matched = 1;
    }
    int64_t width, height;
    double hsync;
    int hb, vb;
    if (mode) {
        // Clocks per pixel: 1. cpp_l within 0.01 of a whole number; 2. the
        // hsync pulse within 2 clocks of k >= 1 times the mode's; 3. cpp_l.
        const double cpp_l = cpp;
        if (fabs(cpp_l - round(cpp_l)) < 0.01) {
            cpp = round(cpp_l);
        } else {
            cpp = cpp_l;
            for (uint64_t k = 1; k * mode->h_sync <= hs->pulse + 2; k++) {
                int64_t d = (int64_t)hs->pulse - (int64_t)(k * mode->h_sync);
                if (d >= -2 && d <= 2) { cpp = (double)k; break; }
            }
        }
        width = mode->h_active;
        height = vertical_matched ? (int64_t)mode->v_active : (int64_t)lines - (int64_t)vpl;
        hsync = (double)hs->pulse / cpp;
        const double B = (double)L / cpp - hsync - (double)width;
        hb = (int)floor(B * mode->h_back / (mode->h_back + mode->h_front) + 0.5);
        if (vertical_matched) {
            const double Bv = (double)lines - (double)vpl - (double)height;
            vb = (int)floor(Bv * mode->v_back / (mode->v_back + mode->v_front) + 0.5);
        } else {
            vb = 0;
        }
    } else {                                  // tb.cpp:441-444
        cpp = 1.0;
        hsync = (double)hs->pulse;
        width = (int64_t)L - (int64_t)hs->pulse;
        height = (int64_t)lines - (int64_t)vpl;
        hb = vb = 0;
    }
    g->mode = mode ? mode->name : "unknown";
    g->cpp = cpp;
    if (width > 4096 || height > 4096 || width < 16 || height < 16 || lines > CV_MAX_LINES || L > CV_MAX_CPL) {
        g->status = "bad-timing";
        return;
    }
    g->ok = 1; g->status = "ok";
    g->width = (uint16_t)width; g->height = (uint16_t)height;
    g->hback = hb + s->dx; g->vback = vb + s->dy;
    s->L = L; s->lines = lines; s->vpl = vpl;
    s->cpp = cpp; s->hsync = hsync;
    s->hb0 = hb; s->vb0 = vb;
}

void centrev1_calibrated(centrev1_t *s, const vgacal_t *cal, const vgafc_t *fc) {
    if (cal->state != VGACAL_DONE || s->calibrated) {
        fprintf(stderr, "centrev1_calibrated: calibration not finished, or called twice\n");
        abort();
    }
    s->calibrated = 1;
    s->fc = fc;
    decide(s, cal);
    if (!s->geo.ok) return;
    const vgasample_geometry_t *g = &s->geo;
    s->frame = xcalloc((size_t)g->width * g->height, 1);
    s->map = xcalloc((size_t)(s->L * s->lines), 1);
    s->xs = xcalloc(g->width, sizeof *s->xs);
    for (uint32_t p = 0; p < g->width; p++) {
        // x = floor((hsync + hback' + p + 0.5) * cpp), evaluated as written.
        double x = floor((s->hsync + (double)g->hback + (double)p + 0.5) * s->cpp);
        s->xs[p] = x >= 0.0 && x < (double)s->L ? (int64_t)x : -1;
    }
}

void centrev1_push(centrev1_t *s, uint8_t uo_out, uint64_t first, uint64_t clocks) {
    if (!clocks) return;
    if (first != s->next) {
        fprintf(stderr, "centrev1_push: clock %llu, expected %llu\n",
                (unsigned long long)first, (unsigned long long)s->next);
        abort();
    }
    s->next = first + clocks;
    const int was_in_frame = s->vf.in_frame;
    vgaframe_push64(&s->vf, uo_out, clocks);
    const int r = s->vf.last_report;
    if (!r) return;
    const vgaframe_timing_learner_t *l = &s->vf.learner;
    if (r == 2) {                                   // row 0
        s->have_start = first >= l->report_x;
        s->start = first - (s->have_start ? l->report_x : 0);
        s->v_found = 0;
    } else if (r == 3 && !was_in_frame) {           // row vsync_lines of a retroactive frame
        uint64_t back = (uint64_t)l->report_x + (uint64_t)l->t.vsync_lines * l->t.clocks_per_line;
        s->have_start = first >= back;
        s->start = first - (s->have_start ? back : 0);
        s->v_found = 1; s->v_clock = first;
    } else if (s->vf.in_frame && !s->v_found && l->prev_v != l->t.vsync_positive) {
        s->v_found = 1; s->v_clock = first;         // the first row out of the vsync pulse
    }
}

// The latest frame-clock end in [lo, hi], as its index; -1 if none.
static int64_t latest_end(const vgafc_t *fc, uint64_t lo, uint64_t hi) {
    size_t a = 0, b = fc->n;                        // first end > hi
    while (a < b) {
        size_t mid = a + (b - a) / 2;
        if (fc->ends[mid] <= hi) a = mid + 1; else b = mid;
    }
    if (a == 0 || fc->ends[a - 1] < lo) return -1;
    return (int64_t)(a - 1);
}

static void on_raw(void *user, const vgaframe_raw_t *r) {
    centrev1_t *s = user;
    if (!s->calibrated || !s->geo.ok || !s->have_start || !s->v_found) return;
    const int64_t k = latest_end(s->fc, s->start, s->v_clock);
    if (k < 0) return;                              // a gap
    const vgasample_geometry_t *g = &s->geo;
    const uint64_t rows = r->lines < s->lines ? r->lines : s->lines;
    const size_t stride = r->stride;

    // The pick.
    uint32_t missing = 0;
    for (uint32_t y = 0; y < g->height; y++) {
        const int64_t row = (int64_t)s->vpl + g->vback + (int64_t)y;
        uint8_t *out = s->frame + (size_t)y * g->width;
        if (row < 0 || (uint64_t)row >= rows) {
            memset(out, 0, g->width);
            missing += g->width;
            continue;
        }
        const uint8_t *src = r->raw + (size_t)row * stride;
        for (uint32_t p = 0; p < g->width; p++) {
            const int64_t x = s->xs[p];
            const uint8_t b = x >= 0 ? src[x] : 0;
            if (b & 0x80) out[p] = (uint8_t)(b & 0x3F);
            else { out[p] = 0; missing++; }
        }
    }

    // The activity map: every lit clock of the line and frame.
    for (uint64_t row = 0; row < rows; row++) {
        const uint8_t *src = r->raw + (size_t)row * stride;
        uint8_t *m = s->map + (size_t)(row * s->L);
        for (uint64_t x = 0; x < s->L; x++)
            if ((src[x] & 0x80) && (src[x] & 0x3F)) m[x] = 1;
    }

    // Colour changes inside the window, and those off the pixel grid.
    const int whole = s->cpp == floor(s->cpp);
    const int64_t icpp = (int64_t)s->cpp;
    const int64_t xlo = window_clock(s, g->hback, 0), xhi = window_clock(s, g->hback, g->width);
    const int64_t c0 = xlo > 1 ? xlo : 1, c1 = xhi < (int64_t)s->L ? xhi : (int64_t)s->L;
    for (uint32_t y = 0; y < g->height; y++) {
        const int64_t row = (int64_t)s->vpl + g->vback + (int64_t)y;
        if (row < 0 || (uint64_t)row >= rows) continue;
        const uint8_t *src = r->raw + (size_t)row * stride;
        for (int64_t x = c0; x < c1; x++) {
            const uint8_t a = src[x - 1], b = src[x];
            if (!(a & 0x80) || !(b & 0x80) || ((a ^ b) & 0x3F) == 0) continue;
            s->colour_changes++;
            if (whole && (x - xlo) % icpp != 0) s->off_grid++;
        }
    }

    if (s->raw_fn) s->raw_fn(s->raw_user, r);
    vgasample_frame_t f = { (uint64_t)k, s->start, s->frame, g->width, g->height, missing };
    if (s->fn) s->fn(s->user, &f);
}

// Lit positions in rows [r0, r1) and clocks [c0, c1) of the map, from its
// prefix sums P ((L + 1) * (lines + 1)), clipped to the map.
static uint64_t lit_in(const centrev1_t *s, const uint32_t *P, int64_t r0, int64_t r1, int64_t c0, int64_t c1) {
    const int64_t W = (int64_t)s->L, H = (int64_t)s->lines;
    if (r0 < 0) r0 = 0;
    if (c0 < 0) c0 = 0;
    if (r1 > H) r1 = H;
    if (c1 > W) c1 = W;
    if (r0 >= r1 || c0 >= c1) return 0;
    const size_t w = (size_t)W + 1;
    return (uint64_t)P[(size_t)r1 * w + (size_t)c1] - P[(size_t)r0 * w + (size_t)c1]
         - P[(size_t)r1 * w + (size_t)c0] + P[(size_t)r0 * w + (size_t)c0];
}

void centrev1_finish(centrev1_t *s, centrev1_diag_t *d) {
    memset(d, 0, sizeof *d);
    d->dx = s->dx; d->dy = s->dy;
    const vgasample_geometry_t *g = &s->geo;
    const int whole = g->cpp == floor(g->cpp);
    d->off_grid = whole ? 0 : UINT64_MAX;
    if (!s->calibrated || !g->ok) return;
    d->colour_changes = s->colour_changes;
    if (whole) d->off_grid = s->off_grid;

    const size_t W = (size_t)s->L, H = (size_t)s->lines, w = W + 1;
    uint32_t *P = xcalloc(w * (H + 1), sizeof *P);
    for (size_t y = 0; y < H; y++) {
        uint32_t run = 0;
        for (size_t x = 0; x < W; x++) {
            run += s->map[y * W + x];
            P[(y + 1) * w + x + 1] = P[y * w + x + 1] + run;
        }
    }
    const uint64_t total = P[H * w + W];
    const int64_t h = g->height, vpl = (int64_t)s->vpl;
    {
        const int64_t r0 = vpl + g->vback;
        d->outside = total - lit_in(s, P, r0, r0 + h, window_clock(s, g->hback, 0),
                                    window_clock(s, g->hback, g->width));
    }
    // The offset from the window without the override that keeps it inside
    // the line and the frame and out of the sync pulses.
    int found = 0;
    uint64_t best = 0;
    for (int64_t dy = -(int64_t)s->vb0; vpl + s->vb0 + dy + h <= (int64_t)H; dy++) {
        const int64_t r0 = vpl + s->vb0 + dy;
        for (int64_t dx = -(int64_t)s->hb0; ; dx++) {
            const int hb = (int)(s->hb0 + dx);
            const int64_t c0 = window_clock(s, hb, 0), c1 = window_clock(s, hb, g->width);
            if (c1 > (int64_t)W) break;
            const uint64_t out = total - lit_in(s, P, r0, r0 + h, c0, c1);
            const int64_t l1 = llabs(dx) + llabs(dy), bl1 = (int64_t)abs(d->suggest_dx) + abs(d->suggest_dy);
            if (!found || out < best ||
                (out == best && (l1 < bl1 || (l1 == bl1 && (dy < d->suggest_dy ||
                                                             (dy == d->suggest_dy && dx < d->suggest_dx)))))) {
                found = 1; best = out;
                d->suggest_dx = (int)dx; d->suggest_dy = (int)dy;
            }
        }
    }
    if (!found) d->suggest_dx = d->suggest_dy = 0;
    free(P);
}

void centrev1_free(centrev1_t *s) {
    if (!s) return;
    free(s->raw); free(s->rgb); free(s->cover);
    free(s->frame); free(s->map); free(s->xs);
    free(s);
}
