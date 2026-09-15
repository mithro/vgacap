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

#endif
