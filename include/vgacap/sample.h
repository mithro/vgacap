// SPDX-License-Identifier: Apache-2.0
// The shared stages every simulation-trace sampler runs first: the sync
// calibration, an exact port of tinytapeout-vga-videos' testbench
// (ttvga/harness/tb.cpp:191-230 and 361-378), and the frame clock, the
// vsync-end counting of tb.cpp's capture loop (tb.cpp:480 and 520).
//
// Clock numbering is the spec's (raw-frames-and-hash-methods design, "Clock
// numbering"): clocks are numbered from 0, clock 0 being the first reset
// clock; clock c is samples 2c (clk low) and 2c + 1 (clk high). Both stages
// are fed the clk-high uo_out (and rst_n) of each clock, as runs: `clocks`
// consecutive clocks from `first`, all with the same values. Runs must be
// fed in order and without gaps.
//
// tb.cpp's `clock` is incremented at the end of each tick, so where tb.cpp
// handles trace clock c it holds T = c + 1. The calibration keeps tb.cpp's
// numbers: the trackers' phase lengths, the 0xFFFF checks and
// capture_after_clock are all in T, exactly as tb.cpp computes them.
//
// Host only; not part of the board builds.
#ifndef VGACAP_SAMPLE_H
#define VGACAP_SAMPLE_H

#include <stddef.h>
#include <stdint.h>

#include "vgacap/frame.h"

