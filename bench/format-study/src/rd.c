// SPDX-License-Identifier: Apache-2.0
// fsbench read: the readers, timed on the consumers' access patterns.
//
//   fsbench read FILE --kind vgch2|stdy|fst --project P [--patterns open,scan,port0,seek,validate]
//                [--reps R] [--seeks N] [--window CLOCKS]
//
// Every reader delivers runs as vgacap's does: (first sample, 43-bit value
// with the clock bit of that sample, run), clipped to the range, in order,
// in batches (fs_run_t). Patterns:
//   open      open the file (vgch2 reads each block's start; stdy its index)
//   scan      every run of the whole trace to a consumer that only counts:
//             the samplers' pass, without any sampler or hash work
//   port0     the runs of uo_out alone (merged when uo_out did not change):
//             an export of one pin group
//   seek      N windows of CLOCKS clocks at fixed pseudo-random clocks
//             (export --from/--to, tracetool slice), each from a fresh open
//   validate  a hash of the canonical runs (adjacent equal values merged)
//             and of the uo_out runs, to check that every format holds the
//             same samples
#define _GNU_SOURCE
#include "codec.h"
#include "fs.h"
#include "fstapi.h"
#include "vgacap/trace.h"
#include <fcntl.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <sys/stat.h>
#include <unistd.h>

#define BATCH 1024
#define CLK43 (1ull << 42)

// ---- consumers ----------------------------------------------------------------------------
typedef struct {
    uint64_t runs, samples;
    // validate: the canonical run being merged, and the hash
    int hashing;
    uint64_t h, pend_first, pend_val, pend_len;
} cons_t;

static inline uint64_t mix(uint64_t h, uint64_t x) {
    h ^= x + 0x9e3779b97f4a7c15ull + (h << 6) + (h >> 2);
    return h * 0xff51afd7ed558ccdull;
}

static void cons_flush(cons_t *c) {
    if (c->pend_len) c->h = mix(mix(mix(c->h, c->pend_first), c->pend_val), c->pend_len);
    c->pend_len = 0;
}

static int cons_fn(void *user, const fs_run_t *r, size_t n) {
    cons_t *c = user;
    c->runs += n;
    uint64_t s = 0;
    for (size_t i = 0; i < n; i++) s += r[i].run;
    c->samples += s;
    if (c->hashing) {
        for (size_t i = 0; i < n; i++) {
            const uint64_t v = r[i].value & ~CLK43;
            // A run continues the pending one if it starts where it ends
            // with the same value: the clock bit is the sample's parity.
            if (c->pend_len && v == c->pend_val && r[i].first_sample == c->pend_first + c->pend_len) {
                c->pend_len += r[i].run;
            } else {
                cons_flush(c);
                c->pend_first = r[i].first_sample; c->pend_val = v; c->pend_len = r[i].run;
            }
            // Every reader must give the right clock bit (full runs only).
            if (c->hashing == 1 && ((r[i].value >> 42) & 1) != (r[i].first_sample & 1)) c->h = mix(c->h, 0xbadc10c);
        }
    }
    return 0;
}

// port0 from full runs: merges into uo_out runs, then to the consumer.
typedef struct {
    cons_t *c;
    fs_run_t out[BATCH];
    size_t n;
    uint64_t first, val, len;
} port0_t;

static void p0_emit(port0_t *p, uint64_t first, uint64_t val, uint64_t len) {
    if (p->len && val == p->val && first == p->first + p->len) { p->len += len; return; }
    if (p->len) {
        p->out[p->n].first_sample = p->first; p->out[p->n].value = p->val; p->out[p->n].run = p->len;
        if (++p->n == BATCH) { cons_fn(p->c, p->out, p->n); p->n = 0; }
    }
    p->first = first; p->val = val; p->len = len;
}
static void p0_done(port0_t *p) {
    if (p->len) {
        p->out[p->n].first_sample = p->first; p->out[p->n].value = p->val; p->out[p->n].run = p->len;
        p->n++;
    }
    if (p->n) cons_fn(p->c, p->out, p->n);
    p->n = 0; p->len = 0;
}
static int p0_fn(void *user, const fs_run_t *r, size_t n) {
    port0_t *p = user;
    for (size_t i = 0; i < n; i++) p0_emit(p, r[i].first_sample, r[i].value & 0xff, r[i].run);
    return 0;
}

