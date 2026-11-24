// SPDX-License-Identifier: Apache-2.0
// The trace reader: a trace (zstd seekable, one frame a block) or a plain
// VGCH stream, mmap'ed read-only, read by sample ranges.
//
// Opening a trace. The blocks are the seek table's frames when the table is
// there and agrees with the file: each frame where the table puts it, of
// the compressed size it gives (ZSTD_findFrameCompressedSize), declaring
// the decompressed size it gives, and the table's skippable frame running
// exactly to the end of the file. Otherwise (a trace cut short, or a table
// that does not match) the frames are walked from the start and kept up to
// the first incomplete one, and table_rebuilt is 1. The start of every
// block is decompressed until its BLKH, which gives the block's first
// sample (block 0 also gives the header and META(open)). The last block is
// decompressed whole: its checksum is checked, its runs give the sample
// total and it may hold META(close). In a rebuilt table a last block that
// fails is dropped and the one before it is tried.
//
// Reading [from, to). The block holding `from` is found by binary search on
// the blocks' first samples; it and the blocks after it, while they start
// before `to`, are decompressed whole (zstd checks each frame's checksum)
// and fed to a stream reader made with vgacap_reader_init_header, which
// takes the position from the BLKH silently (a seek reports nothing). Block
// 0 is read with an ordinary reader, since it starts with the VGCH. A plain
// stream is one pseudo-block, fed from the start.
#define _POSIX_C_SOURCE 200809L
#define ZSTD_STATIC_LINKING_ONLY   // ZSTD_isSkippableFrame
#include "vgacap/trace.h"
#include <errno.h>
#include <fcntl.h>
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <sys/stat.h>
#include <unistd.h>
#include <zstd.h>
#include <zstd_seekable.h>

#define FEED_SLICE (1u << 20)        // a read checks for its end this often
#define START_LIMIT (64u * 1024u)    // a block's BLKH must come within this many bytes

struct block {
    size_t off, csize;   // the zstd frame in the file
    uint64_t first;      // its BLKH
};

struct vgacap_trace_reader {
    vgacap_trace_info_t info;
    const uint8_t *map;
    size_t size;
    struct block *blocks;   // compressed only
    uint32_t nblocks, cap;
    ZSTD_DCtx *dctx;
    uint8_t *buf;           // one block, decompressed
    int64_t buf_block;      // which block `buf` holds, or -1
    size_t buf_len;
    char *open_meta, *close_meta;
    uint64_t clk;           // the clock bit's mask, or 0
    vgacap_reader_t sr;
};

static void set_err(char *err, size_t errlen, const char *fmt, ...) {
    if (!err || !errlen) return;
    va_list ap; va_start(ap, fmt);
    vsnprintf(err, errlen, fmt, ap);
    va_end(ap);
}

static char *copy_text(const char *p, size_t n) {
    char *s = malloc(n + 1);
    if (s) { memcpy(s, p, n); s[n] = 0; }
    return s;
}

static int starts_with(const char *p, size_t n, const char *prefix) {
    size_t k = strlen(prefix);
    return n >= k && memcmp(p, prefix, k) == 0;
}

static int add_block(vgacap_trace_reader_t *R, size_t off, size_t csize) {
    if (R->nblocks == R->cap) {
        uint32_t cap = R->cap ? 2 * R->cap : 64;
        struct block *b = realloc(R->blocks, cap * sizeof *b);
        if (!b) return -1;
        R->blocks = b; R->cap = cap;
    }
    R->blocks[R->nblocks].off = off;
    R->blocks[R->nblocks].csize = csize;
    R->blocks[R->nblocks].first = 0;
    R->nblocks++;
    return 0;
}

// A data frame we can hold: complete, of a known size up to a block's.
static int frame_ok(const uint8_t *p, size_t avail, size_t *csize) {
    if (ZSTD_isSkippableFrame(p, avail)) return 0;
    size_t c = ZSTD_findFrameCompressedSize(p, avail);
    if (ZSTD_isError(c)) return 0;
    unsigned long long d = ZSTD_getFrameContentSize(p, c);
    if (d == ZSTD_CONTENTSIZE_UNKNOWN || d == ZSTD_CONTENTSIZE_ERROR || d > VGACAP_TRACE_BLOCK_BYTES) return 0;
    *csize = c;
    return 1;
}

// ---- the seek table, or the frames walked -------------------------------------

