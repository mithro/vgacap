// SPDX-License-Identifier: Apache-2.0
// vgacap-export's FST against its VCD: the FST read back with libfst
// (fstReaderIterBlocks2), the VCD with the small parser below, and the two
// lists of (time, full signal name, value) compared, plus the timescale and
// the end time.
//
//   test_export_fst FST VCD   compare two files (python/tests/test_vgacap_export.py)
//   test_export_fst           write a trace with the trace writer, export it
//                             both ways (with and without clk, and a range)
//                             through VGACAP_EXPORT_PATH, and compare
#define _POSIX_C_SOURCE 200809L
#include "harness.h"
#include "vgacap/trace.h"

#include <fstapi.h>
#include <inttypes.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

typedef struct change { uint64_t t; char *name; char *value; } change_t;
typedef struct changes {
    change_t *c; size_t n, cap;
    uint64_t end_time; int timescale;
} changes_t;

static char *dupn(const char *s, size_t n) {
    char *d = malloc(n + 1);
    if (!d) abort();
    memcpy(d, s, n); d[n] = 0;
    return d;
}

static void add(changes_t *l, uint64_t t, const char *name, const char *value, size_t vlen) {
    if (l->n == l->cap) {
        l->cap = l->cap ? 2 * l->cap : 1024;
        l->c = realloc(l->c, l->cap * sizeof *l->c);
        if (!l->c) abort();
    }
    l->c[l->n].t = t; l->c[l->n].name = dupn(name, strlen(name)); l->c[l->n].value = dupn(value, vlen);
    l->n++;
}

static void free_changes(changes_t *l) {
    for (size_t i = 0; i < l->n; i++) { free(l->c[i].name); free(l->c[i].value); }
    free(l->c);
    memset(l, 0, sizeof *l);
}

static int cmp_change(const void *a, const void *b) {
    const change_t *x = a, *y = b;
    if (x->t != y->t) return x->t < y->t ? -1 : 1;
    return strcmp(x->name, y->name);
}

// ---- FST ----------------------------------------------------------------------

typedef struct fst_ctx { changes_t *l; char **names; size_t nnames; } fst_ctx_t;

static void on_fst(void *user, uint64_t t, fstHandle h, const unsigned char *v) {
    fst_ctx_t *c = user;
    if (h == 0 || h > c->nnames || !c->names[h - 1]) { fprintf(stderr, "  FST handle %u unknown\n", (unsigned)h); abort(); }
    add(c->l, t, c->names[h - 1], (const char *)v, strlen((const char *)v));
}

static int read_fst(const char *path, changes_t *l) {
    fstReaderContext *r = fstReaderOpen(path);
    if (!r) { fprintf(stderr, "  cannot open FST %s\n", path); return -1; }
    fst_ctx_t c = { l, NULL, 0 };
    c.nnames = (size_t)fstReaderGetMaxHandle(r);
    c.names = calloc(c.nnames ? c.nnames : 1, sizeof *c.names);
    char scope[1024] = "";
    size_t depth_len[64]; int depth = 0;
    struct fstHier *h;
    while ((h = fstReaderIterateHier(r))) {
        if (h->htyp == FST_HT_SCOPE) {
            if (depth == 64) abort();
            depth_len[depth++] = strlen(scope);
            size_t sl = strlen(scope);
            snprintf(scope + sl, sizeof scope - sl, "%s%s", sl ? "." : "", h->u.scope.name);
        } else if (h->htyp == FST_HT_UPSCOPE) {
            if (depth) scope[depth_len[--depth]] = 0;
        } else if (h->htyp == FST_HT_VAR) {
            fstHandle k = h->u.var.handle;
            if (k == 0 || k > c.nnames || h->u.var.is_alias) { fprintf(stderr, "  bad FST var\n"); return -1; }
            char full[1400];
            snprintf(full, sizeof full, "%s.%s", scope, h->u.var.name);
            c.names[k - 1] = dupn(full, strlen(full));
        }
    }
    fstReaderSetFacProcessMaskAll(r);
    int ok = fstReaderIterBlocks2(r, on_fst, NULL, &c, NULL) != 0;
    l->end_time = fstReaderGetEndTime(r);
    l->timescale = fstReaderGetTimescale(r);
    fstReaderClose(r);
    for (size_t i = 0; i < c.nnames; i++) free(c.names[i]);
    free(c.names);
    return ok ? 0 : -1;
}

