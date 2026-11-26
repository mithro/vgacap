// SPDX-License-Identifier: Apache-2.0
// bench-runs: times libvgaframe (vgaframe_push) on run-length input.
// One synthetic 640x480@60 frame (800 x 525 clocks, Tiny VGA samples) is
// converted to (sample, run) pairs once, then replayed until N clocks have
// been pushed. Usage: bench-runs --clocks N --profile busy|bars|static
// [--write-trace PATH]
//
// --write-trace PATH (host extras builds only) writes that stream instead, as
// a 43-bit tt-um-43 simulation trace through vgacap_trace_push, two samples a
// clock, for timing vgacap-sample: clocks 0 .. 9 in reset (uo_out 0, rst_n 0),
// then the replayed frames until N clocks in all; ena 1. The clk-low sample
// of clock c holds clock c - 1's uo_out, as the testbench records it.
// META(open): calib_seconds=1 and seconds = N / clock_hz.
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>

#include "synth.h"
#include "vgacap/frame.h"
#ifdef BENCH_WRITE_TRACE
#include "vgacap/time.h"
#include "vgacap/trace.h"
#endif

#define MAX_CPL 7596
#define MAX_LINES 1200

static uint8_t px_busy(void *u, uint16_t x, uint16_t y) { (void)u; return (uint8_t)((x + y) & 0x3F); }
static uint8_t px_bars(void *u, uint16_t x, uint16_t y) { (void)u; (void)y; return (uint8_t)((x / 10) & 0x3F); }
static uint8_t px_static(void *u, uint16_t x, uint16_t y) { (void)u; (void)x; (void)y; return 0x2A; }

static void on_frame(void *u, const vgaframe_output_t *o) { (void)o; (*(uint64_t *)u)++; }

#ifdef BENCH_WRITE_TRACE
#define RESET_CLOCKS 10
#define CLOCK_HZ 25175000u

// The runs rs/rr (one frame), replayed, as a trace of `clocks` clocks.
static int write_trace(const char *path, const char *profile, uint64_t clocks,
                       const uint32_t *rs, const uint32_t *rr, size_t nruns) {
    vgacap_header_t h;
    vgacap_header_init_v2(&h, 43, 2, 42, 0);
    h.mode = VGACAP_MODE_EVENT; h.clock_hz = CLOCK_HZ;
    char secs[64], meta[256], err[256] = "";
    if (vgacap_fmt_double(secs, sizeof secs, (double)clocks / CLOCK_HZ) < 0) return 1;
    snprintf(meta, sizeof meta, "meta=open\nproject=bench_%s\ncalib_seconds=1\nseconds=%s\nlayout=tt-um-43\n",
             profile, secs);
    struct timespec t0, t1;
    clock_gettime(CLOCK_MONOTONIC, &t0);
    vgacap_trace_writer_t *t = vgacap_trace_create(path, &h, meta, 3, err, sizeof err);
    if (!t) { fprintf(stderr, "bench-runs: %s\n", err); return 1; }
    const uint64_t clk = (uint64_t)1 << 42, ena = (uint64_t)1 << 41, rst = (uint64_t)1 << 40;
    uint64_t c = 0, prev = 0;
    int bad = 0;
    for (; c < clocks && c < RESET_CLOCKS; c++) {
        bad |= vgacap_trace_push(t, ena);
        bad |= vgacap_trace_push(t, ena | clk);
    }
    while (c < clocks && !bad) {
        for (size_t i = 0; i < nruns && c < clocks; i++) {
            uint64_t v = ena | rst | rs[i];
            uint64_t n = rr[i] < clocks - c ? rr[i] : clocks - c;
            bad |= vgacap_trace_push(t, prev);       // clock c's clk-low sample: the last clock's outputs
            bad |= vgacap_trace_push(t, v | clk);
            for (uint64_t k = 1; k < n; k++) {
                bad |= vgacap_trace_push(t, v);
                bad |= vgacap_trace_push(t, v | clk);
            }
            prev = v; c += n;
        }
    }
    if (vgacap_trace_finish(t, "meta=close\ncapture_after_clock=0\nstatus=ok\nframes=0\n", err, sizeof err) || bad) {
        fprintf(stderr, "bench-runs: writing %s failed: %s\n", path, err);
        return 1;
    }
    clock_gettime(CLOCK_MONOTONIC, &t1);
    double s = (double)(t1.tv_sec - t0.tv_sec) + (double)(t1.tv_nsec - t0.tv_nsec) * 1e-9;
    printf("profile=%s clocks=%llu trace=%s seconds=%.3f ns_per_clock=%.3f\n", profile,
           (unsigned long long)clocks, path, s, s * 1e9 / (double)clocks);
    return 0;
}
#endif

int main(int argc, char **argv) {
    uint64_t clocks = 0; const char *profile = NULL, *trace = NULL;
    for (int i = 1; i + 1 < argc; i += 2) {
        if (!strcmp(argv[i], "--clocks")) clocks = strtoull(argv[i + 1], NULL, 10);
        else if (!strcmp(argv[i], "--profile")) profile = argv[i + 1];
        else if (!strcmp(argv[i], "--write-trace")) trace = argv[i + 1];
        else { fprintf(stderr, "unknown option %s\n", argv[i]); return 2; }
    }
#ifndef BENCH_WRITE_TRACE
    if (trace) { fprintf(stderr, "bench-runs: built without VGACAP_HOST_EXTRAS: no --write-trace\n"); return 2; }
#endif
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
#ifdef BENCH_WRITE_TRACE
    if (trace) return write_trace(trace, profile, clocks, rs, rr, nruns);
#endif

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