// The blocks from the seek table, if it is there and matches the frames.
// Returns 0, or 1 if the table cannot be used (R->nblocks is then 0).
static int blocks_from_table(vgacap_trace_reader_t *R) {
    ZSTD_seekable *zs = ZSTD_seekable_create();
    if (!zs) return 1;
    int bad = ZSTD_isError(ZSTD_seekable_initBuff(zs, R->map, R->size)) ? 1 : 0;
    unsigned n = bad ? 0 : ZSTD_seekable_getNumFrames(zs);
    if (n == 0) bad = 1;
    size_t end = 0;
    for (unsigned i = 0; i < n && !bad; i++) {
        unsigned long long off = ZSTD_seekable_getFrameCompressedOffset(zs, i);
        size_t csize = ZSTD_seekable_getFrameCompressedSize(zs, i), c;
        if (off != end || off >= R->size || !frame_ok(R->map + off, R->size - off, &c) || c != csize ||
            ZSTD_getFrameContentSize(R->map + off, c) != ZSTD_seekable_getFrameDecompressedSize(zs, i) ||
            add_block(R, (size_t)off, c)) {
            bad = 1;
        } else {
            end = (size_t)off + c;
        }
    }
    // What follows the last frame is the table itself, a skippable frame to
    // the end of the file.
    if (!bad && (end >= R->size || !ZSTD_isSkippableFrame(R->map + end, R->size - end) ||
                 ZSTD_findFrameCompressedSize(R->map + end, R->size - end) != R->size - end))
        bad = 1;
    ZSTD_seekable_free(zs);
    if (bad) R->nblocks = 0;
    return bad;
}

// Walks the frames from the start, keeping each complete one, up to the
// first incomplete one, or a skippable frame (the table, or what is left of it).
static int blocks_by_walking(vgacap_trace_reader_t *R) {
    size_t pos = 0, c;
    while (pos < R->size && frame_ok(R->map + pos, R->size - pos, &c)) {
        if (add_block(R, pos, c)) return -1;
        pos += c;
    }
    return 0;
}

// ---- decompressing ----------------------------------------------------------------

static int decode_block(vgacap_trace_reader_t *R, uint32_t i, char *err, size_t errlen) {
    if (R->buf_block == (int64_t)i) return 0;
    if (!R->buf && !(R->buf = malloc(VGACAP_TRACE_BLOCK_BYTES))) { set_err(err, errlen, "out of memory"); return -1; }
    R->buf_block = -1;
    const struct block *b = &R->blocks[i];
    size_t d = ZSTD_decompressDCtx(R->dctx, R->buf, VGACAP_TRACE_BLOCK_BYTES, R->map + b->off, b->csize);
    if (ZSTD_isError(d)) {
        set_err(err, errlen, "block %u (offset %zu): zstd: %s", i, b->off, ZSTD_getErrorName(d));
        return -1;
    }
    R->buf_block = i; R->buf_len = d;
    return 0;
}

// What the start of a block holds, up to its BLKH.
struct start {
    vgacap_trace_reader_t *R;
    int block0, events, got_block, bad;
    uint64_t first;
    const char *what;
};

static void on_start(void *user, const vgacap_event_t *ev) {
    struct start *s = user;
    if (s->got_block || s->bad) return;
    int k = s->events++;
    switch (ev->type) {
    case VGACAP_EV_HEADER:
        if (!s->block0 || k != 0) { s->bad = 1; s->what = "VGCH where the container has none"; break; }
        s->R->info.header = *ev->u.header;
        break;
    case VGACAP_EV_META:
        if (!s->block0 || k != 1 || !starts_with(ev->u.meta.text, ev->u.meta.len, "meta=open\n")) {
            s->bad = 1; s->what = "block 0 must be VGCH, META(open), BLKH(0)"; break;
        }
        if (!(s->R->open_meta = copy_text(ev->u.meta.text, ev->u.meta.len))) { s->bad = 1; s->what = "out of memory"; }
        break;
    case VGACAP_EV_BLOCK:
        if (k != (s->block0 ? 2 : 0)) {
            s->bad = 1; s->what = s->block0 ? "block 0 must be VGCH, META(open), BLKH(0)" : "a block must start with its BLKH";
            break;
        }
        s->got_block = 1; s->first = ev->u.block.first_sample;
        break;
    case VGACAP_EV_RESYNC: s->bad = 1; s->what = ev->u.resync.what; break;
    case VGACAP_EV_ERROR: s->bad = 1; s->what = ev->u.error.what; break;
    default:
        s->bad = 1; s->what = s->block0 ? "block 0 must be VGCH, META(open), BLKH(0)" : "a block must start with its BLKH";
        break;
    }
}

