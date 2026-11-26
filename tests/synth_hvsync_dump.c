// SPDX-License-Identifier: Apache-2.0
// Test-only helper for python/tests/test_calib.py: `synth-hvsync-dump OUT`
// writes one 640x480@60 frame of tests/synth.c's synth_frame_hvsync (from
// (hpos, vpos) = (0, 0), bars pixel (x / 10) & 0x3F) to OUT, one uo_out
// byte per clock, so synthtrace's testpatterns phase is compared byte for byte.
#include "synth.h"
#include <stdio.h>
#include <stdlib.h>

static uint8_t bars(void *user, uint16_t x, uint16_t y) {
    (void)user; (void)y;
    return (uint8_t)((x / 10) & 0x3F);
}

int main(int argc, char **argv) {
    if (argc != 2) { fprintf(stderr, "usage: %s OUT\n", argv[0]); return 2; }
    size_t count;
    const vgaframe_mode_t *m = &vgaframe_modes(&count)[0];
    size_t len = (size_t)vgaframe_mode_line_px(m) * vgaframe_mode_frame_lines(m);
    uint32_t *s = malloc(len * sizeof *s);
    uint8_t *b = malloc(len);
    if (!s || !b || synth_frame_hvsync(m, bars, NULL, s, len) != len) {
        fprintf(stderr, "synth-hvsync-dump: cannot synthesise\n"); return 1;
    }
    for (size_t i = 0; i < len; i++) b[i] = (uint8_t)s[i];
    FILE *f = fopen(argv[1], "wb");
    if (!f || fwrite(b, 1, len, f) != len || fclose(f)) {
        fprintf(stderr, "synth-hvsync-dump: cannot write %s\n", argv[1]); return 1;
    }
    free(s); free(b);
    return 0;
}
