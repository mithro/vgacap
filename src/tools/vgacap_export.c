// SPDX-License-Identifier: Apache-2.0
// vgacap-export: a trace, or a clock range of it, as FST or VCD for GTKWave
// and Surfer.
//
//   vgacap-export TRACE --format fst|vcd --out FILE [--from CLOCK] [--to CLOCK]
//                 [--no-clk] [--frames FRAMES_TXT] [--scope NAME]
//
// TRACE is anything vgacap_trace_open reads: a simulation trace (zstd, or a
// plain VGCH v2 stream) or a board capture (VGCH v1 or v2). --format alone
// picks the format. --from and --to are clocks (from inclusive, to
// exclusive; default the whole trace), turned into samples by the header's
// samples_per_clock; a range is read by seeking to its block.
//
// Signals, for a trace with META(open) layout=tt-um-43 (43-bit samples, the
// clock on bit 42):
//   <scope>          clk, rst_n, ena, ui_in[7:0], uio_in[7:0], uo_out[7:0],
//                    uio_out[7:0], uio_oe[7:0]   (bits: uo_out 0-7, uio_out
//                    8-15, uio_oe 16-23, ui_in 24-31, uio_in 32-39, rst_n 40,
//                    ena 41, clk 42); --no-clk leaves clk out
// and for any other trace sample[bits-1:0], the whole sample; then
//   <scope>.decoded  hsync, vsync, r[1:0], g[1:0], b[1:0], rgb6[5:0] from the
//                    header's signal map (each channel's first Tiny VGA bit
//                    the more significant; rgb6 = r<<4 | g<<2 | b; a signal
//                    whose bits the map leaves out is not written), and with
//                    --frames frame[31:0].
// <scope> is --scope, else META(open)'s project with '/', '.', whitespace and
// control characters made '_', else "trace".
//
// frame[31:0], from the runs of a frames.txt (any sampler's: its `sampler`
// key names it, and a VCD says so in a $comment): frame k's number from its
// start clock on; -1 (all ones) before frame 0, and in a gap from
// frame_clocks after the start of the frame before it until the next frame
// starts. A frame start at clock c is a change at sample c * samples_per_clock.
//
// Time: the timescale is 1 ps and sample n is at vgacap_sample_time_ps(n,
// samples_per_clock, clock_hz), n the sample's absolute number and clock_hz
// the VGCH header's. The first value of every signal is at the range's first
// sample; the file ends with the time of the range's end (sample `to`).
// The VCD has no $date and the version "vgacap-export", so the same export
// is the same bytes. Within a time, changes are in declaration order.
//
// Beside FILE (its extension replaced, or added when it has none):
//   .gtkw  GTKWave: [dumpfile] FILE's absolute path, [timestart] the first
//          sample's time, groups "ports" and "decoded"; single bits @28,
//          buses @22, frame @24
//   .sucl  a Surfer command file (Surfer's docs/commands/README.md at
//          v0.7.0): load_file, then scope_add_as_group for each scope;
//          not written (with a note on stderr) when FILE's path has
//          whitespace, which the command file cannot quote
//
// Exit: 0; 2 for usage errors (nothing is written); 1 for I/O, trace and
// frames.txt errors (a partly written FILE is removed).
// VGACAP_EXPORT_DEBUG=1 prints "blocks_read=<n> times=<t> changes=<c>" to
// stderr: n the blocks the read decompressed (opening the trace decodes its
// last block as well; that is not counted), t the time steps and c the value
// changes written (the dump's values included); and it checks every segment's stepped time
// against vgacap_sample_time_ps.
#define _XOPEN_SOURCE 700
#include "vgacap/outputs.h"
#include "vgacap/time.h"
#include "vgacap/trace.h"
#include "trace_read_stats.h"

#include <ctype.h>
#include <errno.h>
#include <inttypes.h>
#include <limits.h>
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

#include <fstapi.h>

#define PROG "vgacap-export"
#define FRAME_NONE 0xFFFFFFFFu
#define MAX_SIGS 16

// ---- signals -----------------------------------------------------------------

enum kind { K_FIELD, K_PAIR, K_RGB6, K_FRAME };

typedef struct sig {
    const char *name;     // without the range
    unsigned width;
    int decoded;          // in <scope>.decoded
    enum kind kind;
    unsigned shift;       // K_FIELD: (v >> shift) & mask
    uint64_t mask;
    unsigned bit[6];      // K_PAIR: bit[0] (msb), bit[1]; K_RGB6: r1 r0 g1 g0 b1 b0
    uint8_t gtkw_flags;   // 28, 22 or 24
    uint64_t deps;        // the sample bits it is made of (0 for frame)
    char id[4];           // VCD identifier
    fstHandle fh;
} sig_t;