// Decompresses the start of block i until its BLKH. Block 0 also gives the
// header and META(open).
static int read_start(vgacap_trace_reader_t *R, uint32_t i, char *err, size_t errlen) {
    struct block *b = &R->blocks[i];
    struct start s; memset(&s, 0, sizeof s);
    s.R = R; s.block0 = i == 0;
    if (i == 0) vgacap_reader_init(&R->sr, on_start, &s);
    else vgacap_reader_init_header(&R->sr, on_start, &s, &R->info.header);
    ZSTD_DCtx_reset(R->dctx, ZSTD_reset_session_only);
    uint8_t out[4096];
    ZSTD_inBuffer in = { R->map + b->off, b->csize, 0 };
    size_t total = 0;
    while (!s.got_block && !s.bad && total < START_LIMIT) {
        ZSTD_outBuffer ob = { out, sizeof out, 0 };
        size_t rc = ZSTD_decompressStream(R->dctx, &ob, &in);
        if (ZSTD_isError(rc)) {
            set_err(err, errlen, "block %u (offset %zu): zstd: %s", i, b->off, ZSTD_getErrorName(rc));
            return -1;
        }
        vgacap_reader_feed(&R->sr, out, ob.pos);
        total += ob.pos;
        if (rc == 0 || (ob.pos == 0 && in.pos == in.size)) break;   // the frame ended
    }
    if (s.bad || !s.got_block) {
        set_err(err, errlen, "block %u (offset %zu): %s", i, b->off,
                s.bad ? s.what : "no BLKH at its start");
        return -1;
    }
    if (i == 0 && s.first != 0) { set_err(err, errlen, "block 0: BLKH(%llu), not BLKH(0)", (unsigned long long)s.first); return -1; }
    if (i > 0 && s.first <= R->blocks[i - 1].first) {
        set_err(err, errlen, "block %u: BLKH(%llu) is not after block %u's BLKH(%llu)", i,
                (unsigned long long)s.first, i - 1, (unsigned long long)R->blocks[i - 1].first);
        return -1;
    }
    b->first = s.first;
    return 0;
}

// The whole of a stream fed through a reader: the sample total, the META
// texts and the first loss.
struct scan {
    vgacap_trace_reader_t *R;
    uint64_t n;
    int have_header;
    const char *loss, *error;
};

static void on_scan(void *user, const vgacap_event_t *ev) {
    struct scan *s = user;
    switch (ev->type) {
    case VGACAP_EV_HEADER:
        if (!s->have_header) s->R->info.header = *ev->u.header;
        s->have_header = 1; s->n = 0;
        break;
    case VGACAP_EV_BLOCK: s->n = ev->u.block.first_sample; break;
    case VGACAP_EV_RUN: s->n += ev->u.run.run; break;
    case VGACAP_EV_META: {
        char **dst = NULL;
        if (starts_with(ev->u.meta.text, ev->u.meta.len, "meta=open\n") && !s->R->open_meta) dst = &s->R->open_meta;
        if (starts_with(ev->u.meta.text, ev->u.meta.len, "meta=close\n")) dst = &s->R->close_meta;
        if (dst) {
            free(*dst);
            if (!(*dst = copy_text(ev->u.meta.text, ev->u.meta.len)) && !s->error) s->error = "out of memory";
        }
        break;
    }
    case VGACAP_EV_RESYNC: if (!s->loss) s->loss = ev->u.resync.what; break;
    case VGACAP_EV_ERROR: if (!s->error) s->error = ev->u.error.what; break;
    default: break;
    }
}

// The last block, whole: its checksum, its sample total and META(close).
static int read_last(vgacap_trace_reader_t *R, char *err, size_t errlen) {
    uint32_t i = R->nblocks - 1;
    if (decode_block(R, i, err, errlen)) return -1;
    struct scan s; memset(&s, 0, sizeof s);
    s.R = R; s.have_header = 1;
    if (i == 0) vgacap_reader_init(&R->sr, on_scan, &s);
    else vgacap_reader_init_header(&R->sr, on_scan, &s, &R->info.header);
    vgacap_reader_feed(&R->sr, R->buf, R->buf_len);
    if (s.error || s.loss || R->sr.scanning) {
        set_err(err, errlen, "block %u: %s", i, s.error ? s.error : s.loss ? s.loss : "ends inside a chunk");
        free(R->close_meta); R->close_meta = NULL;
        return -1;
    }
    R->info.samples = s.n;
    return 0;
}

