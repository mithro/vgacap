// SPDX-License-Identifier: Apache-2.0
// vgacap-dump: prints a human-readable summary of a capture stream file,
// then a total_samples/checksum line derived from the expanded RUN events
// (used to cross-check the C reader against the Python mirror). A version 1
// stream's checksum is crc32=: FNV-1a 32 over each sample as 4 little-endian
// bytes. A version 2 stream's is crc64=: FNV-1a 64 (basis
// 0xcbf29ce484222325) over each sample as (sample_bits + 7) / 8
// little-endian bytes, the clock bit toggling sample by sample within a run.
//
// A simulation trace (zstd magic) is read when built with VGACAP_HOST_EXTRAS
// (VGACAP_DUMP_TRACES): a `trace` line from the trace reader, then each
// complete block's chunks, then the summary of all of them read in order --
// the same lines as for the stream `zstd -d` gives, cut to the complete
// blocks. Without it a zstd file is refused with a message saying so.
#ifdef VGACAP_DUMP_TRACES
#define _POSIX_C_SOURCE 200809L   // fmemopen
#include "vgacap/trace.h"
#endif
#include "vgacap/stream.h"
#include <inttypes.h>
#include <stdio.h>
#include <string.h>

static uint16_t rd_u16(const uint8_t *p) { return (uint16_t)(p[0] | (uint16_t)(p[1] << 8)); }
static uint32_t rd_u32(const uint8_t *p) {
    return (uint32_t)p[0] | ((uint32_t)p[1] << 8) | ((uint32_t)p[2] << 16) | ((uint32_t)p[3] << 24);
}
static uint64_t rd_u64(const uint8_t *p) { return (uint64_t)rd_u32(p) | ((uint64_t)rd_u32(p + 4) << 32); }

static int read_exact(FILE *fp, void *buf, size_t n) { return fread(buf, 1, n, fp) == n ? 0 : -1; }

static void print_map(const uint8_t *m) {
    printf("map=%u,%u,%u,%u,%u,%u,%u,%u", m[0], m[1], m[2], m[3], m[4], m[5], m[6], m[7]);
}

static int skip_bytes(FILE *fp, uint32_t n) {
    uint8_t junk[256];
    while (n) {
        size_t want = n < sizeof junk ? n : sizeof junk;
        if (read_exact(fp, junk, want)) return -1;
        n -= (uint32_t)want;
    }
    return 0;
}

// First pass: a tiny chunk walker (tag + length only) that prints one summary
// line per chunk. It does not use vgacap_reader_t; it just needs enough of
// each payload's fixed fields to report counts. It stops at the first thing
// it cannot walk past, reporting it on stderr, and never fails the run: the
// authoritative diagnosis of a mis-framed stream is the second pass, whose
// reader resynchronises and reports each loss as a `resync` line.
// A RUNS payload after its pair_count: sums the runs of the pairs that fit
// in `left` bytes. Returns 0, or -1 (and leaves `left` as what is still
// unread) at the first malformed or truncated pair.
static int walk_runs(FILE *fp, uint32_t pairs, unsigned vbytes, uint32_t *left, uint64_t *total) {
    for (uint32_t i = 0; i < pairs; i++) {
        uint8_t v[8];
        if (vbytes > sizeof v || *left < vbytes || read_exact(fp, v, vbytes)) return -1;
        *left -= vbytes;
        uint64_t run = 0;
        for (unsigned k = 0;; k++) {
            int c = *left && k < 10 ? fgetc(fp) : EOF;
            if (c == EOF) return -1;
            (*left)--;
            run |= (uint64_t)(c & 0x7F) << (7 * k);
            if (!(c & 0x80)) break;
        }
        *total += run;
    }
    return 0;
}

