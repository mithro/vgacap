// SPDX-License-Identifier: Apache-2.0
// What a trace reader has done so far (internal to src/trace and its tests):
// how many blocks it decompressed whole, and how many bytes of the file it
// handed to zstd. It shows that opening a trace with a good seek table, and
// reading a short range, touch only the parts of the file they need.
#ifndef VGACAP_TRACE_READ_STATS_H
#define VGACAP_TRACE_READ_STATS_H

#include <stdint.h>
#include "vgacap/trace.h"

typedef struct vgacap_trace_read_stats {
    uint64_t blocks_decoded;   // whole frames decompressed (a cached block is not counted again)
    uint64_t bytes_touched;    // compressed bytes read: frame headers, block starts, whole
                               // frames, frames walked, the seek table
} vgacap_trace_read_stats_t;

void vgacap_trace_read_stats(const vgacap_trace_reader_t *r, vgacap_trace_read_stats_t *out);

// A test hook: nonzero makes this reader decode RUNS chunks with the generic
// (unspecialised) body, so a test can compare it with the one specialised
// on the value width. The two must deliver the same runs and errors.
void vgacap_trace_set_generic_runs(vgacap_trace_reader_t *r, int on);

#endif
