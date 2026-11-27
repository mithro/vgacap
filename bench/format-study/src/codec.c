// SPDX-License-Identifier: Apache-2.0
// The study's compressors and raw-block transforms (codec.h).
#define ZSTD_STATIC_LINKING_ONLY
#include "codec.h"
#include "fs.h"
#include "lz4.h"
#include "lz4hc.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <zlib.h>
#include <zstd.h>
#include "vgacap/stream.h"   // vgacap_leb128_put_inline

const char *const fs_comp_name[C_COUNT] = { "none", "zstd-5", "zstd-1", "zstd1", "zstd3", "zstd9", "zstd1-long27",
                                            "lz4", "lz4hc9", "deflate1", "deflate6", "zstd1-nocheck" };
const char *const fs_transform_name[T_COUNT] = { "pack6", "xor6", "byteplanes", "xor-byteplanes", "xor-bitplanes",
                                                 "pack6clk", "planar-runs", "changelog-planar", "events-planar" };

int fs_comp_by_name(const char *name) {
    for (int i = 0; i < C_COUNT; i++)
        if (!strcmp(fs_comp_name[i], name)) return i;
    return -1;
}
int fs_transform_by_name(const char *name) {
    if (!strcmp(name, "identity")) return T_IDENTITY;
    for (int i = 0; i < T_COUNT; i++)
        if (!strcmp(fs_transform_name[i], name)) return i;
    return -1;
}

// ---- compressors -----------------------------------------------------------------------
static __thread ZSTD_CCtx *cctx[C_COUNT];
static __thread ZSTD_DCtx *dctx;

static ZSTD_CCtx *zctx(int comp) {
    if (cctx[comp]) return cctx[comp];
    ZSTD_CCtx *c = ZSTD_createCCtx();
    int level = comp == C_ZSTD_M5 ? -5 : comp == C_ZSTD_M1 ? -1 : comp == C_ZSTD_3 ? 3 : comp == C_ZSTD_9 ? 9 : 1;
    ZSTD_CCtx_setParameter(c, ZSTD_c_compressionLevel, level);
    ZSTD_CCtx_setParameter(c, ZSTD_c_checksumFlag, comp == C_ZSTD_1_NOCHK ? 0 : 1);   // vgch2 sets it
    if (comp == C_ZSTD_1_LONG) {
        ZSTD_CCtx_setParameter(c, ZSTD_c_enableLongDistanceMatching, 1);
        ZSTD_CCtx_setParameter(c, ZSTD_c_windowLog, 27);
    }
    return cctx[comp] = c;
}

size_t fs_comp_bound(size_t n) {
    size_t a = ZSTD_compressBound(n), b = (size_t)LZ4_compressBound((int)(n < 0x7e000000u ? n : 0x7e000000u)),
           c = (size_t)compressBound((uLong)n);
    size_t m = a > b ? a : b;
    return (m > c ? m : c) + 64;
}

size_t fs_compress(int comp, const uint8_t *src, size_t n, uint8_t *dst, size_t cap) {
    switch (comp) {
    case C_NONE: memcpy(dst, src, n); return n ? n : 0;
    case C_LZ4: {
        int r = LZ4_compress_default((const char *)src, (char *)dst, (int)n, (int)cap);
        return r > 0 ? (size_t)r : 0;
    }
    case C_LZ4HC_9: {
        int r = LZ4_compress_HC((const char *)src, (char *)dst, (int)n, (int)cap, 9);
        return r > 0 ? (size_t)r : 0;
    }
    case C_DEFLATE_1:
    case C_DEFLATE_6: {
        uLongf d = (uLongf)cap;
        int r = compress2(dst, &d, src, (uLong)n, comp == C_DEFLATE_1 ? 1 : 6);
        return r == Z_OK ? (size_t)d : 0;
    }
    default: {
        size_t r = ZSTD_compress2(zctx(comp), dst, cap, src, n);
        if (ZSTD_isError(r)) { fprintf(stderr, "fs: zstd: %s\n", ZSTD_getErrorName(r)); return 0; }
        return r;
    }
    }
}

