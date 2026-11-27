// SPDX-License-Identifier: Apache-2.0
// fsbench ctr: what VGCH v2's container choices cost and buy, on a real
// trace's runs. For each block length B (in clocks), the runs are cut into
// blocks exactly as the vgch2 writer cuts them (a block ends at B clocks
// from its first sample, or before its pairs pass 16 MiB - 64 KiB; a run
// crossing the end is split), each block being BLKH + one RUNS chunk. Each
// block is compressed with zstd level 1 (checksum on, as vgch2), then
// decompressed with the checksum checked and with it ignored, and its
// pairs decoded; all timed by the thread CPU clock.
//
//   fsbench ctr TRACE --block-clocks 18,20,22,24,26 (log2 values)
#define ZSTD_STATIC_LINKING_ONLY
#include "fs.h"
#include "vgacap/stream.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <zstd.h>

#define CAP (16u * 1024u * 1024u)
#define FILL_LIMIT (CAP - 64u * 1024u - 16u)

typedef struct {
    uint64_t block_samples;
    uint8_t *buf, *z, *d;
    size_t fill, zcap;
    uint32_t pairs;
    uint64_t first, n;     // the block's first sample, the next sample
    uint64_t cur, run;     // the run being built (value without clk)
    int have;
    ZSTD_CCtx *cc;
    ZSTD_DCtx *dc_check, *dc_ignore;
    // results
    uint64_t blocks, raw_bytes, comp_bytes, max_raw, max_samples_block;
    double comp_ns, dec_check_ns, dec_ignore_ns, parse_ns;
    uint64_t parsed_pairs, by_size;
} ctr_t;

static void block_begin(ctr_t *c) {
    c->fill = 0;
    memcpy(c->buf, "BLKH", 4);
    uint32_t l = 8;
    memcpy(c->buf + 4, &l, 4);
    memcpy(c->buf + 8, &c->first, 8);
    memcpy(c->buf + 16, "RUNS", 4);
    c->fill = 28;   // RUNS length and count at 20, 24
    c->pairs = 0;
}

static void block_end(ctr_t *c) {
    if (c->pairs == 0) return;
    const uint32_t len = (uint32_t)(c->fill - 24), cnt = c->pairs;
    memcpy(c->buf + 20, &len, 4);
    memcpy(c->buf + 24, &cnt, 4);
    double t0 = fs_thread_ns();
    size_t z = ZSTD_compress2(c->cc, c->z, c->zcap, c->buf, c->fill);
    double t1 = fs_thread_ns();
    if (ZSTD_isError(z)) { fprintf(stderr, "fs: zstd: %s\n", ZSTD_getErrorName(z)); exit(2); }
    size_t d1 = ZSTD_decompressDCtx(c->dc_check, c->d, CAP, c->z, z);
    double t2 = fs_thread_ns();
    size_t d2 = ZSTD_decompressDCtx(c->dc_ignore, c->d, CAP, c->z, z);
    double t3 = fs_thread_ns();
    if (ZSTD_isError(d1) || ZSTD_isError(d2) || d1 != c->fill) { fprintf(stderr, "fs: decompress failed\n"); exit(2); }
    // Decode the pairs (6 value bytes, LEB128 run), as a reader must.
    const uint8_t *p = c->d + 28, *e = c->d + c->fill;
    uint64_t acc = 0;
    while (p < e) {
        uint64_t v = 0;
        memcpy(&v, p, 6);
        p += 6;
        uint64_t r = 0;
        unsigned sh = 0;
        uint8_t b;
        do { b = *p++; r |= (uint64_t)(b & 0x7f) << sh; sh += 7; } while (b & 0x80);
        acc += v ^ r;
    }
    double t4 = fs_thread_ns();
    __asm__ volatile("" : : "r"(acc));
    c->comp_ns += t1 - t0; c->dec_check_ns += t2 - t1; c->dec_ignore_ns += t3 - t2; c->parse_ns += t4 - t3;
    c->blocks++; c->raw_bytes += c->fill; c->comp_bytes += z; c->parsed_pairs += c->pairs;
    if (c->fill > c->max_raw) c->max_raw = c->fill;
    if (c->n - c->first > c->max_samples_block) c->max_samples_block = c->n - c->first;
}

