// SPDX-License-Identifier: Apache-2.0
// fsbench encode: one candidate writer over a whole trace, every clock, with
// its blocks going through the background part: transforms (raw only),
// then each compressor of the matrix, each timed by the thread CPU clock.
//
//   fsbench encode TRACE OUTDIR --writer W [--transforms a,b] [--comps a,b]
//                  [--sample-every K] [--primary T:C] [--block-clocks N] [--fst-pack P]
//
// The matrix runs on every K-th block (sizes and CPU then extrapolated by
// samples, stated in each row); --primary's transform and compressor run
// on every block and are written as a file in the study container
// (codec.h), for the readers. vgch2's blocks are the real writer's (tapped
// before its own compression, which still writes the real file); FST and
// LXT2 write their own files and are measured as wholes.
#define _GNU_SOURCE
#include "codec.h"
#include "fs.h"
#include "vgch.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <libgen.h>
#include <signal.h>
#include <sys/resource.h>
#include <sys/stat.h>

extern int fs_fst_pack, fs_fst_parallel;
extern fs_block_fn fs_hook_tap;

#define MAXT 8
#define MAXC 16

typedef struct {
    const char *project, *writer;
    int is_raw;
    int nt, nc, tlist[MAXT], clist[MAXC];
    uint64_t every, blocks, blocks_measured, samples, samples_measured, clk_errors;
    uint64_t raw_bytes[MAXT], comp_bytes[MAXT][MAXC];
    double tr_ns[MAXT], comp_ns[MAXT][MAXC];
    double bg_ns_total;          // everything bg did, every block
    // the primary file
    int pt, pc;                  // -1: none
    FILE *pf;
    char *pfbuf;                 // pf's stdio buffer: this instance's own
    uint64_t *index;             // offset, first_sample pairs
    uint64_t nindex, capindex;
    uint64_t pbytes;
    double p_ns;                 // the primary's transform + compression, every block
    // scratch
    uint8_t *tout[FS_MAX_STREAMS];
    size_t tcap;
    uint8_t *cbuf;
    size_t ccap;
} bg_t;

static void put32(FILE *f, uint32_t v) { fwrite(&v, 4, 1, f); }
static void put64(FILE *f, uint64_t v) { fwrite(&v, 8, 1, f); }

static void ensure(bg_t *g, size_t n) {
    if (g->tcap < n) {
        for (int i = 0; i < FS_MAX_STREAMS; i++) { free(g->tout[i]); g->tout[i] = malloc(n); }
        g->tcap = n;
    }
    size_t cb = fs_comp_bound(n);
    if (g->ccap < cb) { free(g->cbuf); g->cbuf = malloc(cb); g->ccap = cb; }
    for (int i = 0; i < FS_MAX_STREAMS; i++)
        if (!g->tout[i] || !g->cbuf) { fprintf(stderr, "fs: out of memory\n"); exit(2); }
}

// Streams of block b under transform t (raw) or as they are.
static int streams_of(bg_t *g, const fs_block_t *b, int t, uint8_t **s, size_t *len) {
    if (t == T_EV2PLANAR) {   // the events writer's stream
        ensure(g, 4 * b->len[0] + 64 * 1024);
        for (int i = 0; i < FS_MAX_STREAMS; i++) s[i] = g->tout[i];
        return fs_events_to_planar(b->buf[0], b->len[0], b->nsamples, b->first_sample, s, len);
    }
    if (t == T_CLOG) {   // the changelog writer's records
        ensure(g, 24 * (b->len[0] / 24) + 64 * 1024);
        for (int i = 0; i < FS_MAX_STREAMS; i++) s[i] = g->tout[i];
        return fs_clog_to_planar((const uint64_t *)(const void *)b->buf[0], b->len[0] / 24, b->nsamples,
                                 b->first_sample, s, len);
    }
    if (!g->is_raw) {
        for (int i = 0; i < b->nstreams; i++) { s[i] = b->buf[i]; len[i] = b->len[i]; }
        return b->nstreams;
    }
    ensure(g, 8 * (size_t)b->nsamples + 64);
    for (int i = 0; i < FS_MAX_STREAMS; i++) s[i] = g->tout[i];
    return fs_transform(t, (const uint64_t *)(const void *)b->buf[0], b->nsamples, b->first_sample, s, len,
                        &g->clk_errors);
}

