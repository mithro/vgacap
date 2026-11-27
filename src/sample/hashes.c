// SPDX-License-Identifier: Apache-2.0
// The frame hash methods (see vgacap/sample.h): xxh3-128-rgb6 and
// fnv1a64-tb2026-rgb888. python/vgacap/hashes.py is the reference.
#include "vgacap/sample.h"

#include <string.h>

#include <xxhash.h>

#define FNV_PRIME 0x100000001b3ULL
// tb.cpp's 1469598103934665603: the standard 0xcbf29ce484222325 with a digit lost.
#define FNV_BASIS 0x14650fb0739d0383ULL

void vgahash_xxh3_128_rgb6(const uint8_t *rgb6, size_t n, uint8_t out[16]) {
    XXH128_canonical_t c;
    XXH128_canonicalFromHash(&c, XXH3_128bits(rgb6, n));
    memcpy(out, c.digest, 16);
}

uint64_t vgahash_fnv1a64_tb2026_rgb888(const uint8_t *rgb6, size_t n) {
    uint64_t h = FNV_BASIS;
    for (size_t i = 0; i < n; i++) {
        uint8_t v = rgb6[i];
        h = (h ^ (uint64_t)(((v >> 4) & 3u) * 85u)) * FNV_PRIME;
        h = (h ^ (uint64_t)(((v >> 2) & 3u) * 85u)) * FNV_PRIME;
        h = (h ^ (uint64_t)((v & 3u) * 85u)) * FNV_PRIME;
    }
    return h;
}

static const char *const XXH3_DEF[] = {
    "XXH3-128 (xxHash, seed 0, canonical big-endian) of the frame as rgb6: one byte",
    "per pixel, R<<4 | G<<2 | B, row-major, active area only, at native resolution.",
    NULL,
};
static const char *const FNV_DEF[] = {
    "FNV-1a 64 loop (prime 0x100000001b3) with a NON-STANDARD offset basis,",
    "hash_offset_basis below, not 0xcbf29ce484222325: tb.cpp's constant lost a digit.",
    "Over the frame as RGB888, row-major, active area only, at native resolution,",
    "each 2-bit channel stored as value * 85.",
    NULL,
};

const char *const *vgahash_definition(const char *method) {
    if (strcmp(method, "xxh3-128-rgb6") == 0) return XXH3_DEF;
    if (strcmp(method, "fnv1a64-tb2026-rgb888") == 0) return FNV_DEF;
    return NULL;
}
