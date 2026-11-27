// SPDX-License-Identifier: Apache-2.0
// The vendored host-only libraries (third_party/): each one does its job.
#include "harness.h"
#include <stdint.h>
#include <zstd.h>
#include <zstd_seekable.h>
#include <xxhash.h>
#include <fstapi.h>

TEST(zstd_round_trip) {
    size_t n = 1u << 20;
    unsigned char *src = malloc(n), *dst = malloc(n);
    size_t cap = ZSTD_compressBound(n);
    unsigned char *cmp = malloc(cap);
    ASSERT_TRUE(src && dst && cmp);
    for (size_t i = 0; i < n; i++) src[i] = (unsigned char)((i * 7u + (i >> 8)) & 0xff);
    size_t c = ZSTD_compress(cmp, cap, src, n, 3);
    ASSERT_TRUE(!ZSTD_isError(c));
    size_t d = ZSTD_decompress(dst, n, cmp, c);
    ASSERT_EQ_U(d, n);
    ASSERT_EQ_MEM(src, dst, n);
    free(src); free(dst); free(cmp);
}

TEST(seekable_two_frames) {
    enum { BLK = 4096 };
    unsigned char blk[2][BLK], out[BLK], buf[2 * BLK + 1024];
    for (int b = 0; b < 2; b++)
        for (int i = 0; i < BLK; i++) blk[b][i] = (unsigned char)(i * (b + 3) + b);
    size_t pos = 0;
    ZSTD_frameLog *fl = ZSTD_seekable_createFrameLog(0);
    ASSERT_TRUE(fl != NULL);
    for (int b = 0; b < 2; b++) {
        size_t c = ZSTD_compress(buf + pos, sizeof buf - pos, blk[b], BLK, 3);
        ASSERT_TRUE(!ZSTD_isError(c));
        ASSERT_TRUE(!ZSTD_isError(ZSTD_seekable_logFrame(fl, (unsigned)c, BLK, 0)));
        pos += c;
    }
    ZSTD_outBuffer ob = { buf + pos, sizeof buf - pos, 0 };
    ASSERT_TRUE(ZSTD_seekable_writeSeekTable(fl, &ob) == 0);
    pos += ob.pos;
    ZSTD_seekable_freeFrameLog(fl);

    ZSTD_seekable *zs = ZSTD_seekable_create();
    ASSERT_TRUE(zs != NULL);
    ASSERT_TRUE(!ZSTD_isError(ZSTD_seekable_initBuff(zs, buf, pos)));
    ASSERT_EQ_U(ZSTD_seekable_getNumFrames(zs), 2);
    ASSERT_EQ_U(ZSTD_seekable_decompressFrame(zs, out, BLK, 1), BLK);
    ASSERT_EQ_MEM(out, blk[1], BLK);
    ZSTD_seekable_free(zs);
}

TEST(xxh3_128_streaming) {
    // XXH3-128 of nothing, as upstream's tests give it, and a streamed hash in
    // uneven pieces equal to the one-shot hash of the same bytes.
    XXH128_hash_t e = XXH3_128bits("", 0);
    ASSERT_EQ_U(e.high64, 0x99aa06d3014798d8ULL);
    ASSERT_EQ_U(e.low64, 0x6001c324468d497fULL);
    size_t n = 1000003;
    unsigned char *buf = malloc(n);
    ASSERT_TRUE(buf);
    for (size_t i = 0; i < n; i++) buf[i] = (unsigned char)((i * 31u + (i >> 9)) & 0x3f);
    XXH3_state_t *st = XXH3_createState();
    ASSERT_TRUE(st && XXH3_128bits_reset(st) == XXH_OK);
    for (size_t off = 0, step = 1; off < n; off += step, step = step * 3 + 1) {
        size_t len = step < n - off ? step : n - off;
        ASSERT_TRUE(XXH3_128bits_update(st, buf + off, len) == XXH_OK);
    }
    ASSERT_TRUE(XXH128_isEqual(XXH3_128bits_digest(st), XXH3_128bits(buf, n)));
    XXH3_freeState(st);
    free(buf);
}

struct change { uint64_t t; unsigned h; char v[16]; };
struct seen { struct change c[16]; int n; };

static void on_change(void *user, uint64_t t, fstHandle h, const unsigned char *v) {
    struct seen *s = user;
    if (s->n < 16) {
        s->c[s->n].t = t; s->c[s->n].h = h;
        snprintf(s->c[s->n].v, sizeof s->c[s->n].v, "%s", (const char *)v);
    }
    s->n++;
}

TEST(fst_write_read) {
    // Written to the working directory (the build directory under CTest).
    const char *path = "test_third_party.fst";
    void *w = fstWriterCreate(path, 1);
    ASSERT_TRUE(w != NULL);
    fstWriterSetTimescale(w, -9);
    fstHandle a = fstWriterCreateVar(w, FST_VT_VCD_WIRE, FST_VD_IMPLICIT, 1, "a", 0);
    fstHandle b = fstWriterCreateVar(w, FST_VT_VCD_WIRE, FST_VD_IMPLICIT, 8, "b", 0);
    fstWriterEmitValueChange(w, a, "0");
    fstWriterEmitValueChange(w, b, "00000000");
    fstWriterEmitTimeChange(w, 10);
    fstWriterEmitValueChange(w, a, "1");
    fstWriterEmitTimeChange(w, 20);
    fstWriterEmitValueChange(w, b, "10100101");
    fstWriterEmitTimeChange(w, 30);
    fstWriterEmitValueChange(w, a, "0");
    fstWriterClose(w);

    void *r = fstReaderOpen(path);
    ASSERT_TRUE(r != NULL);
    fstReaderSetFacProcessMaskAll(r);
    struct seen s = { .n = 0 };
    ASSERT_TRUE(fstReaderIterBlocks2(r, on_change, NULL, &s, NULL) != 0);
    fstReaderClose(r);
    remove(path);
    // The two initial values at t=0, then the three changes.
    ASSERT_EQ_U(s.n, 5);
    int found = 0;
    for (int i = 0; i < s.n; i++) {
        if (s.c[i].t == 0 && s.c[i].h == a && !strcmp(s.c[i].v, "0")) found |= 1;
        if (s.c[i].t == 10 && s.c[i].h == a && !strcmp(s.c[i].v, "1")) found |= 2;
        if (s.c[i].t == 20 && s.c[i].h == b && !strcmp(s.c[i].v, "10100101")) found |= 4;
        if (s.c[i].t == 30 && s.c[i].h == a && !strcmp(s.c[i].v, "0")) found |= 8;
    }
    ASSERT_EQ_U(found, 15);
}

int main(void) {
    RUN(zstd_round_trip);
    RUN(seekable_two_frames);
    RUN(xxh3_128_streaming);
    RUN(fst_write_read);
    RUN_TESTS_END();
}
