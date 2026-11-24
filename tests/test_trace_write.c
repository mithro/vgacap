// SPDX-License-Identifier: Apache-2.0
// The trace writer (src/trace): a zstd seekable file of VGCH v2 blocks.
// Every file goes under tmp/ in the working directory (the build directory
// under CTest). Each is read back with a reference reader: every zstd frame
// decompressed whole with ZSTD_decompress and fed to vgacap_reader_feed.
//
// With no argument the functional tests run; with --speed only
// test_trace_push_speed (CTest label `perf`, kept out of CI).
#define _POSIX_C_SOURCE 200809L   // clock_gettime
#define ZSTD_STATIC_LINKING_ONLY  // ZSTD_isSkippableFrame
#include "harness.h"
#include "vgacap/trace.h"
#include <errno.h>
#include <stdint.h>
#include <sys/stat.h>
#include <time.h>
#include <zstd.h>
#include <zstd_seekable.h>

#define OPEN_META "meta=open\nproject=tt_um_test\ncalib_seconds=0.5\nseconds=2\nlayout=tt-um-43\n"
#define CLOSE_META "meta=close\ncapture_after_clock=0\nstatus=ok\nframes=0\n"
#define MAX_BLOCKS 1024
#define MAX_RUNS 4096
#define CLK42 ((uint64_t)1 << 42)
#define BASE43 ((uint64_t)3 << 40)   // rst_n and ena high

static void tmp_path(char *out, size_t n, const char *name) {
    if (mkdir("tmp", 0777) != 0 && errno != EEXIST) { perror("mkdir tmp"); exit(2); }
    snprintf(out, n, "tmp/%s", name);
}

static void header43(vgacap_header_t *h) {
    vgacap_header_init_v2(h, 43, 2, 42, 0);
    h->mode = VGACAP_MODE_EVENT; h->clock_hz = 25175000;
}

// What reading a trace back found.
struct readback {
    // per zstd data frame (block)
    unsigned nblocks;
    size_t block_bytes[MAX_BLOCKS];
    uint32_t max_chunk_len;
    char first_tag[MAX_BLOCKS][5], last_tag[MAX_BLOCKS][5];
    unsigned skippable_frames;
    // reader events
    int have_header;
    uint64_t samples;
    unsigned nblkh;
    uint64_t blkh_first[MAX_BLOCKS];
    uint64_t pairs_in_block[MAX_BLOCKS];   // run events after each BLKH
    uint64_t last_run_in_block[MAX_BLOCKS];
    uint64_t block_samples[MAX_BLOCKS];
    unsigned nruns;                        // first MAX_RUNS runs are kept
    uint64_t run_value[MAX_RUNS], run_len[MAX_RUNS];
    unsigned nmeta;
    char meta_first[64], meta_last[64];
    int meta_before_blkh;                  // a META arrived before any BLKH
    unsigned resyncs, errors;
    const char *what;
    size_t stream_bytes;
};

static void on_event(void *user, const vgacap_event_t *ev) {
    struct readback *rb = user;
    switch (ev->type) {
    case VGACAP_EV_HEADER: rb->have_header = 1; break;
    case VGACAP_EV_RUN:
        if (rb->nruns < MAX_RUNS) { rb->run_value[rb->nruns] = ev->u.run.value; rb->run_len[rb->nruns] = ev->u.run.run; }
        rb->nruns++;
        rb->samples += ev->u.run.run;
        if (rb->nblkh) {
            rb->pairs_in_block[rb->nblkh - 1]++;
            rb->last_run_in_block[rb->nblkh - 1] = ev->u.run.run;
            rb->block_samples[rb->nblkh - 1] += ev->u.run.run;
        }
        break;
    case VGACAP_EV_BLOCK:
        if (rb->nblkh < MAX_BLOCKS) rb->blkh_first[rb->nblkh++] = ev->u.block.first_sample;
        break;
    case VGACAP_EV_META: {
        size_t n = ev->u.meta.len < 63 ? ev->u.meta.len : 63;
        if (rb->nmeta == 0) { memcpy(rb->meta_first, ev->u.meta.text, n); rb->meta_first[n] = 0; }
        memcpy(rb->meta_last, ev->u.meta.text, n); rb->meta_last[n] = 0;
        if (rb->nblkh == 0) rb->meta_before_blkh = 1;
        rb->nmeta++;
        break;
    }
    case VGACAP_EV_RESYNC: rb->resyncs++; rb->what = ev->u.resync.what; break;
    case VGACAP_EV_ERROR: rb->errors++; rb->what = ev->u.error.what; break;
    default: break;
    }
}

