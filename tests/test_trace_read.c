// SPDX-License-Identifier: Apache-2.0
// The trace reader (src/trace/trace_read.c): ranges by block, the seek table
// rebuilt for a trace cut short, plain VGCH streams. The traces are made with
// the trace writer; every file goes under tmp/ in the working directory (the
// build directory under CTest).
#define _POSIX_C_SOURCE 200809L   // popen
#define ZSTD_STATIC_LINKING_ONLY  // ZSTD_isSkippableFrame
#include "harness.h"
#include "vgacap/trace.h"
#include "trace_read_stats.h"   // src/trace: what the reader decoded
#include <errno.h>
#include <inttypes.h>
#include <stdint.h>
#include <sys/stat.h>
#include <zlib.h>
#include <zstd.h>

#define OPEN_META "meta=open\nproject=tt_um_test\ncalib_seconds=0.5\nseconds=2\nlayout=tt-um-43\n"
#define CLOSE_META "meta=close\ncapture_after_clock=0\nstatus=ok\nframes=0\n"
#define CLK42 ((uint64_t)1 << 42)
#define BASE43 ((uint64_t)3 << 40)   // rst_n and ena high
#define BLOCK_SAMPLES (2 * VGACAP_TRACE_BLOCK_CLOCKS)

static void tmp_path(char *out, size_t n, const char *name) {
    if (mkdir("tmp", 0777) != 0 && errno != EEXIST) { perror("mkdir tmp"); exit(2); }
    snprintf(out, n, "tmp/%s", name);
}

// The sample pattern of every trace here: uo_out changes every `every`
// clocks (never if 0), in the clock-high sample (as a design's outputs change
// just after the rising edge), so runs start at odd samples.
static uint64_t sample_value(uint64_t s, uint64_t every) {
    uint64_t uo = every ? ((s + 1) / 2 / every) & 0xFF : 0;
    return BASE43 | uo | ((s & 1) ? CLK42 : 0);
}

static int make_trace(const char *path, uint64_t clocks, uint64_t every) {
    vgacap_header_t h;
    vgacap_header_init_v2(&h, 43, 2, 42, 0);
    h.mode = VGACAP_MODE_EVENT; h.clock_hz = 25175000;
    char err[256] = "";
    vgacap_trace_writer_t *t = vgacap_trace_create(path, &h, OPEN_META, 3, err, sizeof err);
    if (!t) { fprintf(stderr, "  create: %s\n", err); return -1; }
    int bad = 0;
    for (uint64_t s = 0; s < 2 * clocks; s++) bad |= vgacap_trace_push(t, sample_value(s, every));
    if (vgacap_trace_finish(t, CLOSE_META, err, sizeof err) || bad) { fprintf(stderr, "  finish: %s\n", err); return -1; }
    return 0;
}

// What a read delivered. Every run is checked against sample_value: its
// value is its first sample's, and its last sample's is that with the clock
// bit toggled run - 1 times.
#define KEEP 64
struct got {
    uint64_t every;
    uint64_t runs, total, next;   // next: where the following run must start
    int gaps, bad_values, bad_clock;
    uint64_t first[KEEP], value[KEEP], len[KEEP];
    uint64_t stop_after;          // nonzero: stop once this many runs arrived
};

static int on_run(void *user, uint64_t first, uint64_t value, uint64_t run) {
    struct got *g = user;
    if (g->runs && first != g->next) g->gaps++;
    if (run == 0) g->bad_values++;
    if (value != sample_value(first, g->every)) g->bad_values++;
    uint64_t last = value ^ (((run - 1) & 1) ? CLK42 : 0);
    if (last != sample_value(first + run - 1, g->every)) g->bad_values++;
    if (((value >> 42) & 1) != (first & 1)) g->bad_clock++;   // clock_first 0
    if (g->runs < KEEP) { g->first[g->runs] = first; g->value[g->runs] = value; g->len[g->runs] = run; }
    g->runs++; g->total += run; g->next = first + run;
    return g->stop_after && g->runs >= g->stop_after;
}

static int read_range(vgacap_trace_reader_t *r, uint64_t from, uint64_t to, struct got *g, uint64_t every) {
    memset(g, 0, sizeof *g);
    g->every = every;
    char err[256] = "";
    int rc = vgacap_trace_read(r, from, to, on_run, g, err, sizeof err);
    if (rc) fprintf(stderr, "  read [%" PRIu64 ", %" PRIu64 "): %s\n", from, to, err);
    return rc;
}

