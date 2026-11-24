// SPDX-License-Identifier: Apache-2.0
// bench-runs: times libvgaframe (vgaframe_push) on run-length input.
// One synthetic 640x480@60 frame (800 x 525 clocks, Tiny VGA samples) is
// converted to (sample, run) pairs once, then replayed until N clocks have
// been pushed. Usage: bench-runs --clocks N --profile busy|bars|static
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>

#include "synth.h"
#include "vgacap/frame.h"

#define MAX_CPL 7596
#define MAX_LINES 1200

static uint8_t px_busy(void *u, uint16_t x, uint16_t y) { (void)u; return (uint8_t)((x + y) & 0x3F); }
static uint8_t px_bars(void *u, uint16_t x, uint16_t y) { (void)u; (void)y; return (uint8_t)((x / 10) & 0x3F); }
static uint8_t px_static(void *u, uint16_t x, uint16_t y) { (void)u; (void)x; (void)y; return 0x2A; }

static void on_frame(void *u, const vgaframe_output_t *o) { (void)o; (*(uint64_t *)u)++; }

int main(int argc, char **argv) {
    uint64_t clocks = 0; const char *profile = NULL;
    for (int i = 1; i + 1 < argc; i += 2) {
        if (!strcmp(argv[i], "--clocks")) clocks = strtoull(argv[i + 1], NULL, 10);
        else if (!strcmp(argv[i], "--profile")) profile = argv[i + 1];
        else { fprintf(stderr, "unknown option %s\n", argv[i]); return 2; }
    }
    uint8_t (*pixel)(void *, uint16_t, uint16_t) = NULL;
    if (profile && !strcmp(profile, "busy")) pixel = px_busy;
    else if (profile && !strcmp(profile, "bars")) pixel = px_bars;
    else if (profile && !strcmp(profile, "static")) pixel = px_static;
    if (!clocks || !pixel) { fprintf(stderr, "usage: bench-runs --clocks N --profile busy|bars|static\n"); return 2; }

    const vgaframe_mode_t *m = vgaframe_mode_match(800, 525);
    size_t nsamp = 800u * 525u;
    uint32_t *samples = malloc(nsamp * sizeof *samples);
    uint32_t *rs = malloc(nsamp * sizeof *rs), *rr = malloc(nsamp * sizeof *rr);
    if (!m || !samples || !rs || !rr || synth_frame(m, pixel, NULL, samples, nsamp) != nsamp) { fprintf(stderr, "frame synthesis failed\n"); return 1; }
    size_t nruns = 0;
    for (size_t i = 0; i < nsamp; i++) {
        if (nruns && rs[nruns - 1] == samples[i]) rr[nruns - 1]++;
        else { rs[nruns] = samples[i]; rr[nruns] = 1; nruns++; }
    }

    uint64_t frames = 0;
    vgaframe_config_t c; memset(&c, 0, sizeof c);
    c.max_clocks_per_line = MAX_CPL; c.max_lines = MAX_LINES;
    static const uint8_t map[8] = { 7, 3, 0, 4, 1, 5, 2, 6 }; memcpy(c.signal_map, map, 8);
    c.on_frame = on_frame; c.user = &frames;
    size_t area = (size_t)MAX_CPL * MAX_LINES;
    uint8_t *raw = malloc(area), *rgb = malloc(area * 3), *cover = malloc(MAX_LINES);
    vgaframe_t *f = malloc(sizeof *f);
    if (!raw || !rgb || !cover || !f || vgaframe_init(f, &c, raw, rgb, cover) != 0) { fprintf(stderr, "init failed\n"); return 1; }

    uint64_t pushed = 0, pushed_runs = 0;
    struct timespec t0, t1;
    clock_gettime(CLOCK_MONOTONIC, &t0);
    while (pushed < clocks) {
        for (size_t i = 0; i < nruns && pushed < clocks; i++) {
            uint64_t left = clocks - pushed;
            uint32_t run = rr[i] > left ? (uint32_t)left : rr[i];
            vgaframe_push(f, rs[i], run);
            pushed += run; pushed_runs++;
        }
    }
    clock_gettime(CLOCK_MONOTONIC, &t1);
    double s = (double)(t1.tv_sec - t0.tv_sec) + (double)(t1.tv_nsec - t0.tv_nsec) * 1e-9;
    printf("profile=%s clocks=%llu runs=%llu seconds=%.3f ns_per_clock=%.3f ns_per_run=%.3f frames=%llu\n",
           profile, (unsigned long long)pushed, (unsigned long long)pushed_runs, s,
           s * 1e9 / (double)pushed, s * 1e9 / (double)pushed_runs, (unsigned long long)frames);
    return 0;
}
