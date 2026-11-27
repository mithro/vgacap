// SPDX-License-Identifier: Apache-2.0
// frames.txt and frame_hashes.<method>.txt writers (vgacap/outputs.h).
// docs/frames-format.md is the format; python/vgacap/framesfile.py is the
// reference writer and the reader.
#include "vgacap/outputs.h"

#include <ctype.h>
#include <inttypes.h>
#include <stdlib.h>
#include <string.h>

#include "vgacap/time.h"

void vgaout_set_centre(vgaout_t *o, const vgasample_geometry_t *g, const centrev1_diag_t *d) {
    o->has_centre = 1;
    o->window_dx = d->dx; o->window_dy = d->dy;
    o->hback = g->hback; o->vback = g->vback;
    o->outside = d->outside;
    o->suggest_dx = d->suggest_dx; o->suggest_dy = d->suggest_dy;
    o->colour_changes = d->colour_changes;
    o->colour_changes_off_grid = d->off_grid;
}

static int cmp_u64(const void *a, const void *b) {
    uint64_t x = *(const uint64_t *)a, y = *(const uint64_t *)b;
    return x < y ? -1 : x > y;
}

uint64_t vgaout_frame_clocks(const vgaout_t *o) {
    uint64_t *d = malloc((o->n_frames ? o->n_frames : 1) * sizeof *d);
    if (!d) abort();
    size_t n = 0; int have = 0; uint64_t prev = 0;
    for (size_t i = 0; i < o->n_frames; i++) {
        if (o->frames[i].distinct < 0) continue;
        if (have) d[n++] = o->frames[i].start_clock - prev;
        prev = o->frames[i].start_clock; have = 1;
    }
    uint64_t r = 0;
    if (n) { qsort(d, n, sizeof *d, cmp_u64); r = d[n / 2]; }
    free(d);
    return r;
}

static int line_ok(const char *s) {   // one non-empty line
    return s && *s && !strpbrk(s, "\r\n");
}
static int store_name_ok(const char *s) {   // a bare file name: no whitespace, '/' or '\'
    if (!s || !*s) return 0;
    for (; *s; s++) if (isspace((unsigned char)*s) || *s == '/' || *s == '\\') return 0;
    return 1;
}
static int hex32_ok(const char *s) {   // exactly 32 lowercase hex digits
    if (!s || strlen(s) != 32) return 0;
    for (; *s; s++) if (!((*s >= '0' && *s <= '9') || (*s >= 'a' && *s <= 'f'))) return 0;
    return 1;
}

static int valid(const vgaout_t *o) {
    if (!line_ok(o->id) || !line_ok(o->status) || !line_ok(o->sampler) || !line_ok(o->sampler_status) ||
        !line_ok(o->mode)) return 0;
    if (strcmp(o->sampler_status, "ok") && strcmp(o->sampler_status, "no-sync") &&
        strcmp(o->sampler_status, "bad-timing") && strcmp(o->sampler_status, "bad-window")) return 0;
    if (!store_name_ok(o->store) || !hex32_ok(o->store_xxh3_128)) return 0;
    if (!!o->has_centre != (strcmp(o->sampler, "centre-v1") == 0)) return 0;   // the centre keys belong to centre-v1
    if (o->n_distinct && !o->xxh3_128) return 0;
    int have = 0; uint64_t prev = 0;
    for (size_t i = 0; i < o->n_frames; i++) {
        int64_t d = o->frames[i].distinct;
        if (d < -1 || d >= (int64_t)o->n_distinct) return 0;
        if (d < 0) continue;
        if (have && o->frames[i].start_clock < prev) return 0;   // starts never go backwards
        prev = o->frames[i].start_clock; have = 1;
    }
    return 1;
}

static int put_double(FILE *f, const char *key, double v) {
    char b[40];
    if (vgacap_fmt_double(b, sizeof b, v) < 0) return -1;
    return fprintf(f, "%s: %s\n", key, b) < 0 ? -1 : 0;
}