static uint32_t le32(const uint8_t *p) {
    return (uint32_t)p[0] | ((uint32_t)p[1] << 8) | ((uint32_t)p[2] << 16) | ((uint32_t)p[3] << 24);
}

static uint8_t *slurp(const char *path, size_t *len) {
    FILE *f = fopen(path, "rb");
    if (!f) { perror(path); return NULL; }
    fseek(f, 0, SEEK_END); long n = ftell(f); fseek(f, 0, SEEK_SET);
    uint8_t *b = malloc((size_t)n + 1);
    if (b && fread(b, 1, (size_t)n, f) != (size_t)n) { free(b); b = NULL; }
    fclose(f);
    *len = (size_t)n;
    return b;
}

// Reads `path` back; returns 0 if every frame decompressed.
static int read_back(const char *path, struct readback *rb) {
    memset(rb, 0, sizeof *rb);
    size_t len; uint8_t *file = slurp(path, &len);
    if (!file) return -1;
    vgacap_reader_t *r = malloc(sizeof *r);
    uint8_t *out = malloc(VGACAP_TRACE_BLOCK_BYTES + 1);
    if (!r || !out) return -1;
    vgacap_reader_init(r, on_event, rb);
    size_t pos = 0; int rc = 0;
    while (pos < len) {
        size_t fsz = ZSTD_findFrameCompressedSize(file + pos, len - pos);
        if (ZSTD_isError(fsz)) { fprintf(stderr, "  bad frame at %zu: %s\n", pos, ZSTD_getErrorName(fsz)); rc = -1; break; }
        if (ZSTD_isSkippableFrame(file + pos, len - pos)) { rb->skippable_frames++; pos += fsz; continue; }
        size_t d = ZSTD_decompress(out, VGACAP_TRACE_BLOCK_BYTES + 1, file + pos, fsz);
        if (ZSTD_isError(d)) { fprintf(stderr, "  decompress: %s\n", ZSTD_getErrorName(d)); rc = -1; break; }
        if (rb->nblocks < MAX_BLOCKS) {
            unsigned b = rb->nblocks;
            rb->block_bytes[b] = d;
            for (size_t c = 0; c + 8 <= d;) {
                uint32_t cl = le32(out + c + 4);
                if (cl > rb->max_chunk_len) rb->max_chunk_len = cl;
                if (c == 0) { memcpy(rb->first_tag[b], out, 4); rb->first_tag[b][4] = 0; }
                memcpy(rb->last_tag[b], out + c, 4); rb->last_tag[b][4] = 0;
                c += 8u + cl;
            }
        }
        rb->nblocks++;
        rb->stream_bytes += d;
        vgacap_reader_feed(r, out, d);
        pos += fsz;
    }
    free(r); free(out); free(file);
    return rc;
}

static void trace_rm(const char *path) { remove(path); }