// ---- a batch of runs, clipped to [from, to) ---------------------------------------------------
typedef struct {
    fs_runs_fn fn;
    void *user;
    uint64_t from, to;
    int done;
    size_t n;
    fs_run_t r[BATCH];
} out_t;

static inline void out_flush(out_t *o) {
    if (o->n) { if (o->fn(o->user, o->r, o->n)) o->done = 1; o->n = 0; }
}
// A run of `len` samples from sample a, value v without the clock bit.
static inline void out_run(out_t *o, uint64_t a, uint64_t v, uint64_t len) {
    uint64_t e = a + len;
    if (e <= o->from) return;
    if (a >= o->to) { o->done = 1; return; }
    if (a < o->from) a = o->from;
    if (e > o->to) e = o->to;
    fs_run_t *r = &o->r[o->n++];
    r->first_sample = a; r->value = v | ((a & 1) ? CLK43 : 0); r->run = e - a;
    if (o->n == BATCH) out_flush(o);
}

// ---- vgch2: vgacap's reader ---------------------------------------------------------------------
typedef struct { vgacap_trace_reader_t *r; } vg_rd_t;
static void *vg_open(const char *path) {
    char err[512];
    vgacap_trace_reader_t *r = vgacap_trace_open(path, err, sizeof err);
    if (!r) { fprintf(stderr, "fs: %s\n", err); return NULL; }
    vg_rd_t *v = calloc(1, sizeof *v);
    v->r = r;
    return v;
}
struct vg_fwd { fs_runs_fn fn; void *user; };
static int vg_fwd_fn(void *user, const vgacap_trace_run_t *runs, size_t n) {
    struct vg_fwd *f = user;
    return f->fn(f->user, (const fs_run_t *)(const void *)runs, n);
}
static int vg_read(void *ctx, uint64_t from, uint64_t to, fs_runs_fn fn, void *user) {
    vg_rd_t *v = ctx;
    char err[512];
    struct vg_fwd f = { fn, user };
    if (vgacap_trace_read_runs(v->r, from, to, vg_fwd_fn, &f, err, sizeof err)) {
        fprintf(stderr, "fs: %s\n", err);
        return -1;
    }
    return 0;
}
static uint64_t vg_samples(void *ctx) { return vgacap_trace_info(((vg_rd_t *)ctx)->r)->samples; }
static void vg_close(void *ctx) { vg_rd_t *v = ctx; vgacap_trace_close(v->r); free(v); }

// ---- stdy: the study's container ------------------------------------------------------------------
typedef struct { uint64_t off, first, nsamples; } sblk_t;
typedef struct {
    const uint8_t *map;
    size_t size;
    int kind, t, c;
    sblk_t *b;
    uint64_t nb;
    int rebuilt;
    uint8_t *dec[FS_MAX_STREAMS];
    size_t deccap;
    uint64_t *raw;
    size_t rawcap;
} st_rd_t;

static uint32_t rd32(const uint8_t *p) { uint32_t v; memcpy(&v, p, 4); return v; }
static uint64_t rd64(const uint8_t *p) { uint64_t v; memcpy(&v, p, 8); return v; }

static int nstreams_of(const st_rd_t *s) {
    if (s->kind == 1) return FS_GROUPS;
    if (s->kind == 3) return (s->t == T_BSHUF || s->t == T_XBSHUF || s->t == T_XBITSHUF) ? FS_GROUPS : 1;
    return 1;
}

