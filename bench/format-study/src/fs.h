// SPDX-License-Identifier: Apache-2.0
// Trace format study (docs/design/2026-11-27-trace-format-study.md): the
// types shared by the replay source, the candidate writers, the background
// encoders and the readers.
//
// A clock is two samples, each an 8-byte "raw image" of the module's ports
// in the layout Verilator keeps them in the model root: one byte per port.
// The study fixes the byte order below (tb.cpp finds Verilator's at run time
// and packs through an offset table; the candidates do the same, so the
// packing cost is the testbench's).
#ifndef FS_H
#define FS_H

#include <stddef.h>
#include <stdint.h>

// Byte k of a raw image.
enum { FS_UO = 0, FS_UIO_OUT, FS_UIO_OE, FS_UI_IN, FS_UIO_IN, FS_RST_N, FS_ENA, FS_CLK, FS_PORTS };
#define FS_GROUPS 7                       // every port but clk
#define FS_CLK_RAW (1ull << (8 * FS_CLK)) // the clk byte's bit 0
#define FS_NOCLK_MASK (~(0xffull << (8 * FS_CLK)))

// tt-um-43 sample (vgacap docs/trace-format.md) <-> raw image.
static inline uint64_t fs_raw_from_sample(uint64_t v) {
    return (v & 0xffffffffffull) | (((v >> 40) & 1) << (8 * FS_RST_N)) | (((v >> 41) & 1) << (8 * FS_ENA)) |
           (((v >> 42) & 1) << (8 * FS_CLK));
}
// tb.cpp's pack_raw, with the offset table a run-time array as there.
extern int fs_off[8];
extern uint32_t fs_clock_hz;   // the source trace's, set by fs_source_open
// round(s * 10^12 / (2 * clock_hz)): sample s in picoseconds (trace-format.md).
static inline uint64_t fs_ps(uint64_t s) {
    const unsigned __int128 d = 2 * (unsigned __int128)fs_clock_hz;
    return (uint64_t)(((unsigned __int128)s * 1000000000000ull + d / 2) / d);
}
static inline uint64_t fs_pack(uint64_t raw) {
    uint8_t b[8];
    __builtin_memcpy(b, &raw, 8);
    const int *o = fs_off;
    return (uint64_t)b[o[0]] | (uint64_t)b[o[1]] << 8 | (uint64_t)b[o[2]] << 16 | (uint64_t)b[o[3]] << 24 |
           (uint64_t)b[o[4]] << 32 | (uint64_t)(b[o[5]] & 1) << 40 | (uint64_t)(b[o[6]] & 1) << 41 |
           (uint64_t)(b[o[7]] & 1) << 42;
}

// ---- the replay source: a real trace, read through vgacap's reader ----------
typedef struct fs_source fs_source_t;
fs_source_t *fs_source_open(const char *path);
uint64_t fs_source_clocks(const fs_source_t *s);
// raw[0 .. 2 * clocks) = the samples of clocks [c0, c0 + clocks).
int fs_source_chunk(fs_source_t *s, uint64_t c0, uint64_t clocks, uint64_t *raw);
// Every run of the trace (value with the clock bit of its first sample), in
// order, in batches; fn returns nonzero to stop.
typedef struct { uint64_t first_sample, value, run; } fs_run_t;
typedef int (*fs_runs_fn)(void *user, const fs_run_t *runs, size_t n);
int fs_source_runs(fs_source_t *s, uint64_t from, uint64_t to, fs_runs_fn fn, void *user);
void fs_source_close(fs_source_t *s);

// ---- blocks: what a candidate's on-thread part hands to its background part --
#define FS_MAX_STREAMS 8
typedef struct {
    uint64_t first_sample, nsamples;
    int nstreams;
    uint8_t *buf[FS_MAX_STREAMS];
    size_t len[FS_MAX_STREAMS];
} fs_block_t;
typedef void (*fs_block_fn)(void *ctx, const fs_block_t *b);

// ---- candidate writers (the simulation-thread part) -------------------------
typedef struct {
    const char *name;
    // block_clocks: a block's length in clocks (0: the candidate's default);
    // out: a file the candidate writes itself (FST, LXT2, vgch2), or NULL;
    // on_block: where the candidate's blocks go (NULL: dropped, for timing).
    void *(*create)(uint64_t block_clocks, const char *out, fs_block_fn on_block, void *ctx);
    // Records clocks [n/2, n/2 + clocks) from raw[0 .. 2 * clocks).
    void (*run)(void *st, const uint64_t *raw, uint64_t clocks);
    void (*finish)(void *st);
} fs_writer_t;

extern const fs_writer_t fs_w_null, fs_w_vgch2, fs_w_planar, fs_w_events, fs_w_runs, fs_w_clog, fs_w_raw, fs_w_fst, fs_w_fst_clk,
    fs_w_lxt2, fs_w_vcd, fs_w_lean;
const fs_writer_t *fs_writer_by_name(const char *name);

// ---- time ------------------------------------------------------------------
double fs_thread_ns(void);   // CLOCK_THREAD_CPUTIME_ID
double fs_wall_ns(void);     // CLOCK_MONOTONIC

#endif