int fs_decompress(int comp, const uint8_t *src, size_t csize, uint8_t *dst, size_t n) {
    switch (comp) {
    case C_NONE: if (csize != n) return -1; memcpy(dst, src, n); return 0;
    case C_LZ4:
    case C_LZ4HC_9: {
        int r = LZ4_decompress_safe((const char *)src, (char *)dst, (int)csize, (int)n);
        return r == (int)n ? 0 : -1;
    }
    case C_DEFLATE_1:
    case C_DEFLATE_6: {
        uLongf d = (uLongf)n;
        int r = uncompress(dst, &d, src, (uLong)csize);
        return r == Z_OK && d == n ? 0 : -1;
    }
    default: {
        if (!dctx) dctx = ZSTD_createDCtx();
        size_t r = ZSTD_decompressDCtx(dctx, dst, n, src, csize);
        return !ZSTD_isError(r) && r == n ? 0 : -1;
    }
    }
}

// ---- transforms --------------------------------------------------------------------------
static const int port_bits[FS_GROUPS] = { 8, 8, 8, 8, 8, 1, 1 };

static inline uint64_t transpose8(uint64_t x) {
    x = (x & 0xAA55AA55AA55AA55ull) | ((x & 0x00AA00AA00AA00AAull) << 7) | ((x >> 7) & 0x00AA00AA00AA00AAull);
    x = (x & 0xCCCC3333CCCC3333ull) | ((x & 0x0000CCCC0000CCCCull) << 14) | ((x >> 14) & 0x0000CCCC0000CCCCull);
    x = (x & 0xF0F0F0F00F0F0F0Full) | ((x & 0x00000000F0F0F0F0ull) << 28) | ((x >> 28) & 0x00000000F0F0F0F0ull);
    return x;
}

// 8x8 byte transpose: w[i] byte k <-> w[k] byte i.
static inline void tbyte8(uint64_t *w) {
    for (int i = 0; i < 4; i++) {
        const uint64_t t = ((w[i] >> 32) ^ w[i + 4]) & 0x00000000FFFFFFFFull;
        w[i] ^= t << 32; w[i + 4] ^= t;
    }
    static const int s2[4] = { 0, 1, 4, 5 };
    for (int j = 0; j < 4; j++) {
        const int i = s2[j];
        const uint64_t t = ((w[i] >> 16) ^ w[i + 2]) & 0x0000FFFF0000FFFFull;
        w[i] ^= t << 16; w[i + 2] ^= t;
    }
    for (int i = 0; i < 8; i += 2) {
        const uint64_t t = ((w[i] >> 8) ^ w[i + 1]) & 0x00FF00FF00FF00FFull;
        w[i] ^= t << 8; w[i + 1] ^= t;
    }
}

static int identity_layout(void) {
    for (int k = 0; k < 8; k++)
        if (fs_off[k] != k) return 0;
    return 1;
}
// fs_pack for the identity layout: masks and two shifts. (Any other layout
// is one pshufb; the study's raw images use this one.)
static inline uint64_t pack_id(uint64_t r) {
    return (r & 0x1ffffffffffull) | ((r >> 7) & (1ull << 41)) | ((r >> 14) & (1ull << 42));
}

