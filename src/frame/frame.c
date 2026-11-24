// SPDX-License-Identifier: Apache-2.0
// Reconstructs RGB24 frames from a continuous stream of (sample, run) pairs,
// and from FRAM partial-frame windows (see vgaframe_frame_begin).
#include "vgacap/frame.h"
#include <string.h>
#include "internal.h"

size_t vgaframe_raw_size(const vgaframe_config_t *c) { return (size_t)c->max_clocks_per_line * c->max_lines; }
size_t vgaframe_rgb_size(const vgaframe_config_t *c) { return vgaframe_raw_size(c) * 3; }

static uint8_t bit(const uint8_t *map, uint32_t s, int sig) {
    uint8_t b = map[sig];
    if (b == VGACAP_SIG_ABSENT) return 0;
    return (uint8_t)((s >> b) & 1);
}

uint8_t vgaframe_colour(const uint8_t *m, uint32_t s) {
    return (uint8_t)((bit(m, s, VGACAP_SIG_R1) << 5) | (bit(m, s, VGACAP_SIG_R0) << 4) |
                     (bit(m, s, VGACAP_SIG_G1) << 3) | (bit(m, s, VGACAP_SIG_G0) << 2) |
                     (bit(m, s, VGACAP_SIG_B1) << 1) | bit(m, s, VGACAP_SIG_B0));
}

void vgaframe_reset(vgaframe_t *f) {
    vgaframe_timing_init(&f->learner);
    memset(&f->out_timing, 0, sizeof f->out_timing);
    f->x = 0; f->y = 0; f->in_frame = 0; f->frames_seen = 0;
    f->last_report = 0;
    f->fram_active = 0; f->fram_pending = 0; f->fram_counter = 0;
    f->fram_first_line = 0; f->fram_line_count = 0; f->fram_cpl = 0;
    f->fram_remaining = 0; f->fram_max_line = 0; f->fram_anchor = 0;
    f->fram_h_high = f->fram_v_high = f->fram_clocks = 0;
    memset(f->raw, 0, vgaframe_raw_size(&f->cfg));
    f->dirty_x_end = f->dirty_y_end = 0;
    memset(f->cover, 0, f->cfg.max_lines);
}

// See vgaframe_t.pixel_table: a lookup by the sample's low 8 bits, valid
// when no colour signal sits at bit 8 or above.
static void build_pixel_table(vgaframe_t *f) {
    const uint8_t *m = f->cfg.signal_map;
    f->pixel_table_ok = 0;
    for (int sig = VGACAP_SIG_R1; sig <= VGACAP_SIG_B0; sig++)
        if (m[sig] != VGACAP_SIG_ABSENT && m[sig] >= 8) return;
    for (uint32_t s = 0; s < 256; s++) f->pixel_table[s] = (uint8_t)(vgaframe_colour(m, s) | 0x80);
    f->pixel_table_ok = 1;
}

int vgaframe_init(vgaframe_t *f, const vgaframe_config_t *cfg, uint8_t *raw, uint8_t *rgb, uint8_t *cover) {
    if (!cfg->max_clocks_per_line || !cfg->max_lines || !raw || !rgb || !cover) return -1;
    memset(f, 0, sizeof *f);
    f->cfg = *cfg; f->raw = raw; f->rgb = rgb; f->cover = cover;
    build_pixel_table(f);
    vgaframe_reset(f);
    return 0;
}

// The mode to use for cropping/coverage, best information currently
// available: force_mode, else the learner's matched mode, else (FRAM mode
// only) a table match on the most recent FRAM chunk's clocks_per_line -
// windows lie inside one frame so the learner alone can never learn
// lines_per_frame from a pure FRAM stream, but each window still carries its
// own clocks_per_line, and that is unique across the built-in table.
static const vgaframe_mode_t *resolved_mode(const vgaframe_t *f) {
    if (f->cfg.force_mode) return f->cfg.force_mode;
    if (f->learner.t.mode) return f->learner.t.mode;
    if (f->fram_active || f->fram_pending) return vgaframe_mode_match_cpl(f->fram_cpl);
    return NULL;
}

// Lines expected in a full frame, best information currently available:
// resolved_mode()'s table value, else the learner's raw measurement (a
// timing that doesn't match any table entry), else (FRAM mode with no mode
// known at all) the largest window seen so far.
static uint32_t expected_lines(const vgaframe_t *f) {
    const vgaframe_mode_t *m = resolved_mode(f);
    if (m) return (uint32_t)m->v_active + m->v_front + m->v_sync + m->v_back;
    if (f->learner.t.lines_per_frame) return f->learner.t.lines_per_frame;
    return f->fram_max_line;
}