// `vbytes`: RUNS value bytes, from the last VGCH (carried across a trace's blocks).
static void print_chunks(FILE *fp, unsigned *vbytes_io) {
    uint8_t head[8];
    unsigned vbytes = *vbytes_io;   // stored back after each chunk
    while (fread(head, 1, 8, fp) == 8) {
        uint32_t length = rd_u32(head + 4);
        if (memcmp(head, VGACAP_TAG_HEADER, 4) == 0) {
            uint8_t buf[24];
            if (length < 20 || read_exact(fp, buf, 20)) { fprintf(stderr, "error: truncated VGCH\n"); return; }
            uint16_t version = rd_u16(buf);
            uint32_t fixed = version == 2 ? 24u : 20u;
            if (fixed == 24 && (length < 24 || read_exact(fp, buf + 20, 4))) { fprintf(stderr, "error: truncated VGCH\n"); return; }
            uint16_t dl = rd_u16(buf + 18);
            char desc[VGACAP_DESC_MAX + 1];
            if (dl > VGACAP_DESC_MAX || read_exact(fp, desc, dl)) { fprintf(stderr, "error: truncated VGCH desc\n"); return; }
            desc[dl] = 0;
            // sample_bits is not validated by this pass otherwise (the
            // reader in the second pass judges the header): a RUNS chunk is
            // walked only with a width the format allows, at most 8 bytes.
            if (buf[2] >= 1 && buf[2] <= 64) {
                vbytes = (buf[2] + 7u) / 8u;
            } else {
                vbytes = 0;
                fprintf(stderr, "error: bad sample_bits %u in VGCH\n", buf[2]);
            }
            if (version == 2) {
                char cb[8];
                if (buf[21] == VGACAP_CLOCK_BIT_NONE) snprintf(cb, sizeof cb, "none");
                else snprintf(cb, sizeof cb, "%u", buf[21]);
                printf("VGCH v2 bits=%u spc=%u clock_bit=%s clock_first=%u spw=%u flags=%u clock=%u ",
                       buf[2], buf[20], cb, buf[22], buf[16], buf[17], rd_u32(buf + 4));
            } else {
                printf("VGCH bits=%u spw=%u flags=%u clock=%u ", buf[2], buf[16], buf[17], rd_u32(buf + 4));
            }
            print_map(buf + 8);
            printf(" desc=\"%s\"\n", desc);
            uint32_t consumed = fixed + dl;
            if (consumed < length && skip_bytes(fp, length - consumed)) { fprintf(stderr, "error: truncated VGCH\n"); return; }
        } else if (memcmp(head, VGACAP_TAG_RAW, 4) == 0) {
            uint8_t buf[4];
            if (length < 4 || read_exact(fp, buf, 4)) { fprintf(stderr, "error: truncated RAW\n"); return; }
            printf("RAW  samples=%u\n", rd_u32(buf));
            if (skip_bytes(fp, length - 4)) { fprintf(stderr, "error: truncated RAW\n"); return; }
        } else if (memcmp(head, VGACAP_TAG_RLE, 4) == 0) {
            uint8_t buf[4];
            if (length < 4 || read_exact(fp, buf, 4)) { fprintf(stderr, "error: truncated RLE\n"); return; }
            // Bound the pair walk by the declared length: a corrupt count
            // would otherwise read straight through the following chunks.
            uint32_t pairs = rd_u32(buf), avail = (length - 4) / 8;
            uint64_t total = 0;
            if (pairs != avail) fprintf(stderr, "error: RLE pairs=%u does not match length %u\n", pairs, length);
            for (uint32_t i = 0; i < avail; i++) {
                uint8_t pair[8];
                if (read_exact(fp, pair, 8)) { fprintf(stderr, "error: truncated RLE pair\n"); return; }
                total += rd_u32(pair + 4);
            }
            printf("RLE  pairs=%u runs=%" PRIu64 "\n", pairs, total);
            if (skip_bytes(fp, length - 4 - avail * 8)) { fprintf(stderr, "error: truncated RLE\n"); return; }
        } else if (memcmp(head, VGACAP_TAG_FRAME, 4) == 0) {
            uint8_t buf[16];
            if (length < 16 || read_exact(fp, buf, 16)) { fprintf(stderr, "error: truncated FRAM\n"); return; }
            printf("FRAM counter=%u first=%u lines=%u cpl=%u samples=%u\n",
                   rd_u32(buf), rd_u16(buf + 4), rd_u16(buf + 6), rd_u32(buf + 8), rd_u32(buf + 12));
            if (skip_bytes(fp, length - 16)) { fprintf(stderr, "error: truncated FRAM\n"); return; }
        } else if (memcmp(head, VGACAP_TAG_EVENT, 4) == 0) {
            uint8_t buf[4];
            if (length < 4 || read_exact(fp, buf, 4)) { fprintf(stderr, "error: truncated EVNT\n"); return; }
            printf("EVNT events=%u\n", rd_u32(buf));
            if (skip_bytes(fp, length - 4)) { fprintf(stderr, "error: truncated EVNT\n"); return; }
        } else if (memcmp(head, VGACAP_TAG_TIME, 4) == 0) {
            uint8_t buf[18];
            if (length < 18 || read_exact(fp, buf, 18)) { fprintf(stderr, "error: truncated TIME\n"); return; }
            uint16_t ml = rd_u16(buf + 16);
            char msg[256];
            if (ml > 255 || read_exact(fp, msg, ml)) { fprintf(stderr, "error: truncated TIME msg\n"); return; }
            msg[ml] = 0;
            printf("TIME t=%" PRIu64 " clock=%u dropped=%u msg=\"%s\"\n",
                   rd_u64(buf), rd_u32(buf + 8), rd_u32(buf + 12), msg);
        } else if (memcmp(head, VGACAP_TAG_RUNS, 4) == 0) {
            uint8_t buf[4];
            if (length < 4 || read_exact(fp, buf, 4)) { fprintf(stderr, "error: truncated RUNS\n"); return; }
            // Bounded by the declared length, like RLE above; the reader in
            // the second pass is the judge of the pairs' validity.
            uint32_t left = length - 4; uint64_t total = 0;
            int bad = !vbytes || walk_runs(fp, rd_u32(buf), vbytes, &left, &total);
            printf("RUNS pairs=%u runs=%" PRIu64 "%s\n", rd_u32(buf), total, bad ? " (malformed)" : "");
            if (left && skip_bytes(fp, left)) { fprintf(stderr, "error: truncated RUNS\n"); return; }
        } else if (memcmp(head, VGACAP_TAG_BLOCK, 4) == 0) {
            uint8_t buf[8];
            if (length != 8) {
                printf("BLKH bad length=%u\n", length);
                if (skip_bytes(fp, length)) { fprintf(stderr, "error: truncated BLKH\n"); return; }
                continue;
            }
            if (read_exact(fp, buf, 8)) { fprintf(stderr, "error: truncated BLKH\n"); return; }
            printf("BLKH first_sample=%" PRIu64 "\n", rd_u64(buf));
        } else if (memcmp(head, VGACAP_TAG_META, 4) == 0) {
            // The first line names the chunk (meta=open / meta=close).
            char first[64]; size_t n = 0; uint32_t left = length; int c = 0;
            while (left && (c = fgetc(fp)) != EOF) {
                left--;
                if (c == '\n') break;
                if (n < sizeof first - 1) first[n++] = (char)c;
            }
            if (c == EOF) { fprintf(stderr, "error: truncated META\n"); return; }
            first[n] = 0;
            printf("META len=%u first=\"%s\"\n", length, first);
            if (left && skip_bytes(fp, left)) { fprintf(stderr, "error: truncated META\n"); return; }
        } else {
            // Unknown tags are skipped, but a truncated one is still an
            // error and must say so like every other tag does.
            if (skip_bytes(fp, length)) { fprintf(stderr, "error: truncated unknown chunk\n"); return; }
        }
        *vbytes_io = vbytes;
    }
}