int fs_transform(int t, const uint64_t *raw, uint64_t n, uint64_t first_sample, uint8_t **out, size_t *lens,
                 uint64_t *clk_errors) {
    uint64_t bad = 0;
    const int cb = fs_off[7];   // the clk byte (FS_CLK in the replay; Verilator's in the simulation check)
    const uint64_t clk = 1ull << (8 * cb), ph = (first_sample & 1) ? clk : 0;
    for (uint64_t i = 0; i < n; i++) bad += ((raw[i] ^ ph ^ ((i & 1) ? clk : 0)) >> (8 * cb)) & 1;
    *clk_errors += bad;
    const int id = identity_layout();
    switch (t) {
    case T_PACK6:
    case T_XOR6:
    case T_PACK6CLK: {
        uint8_t *p = out[0];
        uint64_t prev = 0;
        const uint64_t keep = t == T_PACK6CLK ? ~0ull : ~(1ull << 42);
        for (uint64_t i = 0; i < n; i++) {
            const uint64_t v = (id ? pack_id(raw[i]) : fs_pack(raw[i])) & keep;
            const uint64_t w = t == T_XOR6 ? v ^ prev : v;
            prev = v;
            memcpy(p, &w, 8);
            p += 6;
        }
        lens[0] = (size_t)(p - out[0]);
        return 1;
    }
    case T_BSHUF:
    case T_XBSHUF:
    case T_XBITSHUF: {
        const uint64_t groups = (n + 7) / 8;
        uint64_t prev = 0;
        for (uint64_t g = 0; g < groups; g++) {
            uint64_t w[8];
            for (int j = 0; j < 8; j++) {
                const uint64_t i = 8 * g + (uint64_t)j;
                const uint64_t v = i < n ? raw[i] & FS_NOCLK_MASK : prev;
                w[j] = t == T_BSHUF ? v : v ^ prev;
                prev = v;
            }
            tbyte8(w);   // w[k]: port k's byte of the 8 samples
            if (t != T_XBITSHUF) {
                for (int k = 0; k < FS_GROUPS; k++) memcpy(out[k] + 8 * g, &w[k], 8);
            } else {
                for (int k = 0; k < FS_GROUPS; k++) {
                    const uint64_t y = transpose8(w[k]);
                    for (int b = 0; b < port_bits[k]; b++) out[k][(uint64_t)b * groups + g] = (uint8_t)(y >> (8 * b));
                }
            }
        }
        for (int k = 0; k < FS_GROUPS; k++)
            lens[k] = (size_t)(t == T_XBITSHUF ? groups * (uint64_t)port_bits[k] : 8 * groups);
        return FS_GROUPS;
    }
    case T_PLANAR: {
        // As the planar writer: byte k's runs, in samples, covering the block.
        const uint64_t noclk = ~(0xffull << (8 * cb));
        uint8_t *p[8];
        uint64_t start[8];
        int map[8], np = 0;   // stream j holds raw byte map[j]: every byte but the clk byte
        for (int k = 0; k < 8; k++)
            if (k != cb) { map[np] = k; p[np] = out[np]; start[np] = 0; np++; }
        uint64_t prev = n ? raw[0] & noclk : 0;
        for (uint64_t i = 1; i < n; i++) {
            const uint64_t v = raw[i] & noclk;
            uint64_t x = v ^ prev;
            if (__builtin_expect(x != 0, 0)) {
                for (int j = 0; j < np; j++) {
                    const int k = map[j];
                    if ((x >> (8 * k)) & 0xff) {
                        *p[j] = (uint8_t)(prev >> (8 * k));
                        p[j] += 1 + vgacap_leb128_put_inline(p[j] + 1, i - start[j]);
                        start[j] = i;
                    }
                }
                prev = v;
            }
        }
        for (int j = 0; j < np; j++) {
            if (n) {
                *p[j] = (uint8_t)(prev >> (8 * map[j]));
                p[j] += 1 + vgacap_leb128_put_inline(p[j] + 1, n - start[j]);
            }
            lens[j] = (size_t)(p[j] - out[j]);
        }
        return FS_GROUPS;
    }
    default: return -1;
    }
}

int fs_clog_to_planar(const uint64_t *rec, uint64_t nrec, uint64_t n, uint64_t first_sample, uint8_t **out,
                      size_t *lens) {
    const int cb = fs_off[7];
    const uint64_t noclk = ~(0xffull << (8 * cb));
    uint8_t *p[8];
    uint64_t start[8];
    int map[8], np = 0;
    for (int k = 0; k < 8; k++)
        if (k != cb) { map[np] = k; p[np] = out[np]; start[np] = first_sample; np++; }
    uint64_t prev = nrec ? rec[1] & noclk : 0;   // the block's first record: its state
    for (uint64_t r = 1; r < nrec; r++) {
        for (int h = 0; h < 2; h++) {
            const uint64_t s = rec[3 * r] + (uint64_t)h, v = rec[3 * r + 1 + (uint64_t)h] & noclk;
            uint64_t x = v ^ prev;
            while (x) {   // the changed bytes only (the clk byte never is one)
                const int k = __builtin_ctzll(x) >> 3, j = k < cb ? k : k - 1;
                if (s != start[j]) {
                    *p[j] = (uint8_t)(prev >> (8 * k));
                    p[j] += 1 + vgacap_leb128_put_inline(p[j] + 1, s - start[j]);
                    start[j] = s;
                }
                x &= ~(0xffull << (8 * k));
            }
            prev = v;
        }
    }
    for (int j = 0; j < np; j++) {
        *p[j] = (uint8_t)(prev >> (8 * map[j]));
        p[j] += 1 + vgacap_leb128_put_inline(p[j] + 1, first_sample + n - start[j]);
        lens[j] = (size_t)(p[j] - out[j]);
    }
    return np;
}

