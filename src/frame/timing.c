// SPDX-License-Identifier: Apache-2.0
// Learns hsync/vsync polarity and timing from a live sync stream.
//
// Both hsync and vsync alternate between two phases (pulse and non-pulse).
// Once two consecutive phases of a signal are known, the shorter one is the
// pulse (VESA sync pulses are always shorter than the rest of the line/frame
// period), and the transition into the pulse level is the line/frame start.
#include "vgacap/frame.h"
#include <string.h>

void vgaframe_timing_init(vgaframe_timing_learner_t *l) { memset(l, 0, sizeof *l); }

// Called once per line, right after a new line has been detected (h entering
// its pulse). Samples vsync at this instant; may additionally report a frame
// boundary (upgrading *ret from 1 to 2) when vsync enters its pulse.
static void vsync_sample(vgaframe_timing_learner_t *l, uint8_t v, int *ret) {
    if (v != l->prev_v) {
        uint32_t completed = l->prev_v ? l->v_high_lines : l->v_low_lines;
        uint32_t other      = l->prev_v ? l->v_low_lines : l->v_high_lines;
        if (other > 0) {
            uint8_t  pulse_level;
            uint32_t pulse_len;
            if (completed < other) { pulse_level = l->prev_v; pulse_len = completed; }
            else                   { pulse_level = v;         pulse_len = other; }
            l->t.vsync_positive = pulse_level;
            l->t.vsync_lines = pulse_len;
            int first = !l->v_started;
            l->v_started = 1;
            if (v == pulse_level) {
                l->t.lines_per_frame = l->line_in_frame;
                l->t.locked = (uint8_t)(l->last_lpf != 0 &&
                                        l->t.lines_per_frame == l->last_lpf &&
                                        l->t.clocks_per_line == l->last_cpl);
                l->last_lpf = l->t.lines_per_frame;
                l->last_cpl = l->t.clocks_per_line;
                l->t.mode = vgaframe_mode_match(l->t.clocks_per_line, l->t.lines_per_frame);
                l->line_in_frame = 0;
                *ret = 2;
            } else {
                // Leaving the pulse. line_in_frame should equal "lines
                // since the pulse's leading edge", i.e. pulse_len: true
                // whenever the entry itself was recognised (the normal
                // case - this is then a no-op, since line_in_frame is
                // already pulse_len). But if a stream begins mid-frame,
                // well before its first vsync pulse, that pulse's *entry*
                // is unrecoverably missed here (this comparison needs
                // both phases measured, and the pulse phase has never
                // been seen before it begins) - line_in_frame has been
                // counting since stream start instead of since the
                // pulse's entry. Correct it now so the *next* entry
                // measures the true frame length instead of "lines since
                // the process started".
                l->line_in_frame = pulse_len;
                // This is also the first moment at which that missed entry
                // can be placed: it was pulse_len lines ago. Report it, so
                // the frame it began is claimed retroactively rather than
                // thrown away - it is a whole frame per capture.
                if (first) *ret = 3;
            }
        }
        if (v) l->v_high_lines = 0; else l->v_low_lines = 0;
    }
    if (v) l->v_high_lines++; else l->v_low_lines++;
    l->prev_v = v;
}

// Consecutive rejected pulses after which the learner stops believing its own
// measurement and starts over. Real glitch bursts chop up a line or two at a
// time (five rejections in a row is the worst the tt08 capture shows), so this
// is far above anything the filter should reject legitimately, and it still
// recovers within a handful of lines.
#define GIVE_UP_AFTER 16u

// Is the glitch filter armed? Only once a full measurement exists to judge a
// candidate pulse against; before that every pulse is taken at face value,
// which is what lets the learner bootstrap at all.
static int filter_armed(const vgaframe_timing_learner_t *l) {
    return l->t.hsync_width > 0 && l->t.clocks_per_line > 0;
}

