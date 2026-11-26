// SPDX-License-Identifier: Apache-2.0
// The capture engines (portable): streaming and frame/window capture,
// writing vgacap chunks through the HAL's host link.
//
// Two front ends drive them: the capture firmware's command loop (fw.c,
// firmware/PROTOCOL.md), and the MicroPython module (mpy/, `vgacap.stream()`
// and `vgacap.frame()`). Both hand the engines the arguments as protocol
// text -- `key=value` pairs, parsed here against one table per engine -- so
// every range check and every refusal message is the same whichever front
// end a capture came through.
//
// The streaming engine is python/ttcap/mp/capture_rp2.py's main() and the
// frame engine frame_rp2.py's main(), statement for statement where the
// order matters (when a stop is noticed, what is counted in max_bytes,
// which TIME chunks are written and what they say), so a stream from either
// front end and one from those scripts are read by the same host code.
#ifndef VGACAP_FW_CAPTURE_H
#define VGACAP_FW_CAPTURE_H

#include <stddef.h>
#include <stdint.h>

#include "fw_hal.h"

#ifdef __cplusplus
extern "C" {
#endif

// Bytes that ask a running capture to stop: Ctrl-C, and "q" for a human.
#define FW_STOP_BYTE 0x03
#define FW_STOP_BYTE_HUMAN 'q'

// Longest refusal message an engine writes.
#define FW_WHY_MAX 160

// `stream buf_words max_bytes edge pio sm` (PROTOCOL.md).
typedef struct fw_stream_args {
    uint32_t buf_words;
    uint32_t max_bytes;
    unsigned rising;  // edge: 0 falling, 1 rising
    unsigned pio, sm;
} fw_stream_args_t;

// `frame cpl region_first region_lines window_lines hsync_active
// vsync_active max_frames max_bytes edge sample_delay pio sm`, plus the
// project clock the capture is to lock to (`clock_hz`: the firmware knows
// it from its own `clock` command; the MicroPython module is told it).
typedef struct fw_frame_args {
    uint32_t cpl, region_first, region_lines, window_lines;
    int hsync_active, vsync_active;  // -1: measure
    uint32_t max_frames, max_bytes;
    unsigned rising, sample_delay, pio, sm;
    uint32_t clock_hz;               // 0: the project clock is stopped
} fw_frame_args_t;

// Parse the arguments after the command word. `arena_bytes` bounds
// buf_words (two buffers must fit). `with_clock_hz` makes `clock_hz=` one
// of the keys (the module); without it clock_hz is left 0 for the caller
// to fill in. Return 0, or -1 with the message in `why`.
int fw_stream_parse(const char *text, size_t arena_bytes, fw_stream_args_t *a, char *why,
                    size_t whylen);
int fw_frame_parse(const char *text, int with_clock_hz, fw_frame_args_t *a, char *why,
                   size_t whylen);

// Bytes of capture buffer a frame capture of these arguments can use: a
// whole region (or window) of lines. 0 if the arguments are refused.
size_t fw_frame_wanted_bytes(const fw_frame_args_t *a);

// What a capture reports back to its front end.
typedef struct fw_capture_result {
    int lost;  // the host went away (a write failed): nothing more may be written
} fw_capture_result_t;

// Called once the hardware is running and before the first chunk: the
// firmware answers `ok stream` / `ok frame` there. May be NULL.
typedef void (*fw_started_fn)(void *user);

// Run one capture. A stream captures into `arena` (`arena_bytes`, 4-byte
// aligned); a frame into the `count` buffers `bufs` (fw_hal.h), filled in
// order, each used in whole lines -- the firmware's one arena, or the
// MicroPython module's heap blocks; `count` may be 0 (no buffer: refused).
// Returns -1 with the refusal in `why` when it cannot start (nothing has
// been written then), else 0 once the closing summary TIME chunk has been
// written.
int fw_stream_run(const fw_stream_args_t *a, uint32_t *arena, size_t arena_bytes,
                  fw_started_fn started, void *user, fw_capture_result_t *res, char *why,
                  size_t whylen);
int fw_frame_run(const fw_frame_args_t *a, const fw_span_t *bufs, unsigned count,
                 fw_started_fn started, void *user, fw_capture_result_t *res, char *why,
                 size_t whylen);

#ifdef __cplusplus
}
#endif
#endif