// id, status, sampler, sampler_status: shared by both files.
static int common_head(FILE *f, const vgaout_t *o) {
    return fprintf(f, "id: %s\nstatus: %s\nsampler: %s\nsampler_status: %s\n",
                   o->id, o->status, o->sampler, o->sampler_status) < 0 ? -1 : 0;
}

int vgaout_frames_txt(FILE *f, const vgaout_t *o) {
    if (!valid(o)) return -1;
    if (common_head(f, o) < 0) return -1;
    if (fprintf(f, "pixel_format: rgb6\nwidth: %u\nheight: %u\nmode: %s\nclock_hz: %" PRIu32 "\n",
                (unsigned)o->width, (unsigned)o->height, o->mode, o->clock_hz) < 0) return -1;
    if (put_double(f, "clocks_per_pixel", o->clocks_per_pixel) < 0) return -1;
    uint64_t fc = vgaout_frame_clocks(o);
    if (fprintf(f, "capture_after_clock: %" PRIu64 "\nframe_clocks: %" PRIu64 "\nframes: %zu\ndistinct_frames: %zu\n"
                   "store: %s\nstore_length: %" PRIu64 "\nstore_xxh3_128: %s\n",
                o->capture_after_clock, fc, o->n_frames, o->n_distinct,
                o->store, o->store_length, o->store_xxh3_128) < 0) return -1;
    if (o->has_centre) {
        if (fprintf(f, "window_offset: %d %d\nporches: %d %d\noutside: %" PRIu64 "\nsuggested_offset: %d %d\n"
                       "colour_changes: %" PRIu64 "\n",
                    o->window_dx, o->window_dy, o->hback, o->vback, o->outside,
                    o->suggest_dx, o->suggest_dy, o->colour_changes) < 0) return -1;
        if (o->colour_changes_off_grid == UINT64_MAX) {
            if (fputs("colour_changes_off_grid: -\n", f) < 0) return -1;
        } else if (fprintf(f, "colour_changes_off_grid: %" PRIu64 "\n", o->colour_changes_off_grid) < 0) return -1;
    }
    if (fputs("distinct:\n", f) < 0) return -1;
    for (size_t i = 0; i < o->n_distinct; i++) {
        for (int b = 0; b < 16; b++) if (fprintf(f, "%02x", o->xxh3_128[i][b]) < 0) return -1;
        if (fputc('\n', f) == EOF) return -1;
    }
    if (fputs("runs:\n", f) < 0) return -1;
    size_t i = 0;
    while (i < o->n_frames) {
        const vgaout_frame_t *a = &o->frames[i];
        size_t j = i + 1;
        if (a->distinct < 0) {
            while (j < o->n_frames && o->frames[j].distinct < 0) j++;
            if (fprintf(f, "%zu %zu - - -\n", i, j - i) < 0) return -1;
        } else {
            while (j < o->n_frames) {
                const vgaout_frame_t *p = &o->frames[j - 1], *q = &o->frames[j];
                if (q->distinct != a->distinct || q->missing != a->missing ||
                    q->start_clock != p->start_clock + fc) break;
                j++;
            }
            if (fprintf(f, "%zu %zu %" PRId64 " %" PRIu64 " %" PRIu32 "\n", i, j - i, a->distinct,
                        a->start_clock, a->missing) < 0) return -1;
        }
        i = j;
    }
    return 0;
}