static int open_trace(vgacap_trace_reader_t *R, char *err, size_t errlen) {
    if (!(R->dctx = ZSTD_createDCtx())) { set_err(err, errlen, "out of memory"); return -1; }
    R->info.compressed = 1;
    if (blocks_from_table(R)) {
        R->info.table_rebuilt = 1;
        if (blocks_by_walking(R)) { set_err(err, errlen, "out of memory"); return -1; }
    }
    if (R->nblocks == 0) { set_err(err, errlen, "no complete block (the trace was cut short inside block 0)"); return -1; }
    for (uint32_t i = 0; i < R->nblocks; i++)
        if (read_start(R, i, err, errlen)) return -1;
    // A rebuilt table's last frame may be whole in shape but bad inside
    // (its checksum, a cut that left a valid-looking frame): drop it.
    while (read_last(R, err, errlen)) {
        if (!R->info.table_rebuilt || R->nblocks == 1) return -1;
        R->nblocks--;
    }
    R->info.blocks = R->nblocks;
    return 0;
}

static int open_plain(vgacap_trace_reader_t *R, char *err, size_t errlen) {
    struct scan s; memset(&s, 0, sizeof s);
    s.R = R;
    vgacap_reader_init(&R->sr, on_scan, &s);
    vgacap_reader_feed(&R->sr, R->map, R->size);
    if (s.error) { set_err(err, errlen, "%s", s.error); return -1; }
    if (!s.have_header) { set_err(err, errlen, "no VGCH header"); return -1; }
    R->info.samples = s.n;
    R->info.blocks = 1;
    return 0;
}

vgacap_trace_reader_t *vgacap_trace_open(const char *path, char *err, size_t errlen) {
    vgacap_trace_reader_t *R = calloc(1, sizeof *R);
    if (!R) { set_err(err, errlen, "out of memory"); return NULL; }
    R->buf_block = -1;
    int fd = open(path, O_RDONLY);
    if (fd < 0) { set_err(err, errlen, "%s: %s", path, strerror(errno)); free(R); return NULL; }
    struct stat st;
    if (fstat(fd, &st) != 0) { set_err(err, errlen, "%s: %s", path, strerror(errno)); close(fd); free(R); return NULL; }
    if (st.st_size < 8) {
        set_err(err, errlen, "%s: neither a trace nor a VGCH stream (%lld bytes)", path, (long long)st.st_size);
        close(fd); free(R); return NULL;
    }
    R->size = (size_t)st.st_size;
    void *m = mmap(NULL, R->size, PROT_READ, MAP_PRIVATE, fd, 0);
    close(fd);
    if (m == MAP_FAILED) { set_err(err, errlen, "%s: mmap: %s", path, strerror(errno)); free(R); return NULL; }
    R->map = m;
    static const uint8_t zstd_magic[4] = { 0x28, 0xb5, 0x2f, 0xfd };
    char msg[256] = "";
    int rc;
    if (memcmp(R->map, zstd_magic, 4) == 0) rc = open_trace(R, msg, sizeof msg);
    else if (memcmp(R->map, VGACAP_TAG_HEADER, 4) == 0) rc = open_plain(R, msg, sizeof msg);
    else { snprintf(msg, sizeof msg, "neither a trace nor a VGCH stream"); rc = -1; }
    if (rc) {
        set_err(err, errlen, "%s: %s", path, msg);
        vgacap_trace_close(R);
        return NULL;
    }
    R->info.open_meta = R->open_meta;
    R->info.close_meta = R->close_meta;
    R->clk = R->info.header.clock_bit == VGACAP_CLOCK_BIT_NONE ? 0 : (uint64_t)1 << R->info.header.clock_bit;
    return R;
}

const vgacap_trace_info_t *vgacap_trace_info(const vgacap_trace_reader_t *r) { return &r->info; }

// ---- reading ranges -----------------------------------------------------------------

struct rd {
    uint64_t n, from, to, clk;
    vgacap_trace_run_fn fn;
    void *user;
    int done, strict;
    const char *what;
};

