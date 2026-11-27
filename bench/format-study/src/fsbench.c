// SPDX-License-Identifier: Apache-2.0
// fsbench: the trace format study's benchmark driver. Modes:
//
//   fsbench info   TRACE                      code paths and change rates (whole trace)
//   fsbench writer TRACE OUTDIR [options]     simulation-thread cost by replay
//   fsbench encode TRACE OUTDIR [options]     sizes and background CPU (bg.c)
//   fsbench read   ...                        reader throughput (rd.c)
//
// Every mode prints CSV rows (with a header line) on stdout; run_all.sh
// collects them under tmp/format-study/results/.
#define _GNU_SOURCE
#include "fs.h"
#include "vgch.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <libgen.h>

int fs_mode_encode(int argc, char **argv);
int fs_mode_read(int argc, char **argv);
int fs_mode_ctr(int argc, char **argv);

static const char *project_of(const char *path) {
    static char buf[512];
    snprintf(buf, sizeof buf, "%s", path);
    char *b = basename(buf);
    char *e = strstr(b, "_trace.vgch.zst");
    if (e) *e = 0;
    return b;
}

static double load1(void) {
    FILE *f = fopen("/proc/loadavg", "r");
    double l = -1;
    if (f) { if (fscanf(f, "%lf", &l) != 1) l = -1; fclose(f); }
    return l;
}

// ---- info: what every clock of the trace costs the vgch2 writer ---------------------------
struct info {
    uint64_t runs, samples, changed_clocks, last_c, port_changes[FS_GROUPS], changed_samples;
    uint64_t prev, have_prev;
    uint64_t multi_port_samples;
};

static int on_info(void *user, const fs_run_t *runs, size_t n) {
    struct info *s = user;
    for (size_t i = 0; i < n; i++) {
        const uint64_t raw = fs_raw_from_sample(runs[i].value) & FS_NOCLK_MASK;
        if (s->have_prev && raw == s->prev) {   // a run split at a block boundary: no change
            s->samples += runs[i].run;
            continue;
        }
        s->runs++;
        if (s->have_prev) {
            const uint64_t c = runs[i].first_sample >> 1;
            if (c != s->last_c || s->changed_clocks == 0) s->changed_clocks++;
            s->last_c = c;
            s->changed_samples++;
            uint64_t x = raw ^ s->prev;
            int ports = 0;
            for (int k = 0; k < FS_GROUPS; k++)
                if ((x >> (8 * k)) & 0xff) { s->port_changes[k]++; ports++; }
            if (ports > 1) s->multi_port_samples++;
        }
        s->prev = raw;
        s->have_prev = 1;
        s->samples += runs[i].run;
    }
    return 0;
}

static int mode_info(int argc, char **argv) {
    if (argc < 1) { fprintf(stderr, "usage: fsbench info TRACE\n"); return 2; }
    fs_source_t *src = fs_source_open(argv[0]);
    if (!src) return 1;
    struct info s;
    memset(&s, 0, sizeof s);
    if (fs_source_runs(src, 0, UINT64_MAX, on_info, &s)) return 1;
    printf("project,clocks,samples,runs,changed_samples,changed_clocks,multi_port_samples");
    for (int k = 0; k < FS_GROUPS; k++) printf(",changes_port%d", k);
    printf(",clock_hz");
    printf("\n%s,%llu,%llu,%llu,%llu,%llu,%llu", project_of(argv[0]), (unsigned long long)fs_source_clocks(src),
           (unsigned long long)s.samples, (unsigned long long)s.runs, (unsigned long long)s.changed_samples,
           (unsigned long long)s.changed_clocks, (unsigned long long)s.multi_port_samples);
    for (int k = 0; k < FS_GROUPS; k++) printf(",%llu", (unsigned long long)s.port_changes[k]);
    printf(",%u\n", fs_clock_hz);
    fs_source_close(src);
    return 0;
}

// ---- writer: the replay timing --------------------------------------------------------------
// Chunks of --chunk clocks, every --stride-th chunk of the trace (offset
// --phase), each expanded once into raw images; then --reps rounds, each
// running every writer over the chunk in a rotated order, timed by the
// thread CPU clock. Writers keep their state across chunks (n advances by
// the chunk's clocks), and drop their blocks (no background work), except
// FST and LXT2, whose libraries do their own.
static uint64_t changed_clocks_in(const uint64_t *raw, uint64_t clocks, uint64_t *prevp) {
    uint64_t prev = *prevp, k = 0;
    for (uint64_t c = 0; c < clocks; c++) {
        const uint64_t a = raw[2 * c] & FS_NOCLK_MASK, b = raw[2 * c + 1] & FS_NOCLK_MASK;
        k += (a != prev) | (b != a);
        prev = b;
    }
    *prevp = prev;
    return k;
}