TEST(test_trace_small) {
    char path[256]; tmp_path(path, sizeof path, "trace_small.vgct.zst");
    vgacap_header_t h; header43(&h);
    char err[256] = "";
    vgacap_trace_writer_t *t = vgacap_trace_create(path, &h, OPEN_META, 3, err, sizeof err);
    if (!t) fprintf(stderr, "  create: %s\n", err);
    ASSERT_TRUE(t != NULL);
    static uint64_t want[2000];
    for (unsigned c = 0; c < 1000; c++) {
        uint64_t uo = (c / 7u) & 0xFFu;
        for (unsigned half = 0; half < 2; half++) {
            uint64_t v = BASE43 | uo | (half ? CLK42 : 0);
            want[2 * c + half] = v;
            ASSERT_EQ_U(vgacap_trace_push(t, v), 0);
        }
    }
    ASSERT_EQ_U(vgacap_trace_finish(t, CLOSE_META, err, sizeof err), 0);

    struct readback *rb = calloc(1, sizeof *rb);
    ASSERT_TRUE(read_back(path, rb) == 0);
    ASSERT_EQ_U(rb->resyncs + rb->errors, 0);
    ASSERT_TRUE(rb->have_header);
    ASSERT_EQ_U(rb->nblocks, 1);
    ASSERT_EQ_U(rb->skippable_frames, 1);
    ASSERT_EQ_U(rb->samples, 2000);
    ASSERT_EQ_U(rb->nmeta, 2);
    ASSERT_TRUE(rb->meta_before_blkh);
    ASSERT_TRUE(strncmp(rb->meta_first, "meta=open\n", 10) == 0);
    ASSERT_TRUE(strncmp(rb->meta_last, "meta=close\n", 11) == 0);
    ASSERT_TRUE(strcmp(rb->first_tag[0], "VGCH") == 0);
    ASSERT_TRUE(strcmp(rb->last_tag[0], "META") == 0);
    ASSERT_EQ_U(rb->nblkh, 1);
    ASSERT_EQ_U(rb->blkh_first[0], 0);
    // Expand the runs, toggling the clock bit after each run's first sample.
    uint64_t s = 0;
    for (unsigned i = 0; i < rb->nruns; i++) {
        uint64_t v = rb->run_value[i];
        for (uint64_t k = 0; k < rb->run_len[i]; k++, s++) {
            ASSERT_TRUE(s < 2000);
            if (want[s] != v) fprintf(stderr, "  sample %llu: %llx, want %llx\n",
                                      (unsigned long long)s, (unsigned long long)v, (unsigned long long)want[s]);
            ASSERT_EQ_U(v, want[s]);
            v ^= CLK42;
        }
    }
    ASSERT_EQ_U(s, 2000);
    free(rb);
    trace_rm(path);
}

TEST(test_trace_blocks_by_clock) {
    char path[256]; tmp_path(path, sizeof path, "trace_blocks_by_clock.vgct.zst");
    vgacap_header_t h; header43(&h);
    char err[256] = "";
    vgacap_trace_writer_t *t = vgacap_trace_create(path, &h, OPEN_META, 3, err, sizeof err);
    ASSERT_TRUE(t != NULL);
    uint64_t clocks = 3 * VGACAP_TRACE_BLOCK_CLOCKS + 5;
    int bad = 0;
    for (uint64_t c = 0; c < clocks; c++) {
        bad |= vgacap_trace_push(t, BASE43 | 0x42);
        bad |= vgacap_trace_push(t, BASE43 | 0x42 | CLK42);
    }
    ASSERT_EQ_U(bad, 0);
    ASSERT_EQ_U(vgacap_trace_finish(t, CLOSE_META, err, sizeof err), 0);

    struct readback *rb = calloc(1, sizeof *rb);
    ASSERT_TRUE(read_back(path, rb) == 0);
    ASSERT_EQ_U(rb->resyncs + rb->errors, 0);
    ASSERT_EQ_U(rb->samples, 2 * clocks);
    ASSERT_EQ_U(rb->nblocks, 4);
    ASSERT_EQ_U(rb->nblkh, 4);
    ASSERT_EQ_U(rb->blkh_first[0], 0);
    ASSERT_EQ_U(rb->blkh_first[1], 1ull << 25);
    ASSERT_EQ_U(rb->blkh_first[2], 1ull << 26);
    ASSERT_EQ_U(rb->blkh_first[3], 3ull << 25);
    for (unsigned b = 0; b < 4; b++) {
        ASSERT_EQ_U(rb->pairs_in_block[b], 1);
        ASSERT_TRUE(strcmp(rb->first_tag[b], b ? "BLKH" : "VGCH") == 0);
    }
    ASSERT_EQ_U(rb->last_run_in_block[3], 10);
    free(rb);
    trace_rm(path);
}