typedef struct exporter {
    sig_t sig[MAX_SIGS];
    int nsig, clk;        // clk: index of clk, or -1
    uint64_t clk_mask;    // the header's clock bit (toggled within a run), or 0
    uint64_t cur[MAX_SIGS];
    int fst;
    // VCD
    FILE *f;
    char *buf; size_t len;
    int write_error;
    // FST
    fstWriterContext *ctx;
    // time
    uint64_t q, r, d, sq, sr;   // n * 10^12 = q * d + r for the stepped sample
    uint64_t ts_n;              // that sample
    int overflow;
    uint32_t spc, hz;
    int debug, time_bad;
    // frame events, in samples
    uint64_t *ev_n; uint32_t *ev_v; size_t nev, ev_i;
    uint32_t frame;
    // the range
    uint64_t next, to;
    int started, gap;
    uint64_t n_changes, n_times;   // for VGACAP_EXPORT_DEBUG
    uint64_t prev_v;      // the last sample's value, clock bit included
    int frame_sig;        // index of frame, or -1
    // A time's decimal digits above the last eight change rarely: cached.
    uint64_t hi_val; char hi_str[24]; size_t hi_len;
} exporter_t;

#define VCD_BUF (1u << 20)

static int vcd_flush(exporter_t *x) {
    if (x->len && fwrite(x->buf, 1, x->len, x->f) != x->len) x->write_error = 1;
    x->len = 0;
    return x->write_error ? -1 : 0;
}
static inline void vcd_room(exporter_t *x, size_t n) {
    if (x->len + n > VCD_BUF) vcd_flush(x);   // a write error is seen by ferror at the end
}

static const char DIGITS2[] =
    "00010203040506070809101112131415161718192021222324252627282930313233343536373839"
    "40414243444546474849505152535455565758596061626364656667686970717273747576777879"
    "8081828384858687888990919293949596979899";

// Decimal of v at p; returns the length (1..20).
static inline size_t put_u64(char *p, uint64_t v) {
    char tmp[20];
    size_t n = 20;
    while (v >= 100) {
        unsigned k = (unsigned)(v % 100) * 2; v /= 100;
        tmp[--n] = DIGITS2[k + 1]; tmp[--n] = DIGITS2[k];
    }
    if (v >= 10) { unsigned k = (unsigned)v * 2; tmp[--n] = DIGITS2[k + 1]; tmp[--n] = DIGITS2[k]; }
    else tmp[--n] = (char)('0' + v);
    memcpy(p, tmp + n, 20 - n);
    return 20 - n;
}

// Decimal of a time t at p, its digits above the last eight from the cache.
static inline size_t put_time(exporter_t *x, char *p, uint64_t t) {
    uint64_t hi = t / 100000000u;
    if (!hi) return put_u64(p, t);
    uint32_t lo = (uint32_t)(t - hi * 100000000u);
    if (hi != x->hi_val || !x->hi_len) { x->hi_val = hi; x->hi_len = put_u64(x->hi_str, hi); }
    memcpy(p, x->hi_str, x->hi_len);
    p += x->hi_len;
    for (int i = 3; i >= 0; i--) {
        unsigned k = (lo % 100u) * 2u; lo /= 100u;
        p[2 * i] = DIGITS2[k]; p[2 * i + 1] = DIGITS2[k + 1];
    }
    return x->hi_len + 8;
}

// ---- time --------------------------------------------------------------------
// Sample n's time is round(n * 10^12 / d), d = samples_per_clock * clock_hz,
// halves up: q + (r >= d - r) for n * 10^12 = q * d + r. Stepping one sample
// adds 10^12 = sq * d + sr exactly, so the stepped time is
// vgacap_sample_time_ps's (checked at the ends, and at every segment with
// VGACAP_EXPORT_DEBUG=1).

static void time_init(exporter_t *x) {
    x->d = (uint64_t)x->spc * x->hz;
    x->sq = 1000000000000ull / x->d; x->sr = 1000000000000ull % x->d;
    x->q = x->r = 0; x->ts_n = 0;
}
static void time_advance(exporter_t *x, uint64_t k) {
    x->ts_n += k;
    if (k <= 16) {   // a few steps cost less than the divisions below
        for (; k; k--) {
            x->q += x->sq; x->r += x->sr;
            if (x->r >= x->d) { x->r -= x->d; x->q++; }
        }
        return;
    }
    while (k) {
        uint64_t c = k > (1ull << 28) ? (1ull << 28) : k;   // c * sr < 2^28 * 2^33
        uint64_t r = x->r + c * x->sr;
        if (x->sq && c > (UINT64_MAX - x->q) / x->sq) { x->overflow = 1; return; }
        x->q += c * x->sq;
        if (x->q > UINT64_MAX - r / x->d) { x->overflow = 1; return; }
        x->q += r / x->d; x->r = r % x->d;
        k -= c;
    }
}
static inline void time_step(exporter_t *x) {
    x->ts_n++;
    x->q += x->sq; x->r += x->sr;
    if (x->r >= x->d) { x->r -= x->d; x->q++; }
}
static inline uint64_t time_now(const exporter_t *x) { return x->q + (x->r >= x->d - x->r); }
static void time_to(exporter_t *x, uint64_t n) {
    time_advance(x, n - x->ts_n);
    if (x->debug && !x->overflow && time_now(x) != vgacap_sample_time_ps(n, x->spc, x->hz)) x->time_bad = 1;
}

