// SPDX-License-Identifier: Apache-2.0
// frames.txt and frame_hashes.<method>.txt (src/sample/outputs.c). The
// Python reader and byte-identical rewrite are in python/tests/test_framesfile.py.
#include "harness.h"
#include "vgacap/outputs.h"
#include <stdint.h>
#include <stdlib.h>
#include <string.h>

static const uint8_t SHA[2][32] = {{0x00, 0x11, 0x22, 0x33}, {0xff, 0xee}};
static const uint64_t FNV[2] = {0x009b538b2455c383ULL, 0x123456789abcdef0ULL};

static void base(vgaout_t *o, const vgaout_frame_t *fr, size_t n) {
    memset(o, 0, sizeof *o);
    o->id = "tt07/x"; o->status = "ok"; o->sampler = "tb-v1"; o->sampler_status = "ok";
    o->mode = "640x480@60"; o->width = 640; o->height = 480; o->clock_hz = 25175000;
    o->clocks_per_pixel = 1.0; o->capture_after_clock = 1234;
    o->store = "frames.bin"; o->store_length = 9876; o->store_sha256 = "ab";
    o->n_distinct = 2; o->sha256 = SHA; o->fnv = FNV;
    o->n_frames = n; o->frames = fr;
}

// The whole output as a string (the caller frees).
static char *capture(int (*w)(FILE *, const char *, const vgaout_t *), const char *m, int (*w2)(FILE *, const vgaout_t *),
                     const vgaout_t *o) {
    FILE *f = tmpfile();
    if (!f) return NULL;
    int r = w ? w(f, m, o) : w2(f, o);
    if (r != 0) { fclose(f); return NULL; }
    long n = ftell(f);
    rewind(f);
    char *s = malloc((size_t)n + 1);
    if (s && fread(s, 1, (size_t)n, f) != (size_t)n) { free(s); s = NULL; }
    if (s) s[n] = 0;
    fclose(f);
    return s;
}
static char *frames_txt(const vgaout_t *o) { return capture(NULL, NULL, vgaout_frames_txt, o); }
static char *hash_txt(const char *m, const vgaout_t *o) { return capture(vgaout_hash_file, m, NULL, o); }

// The text from "runs:\n" on.
static const char *runs_of(const char *s) {
    const char *p = strstr(s, "\nruns:\n");
    return p ? p + 7 : NULL;
}

