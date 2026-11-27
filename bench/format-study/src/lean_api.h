// SPDX-License-Identifier: Apache-2.0
// The lean vgch2 writer for the simulation check (sim/bench_tick.cpp), which
// also links vgacap's own writer and so cannot include lean.h: the lean
// writer's create, finish and slow paths under their own names. The
// writer's state is vgacap's vgacap_trace_writer_t, so the caller's inline
// fast path is vgacap_trace_repeat2's, written out.
#ifndef FS_LEAN_API_H
#define FS_LEAN_API_H
#include "vgacap/trace.h"
#ifdef __cplusplus
extern "C" {
#endif
vgacap_trace_writer_t *fs_lean_create(const char *path, const vgacap_header_t *h, const char *open_meta,
                                      int zstd_level, char *err, size_t errlen);
int fs_lean_finish(vgacap_trace_writer_t *t, const char *close_meta, char *err, size_t errlen);
// tb.cpp's raw_slow, for the lean writer: pack both raw images through the
// offset table, then push2. off and clk_raw as tb.cpp's RawPorts.
int fs_lean_raw_slow(vgacap_trace_writer_t *t, const int off[8], uint64_t clk_raw, uint64_t r0, uint64_t r1,
                     uint64_t *want_raw);
// repeat2's slow path (a block end at sample n or n + 1, or a failed writer).
int fs_lean_repeat2_slow(vgacap_trace_writer_t *t);
#ifdef __cplusplus
}
#endif
#endif