// ---- output ------------------------------------------------------------------

static void out_time(exporter_t *x, uint64_t t) {
    x->n_times++;
    if (x->fst) { fstWriterEmitTimeChange(x->ctx, t); return; }
    vcd_room(x, 24);
    char *p = x->buf + x->len;
    *p++ = '#';
    p += put_time(x, p, t);
    *p++ = '\n';
    x->len = (size_t)(p - x->buf);
}

static void out_value(exporter_t *x, const sig_t *s, uint64_t v) {
    x->n_changes++;
    if (x->fst) {
        if (s->width == 1) fstWriterEmitValueChange(x->ctx, s->fh, v ? "1" : "0");
        else fstWriterEmitValueChange64(x->ctx, s->fh, s->width, v);
        return;
    }
    vcd_room(x, 80);
    char *p = x->buf + x->len;
    if (s->width == 1) *p++ = (char)('0' + (v & 1));
    else {
        *p++ = 'b';
        for (unsigned i = s->width; i-- > 0; ) *p++ = (char)('0' + ((v >> i) & 1));
        *p++ = ' ';
    }
    for (const char *c = s->id; *c; c++) *p++ = *c;
    *p++ = '\n';
    x->len = (size_t)(p - x->buf);
}

// clk alone changes for samples ts_n + 1 .. ts_n + k, its value toggling
// from `b` (the value at ts_n). The hot path: one change per sample.
static void clk_toggles(exporter_t *x, uint64_t k, unsigned b) {
    const sig_t *s = &x->sig[x->clk];
    x->n_changes += k; x->n_times += k;
    if (x->fst) {
        for (uint64_t i = 0; i < k; i++) {
            time_step(x); b ^= 1u;
            fstWriterEmitTimeChange(x->ctx, time_now(x));
            fstWriterEmitValueChange(x->ctx, s->fh, b ? "1" : "0");
        }
        x->cur[x->clk] = b;
        x->prev_v ^= (k & 1) ? x->clk_mask : 0;
        return;
    }
    const char id = s->id[0];   // clk is always the first signal: a one-character id
    for (uint64_t i = 0; i < k; i++) {
        time_step(x); b ^= 1u;
        if (x->len + 32 > VCD_BUF) vcd_flush(x);
        char *p = x->buf + x->len;
        *p++ = '#';
        p += put_time(x, p, time_now(x));
        p[0] = '\n'; p[1] = (char)('0' + b); p[2] = id; p[3] = '\n';
        x->len = (size_t)(p + 4 - x->buf);
    }
    x->cur[x->clk] = b;
    x->prev_v ^= (k & 1) ? x->clk_mask : 0;
}

// ---- values ------------------------------------------------------------------

static inline uint64_t bit(uint64_t v, unsigned b) { return (v >> b) & 1u; }

static uint64_t sig_value(const exporter_t *x, const sig_t *s, uint64_t v) {
    switch (s->kind) {
    case K_FIELD: return (v >> s->shift) & s->mask;
    case K_PAIR: return bit(v, s->bit[0]) << 1 | bit(v, s->bit[1]);
    case K_RGB6:
        return bit(v, s->bit[0]) << 5 | bit(v, s->bit[1]) << 4 | bit(v, s->bit[2]) << 3 |
               bit(v, s->bit[3]) << 2 | bit(v, s->bit[4]) << 1 | bit(v, s->bit[5]);
    case K_FRAME: return x->frame;
    }
    return 0;
}

