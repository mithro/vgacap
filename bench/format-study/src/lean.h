// SPDX-License-Identifier: Apache-2.0
// The lean vgch2 writer's names (trace_write_lean.c): vgacap's trace writer
// API under an fs_lean_ prefix. Include this instead of vgacap/trace.h in a
// file that calls the lean writer; vgacap/trace.h's inline push, push2 and
// repeat2 then call the lean writer's slow paths.
#ifndef FS_LEAN_H
#define FS_LEAN_H
#define vgacap_trace_create fs_lean_trace_create
#define vgacap_trace_finish fs_lean_trace_finish
#define vgacap_trace_abandon fs_lean_trace_abandon
#define vgacap_trace_slow fs_lean_trace_slow
#define vgacap_trace_repeat2_slow fs_lean_trace_repeat2_slow
#define vgacap_trace_push2_slow fs_lean_trace_push2_slow
#include "vgacap/trace.h"
#endif