static vgacap_trace_reader_t *open_or_say(const char *path) {
    char err[256] = "";
    vgacap_trace_reader_t *r = vgacap_trace_open(path, err, sizeof err);
    if (!r) fprintf(stderr, "  open %s: %s\n", path, err);
    return r;
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

static int spit(const char *path, const uint8_t *p, size_t n) {
    FILE *f = fopen(path, "wb");
    if (!f) { perror(path); return -1; }
    int rc = fwrite(p, 1, n, f) == n ? 0 : -1;
    if (fclose(f) != 0) rc = -1;
    return rc;
}

// ---- the 3-block static trace -------------------------------------------------

TEST(test_trace_range) {
    char path[256]; tmp_path(path, sizeof path, "read_static3.vgct.zst");
    uint64_t clocks = 2 * VGACAP_TRACE_BLOCK_CLOCKS + 1000;
    ASSERT_TRUE(make_trace(path, clocks, 0) == 0);
    vgacap_trace_reader_t *r = open_or_say(path);
    ASSERT_TRUE(r != NULL);
    const vgacap_trace_info_t *in = vgacap_trace_info(r);
    ASSERT_EQ_U(in->compressed, 1);
    ASSERT_EQ_U(in->table_rebuilt, 0);
    ASSERT_EQ_U(in->blocks, 3);
    ASSERT_EQ_U(in->samples, 2 * clocks);
    ASSERT_EQ_U(in->header.version, 2);
    ASSERT_EQ_U(in->header.sample_bits, 43);
    ASSERT_EQ_U(in->header.clock_bit, 42);
    ASSERT_TRUE(in->open_meta && strcmp(in->open_meta, OPEN_META) == 0);
    ASSERT_TRUE(in->close_meta && strcmp(in->close_meta, CLOSE_META) == 0);

    struct got *g = calloc(1, sizeof *g);
    uint64_t b1 = 1ull << 25;
    ASSERT_TRUE(read_range(r, b1 - 3, b1 + 4, g, 0) == 0);
    ASSERT_EQ_U(g->total, 7);
    ASSERT_EQ_U(g->runs, 2);
    ASSERT_EQ_U(g->first[0], b1 - 3);
    ASSERT_EQ_U(g->len[0], 3);
    ASSERT_EQ_U(g->first[1], b1);
    ASSERT_EQ_U(g->len[1], 4);
    ASSERT_EQ_U(g->bad_values + g->bad_clock + g->gaps, 0);
    // Everything, then a range inside one block, then past the end.
    ASSERT_TRUE(read_range(r, 0, UINT64_MAX, g, 0) == 0);
    ASSERT_EQ_U(g->total, 2 * clocks);
    ASSERT_EQ_U(g->runs, 3);
    ASSERT_EQ_U(g->bad_values + g->bad_clock + g->gaps, 0);
    ASSERT_TRUE(read_range(r, (1ull << 26) + 5, (1ull << 26) + 6, g, 0) == 0);
    ASSERT_EQ_U(g->total, 1);
    ASSERT_EQ_U(g->first[0], (1ull << 26) + 5);
    ASSERT_EQ_U(g->bad_values + g->bad_clock, 0);
    ASSERT_TRUE(read_range(r, 2 * clocks, 2 * clocks + 100, g, 0) == 0);
    ASSERT_EQ_U(g->runs, 0);
    ASSERT_TRUE(read_range(r, 50, 50, g, 0) == 0);
    ASSERT_EQ_U(g->runs, 0);
    // A callback returning nonzero stops the read.
    memset(g, 0, sizeof *g); g->stop_after = 1;
    char err[256] = "";
    ASSERT_EQ_U(vgacap_trace_read(r, 0, UINT64_MAX, on_run, g, err, sizeof err), 0);
    ASSERT_EQ_U(g->runs, 1);
    free(g);
    vgacap_trace_close(r);
    remove(path);
}

TEST(test_trace_clock_bits) {
    char path[256]; tmp_path(path, sizeof path, "read_small.vgct.zst");
    ASSERT_TRUE(make_trace(path, 5000, 7) == 0);
    vgacap_trace_reader_t *r = open_or_say(path);
    ASSERT_TRUE(r != NULL);
    ASSERT_EQ_U(vgacap_trace_info(r)->header.clock_first, 0);
    ASSERT_EQ_U(vgacap_trace_info(r)->samples, 10000);
    struct got *g = calloc(1, sizeof *g);
    ASSERT_TRUE(read_range(r, 0, 10000, g, 7) == 0);
    ASSERT_EQ_U(g->total, 10000);
    ASSERT_TRUE(g->runs > 700);
    ASSERT_EQ_U(g->bad_values + g->bad_clock + g->gaps, 0);
    // Ranges starting on even and odd samples, inside runs and at their ends.
    static const uint64_t ranges[][2] = { { 1, 2 }, { 2, 3 }, { 12, 15 }, { 13, 29 }, { 14, 9000 }, { 9999, 20000 } };
    for (unsigned i = 0; i < sizeof ranges / sizeof ranges[0]; i++) {
        ASSERT_TRUE(read_range(r, ranges[i][0], ranges[i][1], g, 7) == 0);
        uint64_t to = ranges[i][1] < 10000 ? ranges[i][1] : 10000;
        ASSERT_EQ_U(g->total, to - ranges[i][0]);
        ASSERT_EQ_U(g->first[0], ranges[i][0]);
        ASSERT_EQ_U(g->bad_values + g->bad_clock + g->gaps, 0);
    }
    free(g);
    vgacap_trace_close(r);
    remove(path);
}

// ---- the 5-block trace and what a kill leaves of it ----------------------------

#define FIVE_CLOCKS (4 * VGACAP_TRACE_BLOCK_CLOCKS + 5000)
#define FIVE_EVERY 100

static int five_ready;
static const char *five_path(void) {
    static char path[256];
    if (!five_ready) {
        tmp_path(path, sizeof path, "read_five.vgct.zst");
        if (make_trace(path, FIVE_CLOCKS, FIVE_EVERY)) return NULL;
        five_ready = 1;
    }
    return path;
}

// Offsets of the data frames of `file`, and the end of the last one.
static unsigned frame_offsets(const uint8_t *file, size_t len, size_t *off, unsigned max, size_t *end) {
    unsigned n = 0; size_t pos = 0;
    while (pos < len && n < max) {
        size_t fsz = ZSTD_findFrameCompressedSize(file + pos, len - pos);
        if (ZSTD_isError(fsz) || ZSTD_isSkippableFrame(file + pos, len - pos)) break;
        off[n++] = pos; pos += fsz;
    }
    *end = pos;
    return n;
}

// Copies the first `keep` bytes of the 5-block trace to tmp/`name`.
static int cut_copy(const char *name, size_t keep, char *out, size_t outlen) {
    const char *src = five_path();
    if (!src) return -1;
    size_t len; uint8_t *file = slurp(src, &len);
    if (!file || keep > len) { free(file); return -1; }
    tmp_path(out, outlen, name);
    int rc = spit(out, file, keep);
    free(file);
    return rc;
}

TEST(test_trace_truncated) {
    const char *src = five_path();
    ASSERT_TRUE(src != NULL);
    size_t len; uint8_t *file = slurp(src, &len);
    ASSERT_TRUE(file != NULL);
    size_t off[8], end;
    ASSERT_EQ_U(frame_offsets(file, len, off, 8, &end), 5);
    ASSERT_TRUE(end - off[4] > 200);   // block 4's frame is longer than the cut
    free(file);
    char path[256];
    ASSERT_TRUE(cut_copy("read_five_cut4.vgct.zst", off[4] + 100, path, sizeof path) == 0);

    vgacap_trace_reader_t *r = open_or_say(path);
    ASSERT_TRUE(r != NULL);
    const vgacap_trace_info_t *in = vgacap_trace_info(r);
    ASSERT_EQ_U(in->compressed, 1);
    ASSERT_EQ_U(in->table_rebuilt, 1);
    ASSERT_EQ_U(in->blocks, 4);
    ASSERT_TRUE(in->close_meta == NULL);
    ASSERT_TRUE(in->open_meta && strcmp(in->open_meta, OPEN_META) == 0);
    ASSERT_EQ_U(in->samples, 4 * BLOCK_SAMPLES);
    struct got *g = calloc(1, sizeof *g);
    ASSERT_TRUE(read_range(r, 0, UINT64_MAX, g, FIVE_EVERY) == 0);
    ASSERT_EQ_U(g->total, 4 * BLOCK_SAMPLES);
    ASSERT_EQ_U(g->first[0], 0);
    ASSERT_EQ_U(g->next, 4 * BLOCK_SAMPLES);
    ASSERT_EQ_U(g->bad_values + g->bad_clock + g->gaps, 0);
    // A range across the rebuilt table's block 2/3 boundary.
    ASSERT_TRUE(read_range(r, 3 * BLOCK_SAMPLES - 250, 3 * BLOCK_SAMPLES + 250, g, FIVE_EVERY) == 0);
    ASSERT_EQ_U(g->total, 500);
    ASSERT_EQ_U(g->bad_values + g->bad_clock + g->gaps, 0);
    free(g);
    vgacap_trace_close(r);
    remove(path);

    // Cut inside block 0's frame: nothing complete is left.
    ASSERT_TRUE(cut_copy("read_five_cut0.vgct.zst", 100, path, sizeof path) == 0);
    char err[256] = "";
    r = vgacap_trace_open(path, err, sizeof err);
    ASSERT_TRUE(r == NULL);
    ASSERT_TRUE(err[0] != 0);
    remove(path);
}

TEST(test_trace_truncated_in_table) {
    const char *src = five_path();
    ASSERT_TRUE(src != NULL);
    size_t len; uint8_t *file = slurp(src, &len);
    ASSERT_TRUE(file != NULL);
    size_t off[8], end;
    ASSERT_EQ_U(frame_offsets(file, len, off, 8, &end), 5);
    free(file);
    // Into the table's entries, and into its footer; and the table gone whole.
    const size_t keeps[] = { end + 20, len - 5, end };
    for (unsigned i = 0; i < 3; i++) {
        char path[256];
        ASSERT_TRUE(cut_copy("read_five_cut_table.vgct.zst", keeps[i], path, sizeof path) == 0);
        vgacap_trace_reader_t *r = open_or_say(path);
        ASSERT_TRUE(r != NULL);
        const vgacap_trace_info_t *in = vgacap_trace_info(r);
        ASSERT_EQ_U(in->table_rebuilt, 1);
        ASSERT_EQ_U(in->blocks, 5);
        ASSERT_EQ_U(in->samples, 2 * (uint64_t)FIVE_CLOCKS);
        ASSERT_TRUE(in->close_meta && strcmp(in->close_meta, CLOSE_META) == 0);
        struct got *g = calloc(1, sizeof *g);
        ASSERT_TRUE(read_range(r, 0, UINT64_MAX, g, FIVE_EVERY) == 0);
        ASSERT_EQ_U(g->total, 2 * (uint64_t)FIVE_CLOCKS);
        ASSERT_EQ_U(g->bad_values + g->bad_clock + g->gaps, 0);
        free(g);
        vgacap_trace_close(r);
        remove(path);
    }
}

// The whole 5-block trace, then a seek table that names the wrong sizes:
// it is not trusted.
TEST(test_trace_bad_table) {
    const char *src = five_path();
    ASSERT_TRUE(src != NULL);
    vgacap_trace_reader_t *r = open_or_say(src);
    ASSERT_TRUE(r != NULL);
    ASSERT_EQ_U(vgacap_trace_info(r)->table_rebuilt, 0);
    ASSERT_EQ_U(vgacap_trace_info(r)->blocks, 5);
    vgacap_trace_close(r);

    size_t len; uint8_t *file = slurp(src, &len);
    ASSERT_TRUE(file != NULL);
    size_t off[8], end;
    ASSERT_EQ_U(frame_offsets(file, len, off, 8, &end), 5);
    // The first entry's compressed size, just after the skippable header.
    file[end + 8] ^= 0x01;
    char path[256]; tmp_path(path, sizeof path, "read_five_bad_table.vgct.zst");
    ASSERT_TRUE(spit(path, file, len) == 0);
    free(file);
    r = open_or_say(path);
    ASSERT_TRUE(r != NULL);
    ASSERT_EQ_U(vgacap_trace_info(r)->table_rebuilt, 1);
    ASSERT_EQ_U(vgacap_trace_info(r)->blocks, 5);
    ASSERT_EQ_U(vgacap_trace_info(r)->samples, 2 * (uint64_t)FIVE_CLOCKS);
    vgacap_trace_close(r);
    remove(path);
}

// ---- plain VGCH -----------------------------------------------------------------

// The 5-block trace decompressed (what `zstd -d` gives: a plain v2 VGCH
// stream with a BLKH per block) reads the same as the trace.
TEST(test_trace_decompressed_plain) {
    const char *src = five_path();
    ASSERT_TRUE(src != NULL);
    vgacap_trace_reader_t *z = open_or_say(src);
    ASSERT_TRUE(z != NULL);
    // Its blocks' bytes, concatenated, are the plain stream.
    size_t cap = 5u * VGACAP_TRACE_BLOCK_BYTES, fill = 0;
    uint8_t *plain = malloc(cap);
    ASSERT_TRUE(plain != NULL);
    char err[256] = "";
    for (uint32_t b = 0; b < vgacap_trace_info(z)->blocks; b++) {
        const uint8_t *p; size_t n;
        int rc = vgacap_trace_block_data(z, b, &p, &n, err, sizeof err);
        if (rc) fprintf(stderr, "  block %u: %s\n", b, err);
        ASSERT_TRUE(rc == 0);
        ASSERT_TRUE(n <= cap - fill);
        memcpy(plain + fill, p, n); fill += n;
    }
    // ...and match an independent decompression of the file.
    size_t len; uint8_t *file = slurp(src, &len);
    ASSERT_TRUE(file != NULL);
    unsigned long long whole = ZSTD_decompressBound(file, len);
    ASSERT_TRUE(!ZSTD_isError((size_t)whole) && whole <= cap);
    uint8_t *ref = malloc((size_t)whole);
    size_t d = ZSTD_decompress(ref, (size_t)whole, file, len);
    ASSERT_TRUE(!ZSTD_isError(d));
    ASSERT_EQ_U(d, fill);
    ASSERT_EQ_MEM(ref, plain, d);
    free(ref); free(file);
    char path[256]; tmp_path(path, sizeof path, "read_five.vgacap");
    ASSERT_TRUE(spit(path, plain, fill) == 0);
    free(plain);

    vgacap_trace_reader_t *p = open_or_say(path);
    ASSERT_TRUE(p != NULL);
    const vgacap_trace_info_t *pi = vgacap_trace_info(p), *zi = vgacap_trace_info(z);
    ASSERT_EQ_U(pi->compressed, 0);
    ASSERT_EQ_U(pi->blocks, 1);
    ASSERT_EQ_U(pi->table_rebuilt, 0);
    ASSERT_EQ_U(pi->samples, zi->samples);
    ASSERT_EQ_U(pi->header.version, 2);
    ASSERT_EQ_U(pi->header.sample_bits, zi->header.sample_bits);
    ASSERT_EQ_U(pi->header.clock_bit, zi->header.clock_bit);
    ASSERT_EQ_U(pi->header.clock_hz, zi->header.clock_hz);
    ASSERT_TRUE(pi->open_meta && strcmp(pi->open_meta, zi->open_meta) == 0);
    ASSERT_TRUE(pi->close_meta && strcmp(pi->close_meta, zi->close_meta) == 0);
    // The same runs, in full and across a block boundary.
    struct got *a = calloc(1, sizeof *a), *b = calloc(1, sizeof *b);
    const uint64_t ranges[][2] = { { 0, UINT64_MAX }, { 2 * BLOCK_SAMPLES - 1001, 2 * BLOCK_SAMPLES + 1003 },
                                   { 4 * BLOCK_SAMPLES + 17, 4 * BLOCK_SAMPLES + 18 } };
    for (unsigned i = 0; i < 3; i++) {
        ASSERT_TRUE(read_range(p, ranges[i][0], ranges[i][1], a, FIVE_EVERY) == 0);
        ASSERT_TRUE(read_range(z, ranges[i][0], ranges[i][1], b, FIVE_EVERY) == 0);
        ASSERT_EQ_U(a->runs, b->runs);
        ASSERT_EQ_U(a->total, b->total);
        ASSERT_TRUE(a->total > 0);
        unsigned k = a->runs < KEEP ? (unsigned)a->runs : KEEP;
        ASSERT_EQ_MEM(a->first, b->first, k * sizeof a->first[0]);
        ASSERT_EQ_MEM(a->value, b->value, k * sizeof a->value[0]);
        ASSERT_EQ_MEM(a->len, b->len, k * sizeof a->len[0]);
        ASSERT_EQ_U(a->bad_values + a->bad_clock + a->gaps, 0);
    }
    free(a); free(b);
    vgacap_trace_close(p);
    vgacap_trace_close(z);
    remove(path);
}

// Every run a read delivers, in full.
struct all_runs { size_t n, cap; uint64_t *first, *value, *len; };

static int on_all(void *user, uint64_t first, uint64_t value, uint64_t run) {
    struct all_runs *a = user;
    if (a->n == a->cap) {
        a->cap = a->cap ? 2 * a->cap : 1024;
        a->first = realloc(a->first, a->cap * sizeof *a->first);
        a->value = realloc(a->value, a->cap * sizeof *a->value);
        a->len = realloc(a->len, a->cap * sizeof *a->len);
        if (!a->first || !a->value || !a->len) { fprintf(stderr, "  out of memory\n"); exit(2); }
    }
    a->first[a->n] = first; a->value[a->n] = value; a->len[a->n] = run; a->n++;
    return 0;
}

static void all_free(struct all_runs *a) { free(a->first); free(a->value); free(a->len); memset(a, 0, sizeof *a); }

static int all_same(const struct all_runs *a, const struct all_runs *b) {
    if (a->n != b->n) { fprintf(stderr, "  %zu runs vs %zu\n", a->n, b->n); return 0; }
    for (size_t i = 0; i < a->n; i++)
        if (a->first[i] != b->first[i] || a->value[i] != b->value[i] || a->len[i] != b->len[i]) {
            fprintf(stderr, "  run %zu: (%" PRIu64 ", %" PRIx64 ", %" PRIu64 ") vs (%" PRIu64 ", %" PRIx64 ", %" PRIu64 ")\n",
                    i, a->first[i], a->value[i], a->len[i], b->first[i], b->value[i], b->len[i]);
            return 0;
        }
    return 1;
}

// tests/fixtures/plain-v2.vgacap, a plain v2 stream from the Python writer
// (tests/fixtures/make_plain_v2.py: 3000 clocks, uo_out every 7), reads the
// same, run for run, as a trace of the same samples from the C writer.
TEST(test_trace_plain_vgch) {
    char path[256]; tmp_path(path, sizeof path, "read_plain_twin.vgct.zst");
    ASSERT_TRUE(make_trace(path, 3000, 7) == 0);
    vgacap_trace_reader_t *z = open_or_say(path);
    vgacap_trace_reader_t *p = open_or_say(VGACAP_FIXTURES "/plain-v2.vgacap");
    ASSERT_TRUE(z != NULL && p != NULL);
    const vgacap_trace_info_t *pi = vgacap_trace_info(p), *zi = vgacap_trace_info(z);
    ASSERT_EQ_U(pi->compressed, 0);
    ASSERT_EQ_U(zi->compressed, 1);
    ASSERT_EQ_U(pi->blocks, 1);
    ASSERT_EQ_U(pi->header.version, 2);
    ASSERT_EQ_U(pi->header.sample_bits, 43);
    ASSERT_EQ_U(pi->header.samples_per_clock, 2);
    ASSERT_EQ_U(pi->header.clock_bit, 42);
    ASSERT_EQ_U(pi->header.clock_hz, zi->header.clock_hz);
    ASSERT_TRUE(strcmp(pi->header.desc, "plain v2 fixture") == 0);
    ASSERT_EQ_U(pi->samples, 6000);
    ASSERT_EQ_U(zi->samples, 6000);
    ASSERT_TRUE(pi->open_meta && strcmp(pi->open_meta, zi->open_meta) == 0);
    ASSERT_TRUE(pi->close_meta && strcmp(pi->close_meta, zi->close_meta) == 0);
    const uint64_t ranges[][2] = { { 0, UINT64_MAX }, { 1, 2 }, { 13, 29 }, { 2999, 4001 }, { 5990, 7000 } };
    for (unsigned i = 0; i < sizeof ranges / sizeof ranges[0]; i++) {
        struct all_runs a = { 0, 0, NULL, NULL, NULL }, b = { 0, 0, NULL, NULL, NULL };
        char err[256] = "";
        ASSERT_EQ_U(vgacap_trace_read(p, ranges[i][0], ranges[i][1], on_all, &a, err, sizeof err), 0);
        ASSERT_EQ_U(vgacap_trace_read(z, ranges[i][0], ranges[i][1], on_all, &b, err, sizeof err), 0);
        ASSERT_TRUE(a.n > 0);
        ASSERT_EQ_U(a.first[0], ranges[i][0]);
        int same = all_same(&a, &b);
        uint64_t total = 0;
        for (size_t k = 0; k < a.n; k++) {
            if (a.value[k] != sample_value(a.first[k], 7)) same = 0;
            total += a.len[k];
        }
        all_free(&a); all_free(&b);
        ASSERT_TRUE(same);
        ASSERT_EQ_U(total, (ranges[i][1] < 6000 ? ranges[i][1] : 6000) - ranges[i][0]);
    }
    vgacap_trace_close(p);
    vgacap_trace_close(z);
    remove(path);
}

static int count_run(void *user, uint64_t first, uint64_t value, uint64_t run) {
    (void)first; (void)value;
    *(uint64_t *)user += run;
    return 0;
}

// Opening a trace with a good seek table reads the table and each frame's
// header and start, not the frames; a range read decompresses only the
// blocks it needs. The values here barely compress (an LCG), so the frames
// are megabytes each.
TEST(test_trace_open_touches_little) {
    char path[256]; tmp_path(path, sizeof path, "read_random.vgct.zst");
    vgacap_header_t h;
    vgacap_header_init_v2(&h, 43, 2, 42, 0);
    char err[256] = "";
    vgacap_trace_writer_t *t = vgacap_trace_create(path, &h, OPEN_META, 1, err, sizeof err);
    ASSERT_TRUE(t != NULL);
    // A new value every sample: blocks close by size, about 2.39 M samples
    // each; this leaves a short third block.
    const uint64_t samples = 4790000;
    uint64_t x = 1; int bad = 0;
    for (uint64_t s = 0; s < samples; s++) {
        x = x * 6364136223846793005ull + 1442695040888963407ull;
        bad |= vgacap_trace_push(t, ((x >> 22) & (CLK42 - 1)) | ((s & 1) ? CLK42 : 0));
    }
    ASSERT_EQ_U(bad, 0);
    ASSERT_EQ_U(vgacap_trace_finish(t, CLOSE_META, err, sizeof err), 0);

    size_t len; uint8_t *file = slurp(path, &len);
    ASSERT_TRUE(file != NULL);
    size_t off[8], end;
    ASSERT_EQ_U(frame_offsets(file, len, off, 8, &end), 3);
    ASSERT_TRUE(off[2] > 20u * 1024u * 1024u);    // blocks 0 and 1 are big
    ASSERT_TRUE(len - off[2] < 1024u * 1024u);     // block 2 and the table are not

    vgacap_trace_reader_t *r = open_or_say(path);
    ASSERT_TRUE(r != NULL);
    ASSERT_EQ_U(vgacap_trace_info(r)->table_rebuilt, 0);
    ASSERT_EQ_U(vgacap_trace_info(r)->samples, samples);
    vgacap_trace_read_stats_t st;
    vgacap_trace_read_stats(r, &st);
    ASSERT_EQ_U(st.blocks_decoded, 1);   // the last block, for its samples and META(close)
    // Three block starts (zstd takes in a whole zstd block, at most 128 KiB
    // stored, to give the first bytes), the last frame, the table and the
    // frame headers: 418,125 bytes of 29.4 MB when this was written.
    if (st.bytes_touched >= (len - off[2]) + 3 * 140000u)
        fprintf(stderr, "  open touched %" PRIu64 " of %zu bytes\n", st.bytes_touched, len);
    ASSERT_TRUE(st.bytes_touched < (len - off[2]) + 3 * 140000u);

    // A range inside block 1 decompresses block 1 alone.
    // Block 0 holds about (16 MiB - 64 KiB - 16 - 141) / 7 = 2,387,360 one-sample
    // pairs, so b1 + 1000 is in block 1 and b1 - 2000 in block 0.
    uint64_t b1 = 2387000, got = 0;
    vgacap_trace_read_stats_t before = st;
    ASSERT_EQ_U(vgacap_trace_read(r, b1 + 1000, b1 + 1010, count_run, &got, err, sizeof err), 0);
    ASSERT_EQ_U(got, 10);
    vgacap_trace_read_stats(r, &st);
    ASSERT_EQ_U(st.blocks_decoded - before.blocks_decoded, 1);
    ASSERT_EQ_U(st.bytes_touched - before.bytes_touched, off[2] - off[1]);
    // The same block again comes from the cache; a range across blocks 0
    // and 1 decodes both.
    before = st;
    ASSERT_EQ_U(vgacap_trace_read(r, b1 + 2000, b1 + 2010, count_run, &got, err, sizeof err), 0);
    vgacap_trace_read_stats(r, &st);
    ASSERT_EQ_U(st.blocks_decoded - before.blocks_decoded, 0);
    ASSERT_EQ_U(vgacap_trace_read(r, b1 - 2000, b1 + 3000, count_run, &got, err, sizeof err), 0);
    vgacap_trace_read_stats(r, &st);
    ASSERT_EQ_U(st.blocks_decoded - before.blocks_decoded, 2);
    vgacap_trace_close(r);

    // Without the table the frames are walked: the whole file is read.
    char cut[256]; tmp_path(cut, sizeof cut, "read_random_cut.vgct.zst");
    ASSERT_TRUE(spit(cut, file, end) == 0);
    free(file);
    r = open_or_say(cut);
    ASSERT_TRUE(r != NULL);
    ASSERT_EQ_U(vgacap_trace_info(r)->table_rebuilt, 1);
    vgacap_trace_read_stats(r, &st);
    ASSERT_TRUE(st.bytes_touched >= end);
    vgacap_trace_close(r);
    remove(cut);
    remove(path);
}

// Recompresses `n` bytes as one checksummed zstd frame, appended to `f`.
static int put_frame(FILE *f, const uint8_t *p, size_t n) {
    ZSTD_CCtx *c = ZSTD_createCCtx();
    size_t cap = ZSTD_compressBound(n);
    uint8_t *z = malloc(cap);
    int rc = -1;
    if (c && z && !ZSTD_isError(ZSTD_CCtx_setParameter(c, ZSTD_c_checksumFlag, 1))) {
        size_t k = ZSTD_compress2(c, z, cap, p, n);
        if (!ZSTD_isError(k) && fwrite(z, 1, k, f) == k) rc = 0;
    }
    free(z); ZSTD_freeCCtx(c);
    return rc;
}

// A two-block trace rebuilt with one block's bytes cut 3 bytes short (inside
// a chunk) but recompressed, so zstd finds nothing wrong: the reader must.
static int write_cut_block(const char *out, const uint8_t *b0, size_t n0, const uint8_t *b1, size_t n1, int which) {
    FILE *f = fopen(out, "wb");
    if (!f) { perror(out); return -1; }
    int rc = put_frame(f, b0, which == 0 ? n0 - 3 : n0) | put_frame(f, b1, which == 1 ? n1 - 3 : n1);
    if (fclose(f) != 0) rc = -1;
    return rc;
}

TEST(test_trace_block_mid_chunk) {
    char path[256]; tmp_path(path, sizeof path, "read_two.vgct.zst");
    ASSERT_TRUE(make_trace(path, VGACAP_TRACE_BLOCK_CLOCKS + 100, 0) == 0);
    vgacap_trace_reader_t *r = open_or_say(path);
    ASSERT_TRUE(r != NULL);
    ASSERT_EQ_U(vgacap_trace_info(r)->blocks, 2);
    char err[256] = "";
    const uint8_t *p; size_t n0, n1;
    ASSERT_EQ_U(vgacap_trace_block_data(r, 0, &p, &n0, err, sizeof err), 0);
    uint8_t *b0 = malloc(n0); memcpy(b0, p, n0);
    ASSERT_EQ_U(vgacap_trace_block_data(r, 1, &p, &n1, err, sizeof err), 0);
    uint8_t *b1 = malloc(n1); memcpy(b1, p, n1);
    vgacap_trace_close(r);

    // Block 0 ends inside its RUNS pair: reading through it is an error.
    char cut[256]; tmp_path(cut, sizeof cut, "read_two_cut.vgct.zst");
    ASSERT_TRUE(write_cut_block(cut, b0, n0, b1, n1, 0) == 0);
    r = open_or_say(cut);
    ASSERT_TRUE(r != NULL);
    ASSERT_EQ_U(vgacap_trace_info(r)->blocks, 2);
    uint64_t got = 0;
    err[0] = 0;
    ASSERT_EQ_U((unsigned)(vgacap_trace_read(r, 0, UINT64_MAX, count_run, &got, err, sizeof err) + 1), 0);
    if (!strstr(err, "inside a chunk")) fprintf(stderr, "  err: %s\n", err);
    ASSERT_TRUE(strstr(err, "block 0: ends inside a chunk") != NULL);
    vgacap_trace_close(r);

    // The last block ends inside its META(close): it is not a whole block,
    // so it is dropped (the table is rebuilt), leaving block 0.
    ASSERT_TRUE(write_cut_block(cut, b0, n0, b1, n1, 1) == 0);
    r = open_or_say(cut);
    ASSERT_TRUE(r != NULL);
    ASSERT_EQ_U(vgacap_trace_info(r)->table_rebuilt, 1);
    ASSERT_EQ_U(vgacap_trace_info(r)->blocks, 1);
    ASSERT_EQ_U(vgacap_trace_info(r)->samples, BLOCK_SAMPLES);
    ASSERT_TRUE(vgacap_trace_info(r)->close_meta == NULL);
    vgacap_trace_close(r);
    free(b0); free(b1);
    remove(cut);
    remove(path);
}

// A version 1 board capture, gunzipped into tmp/: one pseudo-block with the
// sample total vgacap-dump prints.
static long long dump_total(const char *path) {
    char cmd[1024];
    snprintf(cmd, sizeof cmd, "'%s' '%s'", VGACAP_DUMP_PATH, path);
    FILE *p = popen(cmd, "r");
    if (!p) { perror("popen"); return -1; }
    char line[512]; long long total = -1;
    while (fgets(line, sizeof line, p))
        if (strncmp(line, "total_samples=", 14) == 0) total = strtoll(line + 14, NULL, 10);
    if (pclose(p) != 0) return -1;
    return total;
}

TEST(test_trace_v1_capture) {
    char path[256]; tmp_path(path, sizeof path, "tiny-logo-capture.vgacap");
    gzFile gz = gzopen(VGACAP_FIXTURES "/tiny-logo-capture.vgacap.gz", "rb");
    ASSERT_TRUE(gz != NULL);
    FILE *f = fopen(path, "wb");
    ASSERT_TRUE(f != NULL);
    uint8_t buf[65536]; int n;
    while ((n = gzread(gz, buf, sizeof buf)) > 0) ASSERT_EQ_U(fwrite(buf, 1, (size_t)n, f), (size_t)n);
    ASSERT_TRUE(n == 0);
    gzclose(gz);
    ASSERT_TRUE(fclose(f) == 0);

    long long want = dump_total(path);
    ASSERT_TRUE(want > 0);
    vgacap_trace_reader_t *r = open_or_say(path);
    ASSERT_TRUE(r != NULL);
    const vgacap_trace_info_t *in = vgacap_trace_info(r);
    ASSERT_EQ_U(in->compressed, 0);
    ASSERT_EQ_U(in->header.version, 1);
    ASSERT_EQ_U(in->blocks, 1);
    ASSERT_TRUE(in->open_meta == NULL && in->close_meta == NULL);
    ASSERT_EQ_U(in->samples, (unsigned long long)want);
    // Values are the capture's, not sample_value's: only totals and order.
    struct got *g = calloc(1, sizeof *g);
    char err[256] = "";
    ASSERT_EQ_U(vgacap_trace_read(r, 0, UINT64_MAX, on_run, g, err, sizeof err), 0);
    ASSERT_EQ_U(g->total, (unsigned long long)want);
    ASSERT_EQ_U(g->gaps, 0);
    memset(g, 0, sizeof *g);
    ASSERT_EQ_U(vgacap_trace_read(r, 1000, 1010, on_run, g, err, sizeof err), 0);
    ASSERT_EQ_U(g->total, 10);
    ASSERT_EQ_U(g->first[0], 1000);
    free(g);
    vgacap_trace_close(r);
    remove(path);
}

// ---- RUNS decoded straight from the pairs ------------------------------------
//
// The reader decodes a RUNS chunk it would accept straight from its pairs,
// and hands any other chunk (or one breaking a rule) to the stream reader.
// Plain streams with one RUNS chunk damaged in many ways (bits flipped in
// its header, count, values and runs) must give the same runs, in the same
// order, as the stream reader fed the same bytes, which is how they were
// read before (a plain stream is read leniently: losses are skipped).

struct membuf_t { uint8_t *p; size_t n, cap; };
static int mem_put(void *user, const uint8_t *buf, size_t len) {
    struct membuf_t *m = user;
    if (m->n + len > m->cap) {
        m->cap = (m->n + len) * 2;
        m->p = realloc(m->p, m->cap);
        if (!m->p) return -1;
    }
    memcpy(m->p + m->n, buf, len); m->n += len;
    return 0;
}

struct ev_runs { struct all_runs a; uint64_t n; int done; };
static void on_ev(void *user, const vgacap_event_t *ev) {
    struct ev_runs *e = user;
    if (e->done) return;
    if (ev->type == VGACAP_EV_HEADER) e->n = 0;
    else if (ev->type == VGACAP_EV_BLOCK) e->n = ev->u.block.first_sample;
    else if (ev->type == VGACAP_EV_RUN) { on_all(&e->a, e->n, ev->u.run.value, ev->u.run.run); e->n += ev->u.run.run; }
    else if (ev->type == VGACAP_EV_ERROR) e->done = 1;
}

static int stop_first_batch(void *user, const vgacap_trace_run_t *runs, size_t n) {
    (void)runs;
    *(size_t *)user += n;
    return 1;
}

TEST(test_trace_runs_fast_path) {
    char path[256]; tmp_path(path, sizeof path, "read_runs_fast.vgacap");
    vgacap_header_t h;
    vgacap_header_init_v2(&h, 43, 2, 42, 0);
    h.mode = VGACAP_MODE_EVENT; h.clock_hz = 25175000;
    // Three RUNS chunks of 3000 pairs (uo_out changes every clock), the
    // middle one damaged.
    enum { PAIRS = 3000 };
    uint64_t *vals = malloc(3 * PAIRS * sizeof *vals), *runs = malloc(3 * PAIRS * sizeof *runs);
    ASSERT_TRUE(vals && runs);
    for (size_t i = 0; i < 3 * PAIRS; i++) {
        vals[i] = (sample_value(2 * i + 1, 1) & ~CLK42) | ((uint64_t)(i % 3) << 8);
        runs[i] = i % 97 == 5 ? 300 + i : 2;   // some two-byte LEB128 runs
    }
    struct membuf_t m = { NULL, 0, 0 };
    vgacap_writer_t w;
    ASSERT_EQ_U(vgacap_writer_init(&w, mem_put, &m, &h), 0);
    ASSERT_EQ_U(vgacap_writer_meta(&w, OPEN_META, strlen(OPEN_META)), 0);
    ASSERT_EQ_U(vgacap_writer_block(&w, 0), 0);
    ASSERT_EQ_U(vgacap_writer_runs(&w, vals, runs, PAIRS), 0);
    size_t mid = m.n;
    ASSERT_EQ_U(vgacap_writer_runs(&w, vals + PAIRS, runs + PAIRS, PAIRS), 0);
    size_t mid_end = m.n;
    ASSERT_EQ_U(vgacap_writer_runs(&w, vals + 2 * PAIRS, runs + 2 * PAIRS, PAIRS), 0);
    ASSERT_EQ_U(vgacap_writer_meta(&w, CLOSE_META, strlen(CLOSE_META)), 0);
    uint8_t *damaged = malloc(m.n);
    ASSERT_TRUE(damaged != NULL);
    uint32_t lcg = 12345;
    int mismatches = 0, trials = 0;
    for (int t = 0; t < 400; t++) {
        memcpy(damaged, m.p, m.n);
        lcg = lcg * 1103515245u + 12345u;
        // Mostly within the chunk's first bytes (header, count, first pairs).
        size_t span = t % 2 ? 64 : mid_end - mid;
        size_t at = mid + (size_t)((lcg >> 8) % span);
        if (t == 1) at = m.n;                   // trial 1: undamaged
        if (at < m.n) damaged[at] ^= (uint8_t)(1u << ((lcg >> 4) & 7));
        if (t == 2) memcpy(damaged + mid + 12 + 7, damaged + mid + 12, 6);   // a pair equal to the one before
        ASSERT_EQ_U(spit(path, damaged, m.n), 0);
        struct ev_runs ref; memset(&ref, 0, sizeof ref);
        vgacap_reader_t *sr = malloc(sizeof *sr);
        ASSERT_TRUE(sr != NULL);
        vgacap_reader_init(sr, on_ev, &ref);
        vgacap_reader_feed(sr, damaged, m.n);
        free(sr);
        char err[256] = "";
        vgacap_trace_reader_t *r = vgacap_trace_open(path, err, sizeof err);
        if (r) {
            struct all_runs got = { 0, 0, NULL, NULL, NULL };
            ASSERT_EQ_U(vgacap_trace_read(r, 0, UINT64_MAX, on_all, &got, err, sizeof err), 0);
            if (!all_same(&ref.a, &got)) { mismatches++; fprintf(stderr, "  trial %d (byte %zu)\n", t, at); }
            if (t == 1) {
                ASSERT_EQ_U(got.n, 3 * PAIRS);
                // A batch function returning nonzero stops after that batch.
                size_t seen = 0;
                ASSERT_EQ_U(vgacap_trace_read_runs(r, 0, UINT64_MAX, stop_first_batch, &seen, err, sizeof err), 0);
                ASSERT_EQ_U(seen, 1024);
            }
            all_free(&got);
            vgacap_trace_close(r);
            trials++;
        }
        all_free(&ref.a);
    }
    ASSERT_EQ_U(mismatches, 0);
    ASSERT_TRUE(trials >= 390);
    free(damaged); free(m.p); free(vals); free(runs);
    remove(path);
}

// ---- the batched reader against 12dfaf7's run-at-a-time reader ---------------
//
// ref_read is vgacap_trace_read as it was at 12dfaf7 (before the batched
// reader), on the public API: the stream reader fed each block, every RUN
// event clipped to [from, to) as its on_read did, the same errors. Hostile
// streams, plain (read leniently) and compressed (strict, each block a zstd
// frame with its checksum and no seek table, so the frames are walked), must
// read the same through vgacap_trace_read: the same runs, return and error.

struct ref_rd {
    uint64_t n, from, to, clk;
    int done, strict;
    const char *what;
    struct all_runs a;
};

static void ref_on_read(void *user, const vgacap_event_t *ev) {   // 12dfaf7's on_read
    struct ref_rd *d = user;
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
        on_all(&d->a, s, v, t - s);
        if (e >= d->to) d->done = 1;
        break;
    }
    case VGACAP_EV_RESYNC:
        if (d->strict) { d->what = ev->u.resync.what; d->done = 1; }
        break;
    case VGACAP_EV_ERROR: d->what = ev->u.error.what; d->done = 1; break;
    default: break;
    }
}