int fs_events_to_planar(const uint8_t *ev, size_t len, uint64_t n, uint64_t first_sample, uint8_t **out, size_t *lens) {
    const int cb = fs_off[7];
    uint8_t *p[8];
    uint64_t start[8];
    int np = 0;
    for (int k = 0; k < 8; k++)
        if (k != cb) { p[np] = out[np]; start[np] = first_sample; np++; }
    const uint8_t *q = ev, *e = ev + len;
    uint64_t prev = 0;
    for (int k = 0; k < 8; k++)   // the snapshot: every byte but the clk byte
        if (k != cb) prev |= (uint64_t)*q++ << (8 * k);
    uint64_t s = first_sample;
    while (q < e) {
        uint64_t d = 0;
        unsigned sh = 0;
        uint8_t b;
        do { b = *q++; d |= (uint64_t)(b & 0x7f) << sh; sh += 7; } while (b & 0x80);
        s += d;
        unsigned m = *q++;
        while (m) {
            const int k = __builtin_ctz(m), j = k < cb ? k : k - 1;
            const uint8_t v = *q++;
            if (s != start[j]) {
                *p[j] = (uint8_t)(prev >> (8 * k));
                p[j] += 1 + vgacap_leb128_put_inline(p[j] + 1, s - start[j]);
                start[j] = s;
            }
            prev = (prev & ~(0xffull << (8 * k))) | ((uint64_t)v << (8 * k));
            m &= m - 1;
        }
    }
    int j = 0;
    for (int k = 0; k < 8; k++) {
        if (k == cb) continue;
        *p[j] = (uint8_t)(prev >> (8 * k));
        p[j] += 1 + vgacap_leb128_put_inline(p[j] + 1, first_sample + n - start[j]);
        lens[j] = (size_t)(p[j] - out[j]);
        j++;
    }
    return np;
}

static inline uint64_t clk_of(uint64_t s) { return (s & 1) ? FS_CLK_RAW : 0; }

int fs_untransform(int t, uint8_t *const *in, uint64_t n, uint64_t first_sample, int port, uint64_t *raw) {
    switch (t) {
    case T_PACK6:
    case T_XOR6:
    case T_PACK6CLK: {
        const uint8_t *p = in[0];
        uint64_t prev = 0;
        for (uint64_t i = 0; i < n; i++) {
            uint64_t w = 0;
            memcpy(&w, p, 6);
            p += 6;
            const uint64_t v = t == T_XOR6 ? w ^ prev : w;
            prev = v;
            raw[i] = fs_raw_from_sample(v & ~(1ull << 42)) | clk_of(first_sample + i);
        }
        return 0;
    }
    case T_BSHUF:
    case T_XBSHUF: {
        const int k0 = port < 0 ? 0 : port, k1 = port < 0 ? FS_GROUPS : port + 1;
        for (uint64_t i = 0; i < n; i++) raw[i] = clk_of(first_sample + i);
        for (int k = k0; k < k1; k++) {
            uint8_t prev = 0;
            const uint8_t *p = in[k];
            for (uint64_t i = 0; i < n; i++) {
                const uint8_t v = t == T_XBSHUF ? (uint8_t)(p[i] ^ prev) : p[i];
                prev = v;
                raw[i] |= (uint64_t)v << (8 * k);
            }
        }
        return 0;
    }
    case T_XBITSHUF: {
        const uint64_t groups = (n + 7) / 8;
        const int k0 = port < 0 ? 0 : port, k1 = port < 0 ? FS_GROUPS : port + 1;
        for (uint64_t i = 0; i < n; i++) raw[i] = clk_of(first_sample + i);
        for (int k = k0; k < k1; k++) {
            uint8_t prev = 0;
            const uint8_t *p = in[k];
            for (uint64_t g = 0; g < groups; g++) {
                uint64_t y = 0;
                for (int b = 0; b < port_bits[k]; b++) y |= (uint64_t)p[(uint64_t)b * groups + g] << (8 * b);
                const uint64_t x = transpose8(y);
                for (int j = 0; j < 8; j++) {
                    const uint64_t i = 8 * g + (uint64_t)j;
                    if (i >= n) break;
                    prev ^= (uint8_t)(x >> (8 * j));
                    raw[i] |= (uint64_t)prev << (8 * k);
                }
            }
        }
        return 0;
    }
    default: return -1;
    }
}
