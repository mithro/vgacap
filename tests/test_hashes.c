// SPDX-License-Identifier: Apache-2.0
// The frame hash methods (src/sample/hashes.c). The vectors are shared with
// python/tests/test_hashes.py, which computes them with hashlib and a loop.
// The 640x480 zero FNV, 009b538b2455c383, is the black-frame hash in the
// published tt07 tt_um_MichaelBell_rle_vga file
// (tinytapeout-vga-videos data/results/tt07/tt_um_MichaelBell_rle_vga/frame_hashes.txt).
#include "harness.h"
#include "vgacap/sample.h"
#include <stdint.h>
#include <stdlib.h>
#include <string.h>

static void hex(const uint8_t *d, char *out) {
    for (int i = 0; i < 32; i++) sprintf(out + 2 * i, "%02x", d[i]);
}

static void check(const uint8_t *px, size_t n, const char *sha, uint64_t fnv) {
    uint8_t d[32]; char h[65];
    vgahash_sha256_rgb6(px, n, d);
    hex(d, h);
    ASSERT_TRUE(strcmp(h, sha) == 0);
    ASSERT_EQ_U(vgahash_fnv1a64_tb2026_rgb888(px, n), fnv);
}

TEST(test_bytes_0_7) {
    const uint8_t px[8] = {0, 1, 2, 3, 4, 5, 6, 7};
    check(px, 8, "8a851ff82ee7048ad09ec3847f1ddf44944104d2cbd17ef4e3db22c6785a0d45", 0x760bbc7b258ddb8bULL);
}
TEST(test_zero_frame) {
    uint8_t *px = calloc(640 * 480, 1);
    ASSERT_TRUE(px);
    check(px, 640 * 480, "7818f5542a0404157573be6cffc0e0c8e68ce3c0f5d17d07ccdd9313fb700baf", 0x009b538b2455c383ULL);
    free(px);
}
TEST(test_mod64_frame) {
    uint8_t *px = malloc(640 * 480);
    ASSERT_TRUE(px);
    for (size_t i = 0; i < 640 * 480; i++) px[i] = (uint8_t)(i % 64);
    check(px, 640 * 480, "9cc7a2f413ef0ebe2541f5de1d77972dd92241ffff0c1b5f72279fc23608e8c6", 0x9d900346f0fe6783ULL);
    free(px);
}
TEST(test_empty) {   // FNV of nothing is the offset basis
    ASSERT_EQ_U(vgahash_fnv1a64_tb2026_rgb888(NULL, 0), 0x14650fb0739d0383ULL);
}
TEST(test_definitions) {
    const char *const *d = vgahash_definition("sha256-rgb6");
    ASSERT_TRUE(d);
    ASSERT_TRUE(strcmp(d[0], "SHA-256 of the frame as rgb6: one byte per pixel, R<<4 | G<<2 | B,") == 0);
    ASSERT_TRUE(strcmp(d[1], "row-major, active area only, at native resolution.") == 0);
    ASSERT_TRUE(d[2] == NULL);
    d = vgahash_definition("fnv1a64-tb2026-rgb888");
    ASSERT_TRUE(d);
    ASSERT_TRUE(strcmp(d[0], "FNV-1a 64 loop (prime 0x100000001b3) with a NON-STANDARD offset basis,") == 0);
    ASSERT_TRUE(strcmp(d[1], "hash_offset_basis below, not 0xcbf29ce484222325: tb.cpp's constant lost a digit.") == 0);
    ASSERT_TRUE(strcmp(d[2], "Over the frame as RGB888, row-major, active area only, at native resolution,") == 0);
    ASSERT_TRUE(strcmp(d[3], "each 2-bit channel stored as value * 85.") == 0);
    ASSERT_TRUE(d[4] == NULL);
    ASSERT_TRUE(vgahash_definition("nope") == NULL);
}

int main(void) {
    RUN(test_bytes_0_7); RUN(test_zero_frame); RUN(test_mod64_frame);
    RUN(test_empty); RUN(test_definitions);
    RUN_TESTS_END();
}
