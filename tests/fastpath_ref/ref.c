// SPDX-License-Identifier: Apache-2.0
// Compiles the a26c942 snapshot of frame.c and timing.c (this directory) with
// every symbol they define renamed to ref_*, so test_frame_fastpath can run
// it side by side with the library. The snapshot's own "internal.h" is the
// one found first, next to it. vgaframe_mode_match and the other modes.c
// functions are not renamed: they come from the library, unchanged.
#define vgaframe_raw_size      ref_vgaframe_raw_size
#define vgaframe_rgb_size      ref_vgaframe_rgb_size
#define vgaframe_colour        ref_vgaframe_colour
#define vgaframe_reset         ref_vgaframe_reset
#define vgaframe_init          ref_vgaframe_init
#define vgaframe_push          ref_vgaframe_push
#define vgaframe_push64        ref_vgaframe_push64
#define vgaframe_flush         ref_vgaframe_flush
#define vgaframe_frame_begin   ref_vgaframe_frame_begin
#define vgaframe_timing_init   ref_vgaframe_timing_init
#define vgaframe_timing_push   ref_vgaframe_timing_push
#define vgaframe_timing_push64 ref_vgaframe_timing_push64
#include "timing.c"
#include "frame.c"
