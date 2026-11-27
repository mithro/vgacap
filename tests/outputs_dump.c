// SPDX-License-Identifier: Apache-2.0
// Test-only helper for python/tests/test_framesfile.py: writes frames.txt and
// both hash files for a fixed run into a directory.
//   outputs_dump VARIANT DIR     VARIANT: tb | centre | centre-fraction | nosync
#include "vgacap/outputs.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

enum { NF = 11, ND = 3 };

int main(int argc, char **argv) {
    if (argc != 3) { fprintf(stderr, "usage: outputs_dump VARIANT DIR\n"); return 2; }
    const char *v = argv[1];
    uint8_t xxh[ND][16]; uint64_t fnv[ND];
    for (int d = 0; d < ND; d++) {
        uint8_t px[64 * 3];
        for (int i = 0; i < 64 * 3; i++) px[i] = (uint8_t)((i * (d + 3) + d) & 63);
        vgahash_xxh3_128_rgb6(px, sizeof px, xxh[d]);
        fnv[d] = vgahash_fnv1a64_tb2026_rgb888(px, sizeof px);
    }
    // distinct: 0 0 0 1 1 gap gap 0, then a late 2, a repeat with a missing change, a last 2.
    static const int64_t dist[NF] = {0, 0, 0, 1, 1, -1, -1, 0, 2, 2, 2};
    vgaout_frame_t fr[NF];
    uint64_t fc = 420000;
    for (int i = 0; i < NF; i++) {
        fr[i].distinct = dist[i];
        fr[i].start_clock = dist[i] < 0 ? 0 : 5000 + (uint64_t)i * fc;
        fr[i].missing = dist[i] < 0 ? 0 : 0;
    }
    fr[8].start_clock += 3;          // late: breaks the run
    fr[10].missing = 17;
    vgaout_t o;
    memset(&o, 0, sizeof o);
    o.id = "tt07/tt_um_example"; o.status = "ok"; o.sampler = "tb-v1"; o.sampler_status = "ok";
    o.mode = "640x480@60"; o.width = 640; o.height = 480; o.clock_hz = 25175000;
    o.clocks_per_pixel = 1.0; o.capture_after_clock = 4096;
    o.store = "frames.bin"; o.store_length = 1234567;
    o.store_xxh3_128 = "0123456789abcdef0123456789abcdef";
    o.n_distinct = ND; o.xxh3_128 = (const uint8_t (*)[16])xxh; o.fnv = fnv;
    o.n_frames = NF; o.frames = fr;
    if (strcmp(v, "centre") == 0 || strcmp(v, "centre-fraction") == 0) {
        o.sampler = "centre-v1";
        vgasample_geometry_t g; memset(&g, 0, sizeof g);
        g.hback = 48; g.vback = 33;
        centrev1_diag_t d; memset(&d, 0, sizeof d);
        d.dx = -2; d.dy = 1; d.outside = 12; d.suggest_dx = 1; d.suggest_dy = -1;
        d.colour_changes = 345; d.off_grid = 6;
        if (strcmp(v, "centre-fraction") == 0) {
            o.clocks_per_pixel = 800.0 / 840.0; o.mode = "unknown"; d.off_grid = UINT64_MAX;
            o.status = "truncated";
        }
        vgaout_set_centre(&o, &g, &d);
        fr[0].missing = fr[1].missing = 4;
    } else if (strcmp(v, "nosync") == 0) {
        o.sampler_status = "no-sync"; o.mode = "unknown"; o.width = o.height = 0; o.n_frames = 0;
        o.n_distinct = 0; o.store_length = 0;
    } else if (strcmp(v, "tb") != 0) return 2;

    const char *names[3] = {"frames.txt", "frame_hashes.xxh3-128-rgb6.txt", "frame_hashes.fnv1a64-tb2026-rgb888.txt"};
    for (int k = 0; k < 3; k++) {
        char path[4096];
        snprintf(path, sizeof path, "%s/%s", argv[2], names[k]);
        FILE *f = fopen(path, "wb");
        if (!f) { perror(path); return 1; }
        int r = k == 0 ? vgaout_frames_txt(f, &o) : vgaout_hash_file(f, k == 1 ? "xxh3-128-rgb6" : "fnv1a64-tb2026-rgb888", &o);
        if (fclose(f) != 0 || r != 0) { fprintf(stderr, "write failed: %s\n", path); return 1; }
    }
    return 0;
}
