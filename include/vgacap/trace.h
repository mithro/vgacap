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
#define VGACAP_TRACE_RUNS_MAX      1024u           // runs a vgacap_trace_read_runs batch holds at most

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

// Stub for the tests (not yet the specified behaviour).
static inline int vgacap_trace_repeat2(vgacap_trace_writer_t *t) {
    t->run += 2; t->n += 2; return 0;
}

// Ends the last run, writes META(close) in the last block, the seek table,
// joins the thread, frees `t`. Returns 0, or -1 with `err` set.
int vgacap_trace_finish(vgacap_trace_writer_t *t, const char *close_meta, char *err, size_t errlen);
// Frees `t` without a closing chunk or seek table (what a kill leaves, for tests).
void vgacap_trace_abandon(vgacap_trace_writer_t *t);

// ---- reader -----------------------------------------------------------------
//
// Reads a trace (zstd magic 28 b5 2f fd) or a plain VGCH stream (magic
// "VGCH", version 1 or 2: one pseudo-block, decoded from the start for any
// range). The file is mmap'ed read-only.
//
// A trace's blocks come from its seek table. If the table is missing or does
// not match the frames (a trace cut short by a kill), the reader walks the
// frames instead and keeps every complete one up to the first that is
// incomplete or, for the last, fails its checksum; then table_rebuilt is 1.
// Each block's first sample is its BLKH, read by decompressing only the
// start of its frame; the last block is decompressed whole, for its sample
// count and META(close). The other frames' checksums are checked when a
// read decompresses them.

typedef struct vgacap_trace_info {
    vgacap_header_t header;
    const char *open_meta;    // text of META(open) or NULL
    const char *close_meta;   // NULL when the trace was cut short
    uint64_t samples;         // samples in the complete blocks
    uint32_t blocks;
    uint8_t  compressed;      // 0 for a plain VGCH file
    uint8_t  table_rebuilt;   // 1 if the seek table was missing or bad
} vgacap_trace_info_t;

typedef struct vgacap_trace_reader vgacap_trace_reader_t;
// value: the run's 64-bit value with the clock bit of its first sample;
// return nonzero to stop.
typedef int (*vgacap_trace_run_fn)(void *user, uint64_t first_sample, uint64_t value, uint64_t run);

// Returns NULL with `err` set if the file cannot be read or is neither a
// trace nor a VGCH stream, or if a trace's first block has no VGCH, META(open)
// and BLKH(0).
vgacap_trace_reader_t *vgacap_trace_open(const char *path, char *err, size_t errlen);
const vgacap_trace_info_t *vgacap_trace_info(const vgacap_trace_reader_t *r);
// Delivers every run overlapping [from, to), clipped to it, in order: a run
// cut at `from` starts there, its clock bit adjusted to that sample. Runs
// end at block boundaries (the writer splits them there), so two
// consecutive runs may have the same value. Returns 0 (also when `fn`
// stopped it), or -1 with `err` set: a frame that fails to decompress or
// its checksum, or a block whose chunks the stream reader reports as
// damaged. A plain stream's resynchronisations are not errors (vgacap-dump
// reports them; the samples after one keep their running numbers).
int vgacap_trace_read(vgacap_trace_reader_t *r, uint64_t from, uint64_t to,
                      vgacap_trace_run_fn fn, void *user, char *err, size_t errlen);
// The same runs, in the same order, in batches: fn gets n >= 1 runs at a
// time, at most VGACAP_TRACE_RUNS_MAX, the array valid during the call. Return nonzero to
// stop: no run after that batch is delivered. A read that fails delivers
// every run before the fault first, as vgacap_trace_read does.
// (vgacap_trace_read is this with a loop over each batch. One call a batch,
// not a run, and RUNS pairs decoded straight into the array, are what make
// it fast.)
typedef struct vgacap_trace_run {   // as vgacap_trace_run_fn's arguments
    uint64_t first_sample, value, run;
} vgacap_trace_run_t;
typedef int (*vgacap_trace_runs_fn)(void *user, const vgacap_trace_run_t *runs, size_t n);
int vgacap_trace_read_runs(vgacap_trace_reader_t *r, uint64_t from, uint64_t to,
                           vgacap_trace_runs_fn fn, void *user, char *err, size_t errlen);
// Block `i`'s uncompressed bytes (for a plain stream, block 0 is the whole
// file), valid until the next call on `r`. Returns 0, or -1 with `err` set.
int vgacap_trace_block_data(vgacap_trace_reader_t *r, uint32_t i, const uint8_t **data, size_t *len,
                            char *err, size_t errlen);
void vgacap_trace_close(vgacap_trace_reader_t *r);

#ifdef __cplusplus
}
#endif
#endif