// ---- VCD ----------------------------------------------------------------------

typedef struct vvar { char id[16]; char name[4096]; unsigned width; } vvar_t;   // room for scope, name and range tokens whole

// Next whitespace-separated token of *p (advanced), into tok; 0 at the end.
static int token(const char **p, char *tok, size_t len) {
    const char *s = *p;
    while (*s == ' ' || *s == '\n' || *s == '\t' || *s == '\r') s++;
    if (!*s) return 0;
    size_t n = 0;
    while (*s && *s != ' ' && *s != '\n' && *s != '\t' && *s != '\r') { if (n + 1 < len) tok[n++] = *s; s++; }
    tok[n] = 0; *p = s;
    return 1;
}

static int read_vcd(const char *path, changes_t *l) {
    FILE *f = fopen(path, "rb");
    if (!f) { fprintf(stderr, "  cannot open VCD %s\n", path); return -1; }
    fseek(f, 0, SEEK_END); long sz = ftell(f); fseek(f, 0, SEEK_SET);
    char *buf = malloc((size_t)sz + 1);
    if (!buf || fread(buf, 1, (size_t)sz, f) != (size_t)sz) { fclose(f); return -1; }
    buf[sz] = 0; fclose(f);
    vvar_t *vars = NULL; size_t nv = 0;
    char scope[1024] = ""; size_t depth_len[64]; int depth = 0;
    const char *p = buf;
    char tok[1400];
    int defs = 1, rc = -1;
    uint64_t t = 0; int have_t = 0;
    l->timescale = 99;
    while (token(&p, tok, sizeof tok)) {
        if (defs) {
            if (!strcmp(tok, "$scope")) {
                char kind[64], name[512];
                if (!token(&p, kind, sizeof kind) || !token(&p, name, sizeof name) || !token(&p, tok, sizeof tok)) goto out;
                depth_len[depth++] = strlen(scope);
                size_t sl = strlen(scope);
                snprintf(scope + sl, sizeof scope - sl, "%s%s", sl ? "." : "", name);
            } else if (!strcmp(tok, "$upscope")) {
                if (!token(&p, tok, sizeof tok) || !depth) goto out;
                scope[depth_len[--depth]] = 0;
            } else if (!strcmp(tok, "$var")) {
                char type[32], width[32], id[16], name[512];
                if (!token(&p, type, sizeof type) || !token(&p, width, sizeof width) || !token(&p, id, sizeof id) ||
                    !token(&p, name, sizeof name) || !token(&p, tok, sizeof tok)) goto out;
                vars = realloc(vars, (nv + 1) * sizeof *vars);
                vvar_t *v = &vars[nv++];
                snprintf(v->id, sizeof v->id, "%s", id);
                v->width = (unsigned)strtoul(width, NULL, 10);
                if (strcmp(tok, "$end")) {   // a "[7:0]" range: name[7:0], as GTKWave and FST name it
                    snprintf(v->name, sizeof v->name, "%s.%s%s", scope, name, tok);
                    if (!token(&p, tok, sizeof tok) || strcmp(tok, "$end")) goto out;
                } else snprintf(v->name, sizeof v->name, "%s.%s", scope, name);
            } else if (!strcmp(tok, "$timescale")) {
                if (!token(&p, tok, sizeof tok)) goto out;
                l->timescale = !strcmp(tok, "1ps") ? -12 : !strcmp(tok, "1ns") ? -9 : 98;
                if (!token(&p, tok, sizeof tok)) goto out;
            } else if (!strcmp(tok, "$enddefinitions")) {
                if (!token(&p, tok, sizeof tok)) goto out;
                defs = 0;
            } else if (tok[0] == '$' && strcmp(tok, "$end")) {   // $version, $comment ...: skip to $end
                while (token(&p, tok, sizeof tok) && strcmp(tok, "$end")) {}
            }
            continue;
        }
        if (!strcmp(tok, "$dumpvars") || !strcmp(tok, "$end")) continue;
        if (tok[0] == '#') { t = strtoull(tok + 1, NULL, 10); have_t = 1; l->end_time = t; continue; }
        if (!have_t) goto out;
        char value[1400]; const char *id;
        size_t vl;
        if (tok[0] == 'b') {
            snprintf(value, sizeof value, "%s", tok + 1);
            vl = strlen(value);
            char idt[16];
            if (!token(&p, idt, sizeof idt)) goto out;
            snprintf(tok, sizeof tok, "%s", idt);
            id = tok;
        } else { value[0] = tok[0]; value[1] = 0; vl = 1; id = tok + 1; }
        char full[1400];
        vvar_t *v = NULL;
        for (size_t i = 0; i < nv; i++) if (!strcmp(vars[i].id, id)) { v = &vars[i]; break; }
        if (!v || vl > v->width || v->width >= sizeof full) { fprintf(stderr, "  VCD change for unknown id '%s'\n", id); goto out; }
        size_t pad = v->width - vl;   // left-extend with 0 (only 0/1 values are written)
        memset(full, '0', pad); memcpy(full + pad, value, vl); full[v->width] = 0;
        add(l, t, v->name, full, v->width);
    }
    rc = 0;
out:
    free(vars); free(buf);
    if (rc) fprintf(stderr, "  VCD %s: parse error near '%s'\n", path, tok);
    return rc;
}