static int mode_writer(int argc, char **argv) {
    if (argc < 2) {
        fprintf(stderr, "usage: fsbench writer TRACE OUTDIR [--writers a,b,..] [--chunk N] [--stride S] "
                        "[--phase P] [--reps R] [--max-chunks M]\n");
        return 2;
    }
    const char *trace = argv[0], *outdir = argv[1];
    char writers_arg[512] = "null,vgch2,planar,events,raw,fst,lxt2,vcd";
    uint64_t chunk = 1ull << 20, stride = 16, phase = 0, reps = 5, max_chunks = UINT64_MAX;
    for (int i = 2; i + 1 < argc; i += 2) {
        if (!strcmp(argv[i], "--writers")) snprintf(writers_arg, sizeof writers_arg, "%s", argv[i + 1]);
        else if (!strcmp(argv[i], "--chunk")) chunk = strtoull(argv[i + 1], NULL, 0);
        else if (!strcmp(argv[i], "--stride")) stride = strtoull(argv[i + 1], NULL, 0);
        else if (!strcmp(argv[i], "--phase")) phase = strtoull(argv[i + 1], NULL, 0);
        else if (!strcmp(argv[i], "--reps")) reps = strtoull(argv[i + 1], NULL, 0);
        else if (!strcmp(argv[i], "--max-chunks")) max_chunks = strtoull(argv[i + 1], NULL, 0);
        else { fprintf(stderr, "fsbench writer: unknown option %s\n", argv[i]); return 2; }
    }
    fs_source_t *src = fs_source_open(trace);
    if (!src) return 1;
    fs_vgch_set_source(trace);
    fs_hook_skip = 1;
    const char *project = project_of(trace);

    const fs_writer_t *ws[16];
    void *st[16];
    int nw = 0;
    for (char *save = NULL, *tok = strtok_r(writers_arg, ",", &save); tok; tok = strtok_r(NULL, ",", &save)) {
        ws[nw] = fs_writer_by_name(tok);
        if (!ws[nw]) { fprintf(stderr, "fsbench: no writer %s\n", tok); return 2; }
        char out[1024];
        snprintf(out, sizeof out, "%s/%s.%s.timing-out", outdir, project, tok);
        st[nw] = ws[nw]->create(0, out, NULL, NULL);
        nw++;
    }
    uint64_t *raw = malloc(2 * chunk * sizeof *raw);
    if (!raw) { fprintf(stderr, "fsbench: out of memory\n"); return 1; }
    const uint64_t clocks = fs_source_clocks(src);
    const uint64_t nchunks = clocks / chunk;
    printf("project,chunk,c0,clocks,changed_clocks,rep,slot,writer,ns,load1\n");
    uint64_t done = 0;
    for (uint64_t ci = phase; ci < nchunks && done < max_chunks; ci += stride, done++) {
        const uint64_t c0 = ci * chunk;
        if (fs_source_chunk(src, c0, chunk, raw)) return 1;
        uint64_t prev = raw[0] & FS_NOCLK_MASK;
        const uint64_t changed = changed_clocks_in(raw, chunk, &prev);
        const double l = load1();
        for (uint64_t r = 0; r < reps; r++) {
            for (int j = 0; j < nw; j++) {
                const int w = (int)((j + r + ci) % (uint64_t)nw);   // rotate the order every round
                const double t0 = fs_thread_ns();
                ws[w]->run(st[w], raw, chunk);
                const double t1 = fs_thread_ns();
                printf("%s,%llu,%llu,%llu,%llu,%llu,%d,%s,%.0f,%.2f\n", project, (unsigned long long)ci,
                       (unsigned long long)c0, (unsigned long long)chunk, (unsigned long long)changed,
                       (unsigned long long)r, j, ws[w]->name, t1 - t0, l);
            }
        }
        fflush(stdout);
    }
    for (int w = 0; w < nw; w++) ws[w]->finish(st[w]);
    for (int w = 0; w < nw; w++) {
        char out[1024];
        snprintf(out, sizeof out, "%s/%s.%s.timing-out", outdir, project, ws[w]->name);
        remove(out);
    }
    free(raw);
    fs_source_close(src);
    return 0;
}

int main(int argc, char **argv) {
    if (argc < 2) {
        fprintf(stderr, "usage: fsbench info|writer|encode|read ...\n");
        return 2;
    }
    if (!strcmp(argv[1], "info")) return mode_info(argc - 2, argv + 2);
    if (!strcmp(argv[1], "writer")) return mode_writer(argc - 2, argv + 2);
    if (!strcmp(argv[1], "encode")) return fs_mode_encode(argc - 2, argv + 2);
    if (!strcmp(argv[1], "read")) return fs_mode_read(argc - 2, argv + 2);
    if (!strcmp(argv[1], "ctr")) return fs_mode_ctr(argc - 2, argv + 2);
    fprintf(stderr, "fsbench: unknown mode %s\n", argv[1]);
    return 2;
}