// The block at `off` is whole: returns its total length, or 0.
static uint64_t st_block_len(const st_rd_t *s, uint64_t off) {
    const int ns = nstreams_of(s);
    const uint64_t hl = 4 + 8 + 8 + 8 * (uint64_t)ns;
    if (off + hl > s->size || rd32(s->map + off) != FS_BLOCK_MAGIC) return 0;
    uint64_t total = hl;
    for (int i = 0; i < ns; i++) total += rd32(s->map + off + 20 + 8 * (uint64_t)i + 4);
    return off + total <= s->size ? total : 0;
}

static void *st_open(const char *path) {
    int fd = open(path, O_RDONLY);
    if (fd < 0) { perror(path); return NULL; }
    struct stat st;
    fstat(fd, &st);
    st_rd_t *s = calloc(1, sizeof *s);
    s->size = (size_t)st.st_size;
    s->map = mmap(NULL, s->size, PROT_READ, MAP_PRIVATE, fd, 0);
    close(fd);
    if (s->map == MAP_FAILED || s->size < 16 || memcmp(s->map, FS_FILE_MAGIC, 8)) {
        fprintf(stderr, "fs: %s: not a study file\n", path);
        free(s);
        return NULL;
    }
    s->kind = s->map[8]; s->t = s->map[9]; s->c = s->map[10];
    if (s->t == 100) s->t = T_IDENTITY;
    // The index, if the footer is there and agrees; else walk the blocks.
    int ok = 0;
    if (s->size >= 32 && rd32(s->map + s->size - 4) == FS_INDEX_MAGIC) {
        const uint64_t ioff = rd64(s->map + s->size - 16);
        const uint64_t nb = rd32(s->map + s->size - 8);
        if (ioff + 16 * nb + 16 == s->size) {
            s->b = calloc(nb ? nb : 1, sizeof *s->b);
            s->nb = nb;
            ok = 1;
            for (uint64_t i = 0; i < nb; i++) {
                s->b[i].off = rd64(s->map + ioff + 16 * i);
                s->b[i].first = rd64(s->map + ioff + 16 * i + 8);
                if (s->b[i].off + 20 > s->size) { ok = 0; break; }
                s->b[i].nsamples = rd64(s->map + s->b[i].off + 12);
            }
        }
    }
    if (!ok) {
        free(s->b);
        s->b = NULL; s->nb = 0;
        s->rebuilt = 1;
        uint64_t off = 16, cap = 0, len;
        while ((len = st_block_len(s, off))) {
            if (s->nb == cap) { cap = cap ? 2 * cap : 1024; s->b = realloc(s->b, cap * sizeof *s->b); }
            s->b[s->nb].off = off;
            s->b[s->nb].first = rd64(s->map + off + 4);
            s->b[s->nb].nsamples = rd64(s->map + off + 12);
            s->nb++;
            off += len;
        }
    }
    return s;
}

static uint64_t st_samples(void *ctx) {
    st_rd_t *s = ctx;
    return s->nb ? s->b[s->nb - 1].first + s->b[s->nb - 1].nsamples : 0;
}

static void st_close(void *ctx) {
    st_rd_t *s = ctx;
    munmap((void *)(uintptr_t)s->map, s->size);
    for (int i = 0; i < FS_MAX_STREAMS; i++) free(s->dec[i]);
    free(s->raw); free(s->b); free(s);
}

