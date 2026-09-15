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
    // Spurious hsync pulses ignored so far (see the glitch filter below).
    // Real silicon emits them: a few 2 to 30 clock pulses per capture is
    // normal, and each one used to start a bogus line.
    uint32_t glitches;
    const vgaframe_mode_t *mode; // matched entry or NULL
} vgaframe_timing_t;

// Internal learner used by vgaframe; exposed for tests.
typedef struct vgaframe_timing_learner {
    vgaframe_timing_t t;
    uint8_t  prev_h, prev_v, have_prev;
    uint32_t clk_in_line;        // clocks since the accepted line start (the leading edge of the last accepted hsync pulse)
    uint32_t h_high, h_low;      // duration of the current/previous hsync phases
    // A line start is only reported at the *trailing* edge of its hsync
    // pulse, when the pulse's width is finally known and the glitch filter
    // can judge it. pulse_open marks that a leading edge into the pulse
    // level has been seen and pulse_start_clk holds the clk_in_line it
    // happened at, so an accepted pulse still dates its line from the
    // leading edge: the learner sets clk_in_line to the pulse width and the
    // caller's x position follows clk_in_line (see vgaframe_push).
    uint8_t  pulse_open;
    uint32_t pulse_start_clk;
    uint32_t ignored_run;        // consecutive pulses the filter has rejected (see the give-up rule)
    // Clock offset, within the line just started, of the sample that carried
    // the report: the pulse's width, since the report comes at its trailing
    // edge. The caller starts the new line's pixels there (vgaframe_push),
    // which puts every pixel at its true position in the line.
    uint32_t report_x;
    uint32_t line_in_frame;
    uint8_t  v_started;          // 1 once a vsync phase boundary has been placed (see the retroactive first frame)
    uint32_t v_high_lines, v_low_lines;
    uint32_t last_cpl, last_lpf; // previous measurements for the lock check
    uint32_t h_edge_count;
} vgaframe_timing_learner_t;

void vgaframe_timing_init(vgaframe_timing_learner_t *l);
// Feeds one sample's sync levels for `run` clocks; returns 1 when a new line
// started, 2 when a new frame started, 3 when a new line started and the
// current frame is found to have started vsync_lines lines ago, else 0.
//
// Retroactive first frame: a capture that starts mid-frame cannot recognise
// its first vsync pulse as it begins (the pulse phase has never been
// measured at that point, so there is nothing to compare against). The
// moment that first pulse *ends*, though, both levels have been seen: the
// pulse is known to have been vsync_lines lines long, and the frame it began
// is known to have started that many lines ago. That is reported as 3 so the
// frame can be claimed retroactively - the caller sets y = vsync_lines and
// starts filling in - instead of being discarded, which used to cost one
// whole frame per capture. The lines before the decision are not recoverable
// (they were never stored), but they hold only the vsync pulse, which for
// every real mode is well outside the active area, so the frame is still
// complete.
//
// Glitch tolerance: real silicon (seen on tt08's tt_um_rejunity_vga_logo)
// emits spurious hsync pulses of 2 to 30 clocks a few times per capture, and
// taking each one for a line start leaves every frame short of lines, so no
// frame ever matches a mode and nothing is ever emitted. Once hsync_width
// and clocks_per_line are known - the first full measurement, before which
// nothing is filtered because there is nothing to filter against - a pulse
// only starts a line if it is within 25% of the learned width AND its
// leading edge is at least half a line after the last accepted one. Both
// tests are needed: a 30-clock pulse in the middle of the active area passes
// the distance test, and a full-width pulse just after a line start passes
// the width test. An ignored pulse does not start a line, does not restart
// clk_in_line (so the pixels around it keep their true position in the line,
// and the blanking phase it interrupted still measures a whole line), and
// increments vgaframe_timing_t.glitches. Because a pulse's width is only
// known at its trailing edge, that is where a line start is reported - see
// report_x, which keeps the caller's pixel positions exact regardless.
// Should the filter reject many pulses in a row it throws its own
// measurement away and bootstraps again, so a bad first measurement (a
// stream that starts inside a pulse and meets a glitch before its first
// clean line can learn a 2-clock pulse as the sync width) cannot wedge it.
int  vgaframe_timing_push(vgaframe_timing_learner_t *l, uint8_t hsync, uint8_t vsync, uint32_t run);

// ---- frame reconstruction ------------------------------------------------

typedef struct vgaframe_output {
    // Owned by the vgaframe and reused by the next emitted frame: valid only
    // for the duration of the vgaframe_frame_fn callback. Copy it if you
    // need it afterwards. (Same contract as vgacap_event_t's u.time.msg.)
    const uint8_t *rgb24; uint16_t width, height;
    uint32_t stride;                 // bytes per row of rgb24 (currently width * 3)
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
    // FRAM mode state. FRAM mode is per *chunk*, not for the lifetime of the
    // object: fram_active is set only while a FRAM chunk still has samples
    // outstanding, and gates the window-driven line layout. fram_pending is
    // set while an accumulation is buffered and unemitted, which outlives the
    // chunk - the window that completes a frame's coverage may be several
    // chunks later, with continuous-mode samples in between.
    int fram_active, fram_pending;
    uint32_t fram_counter; uint16_t fram_first_line, fram_line_count; uint32_t fram_cpl; uint32_t fram_remaining;
    uint32_t fram_max_line;
    // Timing actually used for the most recently emitted frame: the
    // learner's own measurement, unless emit() resolved a different mode
    // (force_mode, or a FRAM clocks-per-line table match) - see emit() in
    // frame.c. vgaframe_output_t.timing always points here.
    vgaframe_timing_t out_timing;
} vgaframe_t;

// Binds the caller's buffers and puts the object in its starting state; see
// vgaframe_reset, which vgaframe_init performs as part of its work.
int  vgaframe_init(vgaframe_t *f, const vgaframe_config_t *cfg, uint8_t *raw, uint8_t *rgb, uint8_t *cover);
// Returns the object to its post-init state - accumulated picture, coverage,
// timing learner, FRAM state and frame count all cleared - without touching
// the config or the caller's buffer pointers, so a second stream reconstructs
// exactly as it would after a fresh vgaframe_init. Use it after a stream
// discontinuity, a flush/seek, or a reconnect.
void vgaframe_reset(vgaframe_t *f);
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