TEST(test_trace_blocks_by_bytes) {
    char path[256]; tmp_path(path, sizeof path, "trace_blocks_by_bytes.vgct.zst");
    vgacap_header_t h; header43(&h);
    char err[256] = "";
    vgacap_trace_writer_t *t = vgacap_trace_create(path, &h, OPEN_META, 1, err, sizeof err);
    ASSERT_TRUE(t != NULL);
    // A new value every sample: 7-byte pairs, 2.4 M of them a block. 6 M
    // samples need about 42 MB of pairs, so three blocks.
    uint64_t samples = 6000000;
    int bad = 0;
    for (uint64_t s = 0; s < samples; s++)
        bad |= vgacap_trace_push(t, BASE43 | (s & 0xFF) | ((s & 1) ? CLK42 : 0));
    ASSERT_EQ_U(bad, 0);
    ASSERT_EQ_U(vgacap_trace_finish(t, CLOSE_META, err, sizeof err), 0);

    struct readback *rb = calloc(1, sizeof *rb);
    ASSERT_TRUE(read_back(path, rb) == 0);
    ASSERT_EQ_U(rb->resyncs + rb->errors, 0);
    ASSERT_EQ_U(rb->samples, samples);
    ASSERT_EQ_U(rb->nruns, samples);
    ASSERT_TRUE(rb->nblocks >= 3);
    ASSERT_EQ_U(rb->nblkh, rb->nblocks);
    for (unsigned b = 0; b < rb->nblocks; b++) {
        ASSERT_TRUE(rb->block_bytes[b] <= VGACAP_TRACE_BLOCK_BYTES);
        if (b + 1 < rb->nblocks)   // a block closed by size is nearly full
            ASSERT_TRUE(rb->block_bytes[b] > VGACAP_TRACE_BLOCK_BYTES - VGACAP_TRACE_BLOCK_RESERVE - 64);
        if (b + 1 < rb->nblkh) ASSERT_EQ_U(rb->blkh_first[b + 1], rb->blkh_first[b] + rb->block_samples[b]);
    }
    ASSERT_TRUE(rb->max_chunk_len <= VGACAP_MAX_CHUNK_LEN);
    free(rb);
    trace_rm(path);
}

// Every trace's META(open) carries layout=tt-um-43 (the only layout), which
// requires the 43-bit header with its clock bit, so the brief's 1-bit
// header is not a valid trace (the format note wins): the same check runs
// on the 43-bit layout, where a block is 2^25 samples.
TEST(test_trace_long_static_run) {
    char path[256]; tmp_path(path, sizeof path, "trace_long_static_run.vgct.zst");
    vgacap_header_t h; header43(&h);
    char err[256] = "";
    vgacap_trace_writer_t *t = vgacap_trace_create(path, &h, OPEN_META, 3, err, sizeof err);
    if (!t) fprintf(stderr, "  create: %s\n", err);
    ASSERT_TRUE(t != NULL);
    uint64_t total = (1ull << 32) + 10, per_block = 2 * VGACAP_TRACE_BLOCK_CLOCKS;
    int bad = 0;
    for (uint64_t s = 0; s < total; s += 2) {
        bad |= vgacap_trace_push(t, BASE43 | 1);
        bad |= vgacap_trace_push(t, BASE43 | 1 | CLK42);
    }
    ASSERT_EQ_U(bad, 0);
    ASSERT_EQ_U(vgacap_trace_finish(t, CLOSE_META, err, sizeof err), 0);

    struct readback *rb = calloc(1, sizeof *rb);
    ASSERT_TRUE(read_back(path, rb) == 0);
    ASSERT_EQ_U(rb->resyncs + rb->errors, 0);
    ASSERT_EQ_U(rb->samples, total);
    ASSERT_EQ_U(rb->nblkh, 129);
    ASSERT_EQ_U(rb->nblocks, 129);
    for (unsigned b = 0; b < rb->nblkh; b++) {
        ASSERT_EQ_U(rb->blkh_first[b], (uint64_t)b * per_block);
        ASSERT_EQ_U(rb->pairs_in_block[b], 1);   // so no run crosses a BLKH
        ASSERT_EQ_U(rb->block_samples[b], b + 1 < rb->nblkh ? per_block : 10);
    }
    free(rb);
    trace_rm(path);
}