static size_t compress_streams(bg_t *g, int c, uint8_t **s, const size_t *len, int ns, uint32_t *clen) {
    size_t total = 0;
    for (int i = 0; i < ns; i++) {
        if (g->ccap < fs_comp_bound(len[i])) ensure(g, len[i]);
        size_t z = len[i] ? fs_compress(c, s[i], len[i], g->cbuf, g->ccap) : 0;
        if (len[i] && !z) { fprintf(stderr, "fs: compress %s failed\n", fs_comp_name[c]); exit(2); }
        if (clen) clen[i] = (uint32_t)z;
        total += z;
    }
    return total;
}

// The primary file's block: header, lens, then each stream compressed (again
// into cbuf, one at a time, so the bytes written are the ones counted).
static void write_primary(bg_t *g, const fs_block_t *b, uint8_t **s, const size_t *len, int ns) {
    if (g->nindex == g->capindex) {
        g->capindex = g->capindex ? 2 * g->capindex : 1024;
        g->index = realloc(g->index, 2 * g->capindex * sizeof *g->index);
    }
    g->index[2 * g->nindex] = (uint64_t)ftello(g->pf);
    g->index[2 * g->nindex + 1] = b->first_sample;
    g->nindex++;
    // Compress every stream first, keeping each (lens are written before payloads).
    uint8_t *z[FS_MAX_STREAMS];
    uint32_t zl[FS_MAX_STREAMS];
    for (int i = 0; i < ns; i++) {
        size_t cap = fs_comp_bound(len[i]);
        z[i] = malloc(cap);
        zl[i] = len[i] ? (uint32_t)fs_compress(g->pc, s[i], len[i], z[i], cap) : 0;
    }
    put32(g->pf, FS_BLOCK_MAGIC);
    put64(g->pf, b->first_sample);
    put64(g->pf, b->nsamples);
    for (int i = 0; i < ns; i++) { put32(g->pf, (uint32_t)len[i]); put32(g->pf, zl[i]); }
    for (int i = 0; i < ns; i++) { fwrite(z[i], 1, zl[i], g->pf); g->pbytes += zl[i]; free(z[i]); }
    g->pbytes += 4 + 8 + 8 + 8 * (uint64_t)ns;
    fflush(g->pf);   // as vgch2 does: a killed writer keeps every block it finished
}

static void on_block(void *ctx, const fs_block_t *b) {
    bg_t *g = ctx;
    const double t_all = fs_thread_ns();
    fs_block_t vb = *b;
    if (!strcmp(g->writer, "vgch2")) {
        // The tap has the block's bytes; its samples are not in them (the
        // container says nothing): count samples from the trace at the end.
        vb.nsamples = 0;
    }
    const int measured = (g->blocks % g->every) == 0;
    g->blocks++;
    g->samples += vb.nsamples;
    if (measured) { g->blocks_measured++; g->samples_measured += vb.nsamples; }
    uint8_t *s[FS_MAX_STREAMS];
    size_t len[FS_MAX_STREAMS];
    for (int ti = 0; ti < g->nt; ti++) {
        const int t = g->tlist[ti];
        const int is_primary_t = g->pf && t == g->pt;
        if (!measured && !is_primary_t) continue;
        const double t0 = fs_thread_ns();
        const int ns = streams_of(g, &vb, t, s, len);
        const double t1 = fs_thread_ns();
        if (ns < 0) { fprintf(stderr, "fs: bad transform\n"); exit(2); }
        if (is_primary_t) g->p_ns += t1 - t0;
        if (measured) {
            g->tr_ns[ti] += t1 - t0;
            for (int i = 0; i < ns; i++) g->raw_bytes[ti] += len[i];
            for (int ci = 0; ci < g->nc; ci++) {
                const int c = g->clist[ci];
                const double c0 = fs_thread_ns();
                g->comp_bytes[ti][ci] += compress_streams(g, c, s, len, ns, NULL);
                const double c1 = fs_thread_ns();
                g->comp_ns[ti][ci] += c1 - c0;
            }
        }
        if (is_primary_t) {
            const double p0 = fs_thread_ns();
            write_primary(g, &vb, s, len, ns);
            g->p_ns += fs_thread_ns() - p0;
        }
    }
    g->bg_ns_total += fs_thread_ns() - t_all;
}

static int parse_list(const char *arg, int *out, int max, int (*by_name)(const char *)) {
    char buf[1024];
    snprintf(buf, sizeof buf, "%s", arg);
    int n = 0;
    for (char *save = NULL, *tok = strtok_r(buf, ",", &save); tok && n < max; tok = strtok_r(NULL, ",", &save)) {
        int v = by_name(tok);
        if (v < 0) { fprintf(stderr, "fsbench encode: unknown name %s\n", tok); exit(2); }
        out[n++] = v;
    }
    return n;
}