#define F(d, s, m) {(d), (s), (m)}
TEST(runs_grouping) {   // distinct 0 0 0 1 1 gap gap 0, regular starts (100 apart)
    vgaout_frame_t fr[] = {F(0, 1000, 0), F(0, 1100, 0), F(0, 1200, 0), F(1, 1300, 0), F(1, 1400, 0),
                           F(-1, 0, 0), F(-1, 0, 0), F(0, 1700, 0)};
    vgaout_t o; base(&o, fr, 8);
    ASSERT_EQ_U(vgaout_frame_clocks(&o), 100);
    char *s = frames_txt(&o); ASSERT_TRUE(s);
    ASSERT_TRUE(strcmp(runs_of(s), "0 3 0 1000 0\n3 2 1 1300 0\n5 2 - - -\n7 1 0 1700 0\n") == 0);
    ASSERT_TRUE(strstr(s, "\nframe_clocks: 100\nframes: 8\ndistinct_frames: 2\n"));
    free(s);
}
TEST(runs_break_on_late_start) {
    vgaout_frame_t fr[] = {F(0, 1000, 0), F(0, 1100, 0), F(0, 1201, 0), F(0, 1301, 0)};
    vgaout_t o; base(&o, fr, 4);
    // distances 100, 101, 100: sorted 100 100 101, element 1 = 100.
    ASSERT_EQ_U(vgaout_frame_clocks(&o), 100);
    char *s = frames_txt(&o); ASSERT_TRUE(s);
    ASSERT_TRUE(strcmp(runs_of(s), "0 2 0 1000 0\n2 2 0 1201 0\n") == 0);
    free(s);
}
TEST(runs_break_on_missing) {
    vgaout_frame_t fr[] = {F(0, 1000, 5), F(0, 1100, 5), F(0, 1200, 6), F(0, 1300, 6)};
    vgaout_t o; base(&o, fr, 4);
    char *s = frames_txt(&o); ASSERT_TRUE(s);
    ASSERT_TRUE(strcmp(runs_of(s), "0 2 0 1000 5\n2 2 0 1200 6\n") == 0);
    free(s);
}
TEST(frame_clocks_is_median_across_gaps) {   // starts 0, 10, (gap), 50: distances 10, 40; size/2 = 1 -> 40
    vgaout_frame_t fr[] = {F(0, 0, 0), F(0, 10, 0), F(-1, 0, 0), F(0, 50, 0)};
    vgaout_t o; base(&o, fr, 4);
    ASSERT_EQ_U(vgaout_frame_clocks(&o), 40);
    o.n_frames = 1;
    ASSERT_EQ_U(vgaout_frame_clocks(&o), 0);
    o.n_frames = 0;
    ASSERT_EQ_U(vgaout_frame_clocks(&o), 0);
}
TEST(header_tb) {
    vgaout_frame_t fr[] = {F(0, 1000, 0)};
    vgaout_t o; base(&o, fr, 1);
    char *s = frames_txt(&o); ASSERT_TRUE(s);
    const char *want =
        "id: tt07/x\nstatus: ok\nsampler: tb-v1\nsampler_status: ok\npixel_format: rgb6\nwidth: 640\nheight: 480\n"
        "mode: 640x480@60\nclock_hz: 25175000\nclocks_per_pixel: 1\ncapture_after_clock: 1234\nframe_clocks: 0\n"
        "frames: 1\ndistinct_frames: 2\nstore: frames.bin\nstore_length: 9876\nstore_sha256: ab\n"
        "distinct:\n00112233" "00000000000000000000000000000000000000000000000000000000\n"
        "ffee" "000000000000000000000000000000000000000000000000000000000000\n"
        "runs:\n0 1 0 1000 0\n";
    ASSERT_TRUE(strcmp(s, want) == 0);
    free(s);
}
TEST(header_centre) {
    vgaout_frame_t fr[] = {F(0, 1000, 3)};
    vgaout_t o; base(&o, fr, 1);
    o.sampler = "centre-v1"; o.clocks_per_pixel = 2.5; o.has_centre = 1;
    o.window_dx = -1; o.window_dy = 2; o.hback = 48; o.vback = 33; o.outside = 7;
    o.suggest_dx = 1; o.suggest_dy = -3; o.colour_changes = 99; o.colour_changes_off_grid = UINT64_MAX;
    char *s = frames_txt(&o); ASSERT_TRUE(s);
    ASSERT_TRUE(strstr(s, "clocks_per_pixel: 2.5\n"));
    ASSERT_TRUE(strstr(s, "store_sha256: ab\nwindow_offset: -1 2\nporches: 48 33\noutside: 7\n"
                          "suggested_offset: 1 -3\ncolour_changes: 99\ncolour_changes_off_grid: -\ndistinct:\n"));
    o.colour_changes_off_grid = 4;
    free(s); s = frames_txt(&o); ASSERT_TRUE(s);
    ASSERT_TRUE(strstr(s, "colour_changes_off_grid: 4\n"));
    free(s);
}
TEST(no_frames) {   // a sampler failure: sampler_status says why, no frames, no distinct
    vgaout_t o; base(&o, NULL, 0);
    o.sampler_status = "no-sync"; o.n_distinct = 0;
    char *s = frames_txt(&o); ASSERT_TRUE(s);
    ASSERT_TRUE(strstr(s, "sampler: tb-v1\nsampler_status: no-sync\n"));
    ASSERT_TRUE(strstr(s, "frames: 0\ndistinct_frames: 0\n"));
    ASSERT_TRUE(strcmp(strstr(s, "distinct:\n"), "distinct:\nruns:\n") == 0);
    free(s);
}
TEST(bad_distinct_rejected) {
    vgaout_frame_t fr[] = {F(2, 1000, 0)};
    vgaout_t o; base(&o, fr, 1);
    FILE *f = tmpfile(); ASSERT_TRUE(f);
    ASSERT_TRUE(vgaout_frames_txt(f, &o) != 0);
    ASSERT_TRUE(vgaout_hash_file(f, "sha256-rgb6", &o) != 0);
    ASSERT_TRUE(vgaout_hash_file(f, "nope", &o) != 0);
    fclose(f);
}
TEST(hash_file_lines) {
    vgaout_frame_t fr[] = {F(0, 1000, 0), F(1, 1100, 0), F(-1, 0, 0), F(0, 1300, 0)};
    vgaout_t o; base(&o, fr, 4);
    char *s = hash_txt("fnv1a64-tb2026-rgb888", &o); ASSERT_TRUE(s);
    // line 1, the definition as "# " lines, the parameter, then the header.
    ASSERT_TRUE(strncmp(s, "hash_method: fnv1a64-tb2026-rgb888\n# FNV-1a 64 loop", 50) == 0);
    const char *want_tail =
        "hash_offset_basis: 0x14650fb0739d0383\nid: tt07/x\nstatus: ok\nsampler: tb-v1\nsampler_status: ok\n"
        "mode: 640x480@60\nwidth: 640\nheight: 480\nclock_hz: 25175000\nclocks_per_pixel: 1\n"
        "capture_after_clock: 1234\nframes: 4\n"
        "009b538b2455c383\n123456789abcdef0\n-\n009b538b2455c383\n";
    ASSERT_TRUE(strstr(s, want_tail) != NULL);
    ASSERT_TRUE(strcmp(strstr(s, "\nhash_offset_basis:") + 1, want_tail) == 0);
    // Every definition line is a "# " line.
    const char *const *d = vgahash_definition("fnv1a64-tb2026-rgb888");
    char want[200];
    snprintf(want, sizeof want, "# %s\n", d[0]);
    ASSERT_TRUE(strstr(s, want));
    free(s);
    s = hash_txt("sha256-rgb6", &o); ASSERT_TRUE(s);
    ASSERT_TRUE(strstr(s, "hash_method: sha256-rgb6\n# SHA-256 of the frame as rgb6"));
    ASSERT_TRUE(!strstr(s, "hash_offset_basis"));
    ASSERT_TRUE(strstr(s, "frames: 4\n"
        "00112233" "00000000000000000000000000000000000000000000000000000000\n"
        "ffee" "000000000000000000000000000000000000000000000000000000000000\n-\n"
        "00112233" "00000000000000000000000000000000000000000000000000000000\n"));
    free(s);
}
TEST(hash_file_fnv_needs_list) {
    vgaout_frame_t fr[] = {F(0, 1000, 0)};
    vgaout_t o; base(&o, fr, 1); o.fnv = NULL;
    FILE *f = tmpfile(); ASSERT_TRUE(f);
    ASSERT_TRUE(vgaout_hash_file(f, "fnv1a64-tb2026-rgb888", &o) != 0);
    fclose(f);
}

int main(void) {
    RUN(runs_grouping); RUN(runs_break_on_late_start); RUN(runs_break_on_missing);
    RUN(frame_clocks_is_median_across_gaps); RUN(header_tb); RUN(header_centre); RUN(no_frames);
    RUN(bad_distinct_rejected); RUN(hash_file_lines); RUN(hash_file_fnv_needs_list);
    RUN_TESTS_END();
}