static void on_read(void *user, const vgacap_event_t *ev) {
    struct rd *d = user;
    if (d->done) return;
    switch (ev->type) {
    case VGACAP_EV_HEADER: d->n = 0; break;
    case VGACAP_EV_BLOCK: d->n = ev->u.block.first_sample; break;
    case VGACAP_EV_RUN: {
        uint64_t a = d->n, e = d->n + ev->u.run.run;
        d->n = e;
        if (e <= d->from) break;
        if (a >= d->to) { d->done = 1; break; }
        uint64_t s = a > d->from ? a : d->from, t = e < d->to ? e : d->to;
        uint64_t v = ev->u.run.value ^ (((s - a) & 1) ? d->clk : 0);
        if (d->fn(d->user, s, v, t - s) || e >= d->to) d->done = 1;
        break;
    }
    case VGACAP_EV_RESYNC:
        if (d->strict) { d->what = ev->u.resync.what; d->done = 1; }
        break;
    case VGACAP_EV_ERROR: d->what = ev->u.error.what; d->done = 1; break;
    default: break;
    }
}

static void feed_until_done(vgacap_reader_t *sr, struct rd *d, const uint8_t *p, size_t n) {
    for (size_t pos = 0; pos < n && !d->done; pos += FEED_SLICE) {
        size_t k = n - pos < FEED_SLICE ? n - pos : FEED_SLICE;
        if (vgacap_reader_feed(sr, p + pos, k)) break;
    }
}

int vgacap_trace_read(vgacap_trace_reader_t *R, uint64_t from, uint64_t to,
                      vgacap_trace_run_fn fn, void *user, char *err, size_t errlen) {
    if (from >= to || from >= R->info.samples) return 0;
    struct rd d; memset(&d, 0, sizeof d);
    d.from = from; d.to = to; d.clk = R->clk; d.fn = fn; d.user = user;
    if (!R->info.compressed) {
        vgacap_reader_init(&R->sr, on_read, &d);
        feed_until_done(&R->sr, &d, R->map, R->size);
        if (d.what) { set_err(err, errlen, "%s", d.what); return -1; }
        return 0;
    }
    d.strict = 1;
    // The last block whose first sample is at or before `from`.
    uint32_t lo = 0, hi = R->nblocks;   // blocks[lo].first <= from < blocks[hi].first
    while (hi - lo > 1) {
        uint32_t mid = lo + (hi - lo) / 2;
        if (R->blocks[mid].first <= from) lo = mid; else hi = mid;
    }
    for (uint32_t i = lo; i < R->nblocks && R->blocks[i].first < to && !d.done; i++) {
        if (decode_block(R, i, err, errlen)) return -1;
        if (i == 0) vgacap_reader_init(&R->sr, on_read, &d);
        else vgacap_reader_init_header(&R->sr, on_read, &d, &R->info.header);
        feed_until_done(&R->sr, &d, R->buf, R->buf_len);
        if (d.what) { set_err(err, errlen, "block %u: %s", i, d.what); return -1; }
        if (!d.done && R->sr.scanning) { set_err(err, errlen, "block %u: ends inside a chunk", i); return -1; }
        if (!d.done && i + 1 < R->nblocks && d.n != R->blocks[i + 1].first) {
            set_err(err, errlen, "block %u ends at sample %llu but block %u starts at %llu", i,
                    (unsigned long long)d.n, i + 1, (unsigned long long)R->blocks[i + 1].first);
            return -1;
        }
    }
    return 0;
}

int vgacap_trace_block_data(vgacap_trace_reader_t *R, uint32_t i, const uint8_t **data, size_t *len,
                            char *err, size_t errlen) {
    if (i >= R->info.blocks) { set_err(err, errlen, "block %u: the file has %u", i, R->info.blocks); return -1; }
    if (!R->info.compressed) { *data = R->map; *len = R->size; return 0; }
    if (decode_block(R, i, err, errlen)) return -1;
    *data = R->buf; *len = R->buf_len;
    return 0;
}

void vgacap_trace_close(vgacap_trace_reader_t *R) {
    if (!R) return;
    if (R->map) munmap((void *)(uintptr_t)R->map, R->size);
    ZSTD_freeDCtx(R->dctx);
    free(R->blocks); free(R->buf);
    free(R->open_meta); free(R->close_meta);
    free(R);
}