static int ref_mid_chunk(const vgacap_reader_t *sr) { return sr->scanning || sr->in_payload || sr->hdrfill; }

static void ref_first_block(void *user, const vgacap_event_t *ev) {
    uint64_t *f = user;
    if (ev->type == VGACAP_EV_BLOCK && f[1] == 0) { f[0] = ev->u.block.first_sample; f[1] = 1; }
}

// 12dfaf7's vgacap_trace_read, collecting the runs into d->a.
static int ref_read(vgacap_trace_reader_t *r, uint64_t from, uint64_t to, struct ref_rd *d,
                    const uint8_t *plain, size_t plain_len, char *err, size_t errlen) {
    const vgacap_trace_info_t *in = vgacap_trace_info(r);
    if (from >= to || from >= in->samples) return 0;
    d->from = from; d->to = to;
    d->clk = in->header.clock_bit == VGACAP_CLOCK_BIT_NONE ? 0 : (uint64_t)1 << in->header.clock_bit;
    vgacap_reader_t *sr = malloc(sizeof *sr);
    if (!sr) return -1;
    int rc = 0;
    if (!in->compressed) {
        vgacap_reader_init(sr, ref_on_read, d);
        vgacap_reader_feed(sr, plain, plain_len);
        if (d->what) { snprintf(err, errlen, "%s", d->what); rc = -1; }
        free(sr);
        return rc;
    }
    d->strict = 1;
    uint64_t *first = calloc(in->blocks, sizeof *first);
    for (uint32_t i = 0; i < in->blocks; i++) {   // each block's BLKH (checked at open)
        const uint8_t *p; size_t n;
        if (vgacap_trace_block_data(r, i, &p, &n, err, errlen)) { free(first); free(sr); return -1; }
        uint64_t f[2] = { 0, 0 };
        if (i == 0) vgacap_reader_init(sr, ref_first_block, f);
        else vgacap_reader_init_header(sr, ref_first_block, f, &in->header);
        vgacap_reader_feed(sr, p, n);
        first[i] = f[0];
    }
    uint32_t lo = 0, hi = in->blocks;
    while (hi - lo > 1) {
        uint32_t mid = lo + (hi - lo) / 2;
        if (first[mid] <= from) lo = mid; else hi = mid;
    }
    for (uint32_t i = lo; i < in->blocks && first[i] < to && !d->done; i++) {
        const uint8_t *p; size_t n;
        if (vgacap_trace_block_data(r, i, &p, &n, err, errlen)) { rc = -1; break; }
        if (i == 0) vgacap_reader_init(sr, ref_on_read, d);
        else vgacap_reader_init_header(sr, ref_on_read, d, &in->header);
        vgacap_reader_feed(sr, p, n);
        if (d->what) { snprintf(err, errlen, "block %u: %s", i, d->what); rc = -1; break; }
        if (!d->done && ref_mid_chunk(sr)) { snprintf(err, errlen, "block %u: ends inside a chunk", i); rc = -1; break; }
        if (!d->done && i + 1 < in->blocks && d->n != first[i + 1]) {
            snprintf(err, errlen, "block %u ends at sample %llu but block %u starts at %llu", i,
                     (unsigned long long)d->n, i + 1, (unsigned long long)first[i + 1]);
            rc = -1; break;
        }
    }
    free(first); free(sr);
    return rc;
}

