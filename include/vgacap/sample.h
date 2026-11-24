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

#ifdef __cplusplus
}
#endif
#endif
