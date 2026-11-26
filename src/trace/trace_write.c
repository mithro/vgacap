// SPDX-License-Identifier: Apache-2.0
// The trace writer: samples to RUNS pairs in a block buffer (on the
// caller's thread), blocks to zstd frames in a file (on a compressor thread).
//
// Thread handoff. There are two block buffers. The caller fills one; when
// the block closes it marks that buffer FULL, signals the compressor, and
// moves to the other buffer, waiting on `cv_free` while the compressor still
// holds it. The compressor takes the buffers in the same alternating order,
// so blocks reach the file in order: it waits on `cv_full` for its next
// buffer, compresses it with ZSTD_compress2, writes the frame, logs it in
// the seek table's frame log and marks the buffer free. Nothing is dropped:
// a fast caller waits. An I/O error is recorded under the mutex and seen by
// the caller at its next block handoff (or at finish).
#define _POSIX_C_SOURCE 200809L
#define ZSTD_STATIC_LINKING_ONLY
#include "vgacap/trace.h"
#include "blocks.h"
#include <errno.h>
#include <pthread.h>
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <zstd.h>
#include <zstd_seekable.h>

#define FILE_BUFFER (1u << 20)

struct vgacap_trace_impl {
    FILE *f;
    char *filebuf;
    vgacap_writer_t w;            // writes into `blk`
    vgacap_trace_block_t blk;     // the block being filled, in bufs[cur]
    uint8_t *bufs[2];
    int cur;
    unsigned vbytes;
    size_t fill_limit;            // a block closes once its fill passes this
    uint64_t block_samples;       // VGACAP_TRACE_BLOCK_CLOCKS * samples_per_clock
    uint64_t bad_bits;            // bits at or above sample_bits
    int failed;
    char err[256];
    // compressor thread; everything below is guarded by `mu`
    pthread_t thread;
    int thread_started;
    pthread_mutex_t mu;
    pthread_cond_t cv_full, cv_free;
    int full[2];
    size_t len[2];
    int done;
    int io_failed;
    char io_err[256];
    // owned by the thread while it runs, then by finish
    ZSTD_CCtx *cctx;
    ZSTD_frameLog *frames;
    uint8_t *zbuf;
    size_t zcap;
};

static void set_err(char *err, size_t errlen, const char *msg) {
    if (err && errlen) snprintf(err, errlen, "%s", msg);
}

static uint32_t le32(const uint8_t *p) {
    return (uint32_t)p[0] | ((uint32_t)p[1] << 8) | ((uint32_t)p[2] << 16) | ((uint32_t)p[3] << 24);
}

// ---- compressor thread -----------------------------------------------------

static int compress_one(struct vgacap_trace_impl *I, const uint8_t *src, size_t n, char *msg, size_t msglen) {
    size_t c = ZSTD_compress2(I->cctx, I->zbuf, I->zcap, src, n);
    if (ZSTD_isError(c)) { snprintf(msg, msglen, "zstd: %s", ZSTD_getErrorName(c)); return -1; }
    if (fwrite(I->zbuf, 1, c, I->f) != c) { snprintf(msg, msglen, "write: %s", strerror(errno)); return -1; }
    // Push the frame out of the stdio buffer now: a killed writer keeps every
    // block it finished, and this runs on the compressor thread, off the push path.
    if (fflush(I->f) != 0) { snprintf(msg, msglen, "write: %s", strerror(errno)); return -1; }
    // The frame's own checksum (the low 32 bits of XXH64 of the block) is
    // the value the seek table records for it.
    size_t lr = ZSTD_seekable_logFrame(I->frames, (unsigned)c, (unsigned)n, le32(I->zbuf + c - 4));
    if (ZSTD_isError(lr)) { snprintf(msg, msglen, "seek table: %s", ZSTD_getErrorName(lr)); return -1; }
    return 0;
}

static void *compressor(void *arg) {
    struct vgacap_trace_impl *I = arg;
    char msg[256];
    int broken = 0;
    for (int j = 0;; j ^= 1) {
        pthread_mutex_lock(&I->mu);
        while (!I->full[j] && !I->done) pthread_cond_wait(&I->cv_full, &I->mu);
        if (!I->full[j]) { pthread_mutex_unlock(&I->mu); break; }
        size_t n = I->len[j];
        pthread_mutex_unlock(&I->mu);

        int bad = broken ? 0 : compress_one(I, I->bufs[j], n, msg, sizeof msg);

        pthread_mutex_lock(&I->mu);
        if (bad) { broken = 1; I->io_failed = 1; snprintf(I->io_err, sizeof I->io_err, "%s", msg); }
        I->full[j] = 0;
        pthread_cond_signal(&I->cv_free);
        pthread_mutex_unlock(&I->mu);
    }
    return NULL;
}