// Stream building, chunk by chunk (no writer checks: hostile on purpose).
static void put_chunk(struct membuf_t *m, const char *tag, const uint8_t *p, uint32_t len) {
    uint8_t h[8];
    memcpy(h, tag, 4);
    for (int i = 0; i < 4; i++) h[4 + i] = (uint8_t)(len >> (8 * i));
    mem_put(m, h, 8);
    mem_put(m, p, len);
}

static void put_blkh(struct membuf_t *m, uint64_t first) {
    uint8_t b[8];
    for (int i = 0; i < 8; i++) b[i] = (uint8_t)(first >> (8 * i));
    put_chunk(m, VGACAP_TAG_BLOCK, b, 8);
}

// A RUNS chunk of `pairs` pairs (43-bit values, adjacent ones different),
// runs from rnd: mostly 1 to 4, some multi-byte, some of `huge`.
static uint32_t fz;
static uint32_t rnd(void) { fz = fz * 1103515245u + 12345u; return fz >> 8; }
static void put_runs(struct membuf_t *m, unsigned pairs, uint64_t huge) {
    uint8_t *p = malloc(4 + (size_t)pairs * 16);
    size_t n = 4;
    for (int i = 0; i < 4; i++) p[i] = (uint8_t)(pairs >> (8 * i));
    for (unsigned k = 0; k < pairs; k++) {
        uint64_t v = (BASE43 | (k & 0xFF) | ((uint64_t)(rnd() & 7) << 8)) & ~CLK42;
        for (int i = 0; i < 6; i++) p[n++] = (uint8_t)(v >> (8 * i));
        uint32_t c = rnd() % 64;
        uint64_t run = c < 50 ? 1 + c % 4 : c < 60 ? 100 + rnd() % 100000 : huge ? huge : 1;
        do { uint8_t b = run & 0x7F; run >>= 7; p[n++] = (uint8_t)(b | (run ? 0x80 : 0)); } while (run);
    }
    put_chunk(m, VGACAP_TAG_RUNS, p, (uint32_t)n);
    free(p);
}

