// SPDX-License-Identifier: Apache-2.0
// The "lean" vgch2 writer: vgacap's own src/trace/trace_write.c, included
// unchanged below, with one function replaced: push2's slow path.
//
// In vgacap, vgacap_trace_push2_slow is two vgacap_trace_push calls, so a
// changed clock goes through push2_slow, then vgacap_trace_slow (and its
// checks) once per changed sample. Here the common case of a changed clock
// is one out-of-line call that does both samples, with every check the two
// pushes make (clock bits, bits above sample_bits, a failed writer, a
// block end), and anything else -- a block end by clocks at sample n or
// n + 1, a block that could end by size inside the pair, sample 0, a bad
// sample, a failed writer -- goes to the original two pushes. So the file
// is the one vgacap writes, byte for byte (test_lean.sh checks it against
// all 11 published traces).
//
// The production symbols are renamed (lean.h), so that this writer and
// vgacap's own link into one binary.
#define _POSIX_C_SOURCE 200809L   // as trace_write.c sets them, before any header
#define ZSTD_STATIC_LINKING_ONLY
#include "lean.h"
#undef vgacap_trace_push2_slow
#define vgacap_trace_push2_slow fs_lean_trace_push2_slow_orig
#include "trace_write.c"
#undef vgacap_trace_push2_slow

int fs_lean_trace_push2_slow(vgacap_trace_writer_t *t, uint64_t v0, uint64_t v1);
int fs_lean_trace_push2_slow(vgacap_trace_writer_t *t, uint64_t v0, uint64_t v1) {
    struct vgacap_trace_impl *I = t->impl;
    const uint64_t n = t->n, mask = t->clk_mask, expect = t->clk_expect;
    const uint64_t a = v0 & ~mask, b = v1 & ~mask;
    // limit - n >= 2: no block end by clocks at n or n + 1, not sample 0
    // (limit 0), not a failed writer (limit == n). The fill test leaves room
    // for two pairs of at most vbytes + 10 bytes each, so neither pair can
    // take the block past fill_limit, where the two pushes would end it.
    if (LIKELY(t->limit - n >= 2 && !((v0 ^ expect) & mask) && !((v1 ^ expect ^ mask) & mask) &&
               !((a | b) & I->bad_bits) && I->blk.fill + 2u * (I->vbytes + 10u) <= I->fill_limit)) {
        uint64_t cur = t->cur, run = t->run;
        if (a != cur) { vgacap_trace_block_pair(&I->blk, cur, I->vbytes, run); cur = a; run = 1; }
        else run++;
        if (b != cur) { vgacap_trace_block_pair(&I->blk, cur, I->vbytes, run); cur = b; run = 1; }
        else run++;
        t->cur = cur; t->run = run; t->n = n + 2;   // clk_expect toggles twice: unchanged
        return 0;
    }
    return fs_lean_trace_push2_slow_orig(t, v0, v1);
}
