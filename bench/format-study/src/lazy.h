// SPDX-License-Identifier: Apache-2.0
// The lazy-BLKH reader's names (trace_read_lazy.c): vgacap's trace reader
// API under an fs_lazy_ prefix, so that it links beside vgacap's.
#ifndef FS_LAZY_H
#define FS_LAZY_H
#define vgacap_trace_open fs_lazy_trace_open
#define vgacap_trace_info fs_lazy_trace_info
#define vgacap_trace_read fs_lazy_trace_read
#define vgacap_trace_read_runs fs_lazy_trace_read_runs
#define vgacap_trace_block_data fs_lazy_trace_block_data
#define vgacap_trace_read_stats fs_lazy_trace_read_stats
#define vgacap_trace_set_generic_runs fs_lazy_trace_set_generic_runs
#define vgacap_trace_close fs_lazy_trace_close
#include "vgacap/trace.h"
#endif