struct dump_state {
    uint64_t total; uint32_t hash; uint64_t hash64; int error;
    int v2; unsigned vbytes; uint64_t clock;   // from the HEADER event; clock = the clock bit's mask, or 0
};

static void on_event(void *user, const vgacap_event_t *ev) {
    struct dump_state *s = (struct dump_state *)user;
    if (ev->type == VGACAP_EV_HEADER) {
        const vgacap_header_t *h = ev->u.header;
        s->v2 = h->version == 2; s->vbytes = (h->sample_bits + 7u) / 8u;
        s->clock = h->clock_bit == VGACAP_CLOCK_BIT_NONE ? 0 : (uint64_t)1 << h->clock_bit;
    } else if (ev->type == VGACAP_EV_RUN && s->v2) {
        uint64_t v = ev->u.run.value;
        for (uint64_t i = 0; i < ev->u.run.run; i++, v ^= s->clock)
            for (unsigned k = 0; k < s->vbytes; k++)
                s->hash64 = (s->hash64 ^ (uint8_t)(v >> (8 * k))) * 0x100000001B3u;
        s->total += ev->u.run.run;
    } else if (ev->type == VGACAP_EV_RUN) {
        uint64_t v = ev->u.run.value;
        uint8_t b[4] = { (uint8_t)v, (uint8_t)(v >> 8), (uint8_t)(v >> 16), (uint8_t)(v >> 24) };
        for (uint64_t i = 0; i < ev->u.run.run; i++)
            for (int k = 0; k < 4; k++) s->hash = (s->hash ^ b[k]) * 16777619u;
        s->total += ev->u.run.run;
    } else if (ev->type == VGACAP_EV_RESYNC) {
        // Not an error: chunk framing was lost and regained. Reporting it is
        // the whole point - a capture off a lossy link stays diagnosable, and
        // the sample total below is known to be short by roughly this much.
        printf("resync skipped=%u reason=%s\n",
               (unsigned)ev->u.resync.skipped, ev->u.resync.what ? ev->u.resync.what : "?");
    } else if (ev->type == VGACAP_EV_ERROR) {
        fprintf(stderr, "error: %s\n", ev->u.error.what);
        s->error = 1;
    }
}