// ---- caller side -------------------------------------------------------------

static int fail(vgacap_trace_writer_t *t, const char *fmt, ...) {
    struct vgacap_trace_impl *I = t->impl;
    va_list ap; va_start(ap, fmt);
    vsnprintf(I->err, sizeof I->err, fmt, ap);
    va_end(ap);
    I->failed = 1;
    t->limit = t->n;   // every later push takes the slow path, which returns -1
    return -1;
}

// Hands the current block, complete, to the compressor and switches to the
// other buffer.
static int hand_off(vgacap_trace_writer_t *t) {
    struct vgacap_trace_impl *I = t->impl;
    pthread_mutex_lock(&I->mu);
    I->len[I->cur] = I->blk.fill;
    I->full[I->cur] = 1;
    pthread_cond_signal(&I->cv_full);
    I->cur ^= 1;
    while (I->full[I->cur]) pthread_cond_wait(&I->cv_free, &I->mu);
    int io = I->io_failed;
    pthread_mutex_unlock(&I->mu);
    if (io) return fail(t, "%s", I->io_err);
    I->blk.buf = I->bufs[I->cur];
    I->blk.fill = 0;
    return 0;
}

// Closes the block at sample t->n and opens the next, starting there.
static int next_block(vgacap_trace_writer_t *t) {
    struct vgacap_trace_impl *I = t->impl;
    vgacap_trace_block_end_runs(&I->blk);
    if (hand_off(t)) return -1;
    if (vgacap_trace_block_begin(&I->blk, &I->w, t->n)) return fail(t, "sample %llu: cannot start a block",
                                                                     (unsigned long long)t->n);
    return 0;
}

#if defined(__GNUC__)
#define LIKELY(x) __builtin_expect(!!(x), 1)
#define NOINLINE __attribute__((noinline))
#else
#define LIKELY(x) (x)
#define NOINLINE
#endif

// Everything but an ordinary run end: sample 0, a block end, a failure.
static NOINLINE int slow_rare(vgacap_trace_writer_t *t, uint64_t v) {
    struct vgacap_trace_impl *I = t->impl;
    if (t->n == t->limit && I->failed) return -1;   // a failed writer keeps limit == n
    if ((v ^ t->clk_expect) & t->clk_mask)
        return fail(t, "sample %llu: clock bit is %u, expected %u", (unsigned long long)t->n,
                    (v & t->clk_mask) ? 1u : 0u, t->clk_expect ? 1u : 0u);
    uint64_t value = v & ~t->clk_mask;
    if (value & I->bad_bits)
        return fail(t, "sample %llu: value 0x%llx has bits at or above sample_bits", (unsigned long long)t->n,
                    (unsigned long long)v);
    if (t->n == t->limit) {
        // A block end by clock count (or sample 0): split the run here.
        if (t->run) {
            vgacap_trace_block_pair(&I->blk, t->cur, I->vbytes, t->run);
            if (next_block(t)) return -1;
        }
        t->limit = t->n + I->block_samples;
        t->run = 0;
    } else if (value != t->cur) {
        vgacap_trace_block_pair(&I->blk, t->cur, I->vbytes, t->run);
        t->run = 0;
        if (I->blk.fill > I->fill_limit) {   // no room for another pair: a block end by size
            if (next_block(t)) return -1;
            t->limit = t->n + I->block_samples;
        }
    }
    if (t->run == 0) t->cur = value;
    t->run++; t->n++; t->clk_expect ^= t->clk_mask;
    return 0;
}

// A run has just ended at sample t->n and left no room for another pair:
// the block ends there, and sample t->n, `value`, starts the next.
static NOINLINE int block_full(vgacap_trace_writer_t *t, uint64_t value) {
    if (next_block(t)) return -1;
    t->limit = t->n + t->impl->block_samples;
    t->cur = value; t->run = 1; t->n++; t->clk_expect ^= t->clk_mask;
    return 0;
}