// Sample n has value v: write the time and what changed (everything, as
// the dump, for the first sample).
static void sample_changes(exporter_t *x, uint64_t n, uint64_t v) {
    if (!x->started) {
        time_to(x, n);
        out_time(x, time_now(x));
        if (!x->fst) { vcd_room(x, 16); memcpy(x->buf + x->len, "$dumpvars\n", 10); x->len += 10; }
        for (int i = 0; i < x->nsig; i++) {
            x->cur[i] = sig_value(x, &x->sig[i], v);
            out_value(x, &x->sig[i], x->cur[i]);
        }
        if (!x->fst) { vcd_room(x, 16); memcpy(x->buf + x->len, "$end\n", 5); x->len += 5; }
        x->started = 1; x->prev_v = v;
        return;
    }
    // A signal changes exactly when one of its bits does (each is a
    // one-to-one function of its bits), and frame when the frame does.
    const uint64_t delta = v ^ x->prev_v;
    const int fch = x->frame_sig >= 0 && x->cur[x->frame_sig] != x->frame;
    if (!delta && !fch) return;
    time_to(x, n);
    out_time(x, time_now(x));
    for (int i = 0; i < x->nsig; i++) {
        if (!(delta & x->sig[i].deps)) continue;
        x->cur[i] = sig_value(x, &x->sig[i], v);
        out_value(x, &x->sig[i], x->cur[i]);
    }
    if (fch) { x->cur[x->frame_sig] = x->frame; out_value(x, &x->sig[x->frame_sig], x->frame); }
    x->prev_v = v;
}

static int on_runs(void *user, const vgacap_trace_run_t *runs, size_t nr) {
    exporter_t *x = user;
    for (size_t i = 0; i < nr; i++) {
        const vgacap_trace_run_t *rr = &runs[i];
        if (rr->first_sample != x->next) { x->gap = 1; return 1; }
        uint64_t n = rr->first_sample, end = n + rr->run;
        while (n < end) {
            uint64_t seg = end;
            if (x->ev_i < x->nev && x->ev_n[x->ev_i] < seg) seg = x->ev_n[x->ev_i];
            // seg > n: events at or before n have been taken
            uint64_t v = rr->value;
            if ((n - rr->first_sample) & 1) v ^= x->clk_mask;
            sample_changes(x, n, v);
            if (x->clk >= 0 && seg - n > 1) {
                time_to(x, n);
                clk_toggles(x, seg - n - 1, (unsigned)x->cur[x->clk]);
            }
            n = seg;
            while (x->ev_i < x->nev && x->ev_n[x->ev_i] <= n) x->frame = x->ev_v[x->ev_i++];
        }
        x->next = end;
    }
    return 0;
}

// ---- arguments ---------------------------------------------------------------

static int usage(const char *fmt, ...) {
    fprintf(stderr, PROG ": ");
    va_list ap; va_start(ap, fmt);
    vfprintf(stderr, fmt, ap);
    va_end(ap);
    fprintf(stderr, "\nusage: " PROG " TRACE --format fst|vcd --out FILE [--from CLOCK] [--to CLOCK]\n"
                    "       [--no-clk] [--frames FRAMES_TXT] [--scope NAME]\n");
    return 2;
}

static int parse_u64(const char *s, uint64_t *out) {
    if (!*s || (s[0] == '0' && s[1])) return -1;
    uint64_t v = 0;
    for (; *s; s++) {
        if (*s < '0' || *s > '9') return -1;
        unsigned d = (unsigned)(*s - '0');
        if (v > (UINT64_MAX - d) / 10) return -1;
        v = v * 10 + d;
    }
    *out = v;
    return 0;
}

// META's value for `key` into out; 0, or -1 if absent, empty or too long.
static int meta_get(const char *meta, const char *key, char *out, size_t outlen) {
    size_t kl = strlen(key);
    for (const char *p = meta; p && *p; ) {
        const char *nl = strchr(p, '\n');
        size_t ll = nl ? (size_t)(nl - p) : strlen(p);
        if (ll > kl + 1 && !strncmp(p, key, kl) && p[kl] == '=') {
            size_t vl = ll - kl - 1;
            if (vl >= outlen) return -1;
            memcpy(out, p + kl + 1, vl); out[vl] = 0;
            return 0;
        }
        p = nl ? nl + 1 : NULL;
    }
    return -1;
}

static int name_ok(const char *s) {   // a scope name: printable, no whitespace, '.' or '$'
    if (!*s) return 0;
    for (; *s; s++) if (!isgraph((unsigned char)*s) || *s == '.' || *s == '$') return 0;
    return 1;
}

static void add_sig(exporter_t *x, const char *name, unsigned width, int decoded, enum kind k) {
    sig_t *s = &x->sig[x->nsig++];
    memset(s, 0, sizeof *s);
    s->name = name; s->width = width; s->decoded = decoded; s->kind = k;
    s->mask = width >= 64 ? UINT64_MAX : (1ull << width) - 1;
    s->gtkw_flags = k == K_FRAME ? 24 : width == 1 ? 28 : 22;
}
static void add_field(exporter_t *x, const char *name, unsigned width, unsigned shift) {
    add_sig(x, name, width, 0, K_FIELD);
    x->sig[x->nsig - 1].shift = shift;
}