// One hostile change to bytes [lo, hi) of m: the format note's faults (bits
// flipped, bytes lost or added, a chunk cut short, a length or count off, a
// BLKH moved far or near 2^64).
static void damage(struct membuf_t *m, size_t lo, size_t hi) {
    if (hi <= lo + 16) return;
    size_t at = lo + rnd() % (hi - lo);
    switch (rnd() % 7) {
    case 0: m->p[at] ^= (uint8_t)(1u << (rnd() % 8)); break;
    case 1: memmove(m->p + at, m->p + at + 1, m->n - at - 1); m->n--; break;          // a byte lost
    case 2: mem_put(m, (const uint8_t *)"", 1); memmove(m->p + at + 1, m->p + at, m->n - at - 1);
            m->p[at] = (uint8_t)rnd(); break;                                           // a byte added
    case 3: m->n = at; break;                                                           // cut short
    case 4: m->p[at] = 0; break;
    case 5: m->p[at] = 0xFF; break;
    default: break;                                                                     // none
    }
}

struct fuzz_stats { int plain, zstd, unopened, mismatches; };

static void compare(const char *path, const uint8_t *plain, size_t plain_len, struct fuzz_stats *st, int zstd,
                    const char *what) {
    char err[256] = "";
    vgacap_trace_reader_t *r = vgacap_trace_open(path, err, sizeof err);
    if (!r) { st->unopened++; return; }
    const uint64_t s = vgacap_trace_info(r)->samples;
    const uint64_t ranges[][2] = { { 0, UINT64_MAX }, { 1, s / 2 + 1 }, { s / 3, UINT64_MAX },
                                   { UINT64_MAX - 100, UINT64_MAX }, { rnd() % (s + 1), rnd() % (s + 2) + s / 2 } };
    for (unsigned i = 0; i < sizeof ranges / sizeof ranges[0]; i++) {
        struct ref_rd d; memset(&d, 0, sizeof d);
        char e1[256] = "", e2[256] = "";
        int r1 = ref_read(r, ranges[i][0], ranges[i][1], &d, plain, plain_len, e1, sizeof e1);
        struct all_runs got = { 0, 0, NULL, NULL, NULL };
        int r2 = vgacap_trace_read(r, ranges[i][0], ranges[i][1], on_all, &got, e2, sizeof e2);
        if (r1 != r2 || strcmp(e1, e2) || !all_same(&d.a, &got)) {
            st->mismatches++;
            fprintf(stderr, "  %s, range %u: rc %d/%d, '%s' / '%s'\n", what, i, r1, r2, e1, e2);
        }
        all_free(&d.a); all_free(&got);
    }
    if (zstd) st->zstd++; else st->plain++;
    vgacap_trace_close(r);
}