TEST(test_trace_clock_rule_violation) {
    char path[256]; tmp_path(path, sizeof path, "trace_clock_violation.vgct.zst");
    vgacap_header_t h; header43(&h);
    char err[256] = "";
    vgacap_trace_writer_t *t = vgacap_trace_create(path, &h, OPEN_META, 3, err, sizeof err);
    ASSERT_TRUE(t != NULL);
    ASSERT_EQ_U(vgacap_trace_push(t, BASE43), 0);
    ASSERT_EQ_U((unsigned)(vgacap_trace_push(t, BASE43) + 1), 0);          // -1
    ASSERT_EQ_U((unsigned)(vgacap_trace_push(t, BASE43 | CLK42) + 1), 0);  // still -1
    ASSERT_EQ_U((unsigned)(vgacap_trace_finish(t, CLOSE_META, err, sizeof err) + 1), 0);
    if (strcmp(err, "sample 1: clock bit is 0, expected 1") != 0) fprintf(stderr, "  err: %s\n", err);
    ASSERT_TRUE(strcmp(err, "sample 1: clock bit is 0, expected 1") == 0);
    trace_rm(path);
}

// An I/O error (here /dev/full: every write fails with ENOSPC) fails a
// later push, every push after it, and finish.
TEST(test_trace_io_error) {
    vgacap_header_t h; header43(&h);
    char err[256] = "";
    vgacap_trace_writer_t *t = vgacap_trace_create("/dev/full", &h, OPEN_META, 1, err, sizeof err);
    if (!t) fprintf(stderr, "  create: %s\n", err);
    ASSERT_TRUE(t != NULL);
    // Values from an LCG barely compress, so each block's frame is several
    // MiB, more than the file buffer: the compressor's fwrite fails.
    uint64_t x = 1, s;
    for (s = 0; s < 20000000; s++) {
        x = x * 6364136223846793005ull + 1442695040888963407ull;
        uint64_t v = (x >> 22) & (CLK42 - 1);   // 42 random bits, then the clock
        if (vgacap_trace_push(t, v | ((s & 1) ? CLK42 : 0))) break;
    }
    ASSERT_TRUE(s < 20000000);
    ASSERT_EQ_U((unsigned)(vgacap_trace_push(t, 0) + 1), 0);
    ASSERT_EQ_U((unsigned)(vgacap_trace_push(t, 0) + 1), 0);
    ASSERT_EQ_U((unsigned)(vgacap_trace_finish(t, CLOSE_META, err, sizeof err) + 1), 0);
    if (!strstr(err, "write")) fprintf(stderr, "  err: %s\n", err);
    ASSERT_TRUE(strstr(err, "write") != NULL);
}

TEST(test_trace_abandon) {
    char path[256]; tmp_path(path, sizeof path, "trace_abandon.vgct.zst");
    vgacap_header_t h; header43(&h);
    char err[256] = "";
    vgacap_trace_writer_t *t = vgacap_trace_create(path, &h, OPEN_META, 3, err, sizeof err);
    ASSERT_TRUE(t != NULL);
    // One full block by clock count, and part of a second.
    for (uint64_t c = 0; c < VGACAP_TRACE_BLOCK_CLOCKS + 100; c++) {
        vgacap_trace_push(t, BASE43);
        vgacap_trace_push(t, BASE43 | CLK42);
    }
    vgacap_trace_abandon(t);
    // What a kill leaves: the finished block, no META(close), no seek table.
    struct readback *rb = calloc(1, sizeof *rb);
    ASSERT_TRUE(read_back(path, rb) == 0);
    ASSERT_EQ_U(rb->resyncs + rb->errors, 0);
    ASSERT_EQ_U(rb->nblocks, 1);
    ASSERT_EQ_U(rb->skippable_frames, 0);
    ASSERT_EQ_U(rb->nmeta, 1);
    ASSERT_EQ_U(rb->samples, 2 * VGACAP_TRACE_BLOCK_CLOCKS);
    free(rb);
    trace_rm(path);
}