// The push's slow path. Its common case, a run ending inside a block (one
// call a clock for the busiest designs), is kept short; the rest is
// slow_rare.
int vgacap_trace_slow(vgacap_trace_writer_t *t, uint64_t v) {
    struct vgacap_trace_impl *I = t->impl;
    uint64_t n = t->n, mask = t->clk_mask, expect = t->clk_expect, value = v & ~mask;
    if (LIKELY(n != t->limit && !((v ^ expect) & mask) && !(value & I->bad_bits))) {
        // Here the value differs from t->cur: nothing else sends a sample
        // inside a block to the slow path.
        vgacap_trace_block_pair(&I->blk, t->cur, I->vbytes, t->run);
        if (I->blk.fill > I->fill_limit) return block_full(t, value);
        t->cur = value; t->run = 1; t->n = n + 1; t->clk_expect = expect ^ mask;
        return 0;
    }
    return slow_rare(t, v);
}

// repeat2's slow path: its two pushes, written out. The last sample is the
// current run's value with the clock bit before clk_expect's.
int vgacap_trace_repeat2_slow(vgacap_trace_writer_t *t) {
    if (t->impl->failed) return -1;
    if (t->run == 0)   // only before the first sample
        return fail(t, "sample %llu: vgacap_trace_repeat2 before the first sample", (unsigned long long)t->n);
    uint64_t last = t->cur | (t->clk_expect ^ t->clk_mask);
    if (vgacap_trace_push(t, last ^ t->clk_mask)) return -1;
    return vgacap_trace_push(t, last);
}

// push2's slow path: its two pushes. A failed push leaves the writer as it
// was but failed, so the second, which would return -1, is skipped.
int vgacap_trace_push2_slow(vgacap_trace_writer_t *t, uint64_t v0, uint64_t v1) {
    if (vgacap_trace_push(t, v0)) return -1;
    return vgacap_trace_push(t, v1);
}

// Stops the thread (after it has written every block handed to it), closes
// the file and frees everything. Returns -1 if closing the file failed.
static int teardown(vgacap_trace_writer_t *t, int write_seek_table, char *msg, size_t msglen) {
    struct vgacap_trace_impl *I = t->impl;
    int rc = 0;
    if (I->thread_started) {
        pthread_mutex_lock(&I->mu);
        I->done = 1;
        pthread_cond_signal(&I->cv_full);
        pthread_mutex_unlock(&I->mu);
        pthread_join(I->thread, NULL);
        pthread_mutex_destroy(&I->mu);
        pthread_cond_destroy(&I->cv_full);
        pthread_cond_destroy(&I->cv_free);
        if (I->io_failed) { snprintf(msg, msglen, "%s", I->io_err); rc = -1; }
    }
    if (write_seek_table && rc == 0) {
        uint8_t out[4096];
        for (;;) {
            ZSTD_outBuffer ob = { out, sizeof out, 0 };
            size_t left = ZSTD_seekable_writeSeekTable(I->frames, &ob);
            if (ZSTD_isError(left)) { snprintf(msg, msglen, "seek table: %s", ZSTD_getErrorName(left)); rc = -1; break; }
            if (fwrite(out, 1, ob.pos, I->f) != ob.pos) {
                snprintf(msg, msglen, "write: %s", strerror(errno)); rc = -1; break;
            }
            if (left == 0) break;
        }
    }
    if (I->f && fclose(I->f) != 0 && rc == 0) { snprintf(msg, msglen, "close: %s", strerror(errno)); rc = -1; }
    free(I->filebuf);
    ZSTD_freeCCtx(I->cctx);
    if (I->frames) ZSTD_seekable_freeFrameLog(I->frames);
    free(I->zbuf);
    free(I->bufs[0]); free(I->bufs[1]);
    free(I);
    free(t);
    return rc;
}