static void dump_init(vgacap_reader_t *r, struct dump_state *st) {
    memset(st, 0, sizeof *st);
    st->hash = 0x811C9DC5u; st->hash64 = 0xCBF29CE484222325u;
    vgacap_reader_init(r, on_event, st);
}

// The summary once the whole input has been fed.
static int dump_summary(const vgacap_reader_t *r, const struct dump_state *st) {
    if (st->error) return 1;
    // Input ended while still hunting for a chunk header: the tail is lost
    // and no RESYNC event will ever report it.
    if (r->scanning)
        printf("resync skipped=%u reason=%s (not regained before end of input)\n",
               (unsigned)(r->skipped + r->scanfill), r->resync_what ? r->resync_what : "?");
    if (st->v2) printf("total_samples=%" PRIu64 " crc64=%016" PRIx64 "\n", st->total, st->hash64);
    else printf("total_samples=%" PRIu64 " crc32=%08x\n", st->total, st->hash);
    return 0;
}

// Second pass: feed the real reader to expand runs and compute the
// cross-check hash. Only the final summary line comes from this pass.
static int dump_samples(FILE *fp) {
    static vgacap_reader_t r;
    struct dump_state st;
    dump_init(&r, &st);
    uint8_t buf[65536];
    size_t n;
    while (!st.error && (n = fread(buf, 1, sizeof buf, fp)) > 0) {
        if (vgacap_reader_feed(&r, buf, n)) break;
    }
    return dump_summary(&r, &st);
}

#ifdef VGACAP_DUMP_TRACES
static int dump_trace(const char *path) {
    char err[512];
    vgacap_trace_reader_t *t = vgacap_trace_open(path, err, sizeof err);
    if (!t) { fprintf(stderr, "error: %s\n", err); return 1; }
    const vgacap_trace_info_t *in = vgacap_trace_info(t);
    printf("trace blocks=%u samples=%" PRIu64 " table_rebuilt=%u closed=%u\n",
           in->blocks, in->samples, in->table_rebuilt, in->close_meta ? 1u : 0u);
    unsigned vbytes = 0;
    int rc = 0;
    for (uint32_t i = 0; i < in->blocks && !rc; i++) {
        const uint8_t *p; size_t n;
        if (vgacap_trace_block_data(t, i, &p, &n, err, sizeof err)) { fprintf(stderr, "error: %s\n", err); rc = 1; break; }
        FILE *fp = fmemopen((void *)(uintptr_t)p, n, "rb");
        if (!fp) { fprintf(stderr, "error: fmemopen\n"); rc = 1; break; }
        print_chunks(fp, &vbytes);
        fclose(fp);
    }
    static vgacap_reader_t r;
    struct dump_state st;
    dump_init(&r, &st);
    for (uint32_t i = 0; i < in->blocks && !rc && !st.error; i++) {
        const uint8_t *p; size_t n;
        if (vgacap_trace_block_data(t, i, &p, &n, err, sizeof err)) { fprintf(stderr, "error: %s\n", err); rc = 1; break; }
        if (vgacap_reader_feed(&r, p, n)) break;
    }
    if (!rc) rc = dump_summary(&r, &st);
    vgacap_trace_close(t);
    return rc;
}
#endif

int main(int argc, char **argv) {
    if (argc != 2) { fprintf(stderr, "usage: %s <file>\n", argv[0]); return 1; }
    FILE *fp = fopen(argv[1], "rb");
    if (!fp) { fprintf(stderr, "error: cannot open %s\n", argv[1]); return 1; }
    static const uint8_t zstd_magic[4] = { 0x28, 0xb5, 0x2f, 0xfd };
    uint8_t magic[4];
    if (fread(magic, 1, 4, fp) == 4 && memcmp(magic, zstd_magic, 4) == 0) {
        fclose(fp);
#ifdef VGACAP_DUMP_TRACES
        return dump_trace(argv[1]);
#else
        fprintf(stderr, "vgacap-dump: built without VGACAP_HOST_EXTRAS; decompress with zstd -d first\n");
        return 1;
#endif
    }
    rewind(fp);
    unsigned vbytes = 0;
    print_chunks(fp, &vbytes);
    rewind(fp);
    int rc = dump_samples(fp);
    fclose(fp);
    return rc;
}