int vgaout_hash_file(FILE *f, const char *method, const vgaout_t *o) {
    const char *const *def = vgahash_definition(method);
    if (!def || !valid(o)) return -1;
    int fnv = strcmp(method, "fnv1a64-tb2026-rgb888") == 0;
    if (fnv && !o->fnv && o->n_distinct) return -1;
    if (fprintf(f, "hash_method: %s\n", method) < 0) return -1;
    for (; *def; def++) if (fprintf(f, "# %s\n", *def) < 0) return -1;
    if (fnv && fputs("hash_offset_basis: 0x14650fb0739d0383\n", f) < 0) return -1;
    if (common_head(f, o) < 0) return -1;
    if (fprintf(f, "mode: %s\nwidth: %u\nheight: %u\nclock_hz: %" PRIu32 "\n",
                o->mode, (unsigned)o->width, (unsigned)o->height, o->clock_hz) < 0) return -1;
    if (put_double(f, "clocks_per_pixel", o->clocks_per_pixel) < 0) return -1;
    if (fprintf(f, "capture_after_clock: %" PRIu64 "\nframes: %zu\n", o->capture_after_clock, o->n_frames) < 0) return -1;
    for (size_t i = 0; i < o->n_frames; i++) {
        int64_t d = o->frames[i].distinct;
        if (d < 0) { if (fputs("-\n", f) < 0) return -1; }
        else if (fnv) { if (fprintf(f, "%016" PRIx64 "\n", o->fnv[d]) < 0) return -1; }
        else {
            for (int b = 0; b < 16; b++) if (fprintf(f, "%02x", o->xxh3_128[d][b]) < 0) return -1;
            if (fputc('\n', f) == EOF) return -1;
        }
    }
    return 0;
}

// ---- reading frames.txt's runs ----------------------------------------------

static int rerr(char *err, size_t errlen, const char *msg, size_t line) {
    if (line) snprintf(err, errlen, "frames.txt line %zu: %s", line, msg);
    else snprintf(err, errlen, "frames.txt: %s", msg);
    return -1;
}

// Plain decimal (no sign, no leading zero but "0") of [s, e) into *v.
static int dec_u64(const char *s, const char *e, uint64_t *v) {
    if (s == e || (e - s > 1 && *s == '0')) return -1;
    uint64_t x = 0;
    for (; s < e; s++) {
        if (*s < '0' || *s > '9') return -1;
        unsigned d = (unsigned)(*s - '0');
        if (x > (UINT64_MAX - d) / 10) return -1;
        x = x * 10 + d;
    }
    *v = x;
    return 0;
}

// One run line [s, e): five fields separated by single spaces.
static int parse_run(const char *s, const char *e, vgaout_frame_run_t *r) {
    const char *f[5][2];
    int n = 0;
    for (const char *p = s;; ) {
        const char *q = p;
        while (q < e && *q != ' ') q++;
        if (n == 5) return -1;
        f[n][0] = p; f[n][1] = q; n++;
        if (q == e) break;
        p = q + 1;
    }
    if (n != 5) return -1;
    uint64_t v[5];
    if (dec_u64(f[0][0], f[0][1], &v[0]) || dec_u64(f[1][0], f[1][1], &v[1])) return -1;
    int dash = 0;
    for (int i = 2; i < 5; i++) dash += f[i][1] - f[i][0] == 1 && f[i][0][0] == '-';
    if (dash == 3) {
        r->first = v[0]; r->count = v[1]; r->distinct = -1; r->start_clock = 0; r->missing = 0;
        return 0;
    }
    for (int i = 2; i < 5; i++) if (dec_u64(f[i][0], f[i][1], &v[i])) return -1;
    if (v[2] > INT64_MAX || v[4] > UINT32_MAX) return -1;
    r->first = v[0]; r->count = v[1]; r->distinct = (int64_t)v[2]; r->start_clock = v[3];
    r->missing = (uint32_t)v[4];
    return 0;
}

