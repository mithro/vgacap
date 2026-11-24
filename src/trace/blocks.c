// SPDX-License-Identifier: Apache-2.0
#include "blocks.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static void put_u32(uint8_t *p, uint32_t v) {
    p[0] = (uint8_t)v; p[1] = (uint8_t)(v >> 8); p[2] = (uint8_t)(v >> 16); p[3] = (uint8_t)(v >> 24);
}

int vgacap_trace_block_append(void *user, const uint8_t *p, size_t n) {
    vgacap_trace_block_t *b = user;
    if (n > VGACAP_TRACE_BLOCK_BYTES - b->fill) return -1;
    memcpy(b->buf + b->fill, p, n);
    b->fill += n;
    return 0;
}

int vgacap_trace_block_begin(vgacap_trace_block_t *b, vgacap_writer_t *w, uint64_t first) {
    if (vgacap_writer_block(w, first)) return -1;
    if (VGACAP_TRACE_BLOCK_BYTES - b->fill < 12) return -1;
    b->runs_off = b->fill;
    memcpy(b->buf + b->fill, VGACAP_TAG_RUNS, 4);   // length and count patched at the end
    memset(b->buf + b->fill + 4, 0, 8);
    b->fill += 12;
    b->pairs = 0;
    return 0;
}

void vgacap_trace_block_end_runs(vgacap_trace_block_t *b) {
    if (b->pairs == 0) { b->fill = b->runs_off; return; }
    put_u32(b->buf + b->runs_off + 4, (uint32_t)(b->fill - b->runs_off - 8));
    put_u32(b->buf + b->runs_off + 8, b->pairs);
}

// ---- header and META checks, by the reader itself ---------------------------

struct check {
    int headers, metas, losses;
    const char *what;
};

static void on_check(void *user, const vgacap_event_t *ev) {
    struct check *c = user;
    if (ev->type == VGACAP_EV_HEADER) c->headers++;
    else if (ev->type == VGACAP_EV_META) c->metas++;
    else if (ev->type == VGACAP_EV_RESYNC) { c->losses++; c->what = ev->u.resync.what; }
    else if (ev->type == VGACAP_EV_ERROR) { c->losses++; c->what = ev->u.error.what; }
}

struct membuf { uint8_t data[8 + 24 + VGACAP_DESC_MAX + 8 + VGACAP_META_MAX]; size_t fill; };

static int mem_write(void *user, const uint8_t *p, size_t n) {
    struct membuf *m = user;
    if (n > sizeof m->data - m->fill) return -1;
    memcpy(m->data + m->fill, p, n); m->fill += n;
    return 0;
}

int vgacap_trace_check(const vgacap_header_t *h, const char *text, int open, char *err, size_t errlen) {
    const char *kind = open ? "open" : "close";
    if (h->version != 2) { snprintf(err, errlen, "trace header must be version 2, not %u", h->version); return -1; }
    size_t len = text ? strlen(text) : 0;
    if (len == 0 || len > VGACAP_META_MAX) {
        snprintf(err, errlen, "META(%s): length %zu not in 1..%u", kind, len, VGACAP_META_MAX);
        return -1;
    }
    const char *first = open ? "meta=open\n" : "meta=close\n";
    if (strncmp(text, first, strlen(first)) != 0) {
        snprintf(err, errlen, "META(%s): first line must be %.*s", kind, (int)strlen(first) - 1, first);
        return -1;
    }
    struct membuf *m = malloc(sizeof *m);
    vgacap_reader_t *r = malloc(sizeof *r);
    int rc = -1;
    if (!m || !r) { snprintf(err, errlen, "out of memory"); goto out; }
    m->fill = 0;
    vgacap_writer_t w;
    if (vgacap_writer_init(&w, mem_write, m, h) || vgacap_writer_meta(&w, text, len)) {
        snprintf(err, errlen, "bad VGCH header");
        goto out;
    }
    struct check c = { 0, 0, 0, NULL };
    vgacap_reader_init(r, on_check, &c);
    vgacap_reader_feed(r, m->data, m->fill);
    if (c.headers != 1 || r->scanning) {
        snprintf(err, errlen, "bad VGCH header: %s", c.what ? c.what : "rejected by the reader");
    } else if (c.metas != 1 || c.losses) {
        snprintf(err, errlen, "META(%s): %s", kind, c.what ? c.what : "rejected by the reader");
    } else if (open && strstr(text, "\nlayout=tt-um-43\n") &&
               (h->sample_bits != 43 || h->samples_per_clock != 2 || h->clock_bit != 42 || h->clock_first != 0)) {
        // The layout names the pins; the note requires the header to match it.
        snprintf(err, errlen, "META(open): layout=tt-um-43 needs sample_bits 43, samples_per_clock 2, "
                 "clock_bit 42 and clock_first 0");
    } else {
        rc = 0;
    }
out:
    free(m); free(r);
    return rc;
}