// A stream: VGCH, META(open), BLKH(b0), RUNS..., blocks of RUNS behind
// BLKHs, META(close). Plain: one file; zstd: each block a frame.
static void fuzz_one(const char *path, int zstd, uint64_t b0, uint64_t huge, int hostile, struct fuzz_stats *st,
                     const char *what) {
    vgacap_header_t h;
    vgacap_header_init_v2(&h, 43, 2, 42, 0);
    h.mode = VGACAP_MODE_EVENT; h.clock_hz = 25175000;
    enum { NB = 3 };
    struct membuf_t blk[NB];
    memset(blk, 0, sizeof blk);
    vgacap_writer_t w;
    vgacap_writer_init(&w, mem_put, &blk[0], &h);
    vgacap_writer_meta(&w, OPEN_META, strlen(OPEN_META));
    uint64_t at = b0;
    for (int b = 0; b < NB; b++) {
        size_t start = blk[b].n;
        put_blkh(&blk[b], b == 0 ? (zstd ? 0 : b0) : b == 1 && zstd && b0 ? b0 : at);
        size_t runs_from = blk[b].n;
        unsigned chunks = 1 + rnd() % 3;
        for (unsigned c = 0; c < chunks; c++) put_runs(&blk[b], 20 + rnd() % 300, huge);
        // where the next block starts, from what was written (an undamaged reading)
        struct ev_runs e; memset(&e, 0, sizeof e);
        vgacap_reader_t *sr = malloc(sizeof *sr);
        vgacap_reader_init_header(sr, on_ev, &e, &h);
        vgacap_reader_feed(sr, blk[b].p + start, blk[b].n - start);
        free(sr);
        at = e.n;
        all_free(&e.a);
        if (b == NB - 1) put_chunk(&blk[b], VGACAP_TAG_META, (const uint8_t *)CLOSE_META, (uint32_t)strlen(CLOSE_META));
        if (hostile && (rnd() % NB) == (unsigned)b) damage(&blk[b], b == 0 && zstd ? runs_from : start, blk[b].n);
    }
    if (!zstd) {
        struct membuf_t all = { NULL, 0, 0 };
        for (int b = 0; b < NB; b++) mem_put(&all, blk[b].p, blk[b].n);
        spit(path, all.p, all.n);
        compare(path, all.p, all.n, st, 0, what);
        free(all.p);
    } else {
        struct membuf_t all = { NULL, 0, 0 };
        ZSTD_CCtx *c = ZSTD_createCCtx();
        ZSTD_CCtx_setParameter(c, ZSTD_c_checksumFlag, 1);
        for (int b = 0; b < NB; b++) {
            size_t cap = ZSTD_compressBound(blk[b].n);
            uint8_t *z = malloc(cap);
            size_t zn = ZSTD_compress2(c, z, cap, blk[b].p, blk[b].n);
            if (!ZSTD_isError(zn)) mem_put(&all, z, zn);
            free(z);
        }
        ZSTD_freeCCtx(c);
        spit(path, all.p, all.n);
        compare(path, NULL, 0, st, 1, what);
        free(all.p);
    }
    for (int b = 0; b < NB; b++) free(blk[b].p);
}