TEST(test_trace_bad_meta) {
    char path[256]; tmp_path(path, sizeof path, "trace_bad_meta.vgct.zst");
    vgacap_header_t h; header43(&h);
    char err[256] = "";
    // meta=open without its required `layout`.
    vgacap_trace_writer_t *t = vgacap_trace_create(
        path, &h, "meta=open\nproject=x\ncalib_seconds=1\nseconds=2\n", 3, err, sizeof err);
    ASSERT_TRUE(t == NULL);
    ASSERT_TRUE(strstr(err, "META") != NULL);
    // layout=tt-um-43 with a header that is not that layout's.
    vgacap_header_t h1; vgacap_header_init_v2(&h1, 1, 1, VGACAP_CLOCK_BIT_NONE, 0);
    t = vgacap_trace_create(path, &h1, OPEN_META, 3, err, sizeof err);
    ASSERT_TRUE(t == NULL);
    ASSERT_TRUE(strstr(err, "layout") != NULL);
    // A close META given as the open one.
    t = vgacap_trace_create(path, &h, CLOSE_META, 3, err, sizeof err);
    ASSERT_TRUE(t == NULL);
    // A good open; a bad close is refused at finish.
    t = vgacap_trace_create(path, &h, OPEN_META, 3, err, sizeof err);
    ASSERT_TRUE(t != NULL);
    ASSERT_EQ_U(vgacap_trace_push(t, BASE43), 0);
    ASSERT_EQ_U((unsigned)(vgacap_trace_finish(t, "meta=close\nstatus=fine\n", err, sizeof err) + 1), 0);
    ASSERT_TRUE(strstr(err, "META") != NULL);
    trace_rm(path);
}

TEST(test_trace_seek_table) {
    char path[256]; tmp_path(path, sizeof path, "trace_seek_table.vgct.zst");
    vgacap_header_t h; header43(&h);
    char err[256] = "";
    vgacap_trace_writer_t *t = vgacap_trace_create(path, &h, OPEN_META, 3, err, sizeof err);
    ASSERT_TRUE(t != NULL);
    uint64_t clocks = 2 * VGACAP_TRACE_BLOCK_CLOCKS + 1234;
    int bad = 0;
    for (uint64_t c = 0; c < clocks; c++) {
        uint64_t uo = (c / 100u) & 0xFF;
        bad |= vgacap_trace_push(t, BASE43 | uo);
        bad |= vgacap_trace_push(t, BASE43 | uo | CLK42);
    }
    ASSERT_EQ_U(bad, 0);
    ASSERT_EQ_U(vgacap_trace_finish(t, CLOSE_META, err, sizeof err), 0);

    struct readback *rb = calloc(1, sizeof *rb);
    ASSERT_TRUE(read_back(path, rb) == 0);
    ASSERT_EQ_U(rb->resyncs + rb->errors, 0);
    ASSERT_EQ_U(rb->samples, 2 * clocks);

    FILE *f = fopen(path, "rb");
    ASSERT_TRUE(f != NULL);
    ZSTD_seekable *zs = ZSTD_seekable_create();
    ASSERT_TRUE(zs != NULL);
    size_t rc = ZSTD_seekable_initFile(zs, f);
    if (ZSTD_isError(rc)) fprintf(stderr, "  initFile: %s\n", ZSTD_getErrorName(rc));
    ASSERT_TRUE(!ZSTD_isError(rc));
    unsigned frames = ZSTD_seekable_getNumFrames(zs);
    ASSERT_EQ_U(frames, rb->nblocks);
    ASSERT_EQ_U(frames, 3);
    uint64_t sum = 0;
    for (unsigned i = 0; i < frames; i++) sum += ZSTD_seekable_getFrameDecompressedSize(zs, i);
    ASSERT_EQ_U(sum, rb->stream_bytes);
    // A seek straight to the last block reads back from its BLKH.
    size_t last = ZSTD_seekable_getFrameDecompressedSize(zs, frames - 1);
    uint8_t *buf = malloc(last);
    ASSERT_EQ_U(ZSTD_seekable_decompressFrame(zs, buf, last, frames - 1), last);
    ASSERT_TRUE(memcmp(buf, "BLKH", 4) == 0);
    free(buf);
    ZSTD_seekable_free(zs);
    fclose(f);
    free(rb);
    trace_rm(path);
}