// 0 when the two files hold the same changes, timescale and end time.
static int compare(const char *fst, const char *vcd) {
    changes_t a = {0}, b = {0};
    int rc = -1;
    if (read_fst(fst, &a) || read_vcd(vcd, &b)) goto out;
    qsort(a.c, a.n, sizeof *a.c, cmp_change);
    qsort(b.c, b.n, sizeof *b.c, cmp_change);
    if (a.timescale != -12 || b.timescale != -12) { fprintf(stderr, "  timescale %d / %d\n", a.timescale, b.timescale); goto out; }
    if (a.end_time != b.end_time) {
        fprintf(stderr, "  end time FST %" PRIu64 " VCD %" PRIu64 "\n", a.end_time, b.end_time); goto out;
    }
    if (a.n != b.n) fprintf(stderr, "  FST %zu changes, VCD %zu\n", a.n, b.n);
    if (getenv("TEST_EXPORT_FST_DUMP")) {   // both lists, sorted, for debugging
        for (size_t i = 0; i < a.n; i++) fprintf(stderr, "FST %" PRIu64 " %s %s\n", a.c[i].t, a.c[i].name, a.c[i].value);
        for (size_t i = 0; i < b.n; i++) fprintf(stderr, "VCD %" PRIu64 " %s %s\n", b.c[i].t, b.c[i].name, b.c[i].value);
    }
    for (size_t i = 0; i < a.n && i < b.n; i++)
        if (a.c[i].t != b.c[i].t || strcmp(a.c[i].name, b.c[i].name) || strcmp(a.c[i].value, b.c[i].value)) {
            fprintf(stderr, "  change %zu: FST %" PRIu64 " %s %s, VCD %" PRIu64 " %s %s\n", i, a.c[i].t, a.c[i].name,
                    a.c[i].value, b.c[i].t, b.c[i].name, b.c[i].value);
            goto out;
        }
    if (a.n != b.n || a.n == 0) goto out;
    fprintf(stderr, "  %zu changes agree, end time %" PRIu64 "\n", a.n, a.end_time);
    rc = 0;
out:
    free_changes(&a); free_changes(&b);
    return rc;
}