// Decompresses the streams of block i wanted by `mask` (bit k: stream k).
static int st_decode(st_rd_t *s, uint64_t i, unsigned mask, size_t *lens) {
    const int ns = nstreams_of(s);
    const uint8_t *p = s->map + s->b[i].off;
    const uint8_t *z = p + 20 + 8 * (uint64_t)ns;
    size_t maxrl = 0;
    for (int k = 0; k < ns; k++) {
        const size_t rl = rd32(p + 20 + 8 * (uint64_t)k);
        if (rl > maxrl) maxrl = rl;
    }
    if (s->deccap < maxrl + 64) {
        for (int j = 0; j < FS_MAX_STREAMS; j++) { free(s->dec[j]); s->dec[j] = malloc(maxrl + 64); }
        s->deccap = maxrl + 64;
    }
    for (int k = 0; k < ns; k++) {
        const size_t rl = rd32(p + 20 + 8 * (uint64_t)k), cl = rd32(p + 24 + 8 * (uint64_t)k);
        lens[k] = rl;
        if (mask & (1u << k)) {
            if (rl && fs_decompress(s->c, z, cl, s->dec[k], rl)) {
                fprintf(stderr, "fs: block %llu stream %d: decompression failed\n", (unsigned long long)i, k);
                return -1;
            }
        }
        z += cl;
    }
    return 0;
}

static inline uint64_t get_leb(const uint8_t **pp) {
    const uint8_t *p = *pp;
    uint64_t v = 0;
    unsigned sh = 0;
    uint8_t b;
    do { b = *p++; v |= (uint64_t)(b & 0x7f) << sh; sh += 7; } while (b & 0x80);
    *pp = p;
    return v;
}

// tt-um-43 value (no clock bit) of a raw image without clk.
static inline uint64_t val43(uint64_t raw) { return fs_pack(raw) & ~CLK43; }

static int st_block(st_rd_t *s, uint64_t i, int port0, out_t *o, port0_t *p0) {
    const uint64_t first = s->b[i].first, ns = s->b[i].nsamples;
    size_t lens[FS_MAX_STREAMS];
    if (s->kind == 1) {   // planar
        if (port0) {
            if (st_decode(s, i, 1u, lens)) return -1;
            const uint8_t *p = s->dec[0], *e = p + lens[0];
            uint64_t a = first;
            while (p < e) {
                const uint64_t v = *p++;
                const uint64_t len = get_leb(&p);
                out_run(o, a, v, len);
                a += len;
            }
            return 0;
        }
        if (st_decode(s, i, 0x7fu, lens)) return -1;
        if (getenv("FS_CHECK_PLANAR")) {
            for (int k = 0; k < FS_GROUPS; k++) {
                const uint8_t *q = s->dec[k], *e = q + lens[k];
                uint64_t sum = 0, pairs = 0;
                while (q < e) { q++; sum += get_leb(&q); pairs++; }
                if (sum != ns)
                    fprintf(stderr, "planar block %llu stream %d: %llu pairs, runs sum %llu, block %llu samples\n",
                            (unsigned long long)i, k, (unsigned long long)pairs, (unsigned long long)sum,
                            (unsigned long long)ns);
            }
        }
        const uint8_t *p[FS_GROUPS];
        uint64_t left[FS_GROUPS], cur = 0;
        for (int k = 0; k < FS_GROUPS; k++) {
            p[k] = s->dec[k];
            cur |= (uint64_t)*p[k]++ << (8 * k);
            left[k] = get_leb(&p[k]);
        }
        uint64_t a = first;
        const uint64_t end = first + ns;
        while (a < end && !o->done) {
            uint64_t m = left[0];
            for (int k = 1; k < FS_GROUPS; k++) m = left[k] < m ? left[k] : m;
            out_run(o, a, val43(cur), m);
            a += m;
            if (a >= end) break;
            for (int k = 0; k < FS_GROUPS; k++) {
                left[k] -= m;
                if (!left[k]) {
                    cur = (cur & ~(0xffull << (8 * k))) | ((uint64_t)*p[k]++ << (8 * k));
                    left[k] = get_leb(&p[k]);
                }
            }
        }
        return 0;
    }
    if (s->kind == 2) {   // events
        if (st_decode(s, i, 1u, lens)) return -1;
        const uint8_t *p = s->dec[0], *e = p + lens[0];
        uint64_t cur = 0;
        memcpy(&cur, p, 7);
        p += 7;
        uint64_t a = first, last = first;
        while (p < e && !o->done) {
            const uint64_t at = last + get_leb(&p);
            const unsigned m = *p++;
            uint64_t next = cur;
            for (unsigned k = 0; k < FS_GROUPS; k++)
                if (m & (1u << k)) next = (next & ~(0xffull << (8 * k))) | ((uint64_t)*p++ << (8 * k));
            if (port0) {
                if (m & 1u) { out_run(o, a, cur & 0xff, at - a); a = at; }
            } else {
                out_run(o, a, val43(cur), at - a);
                a = at;
            }
            cur = next;
            last = at;
        }
        out_run(o, a, port0 ? (cur & 0xff) : val43(cur), first + ns - a);
        return 0;
    }
    if (s->kind == 3) {   // raw
        const int multi = nstreams_of(s) > 1;
        if (st_decode(s, i, port0 && multi ? 1u : 0x7fu, lens)) return -1;
        if (s->rawcap < ns) { free(s->raw); s->raw = malloc(ns * sizeof *s->raw); s->rawcap = ns; }
        fs_untransform(s->t, s->dec, ns, first, port0 && multi ? 0 : -1, s->raw);
        const uint64_t keep = port0 ? 0xffull : FS_NOCLK_MASK;
        const uint64_t *r = s->raw;
        uint64_t a = 0, cur = r[0] & keep;
        for (uint64_t j = 1; j < ns; j++) {
            const uint64_t v = r[j] & keep;
            if (v != cur) {
                out_run(o, first + a, port0 ? cur : val43(cur), j - a);
                a = j; cur = v;
                if (o->done) return 0;
            }
        }
        out_run(o, first + a, port0 ? cur : val43(cur), ns - a);
        return 0;
    }
    (void)p0;
    return -1;
}

