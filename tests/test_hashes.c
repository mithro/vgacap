// SPDX-License-Identifier: Apache-2.0
// The frame hash methods (src/sample/hashes.c). The vectors are shared with
// python/tests/test_hashes.py, which computes them with the xxhash package and
// a loop; upstream xxhsum -H2 gives the same XXH3-128 digests.
// The 640x480 zero FNV, 009b538b2455c383, is the black-frame hash in the
// published tt07 tt_um_MichaelBell_rle_vga file
// (tinytapeout-vga-videos data/results/tt07/tt_um_MichaelBell_rle_vga/frame_hashes.txt).
#define _POSIX_C_SOURCE 200809L   // clock_gettime
#include "harness.h"
#include "vgacap/sample.h"
#include <stdint.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>

static void hex(const uint8_t *d, char *out) {
    for (int i = 0; i < 16; i++) sprintf(out + 2 * i, "%02x", d[i]);
}

static void check(const uint8_t *px, size_t n, const char *xxh, uint64_t fnv) {
    uint8_t d[16]; char h[33];
    vgahash_xxh3_128_rgb6(px, n, d);
    hex(d, h);
    ASSERT_TRUE(strcmp(h, xxh) == 0);
    ASSERT_EQ_U(vgahash_fnv1a64_tb2026_rgb888(px, n), fnv);
}

TEST(test_bytes_0_7) {
    const uint8_t px[8] = {0, 1, 2, 3, 4, 5, 6, 7};
    check(px, 8, "e1e4432a62217fe4cfd50c61c8bb98c1", 0x760bbc7b258ddb8bULL);
}
TEST(test_zero_frame) {
    uint8_t *px = calloc(640 * 480, 1);
    ASSERT_TRUE(px);
    check(px, 640 * 480, "f356f76a71c18f05221f2123042fb5ce", 0x009b538b2455c383ULL);
    free(px);
}
TEST(test_mod64_frame) {
    uint8_t *px = malloc(640 * 480);
    ASSERT_TRUE(px);
    for (size_t i = 0; i < 640 * 480; i++) px[i] = (uint8_t)(i % 64);
    check(px, 640 * 480, "e1ca6fb5c48a9634f4c39e84aaa74f6a", 0x9d900346f0fe6783ULL);
    free(px);
}
TEST(test_empty) {   // FNV of nothing is the offset basis; XXH3-128 of nothing is xxHash's
    ASSERT_EQ_U(vgahash_fnv1a64_tb2026_rgb888(NULL, 0), 0x14650fb0739d0383ULL);
    uint8_t d[16]; char h[33];
    vgahash_xxh3_128_rgb6(NULL, 0, d);
    hex(d, h);
    ASSERT_TRUE(strcmp(h, "99aa06d3014798d86001c324468d497f") == 0);
}
TEST(test_definitions) {
    const char *const *d = vgahash_definition("xxh3-128-rgb6");
    ASSERT_TRUE(d);
    ASSERT_TRUE(strcmp(d[0], "XXH3-128 (xxHash, seed 0, canonical big-endian) of the frame as rgb6: one byte") == 0);
    ASSERT_TRUE(strcmp(d[1], "per pixel, R<<4 | G<<2 | B, row-major, active area only, at native resolution.") == 0);
    ASSERT_TRUE(d[2] == NULL);
    d = vgahash_definition("fnv1a64-tb2026-rgb888");
    ASSERT_TRUE(d);
    ASSERT_TRUE(strcmp(d[0], "FNV-1a 64 loop (prime 0x100000001b3) with a NON-STANDARD offset basis,") == 0);
    ASSERT_TRUE(strcmp(d[1], "hash_offset_basis below, not 0xcbf29ce484222325: tb.cpp's constant lost a digit.") == 0);
    ASSERT_TRUE(strcmp(d[2], "Over the frame as RGB888, row-major, active area only, at native resolution,") == 0);
    ASSERT_TRUE(strcmp(d[3], "each 2-bit channel stored as value * 85.") == 0);
    ASSERT_TRUE(d[4] == NULL);
    ASSERT_TRUE(vgahash_definition("nope") == NULL);
    ASSERT_TRUE(vgahash_definition("sha256-rgb6") == NULL);
}

