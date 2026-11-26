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

// Fields above `impl` are read and written by the inline calls; do not touch them.
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
//
// The test is v ^ cur ^ clk_expect: cur never has the clock bit and
// clk_expect has nothing else, so its clock bit is (v ^ clk_expect) & clk_mask
// and its other bits are (v & ~clk_mask) ^ cur. It is 0 exactly when the
// clock bit is as expected and the value is the current run's.
static inline int vgacap_trace_push(vgacap_trace_writer_t *t, uint64_t v) {
    if ((v ^ t->cur ^ t->clk_expect) | (uint64_t)(t->n == t->limit))
        return vgacap_trace_slow(t, v);
    t->run++; t->n++; t->clk_expect ^= t->clk_mask; return 0;
}

int vgacap_trace_repeat2_slow(vgacap_trace_writer_t *t);          // not for callers

// Record samples n and n + 1 as repeats of the last sample pushed: exactly
// vgacap_trace_push(t, last ^ clk) then vgacap_trace_push(t, last), where
// `last` is the last sample's value (clock bit included) and clk is
// 1 << clock_bit, or 0 when the header has no clock bit. With a clock bit,
// the two samples hold the current run's value, the clock bit alternating
// on from `last`. At samples_per_clock 2 and pair-aligned pushes (sample n
// even), that is one more clock equal to the last: clk-low and clk-high
// samples with the same pins, so the current run grows by 2. It is also
// defined off the pairs (n odd), as the same two pushes. Without a clock
// bit, it is `last` twice. (No trace without a clock bit can be created
// today: the only layout, tt-um-43, has one.)
//
// A caller uses it when it knows, from its own state (for example the
// model's raw port bytes), that the next two samples are these, without
// building their values. The writer trusts it and does not check them.
// It is valid only after at least one sample: at sample 0 there is no
// `last`, so it fails the writer and returns -1. Otherwise it returns what
// the two pushes would: 0, or -1 once the writer has failed. The file is
// byte-identical to the one the two pushes would write; a block end inside
// or at the pair splits the run there, as push does.
//
// Its fast path is one test: n <= limit always, so limit - n < 2 covers a
// block end at sample n or n + 1, sample 0 (limit 0) and a failed writer
// (limit == n). The current run then has at least one sample (only sample
// 0 has none), so adding 2 to it is what the two pushes would do.
static inline int vgacap_trace_repeat2(vgacap_trace_writer_t *t) {
    if (t->limit - t->n < 2) return vgacap_trace_repeat2_slow(t);
    t->run += 2; t->n += 2; return 0;
}

int vgacap_trace_push2_slow(vgacap_trace_writer_t *t, uint64_t v0, uint64_t v1);   // not for callers

// Record samples n and n + 1: exactly vgacap_trace_push(t, v0) then
// vgacap_trace_push(t, v1), with the same result and the same file (if
// the first fails, so does the second, so it is not made). For a caller
// that has both samples of a clock but cannot tell "unchanged" itself: the
// two compares are one test. Valid anywhere, sample 0 included.
//
// The fast path: w = cur ^ clk_expect is the value sample n must have to
// extend the run, and w ^ clk_mask sample n + 1's; limit - n < 2 as in
// repeat2.
static inline int vgacap_trace_push2(vgacap_trace_writer_t *t, uint64_t v0, uint64_t v1) {
    uint64_t w = t->cur ^ t->clk_expect;
    if ((v0 ^ w) | (v1 ^ w ^ t->clk_mask) | (uint64_t)(t->limit - t->n < 2))
        return vgacap_trace_push2_slow(t, v0, v1);
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