// A candidate line start, judged at the trailing edge of its pulse: the pulse
// must be within 25% of the learned width, and its leading edge at least half
// a line after the last accepted one. Spurious pulses on real silicon are 2
// to 30 clocks long and can land anywhere in the line, so both tests are
// needed - a 30-clock pulse in mid-active-area passes the distance test.
static int pulse_is_a_line_start(const vgaframe_timing_learner_t *l, uint32_t width) {
    uint32_t w = l->t.hsync_width, slack = w / 4;
    if (width + slack < w || width > w + slack) return 0;
    return l->pulse_start_clk >= l->t.clocks_per_line / 2;
}

int vgaframe_timing_push(vgaframe_timing_learner_t *l, uint8_t h, uint8_t v, uint32_t run) {
    int ret = 0;
    if (!l->have_prev) {
        l->have_prev = 1;
        l->prev_h = h;
        l->prev_v = v;
        if (v) l->v_high_lines = 1; else l->v_low_lines = 1; // line 0 belongs to this phase
    }
    if (h != l->prev_h) {
        l->h_edge_count++;
        uint32_t completed = l->prev_h ? l->h_high : l->h_low;  // the phase that just ended
        uint32_t other      = l->prev_h ? l->h_low : l->h_high; // the phase before that
        uint8_t  pulse_level = 0;
        int      know_pulse = 0, ignored = 0;
        if (filter_armed(l)) {
            // Polarity is settled; re-deriving it from a glitch's own
            // (short) phases is exactly what must not happen here.
            pulse_level = l->t.hsync_positive;
            know_pulse = 1;
        } else if (other > 0) {
            uint32_t pulse_width;
            if (completed < other) { pulse_level = l->prev_h; pulse_width = completed; }
            else                   { pulse_level = h;         pulse_width = other; }
            l->t.hsync_positive = pulse_level;
            l->t.hsync_width = pulse_width;
            know_pulse = 1;
        }
        if (know_pulse && h == pulse_level) {
            // Leading edge: remember where it fell, and let the pulse's own
            // width accumulate from zero (below) so the trailing edge can
            // measure it.
            l->pulse_open = 1;
            l->pulse_start_clk = l->clk_in_line;
        } else if (know_pulse && l->pulse_open) {
            // Trailing edge of a pulse whose leading edge was seen, so
            // `completed` is its full width and the line it would start
            // began `completed` clocks ago.
            l->pulse_open = 0;
            if (l->ignored_run >= GIVE_UP_AFTER) {
                // Nothing has looked like a line for many lines, so it is
                // the reference that must be wrong, not the signal: a
                // stream that starts inside an hsync pulse and meets a
                // glitch before its first clean line learns the glitch's
                // width as the pulse width, and would reject every real
                // pulse from then on. Throw the measurement away and
                // bootstrap the hsync side exactly as at stream start,
                // rather than trust it and lock onto a wrong line length.
                l->t.hsync_width = 0;
                l->t.clocks_per_line = 0;
                l->clk_in_line = 0;
                l->h_high = l->h_low = 0;
                l->pulse_open = 0;
                l->ignored_run = 0;
                l->t.glitches++;
            } else if (!filter_armed(l) || pulse_is_a_line_start(l, completed)) {
                l->t.hsync_positive = pulse_level;
                l->t.hsync_width = completed;
                if (l->pulse_start_clk) l->t.clocks_per_line = l->pulse_start_clk;
                l->clk_in_line = completed;
                l->report_x = completed;
                l->ignored_run = 0;
                l->line_in_frame++;
                ret = 1;
                vsync_sample(l, v, &ret);
            } else {
                l->t.glitches++;
                l->ignored_run++;
                ignored = 1;
            }
        }
        if (ignored) {
            // The pulse never happened: give its clocks back to the phase it
            // interrupted, which is the one being resumed now, so the next
            // real pulse still measures a whole line's worth of blanking.
            // clk_in_line is untouched throughout, so the pixels around the
            // glitch keep their true positions in the line.
            if (h) l->h_high += completed; else l->h_low += completed;
        } else {
            if (h) l->h_high = 0; else l->h_low = 0;
        }
        l->prev_h = h;
    }
    if (h) l->h_high += run; else l->h_low += run;
    l->clk_in_line += run;
    return ret;
}