// Is there anything at all worth publishing as a partial frame?
static int covered_any(const vgaframe_t *f) {
    for (uint32_t y = 0; y < f->cfg.max_lines; y++)
        if (f->cover[y]) return 1;
    return 0;
}

// Is the accumulated FRAM picture complete? With a mode known, that means
// every line of its *active* area: those are the only lines emit() crops
// out, and a window capture has no reason to spend memory on blanking (on
// a board with ~350 KB of usable heap, 525 lines of 800 one-byte samples
// do not fit, the 481 a picture needs nearly do). Without a mode, every
// line of the frame, since nothing says which of them are picture.
static int covered(const vgaframe_t *f) {
    const vgaframe_mode_t *m = resolved_mode(f);
    uint32_t y0 = 0, n;
    if (m) {
        y0 = (uint32_t)m->v_sync + m->v_back;
        n = y0 + m->v_active;
    } else {
        n = expected_lines(f);
        if (!n) return 0;
    }
    for (uint32_t y = y0; y < n && y < f->cfg.max_lines; y++)
        if (!f->cover[y]) return 0;
    return 1;
}

// Zeroes raw's lines [0, lines), as a memset of them would leave it, but
// only inside the dirty box: everything outside it is zero already.
static void clear_raw(vgaframe_t *f, uint32_t lines) {
    uint32_t W = f->cfg.max_clocks_per_line;
    uint32_t ny = lines < f->dirty_y_end ? lines : f->dirty_y_end, nx = f->dirty_x_end;
    if (nx == W) memset(f->raw, 0, (size_t)W * ny);
    else for (uint32_t y = 0; y < ny; y++) memset(f->raw + (size_t)y * W, 0, nx);
    if (lines >= f->dirty_y_end) f->dirty_x_end = f->dirty_y_end = 0;   // raw is all zero now
}

// rgb24 for each raw byte: a written pixel's colour, two bits a channel
// scaled by 85, or magenta for a pixel never written. Four bytes an entry,
// the fourth spare, so that emit can store a pixel with one 4-byte copy.
#define RGB_OF(p) { ((p) & 0x80) ? (((p) >> 4) & 3) * 85 : 255, \
                    ((p) & 0x80) ? (((p) >> 2) & 3) * 85 : 0,   \
                    ((p) & 0x80) ? ((p) & 3) * 85 : 255, 0 }
#define RGB_OF4(p) RGB_OF(p), RGB_OF((p) + 1), RGB_OF((p) + 2), RGB_OF((p) + 3)
#define RGB_OF16(p) RGB_OF4(p), RGB_OF4((p) + 4), RGB_OF4((p) + 8), RGB_OF4((p) + 12)
#define RGB_OF64(p) RGB_OF16(p), RGB_OF16((p) + 16), RGB_OF16((p) + 32), RGB_OF16((p) + 48)
static const uint8_t rgb_of[256][4] = { RGB_OF64(0), RGB_OF64(64), RGB_OF64(128), RGB_OF64(192) };

