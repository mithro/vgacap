// SPDX-License-Identifier: Apache-2.0
// Trace block assembly (internal to src/trace and its tests): one block's
// uncompressed bytes, built in a buffer of VGACAP_TRACE_BLOCK_BYTES.
#ifndef VGACAP_TRACE_BLOCKS_H
#define VGACAP_TRACE_BLOCKS_H

#include <stddef.h>
#include <stdint.h>
#include "vgacap/stream.h"
#include "vgacap/trace.h"

typedef struct vgacap_trace_block {
    uint8_t *buf;        // VGACAP_TRACE_BLOCK_BYTES
    size_t   fill;
    size_t   runs_off;   // offset of this block's RUNS chunk header
    uint32_t pairs;      // pairs in that RUNS chunk so far
} vgacap_trace_block_t;

// vgacap_write_fn appending to the vgacap_trace_block_t `user`; -1 if the
// bytes would not fit in VGACAP_TRACE_BLOCK_BYTES.
int vgacap_trace_block_append(void *user, const uint8_t *p, size_t n);

// Starts a block's sample data through `w` (whose user is `b`): BLKH(first),
// then a RUNS chunk header whose length and count vgacap_trace_block_end_runs
// patches.
int vgacap_trace_block_begin(vgacap_trace_block_t *b, vgacap_writer_t *w, uint64_t first);

// One pair into the open RUNS chunk. The caller keeps room for it
// (vbytes + 10 bytes) and keeps adjacent values different. A pair per run
// is the busiest designs' cost, so each vbytes gets its own copy of the
// encoder with the count a constant: the value's byte loop then compiles to
// a few merged stores instead of a loop.
#define VGACAP_TRACE_PAIR_CASE(k) case k: n = vgacap_run_pair_put_inline(p, value, k, run); break
static inline void vgacap_trace_block_pair(vgacap_trace_block_t *b, uint64_t value, unsigned vbytes,
                                           uint64_t run) {
    uint8_t *p = b->buf + b->fill;
    size_t n;
    switch (vbytes) {
        VGACAP_TRACE_PAIR_CASE(1); VGACAP_TRACE_PAIR_CASE(2); VGACAP_TRACE_PAIR_CASE(3);
        VGACAP_TRACE_PAIR_CASE(4); VGACAP_TRACE_PAIR_CASE(5); VGACAP_TRACE_PAIR_CASE(6);
        VGACAP_TRACE_PAIR_CASE(7);
        default: n = vgacap_run_pair_put_inline(p, value, 8, run); break;
    }
    b->fill += n;
    b->pairs++;
}
#undef VGACAP_TRACE_PAIR_CASE

// Ends the RUNS chunk: patches its length and pair count, or removes its
// header if it has no pairs (a RUNS chunk needs at least one).
void vgacap_trace_block_end_runs(vgacap_trace_block_t *b);

// Checks that `h` is a valid version 2 header and `text` a META text the
// reader accepts whose first line is `meta=open` (open != 0) or
// `meta=close`, by feeding both to the stream reader; and that a
// `layout=tt-um-43` has the header that layout requires (sample_bits 43,
// samples_per_clock 2, clock_bit 42, clock_first 0). Returns 0, or -1 with
// `err` set.
int vgacap_trace_check(const vgacap_header_t *h, const char *text, int open, char *err, size_t errlen);

#endif