static int st_read(void *ctx, uint64_t from, uint64_t to, int port0, out_t *o, port0_t *p0) {
    st_rd_t *s = ctx;
    if (!s->nb) return 0;
    uint64_t lo = 0, hi = s->nb;
    while (hi - lo > 1) { uint64_t mid = (lo + hi) / 2; if (s->b[mid].first <= from) lo = mid; else hi = mid; }
    for (uint64_t i = lo; i < s->nb && s->b[i].first < to && !o->done; i++)
        if (st_block(s, i, port0, o, p0)) return -1;
    return 0;
}

// ---- FST: libfst's reader ------------------------------------------------------------------------
typedef struct {
    const char *path;
    fstReaderContext *r;
    uint64_t samples;
} fst_rd_t;

static uint64_t ps_to_sample(uint64_t t) {
    const unsigned __int128 num = (unsigned __int128)t * 2 * fs_clock_hz + 500000000000ull;
    return (uint64_t)(num / 1000000000000ull);
}

static void *fst_open(const char *path) {
    fstReaderContext *r = fstReaderOpen(path);
    if (!r) { fprintf(stderr, "fs: fst: cannot open %s\n", path); return NULL; }
    fst_rd_t *f = calloc(1, sizeof *f);
    f->path = path; f->r = r;
    f->samples = ps_to_sample(fstReaderGetEndTime(r));
    return f;
}

typedef struct {
    out_t *o;
    int port0;
    uint64_t cur, t_sample, have;
} fst_cb_t;

static void fst_cb(void *user, uint64_t time, fstHandle h, const unsigned char *value) {
    fst_cb_t *c = user;
    const uint64_t s = ps_to_sample(time);
    if (c->have && s != c->t_sample) {
        out_run(c->o, c->t_sample, c->port0 ? (c->cur & 0xff) : val43(c->cur), s - c->t_sample);
        c->t_sample = s;
    }
    if (!c->have) { c->have = 1; c->t_sample = s; }
    const int k = (int)h - 1;
    unsigned v = 0;
    for (const unsigned char *p = value; *p; p++) v = (v << 1) | (unsigned)(*p == '1');
    if (k >= 0 && k < FS_GROUPS) c->cur = (c->cur & ~(0xffull << (8 * k))) | ((uint64_t)v << (8 * k));
}