// Emits the accumulated picture. `lines_known` bounds how much of `raw` to
// scan for the auto-crop bounding box and to clear afterwards. `is_fram`
// says whether this is the FRAM accumulation being published (which names
// the frame by its FRAM counter and may have written anywhere in the
// buffer) or a continuous-mode frame.
static void emit(vgaframe_t *f, uint32_t lines_known, uint8_t partial_hint, int is_fram) {
    const vgaframe_mode_t *m = resolved_mode(f);
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
    // A force_mode (or a table/fram_cpl match) whose active area starts at or
    // past the buffer edge has nothing to crop: bail rather than let the
    // W - x0 / max_lines - y0 clamps below underflow.
    if (x0 >= W || y0 >= f->cfg.max_lines) return;
    if (x0 + w > W) w = W - x0;
    if (y0 + h > f->cfg.max_lines) h = f->cfg.max_lines - y0;
    uint8_t partial = partial_hint;
    if (w && h) {
        // Each pixel is one 4-byte copy from rgb_of, whose spare fourth byte
        // the next pixel overwrites; the very last pixel is copied as 3
        // bytes so nothing past the picture is touched. `written` keeps
        // 0x80 only while every pixel had it.
        uint8_t *o = f->rgb, written = 0x80;
        for (uint32_t y = 0; y < h; y++) {
            const uint8_t *r = f->raw + (y0 + y) * W + x0;
            uint32_t n = y + 1 < h ? w : w - 1;
            for (uint32_t x = 0; x < n; x++, o += 3) { written &= r[x]; memcpy(o, rgb_of[r[x]], 4); }
        }
        uint8_t p = f->raw[(y0 + h - 1) * W + x0 + w - 1];
        written &= p;
        memcpy(o, rgb_of[p], 3);
        if (!(written & 0x80)) partial = 1;
    }
    // Report the timing actually used for this crop: the learner's own
    // measurement, unless resolved_mode() picked a different mode (a
    // force_mode, or - in FRAM mode - a clocks-per-line table match the
    // free-running learner alone could never derive). locked is left as the
    // learner reported it either way.
    f->out_timing = f->learner.t;
    if (m && m != f->learner.t.mode) {
        f->out_timing.mode = m;
        f->out_timing.clocks_per_line = (uint32_t)m->h_active + m->h_front + m->h_sync + m->h_back;
        f->out_timing.lines_per_frame = (uint32_t)m->v_active + m->v_front + m->v_sync + m->v_back;
        f->out_timing.hsync_positive = m->h_sync_positive;
        f->out_timing.vsync_positive = m->v_sync_positive;
        f->out_timing.hsync_width = m->h_sync;
        f->out_timing.vsync_lines = m->v_sync;
    }
    if (is_fram && f->fram_clocks) {
        // A FRAM picture's windows never span a whole frame, so the learner
        // cannot measure polarity and the table's would be reported -- wrong
        // for a design that inverts a mode's usual sense (tt_um_vgacal_modes
        // ui_in[3:2] on fpga-4). A sync pulse is active for a small part of
        // its period, so whichever level held for most of the picture's
        // clocks is the idle one.
        f->out_timing.hsync_positive = (uint8_t)(2 * f->fram_h_high < f->fram_clocks);
        f->out_timing.vsync_positive = (uint8_t)(2 * f->fram_v_high < f->fram_clocks);
    }
    vgaframe_output_t out;
    out.rgb24 = f->rgb; out.width = (uint16_t)w; out.height = (uint16_t)h; out.timing = &f->out_timing;
    out.stride = w * 3;   // rows are packed; emit() writes them that way above
    out.active_x0 = (uint16_t)x0; out.active_y0 = (uint16_t)y0; out.partial = partial;
    out.frame_counter = is_fram ? f->fram_counter : f->frames_seen;
    f->frames_seen++;
    if (is_fram) { f->fram_pending = 0; f->fram_h_high = f->fram_v_high = f->fram_clocks = 0; }
    if (f->cfg.on_frame) f->cfg.on_frame(f->cfg.user, &out);
    // In FRAM mode a frame's windows can land anywhere in [0, max_lines), not
    // just below lines_known (which is only this emit's notion of the frame
    // height), so clear the whole buffer rather than risk leaving stale
    // "written" pixels from a differently-shaped frame for the next one.
    uint32_t clear_lines = is_fram ? f->cfg.max_lines :
                           (lines_known < f->cfg.max_lines ? lines_known + 1 : f->cfg.max_lines);
    clear_raw(f, clear_lines);
    memset(f->cover, 0, f->cfg.max_lines);
}

// A continuous-mode frame has started, so any FRAM accumulation still
// waiting for the window that would complete it is never going to get it:
// publish what was covered and free the buffer for the new frame.
static void flush_pending_fram(vgaframe_t *f) {
    if (f->fram_pending && covered_any(f)) emit(f, expected_lines(f), 1, 1);
    f->fram_pending = 0;
}

// Stores `run` clocks of `sample` at (x, y), clipped to the buffer, and
// moves x on past them.
static inline void store_run(vgaframe_t *f, uint32_t sample, uint32_t run) {
    uint32_t W = f->cfg.max_clocks_per_line;
    if (f->y < f->cfg.max_lines && f->x < W) {
        // f->x < W is guaranteed above, so W - f->x cannot underflow; compare
        // against it directly instead of f->x + run, which can itself wrap
        // for a large run and defeat the clip entirely.
        uint32_t n = run > (W - f->x) ? (W - f->x) : run;
        uint8_t px = f->pixel_table_ok ? f->pixel_table[sample & 0xFF]
                                       : (uint8_t)(vgaframe_colour(f->cfg.signal_map, sample) | 0x80);
        uint8_t *d = f->raw + f->y * W + f->x;
        if (n == 1) *d = px;   // the commonest run on a busy picture: no call
        else memset(d, px, n);
        f->cover[f->y] = 1;
        if (f->x + n > f->dirty_x_end) f->dirty_x_end = f->x + n;
        if (f->y >= f->dirty_y_end) f->dirty_y_end = f->y + 1;
    }
    f->x = vgaframe_sat_add32(f->x, run);
}

