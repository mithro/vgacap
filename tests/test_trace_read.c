// SPDX-License-Identifier: Apache-2.0
// The trace reader (src/trace/trace_read.c): ranges by block, the seek table
// rebuilt for a trace cut short, plain VGCH streams. The traces are made with
// the trace writer; every file goes under tmp/ in the working directory (the
// build directory under CTest).
#define _POSIX_C_SOURCE 200809L   // popen
#define ZSTD_STATIC_LINKING_ONLY  // ZSTD_isSkippableFrame
#include "harness.h"
#include "vgacap/trace.h"
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
TEST(test_trace_plain_vgch) {
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
    RUN(test_trace_plain_vgch);
    RUN(test_trace_v1_capture);
    RUN(test_trace_not_a_trace);
    if (five_ready) remove(five_path());
    RUN_TESTS_END();
}