vgacap_trace_writer_t *vgacap_trace_create(const char *path, const vgacap_header_t *h,
                                           const char *open_meta, int zstd_level,
                                           char *err, size_t errlen) {
    char msg[256];
    if (vgacap_trace_check(h, open_meta, 1, msg, sizeof msg)) { set_err(err, errlen, msg); return NULL; }
    vgacap_trace_writer_t *t = calloc(1, sizeof *t);
    struct vgacap_trace_impl *I = calloc(1, sizeof *I);
    if (!t || !I) { free(t); free(I); set_err(err, errlen, "out of memory"); return NULL; }
    t->impl = I;
    I->bufs[0] = malloc(VGACAP_TRACE_BLOCK_BYTES);
    I->bufs[1] = malloc(VGACAP_TRACE_BLOCK_BYTES);
    I->zcap = ZSTD_compressBound(VGACAP_TRACE_BLOCK_BYTES);
    I->zbuf = malloc(I->zcap);
    I->filebuf = malloc(FILE_BUFFER);
    I->cctx = ZSTD_createCCtx();
    I->frames = ZSTD_seekable_createFrameLog(1);
    if (!I->bufs[0] || !I->bufs[1] || !I->zbuf || !I->filebuf || !I->cctx || !I->frames) {
        teardown(t, 0, msg, sizeof msg); set_err(err, errlen, "out of memory"); return NULL;
    }
    if (ZSTD_isError(ZSTD_CCtx_setParameter(I->cctx, ZSTD_c_compressionLevel, zstd_level)) ||
        ZSTD_isError(ZSTD_CCtx_setParameter(I->cctx, ZSTD_c_checksumFlag, 1))) {
        teardown(t, 0, msg, sizeof msg); set_err(err, errlen, "bad zstd level"); return NULL;
    }
    I->f = fopen(path, "wb");
    if (!I->f) {
        snprintf(msg, sizeof msg, "%s: %s", path, strerror(errno));
        set_err(err, errlen, msg); teardown(t, 0, msg, sizeof msg); return NULL;
    }
    setvbuf(I->f, I->filebuf, _IOFBF, FILE_BUFFER);

    I->vbytes = (h->sample_bits + 7u) / 8u;
    I->fill_limit = VGACAP_TRACE_BLOCK_BYTES - VGACAP_TRACE_BLOCK_RESERVE - (I->vbytes + 10u);
    I->block_samples = VGACAP_TRACE_BLOCK_CLOCKS * h->samples_per_clock;
    I->bad_bits = h->sample_bits >= 64 ? 0 : ~(((uint64_t)1 << h->sample_bits) - 1u);
    if (h->clock_bit != VGACAP_CLOCK_BIT_NONE) {
        t->clk_mask = (uint64_t)1 << h->clock_bit;
        t->clk_expect = h->clock_first ? t->clk_mask : 0;
    }
    // Block 0: VGCH, META(open), BLKH(0), RUNS.
    I->blk.buf = I->bufs[0];
    if (vgacap_writer_init(&I->w, vgacap_trace_block_append, &I->blk, h) ||
        vgacap_writer_meta(&I->w, open_meta, strlen(open_meta)) ||
        vgacap_trace_block_begin(&I->blk, &I->w, 0)) {
        teardown(t, 0, msg, sizeof msg); set_err(err, errlen, "cannot start block 0"); return NULL;
    }
    if (pthread_mutex_init(&I->mu, NULL) || pthread_cond_init(&I->cv_full, NULL) ||
        pthread_cond_init(&I->cv_free, NULL) || pthread_create(&I->thread, NULL, compressor, I)) {
        teardown(t, 0, msg, sizeof msg); set_err(err, errlen, "cannot start the compressor thread"); return NULL;
    }
    I->thread_started = 1;
    return t;
}

int vgacap_trace_finish(vgacap_trace_writer_t *t, const char *close_meta, char *err, size_t errlen) {
    struct vgacap_trace_impl *I = t->impl;
    char msg[256];
    if (!I->failed && vgacap_trace_check(&I->w.header, close_meta, 0, msg, sizeof msg)) {
        fail(t, "%s", msg);
    }
    if (!I->failed) {
        // The last run, then META(close) in the block's reserve.
        if (t->run) vgacap_trace_block_pair(&I->blk, t->cur, I->vbytes, t->run);
        t->run = 0;
        vgacap_trace_block_end_runs(&I->blk);
        if (vgacap_writer_meta(&I->w, close_meta, strlen(close_meta))) fail(t, "cannot write META(close)");
        else hand_off(t);
    }
    if (I->failed) {
        set_err(err, errlen, I->err);
        teardown(t, 0, msg, sizeof msg);
        return -1;
    }
    if (teardown(t, 1, msg, sizeof msg)) { set_err(err, errlen, msg); return -1; }
    return 0;
}

void vgacap_trace_abandon(vgacap_trace_writer_t *t) {
    char msg[256];
    teardown(t, 0, msg, sizeof msg);
}