// ---- the self-contained test ----------------------------------------------------

#ifdef VGACAP_EXPORT_PATH
// A small tt-um-43 trace: 10 reset clocks, then uo_out counting, uio and
// ui_in changing now and then; written to the working directory.
static int make_trace(const char *path) {
    vgacap_header_t h;
    vgacap_header_init_v2(&h, 43, 2, 42, 0);
    h.mode = VGACAP_MODE_EVENT; h.clock_hz = 25175000;
    char err[256];
    vgacap_trace_writer_t *t = vgacap_trace_create(path, &h,
        "meta=open\nproject=tt07/tt_um_fst_check\ncalib_seconds=0\nseconds=1\nlayout=tt-um-43\n", 3, err, sizeof err);
    if (!t) { fprintf(stderr, "  %s\n", err); return -1; }
    const uint64_t clk = 1ull << 42, ena = 1ull << 41, rst = 1ull << 40;
    uint64_t prev = ena;
    int bad = 0;
    for (uint64_t c = 0; c < 3000; c++) {
        uint64_t v = ena | (c >= 10 ? rst : 0);
        if (c >= 10) {
            uint64_t uo = (c / 3) & 0xFF, uio = (c / 50) & 0xFF, oe = (c / 700) & 0xFF, ui = (c / 333) & 0xFF;
            uint64_t uin = (c / 999) & 0xFF;
            v |= uo | uio << 8 | oe << 16 | ui << 24 | uin << 32;
        }
        bad |= vgacap_trace_push(t, prev);         // clk low: the last clock's outputs
        bad |= vgacap_trace_push(t, v | clk);
        prev = v;
    }
    if (vgacap_trace_finish(t, "meta=close\ncapture_after_clock=0\nstatus=ok\nframes=0\n", err, sizeof err) || bad) {
        fprintf(stderr, "  %s\n", err); return -1;
    }
    return 0;
}

static int run(const char *args) {
    char cmd[2048];
    snprintf(cmd, sizeof cmd, "\"%s\" %s", VGACAP_EXPORT_PATH, args);
    int rc = system(cmd);
    if (rc) fprintf(stderr, "  '%s' exited %d\n", cmd, rc);
    return rc;
}

TEST(fst_equals_vcd_whole) {
    ASSERT_TRUE(make_trace("test_export_fst.trace.zst") == 0);
    ASSERT_TRUE(run("test_export_fst.trace.zst --format fst --out test_export_fst.fst") == 0);
    ASSERT_TRUE(run("test_export_fst.trace.zst --format vcd --out test_export_fst.vcd") == 0);
    ASSERT_TRUE(compare("test_export_fst.fst", "test_export_fst.vcd") == 0);
}
TEST(fst_equals_vcd_no_clk_range) {
    ASSERT_TRUE(run("test_export_fst.trace.zst --format fst --out test_export_fst_r.fst --no-clk --from 7 --to 2501") == 0);
    ASSERT_TRUE(run("test_export_fst.trace.zst --format vcd --out test_export_fst_r.vcd --no-clk --from 7 --to 2501") == 0);
    ASSERT_TRUE(compare("test_export_fst_r.fst", "test_export_fst_r.vcd") == 0);
}
TEST(compare_sees_a_difference) {   // the check itself fails when the files differ
    ASSERT_TRUE(compare("test_export_fst.fst", "test_export_fst_r.vcd") != 0);
}
#endif

int main(int argc, char **argv) {
    if (argc == 3) return compare(argv[1], argv[2]) ? 1 : 0;
    if (argc != 1) { fprintf(stderr, "usage: test_export_fst [FST VCD]\n"); return 2; }
#ifdef VGACAP_EXPORT_PATH
    RUN(fst_equals_vcd_whole);
    RUN(fst_equals_vcd_no_clk_range);
    RUN(compare_sees_a_difference);
#endif
    RUN_TESTS_END();
}