static const char *project_of(const char *path) {
    static char buf[512];
    snprintf(buf, sizeof buf, "%s", path);
    char *b = basename(buf);
    char *e = strstr(b, "_trace.vgch.zst");
    if (e) *e = 0;
    return b;
}

static double process_cpu_ns(void) {
    struct rusage ru;
    getrusage(RUSAGE_SELF, &ru);
    return ((double)ru.ru_utime.tv_sec + (double)ru.ru_stime.tv_sec) * 1e9 +
           ((double)ru.ru_utime.tv_usec + (double)ru.ru_stime.tv_usec) * 1e3;
}

static uint64_t file_size(const char *p) {
    struct stat st;
    return stat(p, &st) == 0 ? (uint64_t)st.st_size : 0;
}

// The primary file: its header, and at the end its index and footer.
static int primary_open(bg_t *g, const char *pfile) {
    g->pf = fopen(pfile, "wb");
    if (!g->pf) { perror(pfile); return -1; }
    // Each instance its own buffer: several bg writers share a process in
    // bench_tick, and one static buffer under several FILEs mixes their bytes.
    g->pfbuf = malloc(1 << 20);
    if (!g->pfbuf) { fprintf(stderr, "fs: out of memory\n"); exit(2); }
    setvbuf(g->pf, g->pfbuf, _IOFBF, 1 << 20);
    fwrite(FS_FILE_MAGIC, 1, 8, g->pf);
    uint8_t hdr[8] = { 0, (uint8_t)g->pt, (uint8_t)g->pc, 0, 0, 0, 0, 0 };
    hdr[0] = !strcmp(g->writer, "planar") ? 1 : !strcmp(g->writer, "events") ? 2 : !strcmp(g->writer, "raw") ? 3 : 4;
    // Raw capture stored as planar runs is a planar file.
    if (g->pt == T_PLANAR || g->pt == T_CLOG || g->pt == T_EV2PLANAR) { hdr[0] = 1; hdr[1] = T_IDENTITY; }
    // The raw-image layout (fs_off, 3 bits a port) and 'L': an events file
    // keeps its bytes in raw order, which is Verilator's in the simulation check.
    uint32_t lay = 0;
    for (int k = 0; k < 8; k++) lay |= (uint32_t)fs_off[k] << (3 * k);
    hdr[4] = (uint8_t)lay; hdr[5] = (uint8_t)(lay >> 8); hdr[6] = (uint8_t)(lay >> 16); hdr[7] = 'L';
    fwrite(hdr, 1, 8, g->pf);
    g->pbytes = 16;
    return 0;
}

static void primary_close(bg_t *g) {
    if (!g->pf) return;
    const uint64_t ioff = (uint64_t)ftello(g->pf);
    for (uint64_t i = 0; i < g->nindex; i++) { put64(g->pf, g->index[2 * i]); put64(g->pf, g->index[2 * i + 1]); }
    put64(g->pf, ioff);
    put32(g->pf, (uint32_t)g->nindex);
    put32(g->pf, FS_INDEX_MAGIC);
    if (ferror(g->pf) | fclose(g->pf)) { fprintf(stderr, "fs: writing the primary file failed\n"); exit(2); }
    g->pf = NULL;
    free(g->pfbuf);
    g->pfbuf = NULL;
}

// For the simulation check (w_mine.c fs_sim_create): a background part with
// the primary transform and compressor only, writing `pfile`.
void *fs_bg_new(const char *writer, const char *transform, const char *comp, const char *pfile);
fs_block_fn fs_bg_fn(void);
void fs_bg_close(void *bg);
void *fs_bg_new(const char *writer, const char *transform, const char *comp, const char *pfile) {
    bg_t *g = calloc(1, sizeof *g);
    g->project = "sim"; g->writer = writer;
    g->every = UINT64_MAX;
    g->is_raw = !strcmp(writer, "raw");
    g->pt = fs_transform_by_name(transform);
    g->pc = fs_comp_by_name(comp);
    if (g->pt < 0 || g->pc < 0) { fprintf(stderr, "fs: bad transform or compressor\n"); exit(2); }
    g->tlist[g->nt++] = g->pt;
    if (primary_open(g, pfile)) exit(2);
    return g;
}
fs_block_fn fs_bg_fn(void) { return on_block; }
void fs_bg_close(void *bg) {
    bg_t *g = bg;
    primary_close(g);
    fprintf(stderr, "fs-sim: background %s: %llu blocks, %.3f s CPU, %llu bytes, %llu clock errors\n", g->writer,
            (unsigned long long)g->blocks, g->bg_ns_total / 1e9, (unsigned long long)g->pbytes,
            (unsigned long long)g->clk_errors);
}