static int fst_read(void *ctx, uint64_t from, uint64_t to, int port0, out_t *o) {
    fst_rd_t *f = ctx;
    if (port0) fstReaderSetFacProcessMask(f->r, 1);
    else fstReaderSetFacProcessMaskAll(f->r);
    if (to != UINT64_MAX) fstReaderSetLimitTimeRange(f->r, fs_ps(from), fs_ps(to));
    else fstReaderSetUnlimitedTimeRange(f->r);
    fst_cb_t c = { o, port0, 0, 0, 0 };
    fstReaderIterBlocks2(f->r, fst_cb, NULL, &c, NULL);
    if (c.have && c.t_sample < f->samples)
        out_run(o, c.t_sample, port0 ? (c.cur & 0xff) : val43(c.cur), f->samples - c.t_sample);
    return 0;
}
static void fst_close(void *ctx) { fst_rd_t *f = ctx; fstReaderClose(f->r); free(f); }

// ---- the driver -----------------------------------------------------------------------------------
enum { K_VG, K_ST, K_FST };

typedef struct {
    int kind;
    void *ctx;
} rd_t;

static int rd_open(rd_t *r, int kind, const char *path) {
    r->kind = kind;
    r->ctx = kind == K_VG ? vg_open(path) : kind == K_ST ? st_open(path) : fst_open(path);
    return r->ctx ? 0 : -1;
}
static void rd_close(rd_t *r) {
    if (r->kind == K_VG) vg_close(r->ctx);
    else if (r->kind == K_ST) st_close(r->ctx);
    else fst_close(r->ctx);
}
static uint64_t rd_samples(rd_t *r) {
    return r->kind == K_VG ? vg_samples(r->ctx) : r->kind == K_ST ? st_samples(r->ctx) : ((fst_rd_t *)r->ctx)->samples;
}

// [from, to) to the consumer: full runs, or (port0) uo_out runs merged.
static int rd_read(rd_t *r, uint64_t from, uint64_t to, int port0, cons_t *c) {
    static out_t o;
    static port0_t p0;
    memset(&p0, 0, sizeof p0);
    p0.c = c;
    o.from = from; o.to = to; o.done = 0; o.n = 0;
    int rc;
    if (r->kind == K_VG) {
        rc = vg_read(r->ctx, from, to, port0 ? p0_fn : cons_fn, port0 ? (void *)&p0 : (void *)c);
    } else {
        // Readers that deliver uo_out runs themselves still go through p0
        // (merging what is already merged costs a compare a run).
        o.fn = port0 ? p0_fn : cons_fn;
        o.user = port0 ? (void *)&p0 : (void *)c;
        rc = r->kind == K_ST ? st_read(r->ctx, from, to, port0, &o, &p0) : fst_read(r->ctx, from, to, port0, &o);
        out_flush(&o);
    }
    if (port0) p0_done(&p0);
    return rc;
}

static const char *project_name = "?";

static void row(const char *kind, const char *file, const char *pattern, int rep, double ns, const cons_t *c,
                uint64_t extra) {
    printf("%s,%s,%s,%s,%d,%.0f,%llu,%llu,%016llx\n", project_name, kind, file, pattern, rep, ns,
           (unsigned long long)c->runs, (unsigned long long)c->samples, (unsigned long long)extra);
}