#ifdef __cplusplus
extern "C" {
#endif

// tb.cpp's SyncInfo. Not found: active_low 1, pulse and period 0, as tb.cpp's
// defaults.
typedef struct vgacal_sync { uint8_t found, active_low; uint64_t pulse, period; } vgacal_sync_t;

enum { VGACAL_RUNNING, VGACAL_DONE };

// tb.cpp's SyncTracker. The phases are kept as their count plus the lengths
// of every phase but the first, by level, which is all analyse() reads.
typedef struct vgacal_tracker {
    uint8_t last;            // level of the current phase; false at start
    uint64_t phase_start;    // T of the current phase's first clock
    uint64_t phases;         // tb.cpp's phases.size()
    uint64_t *len[2];        // len[level]: lengths of phases 1.. at that level
    size_t n[2], cap[2];
} vgacal_tracker_t;

typedef struct vgacal {          // port of tb.cpp:191-230 and 361-378
    double clock_hz, calib_seconds;
    uint64_t calib_limit;        // (uint64_t)(calib_seconds * clock_hz), as tb.cpp:364
    int state; int started;      // started: rst_n has been seen high
    uint64_t capture_after_clock;   // valid when state == VGACAL_DONE
    vgacal_sync_t hs, vs;
    uint64_t h_phases, v_phases;    // SyncTracker phase counts
    // internals
    vgacal_tracker_t ht, vt;
    uint64_t *scratch; size_t scratch_cap;   // median's working copy
    uint64_t chk_h, chk_v; int chk_valid, chk_ok;   // the last stop check, by phase counts
} vgacal_t;

void vgacal_init(vgacal_t *c, double clock_hz, double calib_seconds);
// Feeds `clocks` clocks starting at clock `first`, all with this clk-high
// uo_out and rst_n. Returns how many it consumed (fewer only when
// calibration ended inside: the clocks from capture_after_clock on are not
// consumed, so the caller hands them to the frame clock). Returns 0 once
// done.
uint64_t vgacal_push(vgacal_t *c, uint8_t uo_out, uint8_t rst_n, uint64_t first, uint64_t clocks);
// The trace ended (after `clocks_in_trace` clocks) before calibration did:
// analyses as tb.cpp does after its loop, and capture_after_clock is
// clocks_in_trace (tb.cpp's `clock` after its last tick). No-op once done.
void vgacal_end_of_trace(vgacal_t *c, uint64_t clocks_in_trace);
void vgacal_free(vgacal_t *c);

typedef struct vgafc {           // frame clock
    uint8_t v_active_low, prev_v;
    uint64_t *ends; size_t n, cap;  // ends[k]: clock of frame k's vsync end
    uint64_t from;               // capture_after_clock: earlier clocks are ignored
} vgafc_t;
// From a finished calibration. prev_v = 0, as tb.cpp:480.
void vgafc_init(vgafc_t *fc, const vgacal_t *c);
// An end is a clock e >= capture_after_clock + 1 whose uo_out is outside the
// vsync pulse while clock e - 1's was inside it (tb.cpp:520).
void vgafc_push(vgafc_t *fc, uint8_t uo_out, uint64_t first, uint64_t clocks);
void vgafc_free(vgafc_t *fc);

// ---- samplers: trace clocks to frames -------------------------------------

// One frame, handed to the sampler's callback; rgb6 (R<<4 | G<<2 | B, each
// channel's first Tiny VGA bit most significant) is valid during the call.
typedef struct vgasample_frame {
    uint64_t number;        // frame number from the frame clock
    uint64_t start_clock;   // per sampler, as the spec defines
    const uint8_t *rgb6; uint16_t width, height;
    uint32_t missing;       // centre-v1 only
} vgasample_frame_t;
typedef void (*vgasample_frame_fn)(void *user, const vgasample_frame_t *f);

typedef struct vgasample_geometry {     // fixed at capture_after_clock
    int ok;                 // 0: no picture
    const char *status;     // "no-sync", "bad-timing" or (centre-v1) "bad-window" when !ok, else "ok"
    const char *mode;       // table name or "unknown"
    double cpp; uint16_t width, height;
    int hback, vback;       // pixels / lines used (centre-v1: hback', vback')
} vgasample_geometry_t;

// tb-v1: a port of tb.cpp:379-557, after the shared calibration. Mode from
// tb.cpp's own 11-mode table (tb.cpp:39-51, in its order); frame k starts at
// frame-clock end k (start_clock = ends[k]) and is emitted at end k + 1;
// capture stops, as tb.cpp's loop, once target_frames =
// (uint64_t)(seconds * fps + 0.5) frames are out or at clock_limit =
// capture_after_clock + (uint64_t)((seconds + 2.5 / fps) * clock_hz).
// Pixel p of a line is read at the first clock at which
// (int)((x_clock + cpp / 2) / cpp) is p, x_clock counting clocks from the
// hsync pulse's end; nothing written stays 0 (black).
typedef struct tbv1 tbv1_t;   // opaque, heap
// cal must be VGACAL_DONE. fc is the frame clock fed the same clocks (it may
// be NULL); tb-v1 counts the ends by the same rule and, where fc already
// holds end k, checks it is the same clock.
tbv1_t *tbv1_create(const vgacal_t *cal, const vgafc_t *fc, double clock_hz, double seconds,
                    vgasample_frame_fn fn, void *user);
const vgasample_geometry_t *tbv1_geometry(const tbv1_t *s);
// tb.cpp's frame target and clock limit (0 when !ok).
uint64_t tbv1_target_frames(const tbv1_t *s);
uint64_t tbv1_clock_limit(const tbv1_t *s);
// Frames emitted so far (tb.cpp's `frames`).
uint64_t tbv1_frames(const tbv1_t *s);
// Feeds a run, in order and without gaps; clocks before capture_after_clock
// are ignored. No-op when !ok or once capture has stopped.
void tbv1_push(tbv1_t *s, uint8_t uo_out, uint64_t first, uint64_t clocks);   // clocks >= capture_after_clock
void tbv1_free(tbv1_t *s);

// centre-v1: libvgaframe's timing learner finds the lines and frames, at
// clock resolution (its raw frame callback, vgaframe_raw_t); the sampler
// picks every pixel itself, mid-pixel, from that clock-resolution frame.
// The spec (raw-frames-and-hash-methods design, Layer 2's centre-v1 and
// Layer 3) defines it; python/vgacap/centre.py is the reference for the
// window and the pick.
//
// Geometry, fixed at centrev1_calibrated from the shared calibration (L =
// hs.period clocks a line, lines = (vs.period + L / 2) / L, vpulse_lines =
// (vs.pulse + L / 2) / L, as tb.cpp:403-421):
// - mode: tb.cpp's summed-error rule (best below 0.03; cpp_l = L / the mode's
//   line pixels in 0.9..4.5; then the non-standard-line-count fallback)
//   against the shared 12-mode table, vgaframe_modes(), in table order;
// - cpp: 1. |cpp_l - round(cpp_l)| < 0.01 gives round(cpp_l); 2. else the
//   smallest k >= 1 with |hs.pulse - k * mode h_sync| <= 2 gives k; 3. else
//   cpp_l itself;
// - window: hsync = hs.pulse / cpp pixels, B = L / cpp - hsync - width,
//   hback' = floor(B * hback / (hback + hfront) + 0.5); vertically
//   B = lines - vpulse_lines - height and vback' likewise (0 for the
//   non-standard-line-count fallback, whose height is lines - vpulse_lines).
//   Then dx and dy are added; geometry hback and vback are the values used.
// - unknown mode (tb.cpp:441-444): cpp 1, width L - hs.pulse, height lines -
//   vpulse_lines, hback' = vback' = 0.
// - bad-timing by size: width or height outside 16..4096, more than 1,200
//   lines, or L above 7,596 clocks (the raw buffer).
// - the window's clocks: on the pick's clock axis the window spans [a, b),
//   a = (hsync + hback') * cpp, b = a + width * cpp; its clocks are those
//   overlapping that, [floor(a), ceil(b)), in rows [vpulse_lines + vback',
//   + height). At whole cpp that is the window exactly.
// - bounds (the spec: the window never starts inside the hsync or vsync
//   pulse and never extends past the line or the frame): hback' >= 0,
//   vback' >= 0, ceil(b) <= L and vpulse_lines + vback' + height <= lines,
//   checked in int64. Failed by the window the timing gives (dx = dy = 0):
//   "bad-timing". Passed by it but failed once dx and dy are added:
//   "bad-window", an override the run cannot use (vgacap-sample fails on it).
// On failure (ok 0) the geometry follows tb-v1's convention: no-sync, and
// bad-timing by period (hs.period == 0 || vs.period < 2 * hs.period), report
// mode "unknown" and cpp 1; bad-timing by size or bounds, and bad-window,
// report the matched mode (or "unknown") and its cpp. Width, height, hback
// and vback are 0 either way.
//
// Pixel (p, y) is raw row vpulse_lines + vback' + y at clock
// x = (uint32_t)floor((hsync + hback' + p + 0.5) * cpp), evaluated in that
// order. A clock that was not written (no 0x80), or lies outside the line
// (x >= L) or the frame (row >= lines or the rows received), is missing:
// stored as 0 and counted in vgasample_frame_t.missing.
//
// Frames: start_clock is the hsync leading edge of row 0 (the first line of
// the vsync pulse): the clock of the push that brought report 2 less
// report_x, or for a retroactive frame (report 3) that less vsync_lines *
// clocks_per_line. A frame is numbered with the latest frame-clock end in
// [start_clock, the clock of the hsync trailing edge of row v], row v being
// the first row libvgaframe judged out of the vsync pulse; with none it is
// dropped (its number is a gap). Frames are emitted at the learner's next
// frame boundary, so the frame cut off by the end of the trace never is.
typedef struct centrev1 centrev1_t;   // opaque, heap; owns its libvgaframe buffers
// dx, dy: the override (centre_window), in pixels and lines.
centrev1_t *centrev1_create(int dx, int dy, vgasample_frame_fn fn, void *user);
// Every clock from 0, in order and without gaps, with the clk-high uo_out.
// Calling order, per run of clocks: the calibration first (vgacal_push);
// the clocks it consumed (those before capture_after_clock) to centrev1;
// once it is done, centrev1_calibrated (once, before any clock at or after
// capture_after_clock reaches centrev1: it aborts otherwise); then the rest
// of the run to the frame clock fc, then to centrev1. A trace that ends
// before calibration does: vgacal_end_of_trace, then centrev1_calibrated.
void centrev1_push(centrev1_t *s, uint8_t uo_out, uint64_t first, uint64_t clocks);
// The calibration is done (or the trace ended first): fixes the geometry.
// fc is kept and read at every frame. Frames ending before this are dropped.
// Aborts if the calibration is not done, on a second call, or if a clock at
// or after capture_after_clock was already pushed.
void centrev1_calibrated(centrev1_t *s, const vgacal_t *cal, const vgafc_t *fc);
// Valid after centrev1_calibrated (before it: ok 0, status "no-sync").
const vgasample_geometry_t *centrev1_geometry(const centrev1_t *s);
// Tests and tools: fn sees libvgaframe's raw frame of every frame centre-v1
// emits, just before the frame callback.
void centrev1_set_raw_observer(centrev1_t *s, void (*fn)(void *user, const vgaframe_raw_t *r), void *user);
typedef struct centrev1_diag {
    // Over a 1-bit map of the clock positions (x < L, row < lines) ever
    // non-black in an emitted frame: outside is the lit positions outside
    // the window used: its clocks [floor(a), ceil(b)) of its rows, as above.
    uint64_t outside;
    // The offset from the window without the override that passes the
    // bounds above with the fewest lit positions outside; ties to the
    // smallest |dx| + |dy|, then dy, then dx.
    int suggest_dx, suggest_dy;
    // Written clocks x of the window's clocks (x >= 1) in emitted frames
    // whose colour differs from clock x - 1's; off_grid counts those not at a
    // pixel boundary, (x - floor(a)) % cpp != 0, for whole cpp only; else
    // UINT64_MAX.
    uint64_t colour_changes, off_grid;
    int dx, dy;                          // the override used
} centrev1_diag_t;
// End of trace: computes the diagnostics.
void centrev1_finish(centrev1_t *s, centrev1_diag_t *d);
void centrev1_free(centrev1_t *s);

// ---- frame hash methods ----------------------------------------------------

// Over a frame as rgb6 (R<<4 | G<<2 | B), n pixels, row-major, one byte each.
// sha256-rgb6: SHA-256 of the bytes.
void vgahash_sha256_rgb6(const uint8_t *rgb6, size_t n, uint8_t out[32]);
// fnv1a64-tb2026-rgb888: FNV-1a 64 (prime 0x100000001b3, offset basis
// 0x14650fb0739d0383, tb.cpp's constant) over the RGB888 bytes, each rgb6
// channel times 85; hashed per pixel, no RGB888 buffer.
uint64_t vgahash_fnv1a64_tb2026_rgb888(const uint8_t *rgb6, size_t n);
// The method's definition lines, verbatim as written into hash files as "# "
// lines, NULL-terminated; NULL for an unknown method.
const char *const *vgahash_definition(const char *method);

#ifdef __cplusplus
}
#endif
#endif
