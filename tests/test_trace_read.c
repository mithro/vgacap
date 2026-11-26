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

// n is a u64. In a plain v2 stream (no clock bit) a RUNS chunk whose run
// would take n past 2^64 - 1 is skipped by the stream reader ("RUNS run takes
// n past 64 bits"), so neither the sample total nor a read wraps: the chunk
// after it carries on from 2^64 - 2. Before the fix the skipped run was
// delivered, n wrapped to 3 and the total was 4.
static int file_write(void *u, const uint8_t *b, size_t n) { return fwrite(b, 1, n, u) == n ? 0 : -1; }

TEST(test_trace_run_past_64_bits) {
    char path[256]; tmp_path(path, sizeof path, "read_wrap.vgacap");
    FILE *f = fopen(path, "wb");
    ASSERT_TRUE(f != NULL);
    vgacap_header_t h; vgacap_header_init_v2(&h, 8, 1, VGACAP_CLOCK_BIT_NONE, 0);
    vgacap_writer_t w; ASSERT_EQ_U(vgacap_writer_init(&w, file_write, f, &h), 0);
    const uint64_t v[3] = { 1, 2, 3 }, r[3] = { UINT64_MAX - 1u, 5, 1 };
    for (int i = 0; i < 3; i++) ASSERT_EQ_U(vgacap_writer_runs(&w, &v[i], &r[i], 1), 0);
    ASSERT_EQ_U(fclose(f), 0);
    vgacap_trace_reader_t *t = open_or_say(path);
    ASSERT_TRUE(t != NULL);
    ASSERT_EQ_U(vgacap_trace_info(t)->samples, UINT64_MAX);
    struct all_runs a = { 0, 0, NULL, NULL, NULL };
    char err[256] = "";
    ASSERT_EQ_U(vgacap_trace_read(t, UINT64_MAX - 4u, UINT64_MAX, on_all, &a, err, sizeof err), 0);
    ASSERT_EQ_U(a.n, 2);
    ASSERT_EQ_U(a.first[0], UINT64_MAX - 4u); ASSERT_EQ_U(a.value[0], 1); ASSERT_EQ_U(a.len[0], 3);
    ASSERT_EQ_U(a.first[1], UINT64_MAX - 1u); ASSERT_EQ_U(a.value[1], 3); ASSERT_EQ_U(a.len[1], 1);
    all_free(&a);
    vgacap_trace_close(t);
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
    RUN(test_trace_run_past_64_bits);
    RUN(test_trace_not_a_trace);
    if (five_ready) remove(five_path());
    RUN_TESTS_END();
}