TEST(test_trace_runs_against_12dfaf7) {
    char path[256]; tmp_path(path, sizeof path, "read_runs_fuzz.bin");
    struct fuzz_stats st = { 0, 0, 0, 0 };
    fz = 2026;
    // A BLKH near 2^64: the runs after it wrap (plain; a trace's block 0
    // must start at BLKH(0), so its later blocks carry it).
    for (int zstd = 0; zstd < 2; zstd++) {
        char what[64];
        snprintf(what, sizeof what, "%s BLKH near 2^64", zstd ? "zstd" : "plain");
        fuzz_one(path, zstd, UINT64_MAX - 1000, 0, 0, &st, what);
        // Huge (ten-byte LEB128) runs, read from past 0.
        snprintf(what, sizeof what, "%s huge runs", zstd ? "zstd" : "plain");
        fuzz_one(path, zstd, 0, UINT64_MAX / 2 + 12345, 0, &st, what);
    }
    // Hostile streams of both kinds.
    for (int t = 0; t < 400; t++) {
        char what[64];
        snprintf(what, sizeof what, "trial %d", t);
        uint64_t b0 = t % 5 == 0 ? UINT64_MAX - rnd() % 100000 : t % 5 == 1 ? rnd() : 0;
        uint64_t huge = t % 3 == 0 ? UINT64_MAX - rnd() : t % 3 == 1 ? (uint64_t)rnd() << 32 : 0;
        fuzz_one(path, t & 1, b0, huge, 1, &st, what);
    }
    fprintf(stderr, "  %d plain and %d zstd streams compared, %d that do not open\n", st.plain, st.zstd, st.unopened);
    ASSERT_EQ_U(st.mismatches, 0);
    ASSERT_TRUE(st.plain >= 150 && st.zstd >= 100);
    remove(path);
}

