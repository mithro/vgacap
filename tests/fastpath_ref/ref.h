// SPDX-License-Identifier: Apache-2.0
// The reference libvgaframe push path for test_frame_fastpath: frame.c,
// timing.c and internal.h exactly as they were at a26c942, before the fast
// path, compiled under ref_ names (ref.c) so both can be linked into one test.
// Do not edit the snapshot files: they are the behaviour being preserved.
// The one exception is M10 Task 10 (Ruling O), whose deliberate behaviour
// changes - the raw-frame callback, clearing at every frame boundary, emit
// skipping its conversion with no on_frame, and rgb6 - were made to
// frame.c here as well; see the note at the top of that file.
#ifndef VGACAP_FASTPATH_REF_H
#define VGACAP_FASTPATH_REF_H
#include "vgacap/frame.h"

int  ref_vgaframe_init(vgaframe_t *f, const vgaframe_config_t *cfg, uint8_t *raw, uint8_t *rgb, uint8_t *cover);
void ref_vgaframe_push(vgaframe_t *f, uint32_t sample, uint32_t run);
void ref_vgaframe_push64(vgaframe_t *f, uint32_t sample, uint64_t run);
void ref_vgaframe_reset(vgaframe_t *f);
void ref_vgaframe_flush(vgaframe_t *f);
void ref_vgaframe_set_rgb6_buffer(vgaframe_t *f, uint8_t *rgb6);
void ref_vgaframe_frame_begin(vgaframe_t *f, uint32_t frame_counter, uint16_t first_line,
                              uint16_t line_count, uint32_t clocks_per_line, uint32_t sample_count);
#endif