// The signal's full name, scope included: "<scope>[.decoded].<name>[<w-1>:0]".
static void full_name(char *out, size_t len, const char *scope, const sig_t *s) {
    char range[24] = "";
    if (s->width > 1) snprintf(range, sizeof range, "[%u:0]", s->width - 1);
    snprintf(out, len, "%s%s.%s%s", scope, s->decoded ? ".decoded" : "", s->name, range);
}

// The path beside `out` with extension `ext` (".gtkw"): the last '.' of the
// file name is replaced, or `ext` appended.
static int beside(char *dst, size_t len, const char *out, const char *ext) {
    const char *slash = strrchr(out, '/'), *base = slash ? slash + 1 : out, *dot = strrchr(base, '.');
    size_t stem = dot && dot != base ? (size_t)(dot - out) : strlen(out);
    if (stem + strlen(ext) + 1 > len) return -1;
    memcpy(dst, out, stem); strcpy(dst + stem, ext);
    return 0;
}

static int write_gtkw(const exporter_t *x, const char *path, const char *abs, const char *scope, uint64_t t0) {
    FILE *f = fopen(path, "w");
    if (!f) return -1;
    fprintf(f, "[dumpfile] \"%s\"\n[timestart] %" PRIu64 "\n", abs, t0);
    for (int g = 0; g < 2; g++) {
        const char *group = g ? "decoded" : "ports";
        fprintf(f, "@800200\n-%s\n", group);
        int flags = -1;
        for (int i = 0; i < x->nsig; i++) {
            const sig_t *s = &x->sig[i];
            if (s->decoded != g) continue;
            if (s->gtkw_flags != flags) { fprintf(f, "@%u\n", s->gtkw_flags); flags = s->gtkw_flags; }
            char name[1200];
            full_name(name, sizeof name, scope, s);
            fprintf(f, "%s\n", name);
        }
        fprintf(f, "@1000200\n-%s\n", group);
    }
    return fclose(f) ? -1 : 0;
}

static int write_sucl(const char *path, const char *abs, const char *scope, int has_decoded) {
    FILE *f = fopen(path, "w");
    if (!f) return -1;
    fprintf(f, "load_file %s\nscope_add_as_group %s\n", abs, scope);
    if (has_decoded) fprintf(f, "scope_add_as_group %s.decoded\n", scope);
    return fclose(f) ? -1 : 0;
}

// Frame events from frames.txt's runs (see the top of the file), in samples.
static int frame_events(exporter_t *x, const vgaout_frame_runs_t *fr, char *err, size_t errlen) {
    size_t cap = 0;
    int have_prev = 0;
    uint64_t prev = 0;
    if (fr->frames >= FRAME_NONE) { snprintf(err, errlen, "too many frames (%" PRIu64 ")", fr->frames); return -1; }
    for (size_t i = 0; i < fr->n_runs; i++) {
        const vgaout_frame_run_t *r = &fr->runs[i];
        uint64_t k_end = r->distinct < 0 ? 1 : r->count;
        for (uint64_t k = 0; k < k_end; k++) {
            uint64_t clock; uint32_t value;
            if (r->distinct < 0) {
                if (!have_prev || !fr->frame_clocks) break;
                clock = prev + fr->frame_clocks; value = FRAME_NONE; have_prev = 0;
            } else {
                clock = r->start_clock + k * fr->frame_clocks; value = (uint32_t)(r->first + k);
                prev = clock; have_prev = 1;
            }
            if (clock > UINT64_MAX / x->spc) { snprintf(err, errlen, "a frame start is out of range"); return -1; }
            uint64_t n = clock * x->spc;
            while (x->nev && x->ev_n[x->nev - 1] >= n) x->nev--;   // a later event at or before it wins
            if (x->nev == cap) {
                cap = cap ? 2 * cap : 1024;
                x->ev_n = realloc(x->ev_n, cap * sizeof *x->ev_n);
                x->ev_v = realloc(x->ev_v, cap * sizeof *x->ev_v);
                if (!x->ev_n || !x->ev_v) { snprintf(err, errlen, "out of memory"); return -1; }
            }
            x->ev_n[x->nev] = n; x->ev_v[x->nev] = value; x->nev++;
        }
    }
    return 0;
}