// The rest of a push, after the learner has seen it and reported r: kept
// out of vgaframe_push so that the common case there stays small.
static void push_rest(vgaframe_t *f, uint32_t sample, uint32_t run, uint8_t h, int r) {
    // vgaframe_push's fast path handles a no-edge push (r == 0) outside FRAM
    // mode without coming here, so r == 0 logic added here would be bypassed.
    uint8_t v = bit(f->cfg.signal_map, sample, VGACAP_SIG_VSYNC);
    // x is the clock offset within the line, which the learner tracks as
    // clk_in_line: a line start is reported at the *trailing* edge of its
    // hsync pulse (that is when the glitch filter can judge the pulse), so
    // clk_in_line is the pulse's width there, not zero, and copying it keeps
    // every pixel at its true position in the line. It also keeps the
    // ignored clocks of a glitch in place instead of restarting the line.
    if (f->fram_active) {
        if (r == 1 || r == 3) { f->x = f->learner.report_x; f->y++; }
        // r == 2 (learner-detected frame boundary) is ignored, and r == 3 is
        // taken as a plain line start: inside a FRAM chunk the window
        // metadata (vgaframe_frame_begin), not the free-running learner,
        // defines line/frame layout.
    } else if (r == 3) {
        // The learner has just placed the frame start it could not recognise
        // as it happened: it was vsync_lines lines ago, so this line is line
        // vsync_lines of a frame that is already under way. Those lines hold
        // only the vsync pulse and were never stored, which costs nothing -
        // no real mode puts active pixels inside its own vsync.
        f->x = f->learner.report_x;
        if (f->in_frame) f->y++;
        else { flush_pending_fram(f); f->in_frame = 1; f->y = f->learner.t.vsync_lines; }
    } else if (r == 2) {
        flush_pending_fram(f);
        // Trust an exact (clocks_per_line, lines_per_frame) match against
        // the built-in table on the very first fully measured frame, not
        // only a `locked` (two independently agreeing measurements) one:
        // a random signal matching both dimensions of a real VESA mode is
        // strong enough evidence on its own. `locked` remains the stronger
        // signal and is unaffected by this - see vgaframe_timing_t.
        if (f->in_frame && (f->learner.t.locked || f->learner.t.mode || f->cfg.force_mode)) emit(f, f->y + 1, 0, 0);
        f->y = 0; f->x = f->learner.report_x; f->in_frame = 1;
    } else if (r == 1) {
        f->x = f->learner.report_x;
        if (f->in_frame) f->y++;
    }
    if (f->fram_active && f->fram_anchor) {
        // A window starts at an hsync leading edge by contract, but a
        // hardware trigger sees that edge through an input synchroniser and
        // may start a sample or two into the pulse (the RP2350 at 5 system
        // clocks a pixel does). The learner cannot re-place x at this
        // pulse's trailing edge, because it never saw the leading one, so
        // without this the window's first line would keep the start error.
        // The pulse's trailing edge is at x = hsync width whatever the
        // start, so put x there.
        //
        // The pulse's level is taken from the window's own first sample,
        // not from the mode table: by contract that sample is inside the
        // pulse, and a design may invert a mode's usual polarity
        // (tt_um_vgacal_modes with ui_in[2] set did, on fpga-4, and the
        // table's level made this give up and leave the line a pixel off).
        // A first change later than the pulse width allows means the window
        // did not start in a pulse after all, and nothing is moved.
        const vgaframe_mode_t *m = resolved_mode(f);
        uint32_t width = m ? m->h_sync : f->learner.t.hsync_width;
        if (f->fram_anchor == 1) {
            f->fram_anchor_level = h;
            f->fram_anchor = 2;
        } else if (h != f->fram_anchor_level) {
            if (width && f->x <= width + width / 4) f->x = width;
            f->fram_anchor = 0;
        }
    }
    if (!f->in_frame && !f->fram_active) return;
    store_run(f, sample, run);
    if (f->fram_active) {
        f->fram_clocks += run;
        if (h) f->fram_h_high += run;
        if (v) f->fram_v_high += run;
        uint32_t dec = run < f->fram_remaining ? run : f->fram_remaining;
        f->fram_remaining -= dec;
        if (f->fram_remaining == 0) {
            // This chunk's samples are done. Leave FRAM mode: anything that
            // follows without a new vgaframe_frame_begin is continuous-mode
            // data again. The accumulation stays pending unless it is now
            // complete.
            f->fram_active = 0;
            if (covered(f)) emit(f, expected_lines(f), 0, 1);
        }
    }
}