// ---- speed (perf label; run by hand, not in CI) -----------------------------

static double clock_s(clockid_t id) {
    struct timespec ts; clock_gettime(id, &ts);
    return (double)ts.tv_sec + (double)ts.tv_nsec * 1e-9;
}
static double now_s(void) { return clock_s(CLOCK_MONOTONIC); }
static double cpu_s(void) { return clock_s(CLOCK_THREAD_CPUTIME_ID); }

// One profile: 1e8 clocks, two samples each; `every` is how many clocks
// uo_out holds a value (1: a new value every clock, so one run a clock).
static double push_profile(const char *name, uint64_t every) {
    char path[256]; tmp_path(path, sizeof path, "trace_speed.vgct.zst");
    vgacap_header_t h; header43(&h);
    char err[256] = "";
    vgacap_trace_writer_t *t = vgacap_trace_create(path, &h, OPEN_META, 3, err, sizeof err);
    if (!t) { fprintf(stderr, "  create: %s\n", err); return 1e9; }
    const uint64_t clocks = 100000000;
    int bad = 0;
    uint64_t uo = 0, left = every;
    double t0 = now_s(), c0 = cpu_s();
    for (uint64_t c = 0; c < clocks; c++) {
        // uo_out changes just after the rising edge: in the clk = 1 sample.
        bad |= vgacap_trace_push(t, BASE43 | uo);
        if (--left == 0) { uo = (uo + 1) & 0xFF; left = every; }
        bad |= vgacap_trace_push(t, BASE43 | uo | CLK42);
    }
    double t1 = now_s(), c1 = cpu_s();
    int frc = vgacap_trace_finish(t, CLOSE_META, err, sizeof err);
    double t2 = now_s();
    double ns = (t1 - t0) * 1e9 / (double)clocks;
    double cpu_ns = (c1 - c0) * 1e9 / (double)clocks;
    fprintf(stderr, "  %-5s: %.2f ns/clock wall (%.2f ns/sample), %.2f ns/clock caller CPU, "
            "push %.3f s, finish %.3f s%s\n",
            name, ns, ns / 2, cpu_ns, t1 - t0, t2 - t1, (bad || frc) ? " FAILED" : "");
    trace_rm(path);
    return (bad || frc) ? 1e9 : ns;
}

TEST(test_trace_push_speed) {
    double slow = push_profile("slow", 400);
    double busy = push_profile("busy", 1);
    ASSERT_TRUE(slow <= 10.0);
    ASSERT_TRUE(busy <= 10.0);
}

int main(int argc, char **argv) {
    if (argc > 1 && strcmp(argv[1], "--speed") == 0) {
        RUN(test_trace_push_speed);
        RUN_TESTS_END();
    }
    RUN(test_trace_small);
    RUN(test_trace_blocks_by_clock);
    RUN(test_trace_blocks_by_bytes);
    RUN(test_trace_long_static_run);
    RUN(test_trace_clock_rule_violation);
    RUN(test_trace_io_error);
    RUN(test_trace_abandon);
    RUN(test_trace_bad_meta);
    RUN(test_trace_seek_table);
    RUN_TESTS_END();
}