int vgaout_read_frame_runs(FILE *f, vgaout_frame_runs_t *out, char *err, size_t errlen) {
    memset(out, 0, sizeof *out);
    size_t cap = 1 << 16, len = 0;
    char *buf = malloc(cap);
    if (!buf) return rerr(err, errlen, "out of memory", 0);
    for (;;) {
        if (len == cap) {
            char *nb = realloc(buf, cap *= 2);
            if (!nb) { free(buf); return rerr(err, errlen, "out of memory", 0); }
            buf = nb;
        }
        size_t got = fread(buf + len, 1, cap - len, f);
        len += got;
        if (got == 0) break;
    }
    if (ferror(f)) { free(buf); return rerr(err, errlen, "read error", 0); }
    if (len && buf[len - 1] != '\n') { free(buf); return rerr(err, errlen, "the last line has no newline", 0); }
    int have_s = 0, have_fc = 0, have_n = 0, in_runs = 0;
    size_t line = 0, cap_runs = 0;
    uint64_t next = 0, prev_start = 0;
    int have_start = 0;
    const char *why = NULL;
    for (const char *p = buf, *end = buf + len; p < end && !why; ) {
        const char *e = memchr(p, '\n', (size_t)(end - p));   // never NULL: the text ends in '\n'
        line++;
        if (!in_runs) {
            size_t ll = (size_t)(e - p);
            if (ll == 5 && !memcmp(p, "runs:", 5)) in_runs = 1;
            else if (ll >= 9 && !memcmp(p, "sampler: ", 9)) {
                if (have_s) why = "sampler given twice";
                else if (ll == 9 || ll - 9 >= sizeof out->sampler) why = "sampler is empty or too long";
                else { memcpy(out->sampler, p + 9, ll - 9); out->sampler[ll - 9] = 0; have_s = 1; }
            } else if (ll >= 14 && !memcmp(p, "frame_clocks: ", 14)) {
                if (have_fc || dec_u64(p + 14, e, &out->frame_clocks)) why = "bad frame_clocks";
                have_fc = 1;
            } else if (ll >= 8 && !memcmp(p, "frames: ", 8)) {
                if (have_n || dec_u64(p + 8, e, &out->frames)) why = "bad frames";
                have_n = 1;
            }
        } else {
            vgaout_frame_run_t r;
            if (parse_run(p, e, &r)) why = "not a run line (<first> <count> <distinct> <start> <missing>, or <first> <count> - - -)";
            else if (r.first != next) why = "the run does not start where the last one ended";
            else if (r.count == 0 || r.count > UINT64_MAX - next) why = "bad run count";
            else if (r.distinct >= 0 && have_start && r.start_clock < prev_start) why = "frame starts go backwards";
            else if (r.distinct >= 0 && r.count > 1 && out->frame_clocks &&
                     (r.count - 1) > (UINT64_MAX - r.start_clock) / out->frame_clocks) why = "the run's starts overflow";
            else {
                if (out->n_runs == cap_runs) {
                    size_t nc = cap_runs ? 2 * cap_runs : 64;
                    vgaout_frame_run_t *nr = realloc(out->runs, nc * sizeof *nr);
                    if (!nr) why = "out of memory";
                    else { out->runs = nr; cap_runs = nc; }
                }
                if (!why) {
                    out->runs[out->n_runs++] = r;
                    next += r.count;
                    if (r.distinct >= 0) {
                        have_start = 1;
                        prev_start = r.start_clock + (r.count - 1) * out->frame_clocks;
                    }
                }
            }
        }
        p = e + 1;
    }
    free(buf);
    if (!why) {
        if (!have_s) { why = "no sampler key"; line = 0; }
        else if (!have_fc) { why = "no frame_clocks key"; line = 0; }
        else if (!have_n) { why = "no frames key"; line = 0; }
        else if (!in_runs) { why = "no runs: section"; line = 0; }
        else if (next != out->frames) { why = "the runs do not cover frames 0 to frames - 1"; line = 0; }
    }
    if (why) {
        free(out->runs);
        memset(out, 0, sizeof *out);
        return rerr(err, errlen, why, line);
    }
    return 0;
}

void vgaout_frame_runs_free(vgaout_frame_runs_t *r) {
    free(r->runs);
    memset(r, 0, sizeof *r);
}