int fs_mode_encode(int argc, char **argv);
int fs_mode_encode(int argc, char **argv) {
    if (argc < 2) { fprintf(stderr, "usage: fsbench encode TRACE OUTDIR --writer W ...\n"); return 2; }
    const char *trace = argv[0], *outdir = argv[1];
    const char *writer = NULL, *transforms = "identity", *comps = "zstd1", *primary = NULL;
    uint64_t every = 1, block_clocks = 0, stride = 1, kill_after = 0;
    for (int i = 2; i + 1 < argc; i += 2) {
        if (!strcmp(argv[i], "--writer")) writer = argv[i + 1];
        else if (!strcmp(argv[i], "--transforms")) transforms = argv[i + 1];
        else if (!strcmp(argv[i], "--comps")) comps = argv[i + 1];
        else if (!strcmp(argv[i], "--sample-every")) every = strtoull(argv[i + 1], NULL, 0);
        else if (!strcmp(argv[i], "--primary")) primary = argv[i + 1];
        else if (!strcmp(argv[i], "--block-clocks")) block_clocks = strtoull(argv[i + 1], NULL, 0);
        else if (!strcmp(argv[i], "--stride")) stride = strtoull(argv[i + 1], NULL, 0);
        else if (!strcmp(argv[i], "--kill-after")) kill_after = strtoull(argv[i + 1], NULL, 0);
        else if (!strcmp(argv[i], "--fst-pack")) {
            const char *p = argv[i + 1];
            fs_fst_pack = !strcmp(p, "zlib") ? 0 : !strcmp(p, "fastlz") ? 1 : 2;
        } else { fprintf(stderr, "fsbench encode: unknown option %s\n", argv[i]); return 2; }
    }
    const fs_writer_t *w = writer ? fs_writer_by_name(writer) : NULL;
    if (!w) { fprintf(stderr, "fsbench encode: --writer needed\n"); return 2; }
    fs_source_t *src = fs_source_open(trace);
    if (!src) return 1;
    const char *project = project_of(trace);
    bg_t *g = calloc(1, sizeof *g);
    g->project = project; g->writer = w->name; g->every = every ? every : 1;
    g->is_raw = !strcmp(w->name, "raw");
    g->nt = parse_list(transforms, g->tlist, MAXT, fs_transform_by_name);
    g->nc = parse_list(comps, g->clist, MAXC, fs_comp_by_name);
    g->pt = g->pc = -1;
    char out[1024] = "", pfile[1024] = "";
    const int native = !strcmp(w->name, "fst") || !strcmp(w->name, "fst_clk") || !strcmp(w->name, "lxt2");
    if (!strcmp(w->name, "vgch2")) {
        fs_vgch_set_source(trace);
        fs_hook_skip = 0;
        snprintf(out, sizeof out, "%s/%s.vgch2.trace.zst", outdir, project);
    } else if (native) {
        const char *ext = !strcmp(w->name, "lxt2") ? "lxt2" : "fst";
        const char *pack = !strcmp(w->name, "lxt2") ? "gzip" : fs_fst_pack == 0 ? "zlib" : fs_fst_pack == 1 ? "fastlz" : "lz4";
        snprintf(out, sizeof out, "%s/%s.%s.%s.%s", outdir, project, w->name, pack, ext);
    }
    if (primary && !native) {
        char tbuf[128], cbuf[128];
        const char *colon = strchr(primary, ':');
        if (!colon) { fprintf(stderr, "fsbench encode: --primary T:C\n"); return 2; }
        snprintf(tbuf, sizeof tbuf, "%.*s", (int)(colon - primary), primary);
        snprintf(cbuf, sizeof cbuf, "%s", colon + 1);
        g->pt = fs_transform_by_name(tbuf);
        g->pc = fs_comp_by_name(cbuf);
        if (g->pc < 0 || (g->pt < 0)) { fprintf(stderr, "fsbench encode: bad --primary\n"); return 2; }
        int found = 0;
        for (int i = 0; i < g->nt; i++) found |= g->tlist[i] == g->pt;
        if (!found) g->tlist[g->nt++] = g->pt;
        snprintf(pfile, sizeof pfile, "%s/%s.%s.%s.%s.stdy", outdir, project, w->name, tbuf, cbuf);
        if (primary_open(g, pfile)) return 1;
    }
    const double cpu0 = process_cpu_ns(), th0 = fs_thread_ns(), wall0 = fs_wall_ns();
    void *st = w->create(block_clocks, out[0] ? out : NULL, native ? NULL : on_block, g);
    const uint64_t chunk = 1ull << 20, clocks = fs_source_clocks(src);
    uint64_t *raw = malloc(2 * chunk * sizeof *raw);
    double src_ns = 0;
    uint64_t processed = 0;
    if (!stride) stride = 1;
    for (uint64_t c0 = 0; c0 < clocks; c0 += stride * chunk) {
        const uint64_t k = clocks - c0 < chunk ? clocks - c0 : chunk;
        const double s0 = fs_thread_ns();
        if (fs_source_chunk(src, c0, k, raw)) return 1;
        src_ns += fs_thread_ns() - s0;
        w->run(st, raw, k);
        processed += k;
        // A crash mid-write: no finish, no index, threads killed with the process.
        if (kill_after && processed >= kill_after) raise(SIGKILL);
    }
    w->finish(st);
    const double th1 = fs_thread_ns(), cpu1 = process_cpu_ns(), wall1 = fs_wall_ns();
    if (!strcmp(w->name, "vgch2")) { g->samples = 2 * clocks; g->samples_measured = g->blocks_measured ? 0 : 0; }
    // vgch2: samples per measured block are not known to the tap; extrapolate by blocks.
    const int by_blocks = !strcmp(w->name, "vgch2");
    primary_close(g);
    // Rows: one per (transform, compressor) of the matrix, then the totals.
    printf("project,writer,transform,comp,blocks,blocks_measured,samples,samples_measured,raw_bytes_measured,"
           "comp_bytes_measured,transform_ns_measured,comp_ns_measured,est_comp_bytes,est_bg_ns,clk_errors\n");
    if (!native) {
        for (int ti = 0; ti < g->nt; ti++)
            for (int ci = 0; ci < g->nc; ci++) {
                const double f = by_blocks ? (g->blocks_measured ? (double)g->blocks / (double)g->blocks_measured : 0)
                                           : (g->samples_measured ? (double)g->samples / (double)g->samples_measured : 0);
                printf("%s,%s,%s,%s,%llu,%llu,%llu,%llu,%llu,%llu,%.0f,%.0f,%.0f,%.0f,%llu\n", project, w->name,
                       g->tlist[ti] == T_IDENTITY ? "identity" : fs_transform_name[g->tlist[ti]],
                       fs_comp_name[g->clist[ci]], (unsigned long long)g->blocks,
                       (unsigned long long)g->blocks_measured, (unsigned long long)g->samples,
                       (unsigned long long)g->samples_measured, (unsigned long long)g->raw_bytes[ti],
                       (unsigned long long)g->comp_bytes[ti][ci], g->tr_ns[ti], g->comp_ns[ti][ci],
                       (double)g->comp_bytes[ti][ci] * f, (g->tr_ns[ti] + g->comp_ns[ti][ci]) * f,
                       (unsigned long long)g->clk_errors);
            }
    }
    // The pass as a whole: the simulation thread's own share (minus the
    // source expansion and minus bg, which runs on it here), the process's
    // other threads (FST's and vgch2's compressor), the file written.
    fprintf(stderr, "pass %s %s: thread %.1f s (source %.1f s, bg %.1f s), process %.1f s, wall %.1f s\n", project,
            w->name, (th1 - th0) / 1e9, src_ns / 1e9, g->bg_ns_total / 1e9, (cpu1 - cpu0) / 1e9, (wall1 - wall0) / 1e9);
    printf("#pass,%s,%s,%s,thread_ns=%.0f,source_ns=%.0f,bg_ns=%.0f,process_ns=%.0f,wall_ns=%.0f,file=%s,file_bytes=%llu,"
           "primary=%s,primary_bytes=%llu,primary_ns=%.0f,clocks=%llu,processed_clocks=%llu\n",
           project, w->name, native ? out : "-", th1 - th0, src_ns, g->bg_ns_total, cpu1 - cpu0, wall1 - wall0,
           out[0] ? out : "-", (unsigned long long)(out[0] ? file_size(out) : 0), pfile[0] ? pfile : "-",
           (unsigned long long)(pfile[0] ? file_size(pfile) : 0), g->p_ns, (unsigned long long)clocks,
           (unsigned long long)processed);
    free(raw);
    fs_source_close(src);
    return 0;
}
