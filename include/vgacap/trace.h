// SPDX-License-Identifier: Apache-2.0
// vgacap simulation trace writer: a zstd seekable-format file of VGCH
// version 2 blocks (docs/design/2026-11-25-format-v2-runs.md, "Container").
//
// Each zstd frame is one block. Block 0 is VGCH, META(open), BLKH(0), RUNS;
// every later block is BLKH(first_sample), RUNS; the last block ends with
// META(close). A block closes at VGACAP_TRACE_BLOCK_CLOCKS clocks counted
// from its first sample, or before its pairs would pass
// VGACAP_TRACE_BLOCK_BYTES - VGACAP_TRACE_BLOCK_RESERVE bytes; a run that
// would cross the boundary is split. The seek table is a skippable frame at
// the end, so plain `zstd -d` yields the whole VGCH stream.
//
// A simulation testbench calls vgacap_trace_push() once per sample (twice a
// clock with samples_per_clock 2). The common case -- same value, clock bit
// as expected, not at a block end -- is inline: no call, allocation or
// syscall. Finished blocks are compressed and written by a thread of the
// writer's own, double-buffered.
//
// Host only (pthreads, zstd); not part of the board builds.
#ifndef VGACAP_TRACE_H
#define VGACAP_TRACE_H

#include <stddef.h>
#include <stdint.h>
#include "vgacap/stream.h"

#ifdef __cplusplus
extern "C" {
#endif

#define VGACAP_TRACE_BLOCK_BYTES   (16u * 1024u * 1024u)
#define VGACAP_TRACE_BLOCK_RESERVE (64u * 1024u)    // closing META and the last pair
#define VGACAP_TRACE_BLOCK_CLOCKS  (1ull << 24)

// Fields above `impl` are read by the inline push; do not touch them.
typedef struct vgacap_trace_writer {
    uint64_t cur;        // the current run's value, clock bit cleared
    uint64_t run;        // samples in the current run
    uint64_t n;          // number of the next sample
    uint64_t limit;      // n at which the slow path must run (block end); 0 before the first sample
    uint64_t clk_mask;   // 1 << clock_bit, or 0
    uint64_t clk_expect; // clk_mask if the clock bit must be 1 at sample n, else 0
    struct vgacap_trace_impl *impl;
} vgacap_trace_writer_t;

// Creates `path` and starts block 0 with the VGCH header `h` (version 2)
// and META(open) `open_meta` (key=value lines, the first `meta=open`; see
// the format note). Starts the compressor thread. Returns NULL with `err`
// set on a bad header or META text, or if the file cannot be created.
vgacap_trace_writer_t *vgacap_trace_create(const char *path, const vgacap_header_t *h,
                                           const char *open_meta, int zstd_level,
                                           char *err, size_t errlen);
int vgacap_trace_slow(vgacap_trace_writer_t *t, uint64_t v);       // not for callers

// Record sample n = t->n, with value `v` (clock bit included). Returns 0, or
// -1 once the writer has failed (a clock bit that breaks the alternation,
// a bit at or above sample_bits, or an I/O error); every later push then
// returns -1 too, and vgacap_trace_finish() reports why.
static inline int vgacap_trace_push(vgacap_trace_writer_t *t, uint64_t v) {
    if (((v ^ t->clk_expect) & t->clk_mask) | ((v & ~t->clk_mask) ^ t->cur) | (uint64_t)(t->n == t->limit))
        return vgacap_trace_slow(t, v);
    t->run++; t->n++; t->clk_expect ^= t->clk_mask; return 0;
}

// Ends the last run, writes META(close) in the last block, the seek table,
// joins the thread, frees `t`. Returns 0, or -1 with `err` set.
int vgacap_trace_finish(vgacap_trace_writer_t *t, const char *close_meta, char *err, size_t errlen);
// Frees `t` without a closing chunk or seek table (what a kill leaves, for tests).
void vgacap_trace_abandon(vgacap_trace_writer_t *t);

#ifdef __cplusplus
}
#endif
#endif