int main(int argc, char **argv) {
    const char *trace = NULL, *out = NULL, *format = NULL, *frames = NULL, *scope_arg = NULL;
    uint64_t from = 0, to = 0;
    int have_to = 0, no_clk = 0;
    for (int i = 1; i < argc; i++) {
        const char *a = argv[i];
        if (a[0] != '-' || !a[1]) {
            if (trace) return usage("one TRACE only ('%s')", a);
            trace = a; continue;
        }
        if (!strcmp(a, "--no-clk")) { no_clk = 1; continue; }
        if (i + 1 >= argc) return usage("%s needs a value", a);
        const char *v = argv[++i];
        if (!strcmp(a, "--format")) {
            if (strcmp(v, "fst") && strcmp(v, "vcd")) return usage("--format is fst or vcd, not '%s'", v);
            format = v;
        } else if (!strcmp(a, "--out")) out = v;
        else if (!strcmp(a, "--from")) { if (parse_u64(v, &from)) return usage("--from wants a clock, not '%s'", v); }
        else if (!strcmp(a, "--to")) { if (parse_u64(v, &to)) return usage("--to wants a clock, not '%s'", v); have_to = 1; }
        else if (!strcmp(a, "--frames")) frames = v;
        else if (!strcmp(a, "--scope")) {
            if (!name_ok(v)) return usage("--scope wants a name without whitespace, '.' or '$', not '%s'", v);
            scope_arg = v;
        } else return usage("unknown option '%s'", a);
    }
    if (!trace || !out || !format) return usage("TRACE, --format and --out are required");

    char err[512] = "";
    vgacap_trace_reader_t *rd = vgacap_trace_open(trace, err, sizeof err);
    if (!rd) { fprintf(stderr, PROG ": %s\n", err); return 1; }
    const vgacap_trace_info_t *info = vgacap_trace_info(rd);
    const vgacap_header_t *h = &info->header;
    exporter_t *x = calloc(1, sizeof *x);
    if (!x) { fprintf(stderr, PROG ": out of memory\n"); return 1; }
    x->fst = !strcmp(format, "fst");
    x->clk = -1;
    x->spc = h->samples_per_clock ? h->samples_per_clock : 1;
    x->hz = h->clock_hz;
    const char *dbg = getenv("VGACAP_EXPORT_DEBUG");
    x->debug = dbg && !strcmp(dbg, "1");
    if (!x->hz) { fprintf(stderr, PROG ": %s: the header has no clock_hz, so samples have no time\n", trace); return 1; }

    // The range, in samples.
    if (from > UINT64_MAX / x->spc || (have_to && to > UINT64_MAX / x->spc))
        return usage("the range is past the trace's end");
    uint64_t from_s = from * x->spc, to_s = have_to ? to * x->spc : info->samples;
    if (to_s > info->samples)
        return usage("--to %" PRIu64 " is past the trace's end (%" PRIu64 " samples, %" PRIu64 " a clock)",
                     to, info->samples, (uint64_t)x->spc);
    if (from_s >= to_s) return usage("the range is empty (--from %" PRIu64 ", to sample %" PRIu64 ")", from, to_s);

    // Signals.
    char layout[64] = "", project[1024] = "", scope[1024];
    int tt = h->version == 2 && info->open_meta && !meta_get(info->open_meta, "layout", layout, sizeof layout) &&
             !strcmp(layout, "tt-um-43");
    if (tt && (h->sample_bits != 43 || h->clock_bit != 42 || h->clock_first != 0 || h->samples_per_clock != 2)) {
        fprintf(stderr, PROG ": %s: header disagrees with layout tt-um-43 (bits %u, clock bit %u, clock first %u, "
                "%u samples a clock)\n", trace, h->sample_bits, h->clock_bit, h->clock_first, h->samples_per_clock);
        return 1;
    }
    if (tt) {
        if (!no_clk) { x->clk = x->nsig; add_field(x, "clk", 1, 42); }
        add_field(x, "rst_n", 1, 40); add_field(x, "ena", 1, 41);
        add_field(x, "ui_in", 8, 24); add_field(x, "uio_in", 8, 32); add_field(x, "uo_out", 8, 0);
        add_field(x, "uio_out", 8, 8); add_field(x, "uio_oe", 8, 16);
    } else {
        // Another trace with a clock bit (none exists yet): clk on its own,
        // and sample with that bit cleared, so sample changes with the data.
        int cb = h->clock_bit != VGACAP_CLOCK_BIT_NONE && h->clock_bit < h->sample_bits;
        if (cb && !no_clk) { x->clk = x->nsig; add_field(x, "clk", 1, h->clock_bit); }
        add_field(x, "sample", h->sample_bits, 0);
        if (cb) x->sig[x->nsig - 1].mask &= ~(1ull << h->clock_bit);
    }
    if (h->clock_bit != VGACAP_CLOCK_BIT_NONE && h->clock_bit < 64) x->clk_mask = 1ull << h->clock_bit;
    const uint8_t *m = h->signal_map;
    #define MAPPED(i) (m[i] != VGACAP_SIG_ABSENT && m[i] < h->sample_bits)
    if (MAPPED(VGACAP_SIG_HSYNC)) { add_sig(x, "hsync", 1, 1, K_FIELD); x->sig[x->nsig - 1].shift = m[VGACAP_SIG_HSYNC]; }
    if (MAPPED(VGACAP_SIG_VSYNC)) { add_sig(x, "vsync", 1, 1, K_FIELD); x->sig[x->nsig - 1].shift = m[VGACAP_SIG_VSYNC]; }
    int colour = 1;
    for (int i = VGACAP_SIG_R1; i <= VGACAP_SIG_B0; i++) colour &= MAPPED(i);
    if (colour) {
        static const char *const ch[3] = { "r", "g", "b" };
        for (int c = 0; c < 3; c++) {
            add_sig(x, ch[c], 2, 1, K_PAIR);
            x->sig[x->nsig - 1].bit[0] = m[VGACAP_SIG_R1 + 2 * c];
            x->sig[x->nsig - 1].bit[1] = m[VGACAP_SIG_R0 + 2 * c];
        }
        add_sig(x, "rgb6", 6, 1, K_RGB6);
        for (int i = 0; i < 6; i++) x->sig[x->nsig - 1].bit[i] = m[VGACAP_SIG_R1 + i];
    }
    #undef MAPPED
    vgaout_frame_runs_t fr;
    memset(&fr, 0, sizeof fr);
    if (frames) {
        FILE *ff = fopen(frames, "r");
        if (!ff) { fprintf(stderr, PROG ": cannot open %s: %s\n", frames, strerror(errno)); return 1; }
        int rc = vgaout_read_frame_runs(ff, &fr, err, sizeof err);
        fclose(ff);
        if (rc) { fprintf(stderr, PROG ": %s: %s\n", frames, err); return 1; }
        for (const char *c = fr.sampler; *c; c++)
            if (!isalnum((unsigned char)*c) && !strchr("._-", *c)) {
                fprintf(stderr, PROG ": %s: sampler '%s' is not a plain name\n", frames, fr.sampler); return 1;
            }
        if (frame_events(x, &fr, err, sizeof err)) { fprintf(stderr, PROG ": %s: %s\n", frames, err); return 1; }
        add_sig(x, "frame", 32, 1, K_FRAME);
    }
    x->frame_sig = -1;
    for (int i = 0; i < x->nsig; i++) {   // each signal's bits
        sig_t *s = &x->sig[i];
        switch (s->kind) {
        case K_FIELD: s->deps = s->mask << s->shift; break;
        case K_PAIR: s->deps = 1ull << s->bit[0] | 1ull << s->bit[1]; break;
        case K_RGB6: for (int j = 0; j < 6; j++) s->deps |= 1ull << s->bit[j]; break;
        case K_FRAME: s->deps = 0; x->frame_sig = i; break;
        }
    }
    for (int i = 0; i < x->nsig; i++) {   // VCD ids '!' onwards: one character each
        x->sig[i].id[0] = (char)('!' + i); x->sig[i].id[1] = 0;
    }
    if (scope_arg) snprintf(scope, sizeof scope, "%s", scope_arg);
    else if (info->open_meta && !meta_get(info->open_meta, "project", project, sizeof project)) {
        snprintf(scope, sizeof scope, "%s", project);
        for (char *c = scope; *c; c++) if (!isgraph((unsigned char)*c) || *c == '/' || *c == '.' || *c == '$') *c = '_';
    } else snprintf(scope, sizeof scope, "trace");

    // The frame value at the first sample.
    x->frame = FRAME_NONE;
    while (x->ev_i < x->nev && x->ev_n[x->ev_i] <= from_s) x->frame = x->ev_v[x->ev_i++];

    // Open and write the header.
    time_init(x);
    time_to(x, from_s);
    uint64_t t0 = time_now(x);
    if (t0 != vgacap_sample_time_ps(from_s, x->spc, x->hz)) { fprintf(stderr, PROG ": time step is wrong\n"); return 1; }
    if (x->fst) {
        x->ctx = fstWriterCreate(out, 1);
        if (!x->ctx) { fprintf(stderr, PROG ": cannot create %s\n", out); return 1; }
        fstWriterSetPackType(x->ctx, FST_WR_PT_LZ4);
        fstWriterSetTimescale(x->ctx, -12);
        fstWriterSetVersion(x->ctx, "vgacap-export");
        fstWriterSetDate(x->ctx, "");
        fstWriterSetScope(x->ctx, FST_ST_VCD_MODULE, scope, NULL);
        int in_dec = 0;
        for (int i = 0; i < x->nsig; i++) {
            sig_t *s = &x->sig[i];
            if (s->decoded && !in_dec) { fstWriterSetScope(x->ctx, FST_ST_VCD_MODULE, "decoded", NULL); in_dec = 1; }
            char name[64];
            if (s->width > 1) snprintf(name, sizeof name, "%s[%u:0]", s->name, s->width - 1);
            else snprintf(name, sizeof name, "%s", s->name);
            s->fh = fstWriterCreateVar(x->ctx, FST_VT_VCD_WIRE, FST_VD_IMPLICIT, s->width, name, 0);
        }
        if (in_dec) fstWriterSetUpscope(x->ctx);
        fstWriterSetUpscope(x->ctx);
    } else {
        x->f = fopen(out, "wb");
        x->buf = malloc(VCD_BUF);
        if (!x->f || !x->buf) { fprintf(stderr, PROG ": cannot create %s: %s\n", out, strerror(errno)); return 1; }
        FILE *f = x->f;
        fprintf(f, "$version vgacap-export $end\n$timescale 1ps $end\n");
        if (frames) fprintf(f, "$comment frame: the frame starts of %s's frames.txt $end\n", fr.sampler);
        fprintf(f, "$scope module %s $end\n", scope);
        int in_dec = 0;
        for (int i = 0; i < x->nsig; i++) {
            const sig_t *s = &x->sig[i];
            if (s->decoded && !in_dec) { fprintf(f, "$scope module decoded $end\n"); in_dec = 1; }
            if (s->width > 1) fprintf(f, "$var wire %u %s %s [%u:0] $end\n", s->width, s->id, s->name, s->width - 1);
            else fprintf(f, "$var wire 1 %s %s $end\n", s->id, s->name);
        }
        if (in_dec) fprintf(f, "$upscope $end\n");
        fprintf(f, "$upscope $end\n$enddefinitions $end\n");
    }

    // The values.
    vgacap_trace_read_stats_t before, after;
    vgacap_trace_read_stats(rd, &before);
    x->next = from_s; x->to = to_s;
    int rc = vgacap_trace_read_runs(rd, from_s, to_s, on_runs, x, err, sizeof err);
    vgacap_trace_read_stats(rd, &after);
    const char *why = NULL;
    char whybuf[600];
    if (rc) { snprintf(whybuf, sizeof whybuf, "%s: %s", trace, err); why = whybuf; }
    else if (x->gap || x->next != to_s) {
        snprintf(whybuf, sizeof whybuf, "%s: the runs do not cover samples %" PRIu64 " to %" PRIu64, trace, from_s, to_s);
        why = whybuf;
    }
    if (!why) {
        time_to(x, to_s);
        if (x->overflow) why = "a time is past 2^64 ps";
        else if (x->time_bad || time_now(x) != vgacap_sample_time_ps(to_s, x->spc, x->hz)) why = "time step is wrong";
        else out_time(x, time_now(x));
    }
    if (x->fst) {
        int e = fstWriterClose2(x->ctx);
        if (!why && e) { snprintf(whybuf, sizeof whybuf, "write error on %s: %s", out, strerror(e)); why = whybuf; }
    } else {
        int e = vcd_flush(x) || ferror(x->f);
        if (fclose(x->f) || e) {
            if (!why) { snprintf(whybuf, sizeof whybuf, "write error on %s: %s", out, strerror(errno)); why = whybuf; }
        }
        free(x->buf);
    }
    if (why) { fprintf(stderr, PROG ": %s\n", why); unlink(out); return 1; }
    if (x->debug)
        fprintf(stderr, "blocks_read=%" PRIu64 " times=%" PRIu64 " changes=%" PRIu64 "\n",
                after.blocks_decoded - before.blocks_decoded, x->n_times, x->n_changes);

    // The viewer files.
    char abs[PATH_MAX], gtkw[PATH_MAX + 8], sucl[PATH_MAX + 8];
    if (!realpath(out, abs) || beside(gtkw, sizeof gtkw, out, ".gtkw") || beside(sucl, sizeof sucl, out, ".sucl")) {
        fprintf(stderr, PROG ": cannot name the files beside %s\n", out); return 1;
    }
    int has_decoded = x->nsig && x->sig[x->nsig - 1].decoded;
    if (write_gtkw(x, gtkw, abs, scope, t0)) { fprintf(stderr, PROG ": cannot write %s\n", gtkw); return 1; }
    int ws = 0;
    for (const char *c = abs; *c; c++) ws |= isspace((unsigned char)*c) != 0;
    if (ws) fprintf(stderr, PROG ": note: no %s: a Surfer command file cannot name a path with whitespace\n", sucl);
    else if (write_sucl(sucl, abs, scope, has_decoded)) { fprintf(stderr, PROG ": cannot write %s\n", sucl); return 1; }

    vgaout_frame_runs_free(&fr);
    free(x->ev_n); free(x->ev_v); free(x);
    vgacap_trace_close(rd);
    return 0;
}
