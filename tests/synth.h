// SPDX-License-Identifier: Apache-2.0
// Synthesises a full frame of Tiny VGA samples for a given mode, for use in
// timing/frame reconstruction tests.
#ifndef VGACAP_TEST_SYNTH_H
#define VGACAP_TEST_SYNTH_H
#include <stddef.h>
#include <stdint.h>
#include "vgacap/frame.h"

// Writes one full frame of 8-bit Tiny VGA samples (hsync bit 7, vsync bit 3,
// colour in bits 0,1,2,4,5,6 per the Tiny VGA map; `pixel` returns a 6-bit
// colour as `rr gg bb` in bits 5..0), starting at the leading edge of the
// vsync pulse's first line's hsync pulse. Returns the sample count
// (cpl * lpf), or 0 if out_len is too small.
size_t synth_frame(const vgaframe_mode_t *m, uint8_t (*pixel)(void *user, uint16_t x, uint16_t y),
                   void *user, uint32_t *out, size_t out_len);


// Writes one full frame of Tiny VGA samples as tt-vga-testpatterns'
// common/hvsync_generator.v registers them, for one pass of hpos/vpos from
// (0, 0): the stream does NOT start at a sync edge, and vsync changes on the
// very clock hsync starts (see synth.c for the derivation). `pixel` is called
// with (hpos, vpos) for the active area. Returns the sample count (cpl * lpf),
// or 0 if out_len is too small.
size_t synth_frame_hvsync(const vgaframe_mode_t *m, uint8_t (*pixel)(void *user, uint16_t x, uint16_t y),
                          void *user, uint32_t *out, size_t out_len);

#endif
