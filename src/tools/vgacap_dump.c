// SPDX-License-Identifier: Apache-2.0
// vgacap-dump: prints a human-readable summary of a capture stream file,
// then a total_samples/crc32 line derived from the expanded RUN events
// (used to cross-check the C reader against the Python mirror).
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
// each payload's fixed fields to report counts.
static int print_chunks(FILE *fp) {
    uint8_t head[8];
    while (fread(head, 1, 8, fp) == 8) {
        uint32_t length = rd_u32(head + 4);
        if (memcmp(head, VGACAP_TAG_HEADER, 4) == 0) {
            uint8_t buf[20];
            if (length < 20 || read_exact(fp, buf, 20)) { fprintf(stderr, "error: truncated VGCH\n"); return 1; }
            uint16_t dl = rd_u16(buf + 18);
            char desc[VGACAP_DESC_MAX + 1];
            if (dl > VGACAP_DESC_MAX || read_exact(fp, desc, dl)) { fprintf(stderr, "error: truncated VGCH desc\n"); return 1; }
            desc[dl] = 0;
            printf("VGCH bits=%u spw=%u flags=%u clock=%u map=%u,%u,%u,%u,%u,%u,%u,%u desc=\"%s\"\n",
                   buf[2], buf[16], buf[17], rd_u32(buf + 4),
                   buf[8], buf[9], buf[10], buf[11], buf[12], buf[13], buf[14], buf[15], desc);
            uint32_t consumed = 20u + dl;
            if (consumed < length && skip_bytes(fp, length - consumed)) return 1;
        } else if (memcmp(head, VGACAP_TAG_RAW, 4) == 0) {
            uint8_t buf[4];
            if (length < 4 || read_exact(fp, buf, 4)) { fprintf(stderr, "error: truncated RAW\n"); return 1; }
            printf("RAW  samples=%u\n", rd_u32(buf));
            if (skip_bytes(fp, length - 4)) return 1;
        } else if (memcmp(head, VGACAP_TAG_RLE, 4) == 0) {
            uint8_t buf[4];
            if (length < 4 || read_exact(fp, buf, 4)) { fprintf(stderr, "error: truncated RLE\n"); return 1; }
            uint32_t pairs = rd_u32(buf);
            uint64_t total = 0;
            for (uint32_t i = 0; i < pairs; i++) {
                uint8_t pair[8];
                if (read_exact(fp, pair, 8)) { fprintf(stderr, "error: truncated RLE pair\n"); return 1; }
                total += rd_u32(pair + 4);
            }
            printf("RLE  pairs=%u runs=%" PRIu64 "\n", pairs, total);
        } else if (memcmp(head, VGACAP_TAG_FRAME, 4) == 0) {
            uint8_t buf[16];
            if (length < 16 || read_exact(fp, buf, 16)) { fprintf(stderr, "error: truncated FRAM\n"); return 1; }
            printf("FRAM counter=%u first=%u lines=%u cpl=%u samples=%u\n",
                   rd_u32(buf), rd_u16(buf + 4), rd_u16(buf + 6), rd_u32(buf + 8), rd_u32(buf + 12));
            if (skip_bytes(fp, length - 16)) return 1;
        } else if (memcmp(head, VGACAP_TAG_EVENT, 4) == 0) {
            uint8_t buf[4];
            if (length < 4 || read_exact(fp, buf, 4)) { fprintf(stderr, "error: truncated EVNT\n"); return 1; }
            printf("EVNT events=%u\n", rd_u32(buf));
            if (skip_bytes(fp, length - 4)) return 1;
        } else if (memcmp(head, VGACAP_TAG_TIME, 4) == 0) {
            uint8_t buf[18];
            if (length < 18 || read_exact(fp, buf, 18)) { fprintf(stderr, "error: truncated TIME\n"); return 1; }
            uint16_t ml = rd_u16(buf + 16);
            char msg[256];
            if (ml > 255 || read_exact(fp, msg, ml)) { fprintf(stderr, "error: truncated TIME msg\n"); return 1; }
            msg[ml] = 0;
            printf("TIME t=%" PRIu64 " clock=%u dropped=%u msg=\"%s\"\n",
                   rd_u64(buf), rd_u32(buf + 8), rd_u32(buf + 12), msg);
        } else {
            if (skip_bytes(fp, length)) return 1;
        }
    }
    return 0;
}

struct dump_state { uint64_t total; uint32_t hash; int error; };

static void on_event(void *user, const vgacap_event_t *ev) {
    struct dump_state *s = (struct dump_state *)user;
    if (ev->type == VGACAP_EV_RUN) {
        uint32_t v = ev->u.run.value;
        uint8_t b[4] = { (uint8_t)v, (uint8_t)(v >> 8), (uint8_t)(v >> 16), (uint8_t)(v >> 24) };
        for (uint32_t i = 0; i < ev->u.run.run; i++)
            for (int k = 0; k < 4; k++) s->hash = (s->hash ^ b[k]) * 16777619u;
        s->total += ev->u.run.run;
    } else if (ev->type == VGACAP_EV_ERROR) {
        fprintf(stderr, "error: %s\n", ev->u.error.what);
        s->error = 1;
    }
}

// Second pass: feed the real reader to expand runs and compute the
// cross-check hash. Only the final summary line comes from this pass.
static int dump_samples(FILE *fp) {
    vgacap_reader_t r;
    struct dump_state st; st.total = 0; st.hash = 0x811C9DC5u; st.error = 0;
    vgacap_reader_init(&r, on_event, &st);
    uint8_t buf[65536];
    size_t n;
    while (!st.error && (n = fread(buf, 1, sizeof buf, fp)) > 0) {
        if (vgacap_reader_feed(&r, buf, n)) break;
    }
    if (st.error) return 1;
    printf("total_samples=%" PRIu64 " crc32=%08x\n", st.total, st.hash);
    return 0;
}

int main(int argc, char **argv) {
    if (argc != 2) { fprintf(stderr, "usage: %s <file>\n", argv[0]); return 1; }
    FILE *fp = fopen(argv[1], "rb");
    if (!fp) { fprintf(stderr, "error: cannot open %s\n", argv[1]); return 1; }
    int rc = print_chunks(fp);
    if (rc == 0) {
        rewind(fp);
        rc = dump_samples(fp);
    }
    fclose(fp);
    return rc;
}
