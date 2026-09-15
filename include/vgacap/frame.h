// SPDX-License-Identifier: Apache-2.0
// vgacap frame reconstruction: mode table, sync timing learner, and
// continuous/FRAM frame accumulation into RGB24 pictures.
#ifndef VGACAP_FRAME_H
#define VGACAP_FRAME_H

#include <stddef.h>
#include <stdint.h>

#include "vgacap/stream.h"

#ifdef __cplusplus
extern "C" {
#endif

typedef struct vgaframe_mode {
    const char *name;
    uint16_t h_active, h_front, h_sync, h_back;   // clocks
    uint16_t v_active, v_front, v_sync, v_back;   // lines
    uint8_t  h_sync_positive, v_sync_positive;    // 1 = pulse is high
} vgaframe_mode_t;

// clocks per line = h_active+h_front+h_sync+h_back; lines per frame likewise.
const vgaframe_mode_t *vgaframe_modes(size_t *count);
// exact match on clocks_per_line and lines_per_frame; NULL if none
const vgaframe_mode_t *vgaframe_mode_match(uint32_t clocks_per_line, uint32_t lines_per_frame);
// match on clocks_per_line alone; NULL if none. clocks_per_line is unique
// across the built-in table, so this is unambiguous; used to recognise a
// mode from a single FRAM window, which knows its clocks_per_line but not
// (by itself) the frame's line count.
const vgaframe_mode_t *vgaframe_mode_match_cpl(uint32_t clocks_per_line);

typedef struct vgaframe_timing {
    uint32_t clocks_per_line, lines_per_frame;
    uint32_t hsync_width;        // clocks
    uint32_t vsync_lines;        // lines
    uint8_t  hsync_positive, vsync_positive;
    uint8_t  locked;             // 1 once two consecutive frames agree
    const vgaframe_mode_t *mode; // matched entry or NULL
} vgaframe_timing_t;

// Internal learner used by vgaframe; exposed for tests.
typedef struct vgaframe_timing_learner {
    vgaframe_timing_t t;
    uint8_t  prev_h, prev_v, have_prev;
    uint32_t clk_in_line;        // clocks since the last hsync leading edge (rising or falling, whichever came first)
    uint32_t h_high, h_low;      // duration of the current/previous hsync phases
    uint32_t line_in_frame;
    uint32_t v_high_lines, v_low_lines;
    uint32_t last_cpl, last_lpf; // previous measurements for the lock check
    uint32_t h_edge_count;
} vgaframe_timing_learner_t;

void vgaframe_timing_init(vgaframe_timing_learner_t *l);
// feed one sample's sync levels for `run` clocks; returns 1 when a new line started, 2 when a new frame started, else 0
int  vgaframe_timing_push(vgaframe_timing_learner_t *l, uint8_t hsync, uint8_t vsync, uint32_t run);

// ---- frame reconstruction ------------------------------------------------

typedef struct vgaframe_output {
    const uint8_t *rgb24; uint16_t width, height;
    const vgaframe_timing_t *timing;
    uint16_t active_x0, active_y0;   // where the crop came from
    uint8_t  partial;                // 1 if not every line was covered
    uint32_t frame_counter;          // FRAM counter, or a running count for continuous streams
} vgaframe_output_t;

typedef void (*vgaframe_frame_fn)(void *user, const vgaframe_output_t *out);

typedef struct vgaframe_config {
    uint16_t max_clocks_per_line;    // raw buffer width  (e.g. 1400)
    uint16_t max_lines;              // raw buffer height (e.g. 900)
    uint8_t  signal_map[VGACAP_SIG_COUNT];
    const vgaframe_mode_t *force_mode;  // NULL = detect
    vgaframe_frame_fn on_frame; void *user;
} vgaframe_config_t;

// raw:  max_clocks_per_line * max_lines bytes (6-bit colour + 0x80 "written" bit per pixel)
// rgb:  max_clocks_per_line * max_lines * 3 bytes
// cover: max_lines bytes
size_t vgaframe_raw_size(const vgaframe_config_t *c);
size_t vgaframe_rgb_size(const vgaframe_config_t *c);
typedef struct vgaframe {
    vgaframe_config_t cfg; vgaframe_timing_learner_t learner;
    uint8_t *raw, *rgb, *cover;
    uint32_t x, y; int in_frame; uint32_t frames_seen;
    // FRAM mode state
    int fram_mode; uint32_t fram_counter; uint16_t fram_first_line, fram_line_count; uint32_t fram_cpl; uint32_t fram_remaining;
    uint32_t fram_max_line;
    // Timing actually used for the most recently emitted frame: the
    // learner's own measurement, unless emit() resolved a different mode
    // (force_mode, or a FRAM clocks-per-line table match) - see emit() in
    // frame.c. vgaframe_output_t.timing always points here.
    vgaframe_timing_t out_timing;
} vgaframe_t;

int  vgaframe_init(vgaframe_t *f, const vgaframe_config_t *cfg, uint8_t *raw, uint8_t *rgb, uint8_t *cover);
void vgaframe_push(vgaframe_t *f, uint32_t sample, uint32_t run);
void vgaframe_flush(vgaframe_t *f);   // emit whatever is buffered as partial (end of stream)
// helper: 6-bit colour (rr gg bb) from a sample via the signal map
uint8_t vgaframe_colour(const uint8_t *signal_map, uint32_t sample);

// Enter FRAM (partial-frame window) accumulation mode: see frame.c for the
// coverage/reassembly rules.
void vgaframe_frame_begin(vgaframe_t *f, uint32_t frame_counter, uint16_t first_line,
                          uint16_t line_count, uint32_t clocks_per_line, uint32_t sample_count);

#ifdef __cplusplus
}
#endif
#endif
