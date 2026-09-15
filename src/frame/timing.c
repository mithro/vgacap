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
            }
        }
        if (v) l->v_high_lines = 0; else l->v_low_lines = 0;
    }
    if (v) l->v_high_lines++; else l->v_low_lines++;
    l->prev_v = v;
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
        uint32_t completed = l->prev_h ? l->h_high : l->h_low;
        uint32_t other      = l->prev_h ? l->h_low : l->h_high;
        if (other > 0) {
            uint8_t  pulse_level;
            uint32_t pulse_width;
            if (completed < other) { pulse_level = l->prev_h; pulse_width = completed; }
            else                   { pulse_level = h;         pulse_width = other; }
            l->t.hsync_positive = pulse_level;
            l->t.hsync_width = pulse_width;
            if (h == pulse_level) {
                l->t.clocks_per_line = l->clk_in_line;
                l->clk_in_line = 0;
                l->line_in_frame++;
                ret = 1;
                vsync_sample(l, v, &ret);
            }
        }
        if (h) l->h_high = 0; else l->h_low = 0;
        l->prev_h = h;
    }
    if (h) l->h_high += run; else l->h_low += run;
    l->clk_in_line += run;
    return ret;
}