int fs_mode_read(int argc, char **argv);
int fs_mode_read(int argc, char **argv) {
    if (argc < 1) { fprintf(stderr, "usage: fsbench read FILE --kind K --project P ...\n"); return 2; }
    const char *path = argv[0];
    const char *kind_s = "vgch2", *patterns = "open,scan,port0,seek";
    int reps = 5, seeks = 20;
    uint64_t window = 420000;
    uint32_t hz = 0;
    for (int i = 1; i + 1 < argc; i += 2) {
        if (!strcmp(argv[i], "--kind")) kind_s = argv[i + 1];
        else if (!strcmp(argv[i], "--project")) project_name = argv[i + 1];
        else if (!strcmp(argv[i], "--patterns")) patterns = argv[i + 1];
        else if (!strcmp(argv[i], "--reps")) reps = atoi(argv[i + 1]);
        else if (!strcmp(argv[i], "--seeks")) seeks = atoi(argv[i + 1]);
        else if (!strcmp(argv[i], "--window")) window = strtoull(argv[i + 1], NULL, 0);
        else if (!strcmp(argv[i], "--clock-hz")) hz = (uint32_t)strtoul(argv[i + 1], NULL, 0);
        else { fprintf(stderr, "fsbench read: unknown option %s\n", argv[i]); return 2; }
    }
    if (hz) fs_clock_hz = hz;
    const int kind = !strcmp(kind_s, "vgch2") ? K_VG : !strcmp(kind_s, "stdy") ? K_ST : K_FST;
    const char *base = strrchr(path, '/');
    base = base ? base + 1 : path;
    printf("project,kind,file,pattern,rep,ns,runs,samples,hash\n");
    char pats[256];
    snprintf(pats, sizeof pats, "%s", patterns);
    for (char *save = NULL, *pat = strtok_r(pats, ",", &save); pat; pat = strtok_r(NULL, ",", &save)) {
        for (int rep = 0; rep < (strcmp(pat, "validate") ? reps : 1); rep++) {
            rd_t r;
            cons_t c;
            memset(&c, 0, sizeof c);
            const double t0 = fs_thread_ns();
            if (rd_open(&r, kind, path)) return 1;
            const double t1 = fs_thread_ns();
            const uint64_t total = rd_samples(&r);
            if (!strcmp(pat, "open")) {
                c.samples = total;
                row(kind_s, base, "open", rep, t1 - t0, &c, 0);
            } else if (!strcmp(pat, "scan") || !strcmp(pat, "port0")) {
                const int p0 = !strcmp(pat, "port0");
                const double s0 = fs_thread_ns();
                if (rd_read(&r, 0, UINT64_MAX, p0, &c)) return 1;
                row(kind_s, base, pat, rep, fs_thread_ns() - s0, &c, 0);
            } else if (!strcmp(pat, "seek")) {
                rd_close(&r);
                uint64_t x = 0x2545f4914f6cdd1dull + (uint64_t)rep;
                const uint64_t clocks = total / 2;
                for (int k = 0; k < seeks; k++) {
                    x ^= x << 13; x ^= x >> 7; x ^= x << 17;
                    const uint64_t c0 = clocks > window ? x % (clocks - window) : 0;
                    memset(&c, 0, sizeof c);
                    const double s0 = fs_thread_ns();
                    if (rd_open(&r, kind, path)) return 1;
                    if (rd_read(&r, 2 * c0, 2 * (c0 + window), 0, &c)) return 1;
                    rd_close(&r);
                    row(kind_s, base, "seek", rep * 1000 + k, fs_thread_ns() - s0, &c, 2 * c0);
                }
                continue;   // closed already
            } else if (!strcmp(pat, "validate")) {
                c.hashing = 1;
                if (rd_read(&r, 0, UINT64_MAX, 0, &c)) return 1;
                cons_flush(&c);
                row(kind_s, base, "validate-full", rep, 0, &c, c.h);
                rd_close(&r);
                if (rd_open(&r, kind, path)) return 1;
                memset(&c, 0, sizeof c);
                c.hashing = 2;
                if (rd_read(&r, 0, UINT64_MAX, 1, &c)) return 1;
                cons_flush(&c);
                row(kind_s, base, "validate-port0", rep, 0, &c, c.h);
            }
            rd_close(&r);
            fflush(stdout);
        }
    }
    return 0;
}