TEST(test_trace_not_a_trace) {
    char path[256]; tmp_path(path, sizeof path, "read_junk.bin");
    char err[256] = "";
    ASSERT_TRUE(spit(path, (const uint8_t *)"hello, world", 12) == 0);
    ASSERT_TRUE(vgacap_trace_open(path, err, sizeof err) == NULL);
    ASSERT_TRUE(strstr(err, "neither") != NULL);
    ASSERT_TRUE(spit(path, (const uint8_t *)"", 0) == 0);
    err[0] = 0;
    ASSERT_TRUE(vgacap_trace_open(path, err, sizeof err) == NULL);
    ASSERT_TRUE(err[0] != 0);
    remove(path);
    err[0] = 0;
    ASSERT_TRUE(vgacap_trace_open("tmp/no-such-file", err, sizeof err) == NULL);
    ASSERT_TRUE(strstr(err, "no-such-file") != NULL);
}

int main(void) {
    RUN(test_trace_range);
    RUN(test_trace_clock_bits);
    RUN(test_trace_truncated);
    RUN(test_trace_truncated_in_table);
    RUN(test_trace_bad_table);
    RUN(test_trace_decompressed_plain);
    RUN(test_trace_plain_vgch);
    RUN(test_trace_open_touches_little);
    RUN(test_trace_block_mid_chunk);
    RUN(test_trace_v1_capture);
    RUN(test_trace_runs_fast_path);
    RUN(test_trace_runs_against_12dfaf7);
    RUN(test_trace_not_a_trace);
    if (five_ready) remove(five_path());
    RUN_TESTS_END();
}