// vgacap-sample hashes every frame that differs from the one before it. The
// SHA-256 this method replaced took 72% of a run on a design whose frames all
// differ. The sampler hashes a frame it has just written, so the frame is in
// cache: 4 frames of 640x480 (1.2 MB) are hashed in turn.
enum { FRAME = 640 * 480, KEEP = 4, FRAMES = 16 };

static double now(void) {
    struct timespec t;
    clock_gettime(CLOCK_MONOTONIC, &t);
    return (double)t.tv_sec + (double)t.tv_nsec * 1e-9;
}
// Seconds for FRAMES frames: method 0 XXH3-128, 1 FNV-1a 64.
static double time_frames(const uint8_t *px, int method, unsigned *acc) {
    double t0 = now();
    for (int f = 0; f < FRAMES; f++) {
        const uint8_t *p = px + (size_t)(f % KEEP) * FRAME;
        if (method == 0) { uint8_t d[16]; vgahash_xxh3_128_rgb6(p, FRAME, d); *acc += d[0]; }
        else *acc += (unsigned)vgahash_fnv1a64_tb2026_rgb888(p, FRAME);
    }
    return now() - t0;
}
// Best of 5 interleaved passes of each method, so a busy host slows both.
static void best_times(double *xxh, double *fnv, unsigned *acc) {
    uint8_t *px = malloc((size_t)FRAME * KEEP);
    *xxh = *fnv = 1e9;
    if (!px) return;
    for (size_t i = 0; i < (size_t)FRAME * KEEP; i++) px[i] = (uint8_t)((i * 7 + (i >> 11)) & 63);
    for (int pass = 0; pass < 5; pass++) {
        double a = time_frames(px, 0, acc), b = time_frames(px, 1, acc);
        if (a < *xxh) *xxh = a;
        if (b < *fnv) *fnv = b;
    }
    free(px);
}

// Relative, so it holds on any machine and build: XXH3-128 at least 5x the
// FNV-1a 64 loop on the same frames. On big-storage it is about 50x; the
// SHA-256 it replaced ran at about half the speed of FNV, so it would fail.
TEST(test_xxh3_faster_than_fnv) {
    double xxh, fnv; unsigned acc = 0;
    best_times(&xxh, &fnv, &acc);
    fprintf(stderr, "  xxh3-128-rgb6 %.1f us, fnv1a64 %.1f us a 640x480 frame: %.1fx (check %u)\n",
            xxh / FRAMES * 1e6, fnv / FRAMES * 1e6, fnv / xxh, acc & 0xff);
    ASSERT_TRUE(xxh * 5.0 <= fnv);
}

// test_hashes --speed (the perf-labelled test_hash_speed, run by hand on
// big-storage): an absolute floor of 1000 MB/s, where the old SHA-256 ran at
// about 136 MB/s.
TEST(test_xxh3_speed) {
    double xxh, fnv; unsigned acc = 0;
    best_times(&xxh, &fnv, &acc);
    double mbs = (double)FRAME * FRAMES / xxh / 1e6;
    fprintf(stderr, "  xxh3-128-rgb6: %.0f MB/s, %.1f us a 640x480 frame (check %u)\n", mbs,
            xxh / FRAMES * 1e6, acc & 0xff);
    ASSERT_TRUE(mbs >= 1000.0);
}

int main(int argc, char **argv) {
    if (argc == 2 && strcmp(argv[1], "--speed") == 0) {
        RUN(test_xxh3_speed);
        RUN_TESTS_END();
    }
    RUN(test_bytes_0_7); RUN(test_zero_frame); RUN(test_mod64_frame);
    RUN(test_empty); RUN(test_definitions); RUN(test_xxh3_faster_than_fnv);
    RUN_TESTS_END();
}