void vgaframe_push(vgaframe_t *f, uint32_t sample, uint32_t run) {
    uint8_t h = bit(f->cfg.signal_map, sample, VGACAP_SIG_HSYNC);
    if (vgaframe_timing_push_no_edge(&f->learner, h, run)) {
        // No hsync edge, so no report: x and y stay where they are, and in
        // continuous mode all that is left is to store the run.
        f->last_report = 0;
        if (!f->fram_active) {
            if (f->in_frame) store_run(f, sample, run);
            return;
        }
        push_rest(f, sample, run, h, 0);
        return;
    }
    int r = vgaframe_timing_push(&f->learner, h, bit(f->cfg.signal_map, sample, VGACAP_SIG_VSYNC), run);
    f->last_report = r;
    push_rest(f, sample, run, h, r);
}

void vgaframe_flush(vgaframe_t *f) {
    // A pending FRAM accumulation and an in-progress continuous frame are
    // mutually exclusive (vgaframe_frame_begin clears in_frame, and a
    // continuous frame start flushes the pending accumulation), so at most
    // one of these can fire.
    if (f->fram_pending) emit(f, expected_lines(f), 1, 1);
    else if (f->in_frame) emit(f, f->y + 1, 1, 0);
    f->in_frame = 0;
}

void vgaframe_frame_begin(vgaframe_t *f, uint32_t frame_counter, uint16_t first_line,
                          uint16_t line_count, uint32_t clocks_per_line, uint32_t sample_count) {
    if (f->fram_pending && frame_counter != f->fram_counter && covered_any(f))
        emit(f, expected_lines(f), 1, 1);
    if (frame_counter != f->fram_counter) f->fram_h_high = f->fram_v_high = f->fram_clocks = 0;
    f->fram_active = 1;
    f->fram_pending = 1;
    // A window interrupts whatever continuous-mode frame was in progress;
    // it does not continue it. Reconstruction resumes at the next
    // learner-detected frame start.
    f->in_frame = 0;
    f->fram_counter = frame_counter;
    f->fram_first_line = first_line;
    f->fram_line_count = line_count;
    f->fram_cpl = clocks_per_line;
    f->fram_remaining = sample_count;
    f->y = first_line;
    f->x = 0;
    f->fram_anchor = 1;
    if ((uint32_t)first_line + line_count > f->fram_max_line) f->fram_max_line = (uint32_t)first_line + line_count;
    // The window starts at an hsync leading edge, but is not contiguous with
    // whatever the learner last saw (a different, possibly distant, window).
    // Forget the in-progress hsync phase so the discontinuity is not
    // mistaken for a real edge; vgaframe_timing_push will treat the first
    // sample of this window as a fresh start, exactly like stream start.
    f->learner.clk_in_line = 0;
    f->learner.have_prev = 0;
    f->learner.pulse_open = 0;
    f->learner.pulse_start_clk = 0;
    f->learner.report_x = 0;
    f->learner.h_high = 0;
    f->learner.h_low = 0;
    f->learner.v_high_lines = 0;
    f->learner.v_low_lines = 0;
}

void vgaframe_push64(vgaframe_t *f, uint32_t sample, uint64_t run) {
    uint32_t first = run > UINT32_MAX ? UINT32_MAX : (uint32_t)run;
    vgaframe_push(f, sample, first);
    int report = f->last_report;
    for (run -= first; run; run -= first) {
        first = run > UINT32_MAX ? UINT32_MAX : (uint32_t)run;
        vgaframe_push(f, sample, first);
    }
    f->last_report = report;
}

void vgaframe_set_rgb6_buffer(vgaframe_t *f, uint8_t *rgb6) { (void)f; (void)rgb6; }   // stub (RED)