static inline void put_pair(ctr_t *c, uint64_t v, uint64_t run) {
    c->fill += vgacap_run_pair_put_inline(c->buf + c->fill, v, 6, run);
    c->pairs++;
}

// Adds `len` samples of value v (clock bit cleared) at sample c->n.
static void add(ctr_t *c, uint64_t v, uint64_t len) {
    while (len) {
        const uint64_t room = c->first + c->block_samples - c->n;
        const uint64_t take = len < room ? len : room;
        if (c->have && v == c->cur) {
            c->run += take;
        } else {
            if (c->have) {
                put_pair(c, c->cur, c->run);
                if (c->fill > FILL_LIMIT) {   // a block end by size
                    block_end(c);
                    c->by_size++;
                    c->have = 0;
                    c->first = c->n;
                    block_begin(c);
                    add(c, v, len);
                    return;
                }
            }
            c->cur = v; c->run = take; c->have = 1;
        }
        c->n += take;
        len -= take;
        if (c->n == c->first + c->block_samples) {   // a block end by clocks: split the run
            put_pair(c, c->cur, c->run);
            c->have = 0;
            block_end(c);
            c->first = c->n;
            block_begin(c);
        }
    }
}

static int on_runs(void *user, const fs_run_t *r, size_t n) {
    ctr_t *c = user;
    for (size_t i = 0; i < n; i++) add(c, r[i].value & ~(1ull << 42), r[i].run);
    return 0;
}

int fs_mode_ctr(int argc, char **argv);
int fs_mode_ctr(int argc, char **argv) {
    if (argc < 1) { fprintf(stderr, "usage: fsbench ctr TRACE [--block-clocks 18,20,...]\n"); return 2; }
    char list[256] = "18,20,22,24,26";
    for (int i = 1; i + 1 < argc; i += 2)
        if (!strcmp(argv[i], "--block-clocks")) snprintf(list, sizeof list, "%s", argv[i + 1]);
    fs_source_t *src = fs_source_open(argv[0]);
    if (!src) return 1;
    const char *base = strrchr(argv[0], '/');
    base = base ? base + 1 : argv[0];
    printf("trace,log2_block_clocks,blocks,blocks_by_size,raw_bytes,comp_bytes,max_block_raw,comp_ns,dec_check_ns,"
           "dec_ignore_ns,parse_ns,pairs\n");
    for (char *save = NULL, *tok = strtok_r(list, ",", &save); tok; tok = strtok_r(NULL, ",", &save)) {
        const int lg = atoi(tok);
        ctr_t *c = calloc(1, sizeof *c);
        c->block_samples = 2ull << lg;
        c->buf = malloc(CAP); c->d = malloc(CAP);
        c->zcap = ZSTD_compressBound(CAP); c->z = malloc(c->zcap);
        c->cc = ZSTD_createCCtx();
        ZSTD_CCtx_setParameter(c->cc, ZSTD_c_compressionLevel, 1);
        ZSTD_CCtx_setParameter(c->cc, ZSTD_c_checksumFlag, 1);
        c->dc_check = ZSTD_createDCtx();
        c->dc_ignore = ZSTD_createDCtx();
        ZSTD_DCtx_setParameter(c->dc_ignore, ZSTD_d_forceIgnoreChecksum, ZSTD_d_ignoreChecksum);
        block_begin(c);
        if (fs_source_runs(src, 0, UINT64_MAX, on_runs, c)) return 1;
        if (c->have) put_pair(c, c->cur, c->run);
        block_end(c);
        printf("%s,%d,%llu,%llu,%llu,%llu,%llu,%.0f,%.0f,%.0f,%.0f,%llu\n", base, lg, (unsigned long long)c->blocks,
               (unsigned long long)c->by_size, (unsigned long long)c->raw_bytes, (unsigned long long)c->comp_bytes,
               (unsigned long long)c->max_raw, c->comp_ns, c->dec_check_ns, c->dec_ignore_ns, c->parse_ns,
               (unsigned long long)c->parsed_pairs);
        fflush(stdout);
        ZSTD_freeCCtx(c->cc); ZSTD_freeDCtx(c->dc_check); ZSTD_freeDCtx(c->dc_ignore);
        free(c->buf); free(c->d); free(c->z); free(c);
    }
    fs_source_close(src);
    return 0;
}
