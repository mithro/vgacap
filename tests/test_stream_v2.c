// SPDX-License-Identifier: Apache-2.0
// VGCH format v2: the 24-byte header, RUNS, BLKH and META, and the rules of
// tt-vga-capture docs/design/2026-11-25-format-v2-runs.md ("the note").
#include "harness.h"
#include "vgacap/stream.h"

// ---- capture and decode helpers -------------------------------------------

static uint8_t outbuf[1u << 16]; static size_t outlen;
static int capture_write(void *u, const uint8_t *b, size_t n) {
    (void)u; if (outlen + n > sizeof outbuf) return -1;
    memcpy(outbuf + outlen, b, n); outlen += n; return 0;
}
static void put(const void *b, size_t n) { capture_write(NULL, (const uint8_t *)b, n); }
static void put_u8(uint8_t v) { put(&v, 1); }
static void put_u16le(uint16_t v) { put_u8((uint8_t)v); put_u8((uint8_t)(v >> 8)); }
static void put_u32le(uint32_t v) { put_u16le((uint16_t)v); put_u16le((uint16_t)(v >> 16)); }
static void put_u64le(uint64_t v) { put_u32le((uint32_t)v); put_u32le((uint32_t)(v >> 32)); }
static void put_head(const char *tag, uint32_t len) { put(tag, 4); put_u32le(len); }

#define MAXEV 4096
static vgacap_event_t evs[MAXEV]; static size_t nev;
static vgacap_header_t hdr_seen;
static char metatext[8][VGACAP_META_MAX + 1];
static size_t nmeta;
static void on_event(void *u, const vgacap_event_t *ev) {
    (void)u;
    if (nev >= MAXEV) return;
    evs[nev] = *ev;
    if (ev->type == VGACAP_EV_HEADER) { hdr_seen = *ev->u.header; evs[nev].u.header = &hdr_seen; }
    if (ev->type == VGACAP_EV_META && nmeta < 8) {
        memcpy(metatext[nmeta], ev->u.meta.text, ev->u.meta.len); metatext[nmeta][ev->u.meta.len] = 0;
        evs[nev].u.meta.text = metatext[nmeta]; nmeta++;
    }
    nev++;
}
static vgacap_reader_t rd;
static void reset_events(void) { nev = 0; nmeta = 0; }
// Byte at a time, so every parser is exercised incrementally.
static int feed_bytes(vgacap_reader_t *r, const uint8_t *b, size_t n) {
    int rc = 0;
    for (size_t i = 0; i < n; i++) if (vgacap_reader_feed(r, b + i, 1)) rc = -1;
    return rc;
}
static int feed_all(void) { reset_events(); vgacap_reader_init(&rd, on_event, NULL); return feed_bytes(&rd, outbuf, outlen); }

static size_t count(vgacap_event_type_t t) { size_t k = 0; for (size_t i = 0; i < nev; i++) k += evs[i].type == t; return k; }
static const vgacap_event_t *nth(vgacap_event_type_t t, size_t n) {
    static vgacap_event_t none;   // a missing event fails its assertions, not the run
    for (size_t i = 0; i < nev; i++) if (evs[i].type == t && n-- == 0) return &evs[i];
    memset(&none, 0, sizeof none); none.type = (vgacap_event_type_t)-1; return &none;
}
static int what_is(const vgacap_event_t *e, const char *s) {
    return e && e->type == VGACAP_EV_RESYNC && e->u.resync.what && strcmp(e->u.resync.what, s) == 0;
}

// A v2 VGCH with every field given; `len_adj` makes the declared chunk
// length differ from 24 + desc_len.
static void put_vgch_v2_raw(uint8_t bits, uint8_t spw, uint8_t spc, uint8_t cb, uint8_t cf, uint8_t rsv,
                            uint16_t desc_len, int len_adj) {
    static const uint8_t map[8] = { 7, 3, 0, 4, 1, 5, 2, 6 };
    put_head("VGCH", (uint32_t)((int)(24u + desc_len) + len_adj));
    put_u16le(2); put_u8(bits); put_u8(2); put_u32le(25175000u); put(map, 8);
    put_u8(spw); put_u8(0); put_u16le(desc_len);
    put_u8(spc); put_u8(cb); put_u8(cf); put_u8(rsv);
    for (uint16_t i = 0; i < desc_len; i++) put_u8('d');
}
// A good v1 header (8 bits, 4 per word) by the library writer.
static void put_v1_header(void) {
    vgacap_header_t h; vgacap_header_init_tinyvga(&h, 8, 4, 0);
    vgacap_writer_t w; vgacap_writer_init(&w, capture_write, NULL, &h);
}
static vgacap_writer_t W;
static void start_v2(uint8_t bits, uint8_t spc, uint8_t cb, uint8_t cf) {
    vgacap_header_t h; vgacap_header_init_v2(&h, bits, spc, cb, cf);
    outlen = 0; vgacap_writer_init(&W, capture_write, NULL, &h);
}
static void start_trace(void) { start_v2(43, 2, 42, 0); }
static void start_v1(void) {
    vgacap_header_t h; vgacap_header_init_tinyvga(&h, 8, 4, 0);
    outlen = 0; vgacap_writer_init(&W, capture_write, NULL, &h);
}

// A RUNS chunk with one pair, written by hand: value as `vb` bytes, then the
// given run bytes.
static void put_runs1(uint64_t value, unsigned vb, const uint8_t *run, size_t runlen) {
    put_head("RUNS", (uint32_t)(4u + vb + runlen)); put_u32le(1);
    for (unsigned i = 0; i < vb; i++) put_u8((uint8_t)(value >> (8 * i)));
    put(run, runlen);
}
static void put_meta(const char *t) { put_head("META", (uint32_t)strlen(t)); put(t, strlen(t)); }
static void writer_runs1(uint64_t value, uint64_t run) { ASSERT_EQ_U(vgacap_writer_runs(&W, &value, &run, 1), 0); }

#define CLK42 (1ull << 42)

// ---- LEB128 ---------------------------------------------------------------

TEST(leb128_vectors) {
    struct { uint64_t v; size_t n; uint8_t b[10]; } t[] = {
        { 1, 1, { 0x01 } }, { 127, 1, { 0x7f } }, { 128, 2, { 0x80, 0x01 } }, { 300, 2, { 0xac, 0x02 } },
        { 1ull << 32, 5, { 0x80, 0x80, 0x80, 0x80, 0x10 } },
        { ~0ull, 10, { 0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0x01 } },
    };
    for (size_t i = 0; i < sizeof t / sizeof t[0]; i++) {
        uint8_t b[10]; memset(b, 0xEE, sizeof b);
        ASSERT_EQ_U(vgacap_leb128_put(b, t[i].v), t[i].n);
        ASSERT_EQ_MEM(b, t[i].b, t[i].n);
    }
    uint8_t p[16];
    ASSERT_EQ_U(vgacap_run_pair_put(p, 0x030000000000ull, 6, 301), 8);
    const uint8_t want[8] = { 0, 0, 0, 0, 0, 0x03, 0xad, 0x02 };
    ASSERT_EQ_MEM(p, want, 8);
}

// ---- header ---------------------------------------------------------------

TEST(header_v2_round_trip) {
    vgacap_header_t h; vgacap_header_init_v2(&h, 43, 2, 42, 0);
    ASSERT_EQ_U(h.version, 2); ASSERT_EQ_U(h.samples_per_word, 0); ASSERT_EQ_U(h.signal_map[VGACAP_SIG_HSYNC], 7);
    h.clock_hz = 25175000; h.mode = VGACAP_MODE_EVENT; strcpy(h.desc, "t"); h.desc_len = 1;
    vgacap_writer_t w; outlen = 0;
    ASSERT_EQ_U(vgacap_writer_init(&w, capture_write, NULL, &h), 0);
    ASSERT_EQ_MEM(outbuf, "VGCH", 4);
    ASSERT_EQ_U(outbuf[4], 25); ASSERT_EQ_U(outlen, 8 + 25);
    ASSERT_EQ_U(feed_all(), 0);
    ASSERT_EQ_U(nev, 1); ASSERT_EQ_U(evs[0].type, VGACAP_EV_HEADER);
    const vgacap_header_t *g = evs[0].u.header;
    ASSERT_EQ_U(g->version, 2); ASSERT_EQ_U(g->sample_bits, 43); ASSERT_EQ_U(g->mode, VGACAP_MODE_EVENT);
    ASSERT_EQ_U(g->clock_hz, 25175000); ASSERT_EQ_U(g->samples_per_word, 0);
    ASSERT_EQ_U(g->samples_per_clock, 2); ASSERT_EQ_U(g->clock_bit, 42); ASSERT_EQ_U(g->clock_first, 0);
    ASSERT_EQ_U(g->desc_len, 1); ASSERT_TRUE(strcmp(g->desc, "t") == 0);
    ASSERT_EQ_MEM(g->signal_map, h.signal_map, 8);
    // At most 32 bits a sample: one sample a word.
    vgacap_header_init_v2(&h, 8, 1, VGACAP_CLOCK_BIT_NONE, 0);
    ASSERT_EQ_U(h.samples_per_word, 1); ASSERT_EQ_U(h.clock_bit, 0xFF);
}

// The note's worked example, byte for byte (59 bytes).
TEST(worked_example_bytes) {
    static const uint8_t want[59] = {
        0x56, 0x47, 0x43, 0x48, 0x18, 0, 0, 0,
        0x02, 0x00, 0x2b, 0x02, 0xd8, 0x23, 0x80, 0x01,
        7, 3, 0, 4, 1, 5, 2, 6,
        0, 0, 0, 0,
        0x02, 0x2a, 0x00, 0x00,
        0x52, 0x55, 0x4e, 0x53, 0x13, 0, 0, 0,
        0x02, 0, 0, 0,
        0, 0, 0, 0, 0, 0x03, 0xad, 0x02,
        0x80, 0, 0, 0, 0, 0x03, 0x01,
    };
    vgacap_header_t h; vgacap_header_init_v2(&h, 43, 2, 42, 0);
    h.mode = VGACAP_MODE_EVENT; h.clock_hz = 25175000;
    outlen = 0; ASSERT_EQ_U(vgacap_writer_init(&W, capture_write, NULL, &h), 0);
    uint64_t v[2] = { 0x030000000000ull, 0x030000000080ull }, r[2] = { 301, 1 };
    ASSERT_EQ_U(vgacap_writer_runs(&W, v, r, 2), 0);
    ASSERT_EQ_U(outlen, 59); ASSERT_EQ_MEM(outbuf, want, 59);
    ASSERT_EQ_U(feed_all(), 0);
    ASSERT_EQ_U(count(VGACAP_EV_RUN), 2);
    ASSERT_EQ_U(nth(VGACAP_EV_RUN, 0)->u.run.value, 0x030000000000ull); ASSERT_EQ_U(nth(VGACAP_EV_RUN, 0)->u.run.run, 301);
    ASSERT_EQ_U(nth(VGACAP_EV_RUN, 1)->u.run.value, 0x070000000080ull); ASSERT_EQ_U(nth(VGACAP_EV_RUN, 1)->u.run.run, 1);
    ASSERT_EQ_U(rd.sample_pos, 302);
}

// One bad v2 header, then a good v1 header: the bad one is rejected by
// resynchronising, reported with `what` once the good one is found.
static void expect_header_resync(const char *what) {
    put_v1_header();
    ASSERT_EQ_U(feed_all(), 0);
    ASSERT_TRUE(nev >= 2);
    ASSERT_TRUE(what_is(&evs[0], what));
    ASSERT_EQ_U(evs[1].type, VGACAP_EV_HEADER); ASSERT_EQ_U(evs[1].u.header->version, 1);
}

TEST(header_v2_rules) {
    // The brief's three cases.
    outlen = 0; put_vgch_v2_raw(43, 0, 1, 42, 0, 0, 0, 0); expect_header_resync("bad VGCH v2 clock fields");
    outlen = 0; put_vgch_v2_raw(43, 0, 1, 0xFF, 1, 0, 0, 0); expect_header_resync("bad VGCH v2 clock fields");
    outlen = 0; put_vgch_v2_raw(43, 0, 2, 42, 0, 7, 0, 0); expect_header_resync("bad VGCH v2 clock fields");
    // Every other row of the note's validity table.
    outlen = 0; put_vgch_v2_raw(43, 0, 2, 0xFF, 0, 0, 0, 0); expect_header_resync("bad VGCH v2 clock fields");
    outlen = 0; put_vgch_v2_raw(43, 0, 3, 42, 0, 0, 0, 0); expect_header_resync("bad VGCH v2 clock fields");
    outlen = 0; put_vgch_v2_raw(43, 0, 0, 0xFF, 0, 0, 0, 0); expect_header_resync("bad VGCH v2 clock fields");
    outlen = 0; put_vgch_v2_raw(43, 0, 2, 43, 0, 0, 0, 0); expect_header_resync("bad VGCH v2 clock fields");
    outlen = 0; put_vgch_v2_raw(43, 0, 2, 42, 2, 0, 0, 0); expect_header_resync("bad VGCH v2 clock fields");
    outlen = 0; put_vgch_v2_raw(0, 1, 1, 0xFF, 0, 0, 0, 0); expect_header_resync("bad sample_bits");
    outlen = 0; put_vgch_v2_raw(65, 0, 1, 0xFF, 0, 0, 0, 0); expect_header_resync("bad sample_bits");
    outlen = 0; put_vgch_v2_raw(43, 1, 1, 0xFF, 0, 0, 0, 0); expect_header_resync("bad samples_per_word");
    outlen = 0; put_vgch_v2_raw(8, 0, 1, 0xFF, 0, 0, 0, 0); expect_header_resync("bad samples_per_word");
    outlen = 0; put_vgch_v2_raw(8, 8, 1, 0xFF, 0, 0, 0, 0); expect_header_resync("bad samples_per_word");
    outlen = 0; put_vgch_v2_raw(8, 3, 1, 0xFF, 0, 0, 0, 0); expect_header_resync("bad samples_per_word");
    outlen = 0; put_vgch_v2_raw(43, 0, 2, 42, 0, 0, 3, 1); expect_header_resync("VGCH desc length mismatch");
    // desc_len <= 251 in version 2: a 279-byte VGCH (255-byte description)
    // is "bad VGCH length", where version 1 accepts its 275-byte maximum.
    outlen = 0; put_vgch_v2_raw(43, 0, 2, 42, 0, 0, 255, 0); expect_header_resync("bad VGCH length");
    outlen = 0; put_vgch_v2_raw(43, 0, 2, 42, 0, 0, 252, 0); expect_header_resync("bad VGCH length");
    // A v2 header shorter than its 24 fixed bytes.
    outlen = 0; put_vgch_v2_raw(43, 0, 2, 42, 0, 0, 0, -2); expect_header_resync("bad VGCH length");
    // The longest legal v2 header, and odd widths that v2 allows.
    outlen = 0; put_vgch_v2_raw(43, 0, 2, 42, 0, 0, 251, 0);
    ASSERT_EQ_U(feed_all(), 0); ASSERT_EQ_U(nev, 1); ASSERT_EQ_U(evs[0].type, VGACAP_EV_HEADER);
    ASSERT_EQ_U(evs[0].u.header->desc_len, 251);
    outlen = 0; put_vgch_v2_raw(5, 4, 1, 0xFF, 0, 0, 0, 0);
    ASSERT_EQ_U(feed_all(), 0); ASSERT_EQ_U(nev, 1); ASSERT_EQ_U(evs[0].type, VGACAP_EV_HEADER);
    outlen = 0; put_vgch_v2_raw(64, 0, 2, 63, 1, 0, 0, 0);
    ASSERT_EQ_U(feed_all(), 0); ASSERT_EQ_U(nev, 1); ASSERT_EQ_U(evs[0].u.header->clock_first, 1);
    // Version 3 is fatal, as version 0 and 9 are today.
    outlen = 0; put_vgch_v2_raw(43, 0, 2, 42, 0, 0, 0, 0); outbuf[8] = 3;
    ASSERT_EQ_U(feed_all(), (unsigned long long)-1);
    ASSERT_EQ_U(evs[nev - 1].type, VGACAP_EV_ERROR);
    ASSERT_TRUE(strcmp(evs[nev - 1].u.error.what, "unsupported version") == 0);
    // The writer will not make a v2 header a v1 reader cannot reject cleanly.
    vgacap_header_t h; vgacap_header_init_v2(&h, 43, 2, 42, 0); h.desc_len = 252;
    outlen = 0; ASSERT_TRUE(vgacap_writer_init(&W, capture_write, NULL, &h) != 0);
}

TEST(header_v1_defaults) {
    vgacap_header_t h; vgacap_header_init_tinyvga(&h, 8, 4, 0);
    ASSERT_EQ_U(h.version, 1); ASSERT_EQ_U(h.samples_per_clock, 1); ASSERT_EQ_U(h.clock_bit, 0xFF); ASSERT_EQ_U(h.clock_first, 0);
    outlen = 0; vgacap_writer_init(&W, capture_write, NULL, &h);
    ASSERT_EQ_U(outbuf[4], 20);   // still a 20-byte v1 header
    uint32_t s[4] = { 1, 2, 3, 4 }, words[1];
    ASSERT_EQ_U(vgacap_writer_raw_samples(&W, s, 4, words, 1), 0);
    // The reader's own header starts as garbage: a memset reader must still
    // report the v1 defaults, not leftovers.
    reset_events(); memset(&rd, 0xA5, sizeof rd); vgacap_reader_init(&rd, on_event, NULL);
    memset(&rd.header, 0xA5, sizeof rd.header);
    ASSERT_EQ_U(feed_bytes(&rd, outbuf, outlen), 0);
    ASSERT_EQ_U(evs[0].type, VGACAP_EV_HEADER);
    ASSERT_EQ_U(evs[0].u.header->samples_per_clock, 1); ASSERT_EQ_U(evs[0].u.header->clock_bit, 0xFF);
    ASSERT_EQ_U(evs[0].u.header->clock_first, 0);
    ASSERT_EQ_U(count(VGACAP_EV_RUN), 4);
    for (size_t i = 0; i < 4; i++) { ASSERT_EQ_U(nth(VGACAP_EV_RUN, i)->u.run.value, i + 1); ASSERT_EQ_U(nth(VGACAP_EV_RUN, i)->u.run.run, 1); }
}

// ---- RUNS -----------------------------------------------------------------

TEST(runs_round_trip_43bit) {
    start_trace();
    uint64_t v[3] = { 0x100000001ull, 0x0FFull, 0x3FFFFFFFFFFull & ~CLK42 };
    uint64_t r[3] = { 1, (1ull << 32) + 5, 3 };
    ASSERT_EQ_U(vgacap_writer_runs(&W, v, r, 3), 0);
    ASSERT_EQ_U(feed_all(), 0);
    ASSERT_EQ_U(count(VGACAP_EV_RESYNC), 0);
    ASSERT_EQ_U(count(VGACAP_EV_RUN), 3);
    const uint64_t first[3] = { 0, 1, (1ull << 32) + 6 };
    for (size_t i = 0; i < 3; i++) {
        const vgacap_event_t *e = nth(VGACAP_EV_RUN, i);
        ASSERT_EQ_U(e->u.run.run, r[i]);
        ASSERT_EQ_U(e->u.run.value & ~CLK42, v[i]);
        ASSERT_EQ_U((e->u.run.value >> 42) & 1, first[i] & 1);
    }
    ASSERT_EQ_U(rd.sample_pos, (1ull << 32) + 9);
}

TEST(runs_clock_first_one) {
    // clock_first = 1 inverts the regenerated clock.
    start_v2(43, 2, 42, 1);
    uint64_t v[2] = { 5, 6 }, r[2] = { 2, 1 };
    ASSERT_EQ_U(vgacap_writer_runs(&W, v, r, 2), 0);
    ASSERT_EQ_U(feed_all(), 0);
    ASSERT_EQ_U(nth(VGACAP_EV_RUN, 0)->u.run.value, 5 | CLK42);   // n = 0: 1 ^ 0
    ASSERT_EQ_U(nth(VGACAP_EV_RUN, 1)->u.run.value, 6 | CLK42);   // n = 2: 1 ^ 0
}

TEST(runs_other_widths) {
    // Board profiles (8 and 12 bits, no clock) and the full 64 bits.
    const uint8_t widths[4] = { 8, 12, 33, 64 };
    for (size_t k = 0; k < 4; k++) {
        uint8_t bits = widths[k];
        start_v2(bits, 1, VGACAP_CLOCK_BIT_NONE, 0);
        uint64_t mask = bits == 64 ? ~0ull : ((1ull << bits) - 1);
        // The last run is the largest that keeps n within 64 bits.
        uint64_t v[3] = { mask, 0, mask >> 1 }, r[3] = { 1, 300, ~0ull - 301u };
        ASSERT_EQ_U(vgacap_writer_runs(&W, v, r, 3), 0);
        ASSERT_EQ_U(outbuf[outlen - 3 * ((bits + 7u) / 8u) - 1 - 2 - 10 - 4 - 8], 'R');
        ASSERT_EQ_U(feed_all(), 0);
        ASSERT_EQ_U(count(VGACAP_EV_RESYNC), 0);
        ASSERT_EQ_U(count(VGACAP_EV_RUN), 3);
        for (size_t i = 0; i < 3; i++) {
            ASSERT_EQ_U(nth(VGACAP_EV_RUN, i)->u.run.value, v[i]);
            ASSERT_EQ_U(nth(VGACAP_EV_RUN, i)->u.run.run, r[i]);
        }
    }
}

// Each RUNS content error: the chunk is skipped by its length (framing kept)
// and reported once as a RESYNC; the RUNS chunk after it still decodes.
static void expect_runs_skip(const char *what, size_t chunk_bytes, size_t runs_before) {
    writer_runs1(0x55, 4);
    ASSERT_EQ_U(feed_all(), 0);
    ASSERT_EQ_U(count(VGACAP_EV_RESYNC), 1);
    const vgacap_event_t *e = nth(VGACAP_EV_RESYNC, 0);
    ASSERT_TRUE(what_is(e, what));
    ASSERT_EQ_U(e->u.resync.skipped, chunk_bytes);
    ASSERT_EQ_U(count(VGACAP_EV_RUN), runs_before + 1);
    ASSERT_EQ_U(nth(VGACAP_EV_RUN, runs_before)->u.run.value & 0xFF, 0x55);
}

// A content error in a clock stream: the chunk is skipped and n becomes
// unknown, so the next RUNS is discarded too.
TEST(runs_reject_clock_bit_set) {
    start_v2(43, 2, 42, 0);
    const uint8_t one = 1;
    put_runs1(CLK42 | 1, 6, &one, 1);
    // A loss in a clock stream: the next RUNS is discarded until a BLKH.
    writer_runs1(0x55, 4);
    ASSERT_EQ_U(feed_all(), 0);
    ASSERT_TRUE(what_is(nth(VGACAP_EV_RESYNC, 0), "RUNS value has the clock bit set"));
    ASSERT_EQ_U(nth(VGACAP_EV_RESYNC, 0)->u.resync.skipped, 8 + 4 + 6 + 1);
    ASSERT_EQ_U(count(VGACAP_EV_RUN), 0);
}

// The rest of the RUNS content errors use streams with no clock bit, where n
// stays known after a loss and the next chunk's run is delivered without a
// BLKH (expect_runs_skip).
TEST(runs_reject_overlong_leb) {
    start_v2(43, 1, VGACAP_CLOCK_BIT_NONE, 0);
    const uint8_t nonmin[3] = { 0x81, 0x80, 0x00 };
    put_runs1(1, 6, nonmin, 3);
    expect_runs_skip("RUNS run not minimally encoded", 8 + 4 + 6 + 3, 0);
    start_v2(43, 1, VGACAP_CLOCK_BIT_NONE, 0);
    const uint8_t nonmin2[2] = { 0x80, 0x00 };
    put_runs1(1, 6, nonmin2, 2);
    expect_runs_skip("RUNS run not minimally encoded", 8 + 4 + 6 + 2, 0);
    start_v2(43, 1, VGACAP_CLOCK_BIT_NONE, 0);
    const uint8_t over[10] = { 0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0x02 };
    put_runs1(1, 6, over, 10);
    expect_runs_skip("RUNS run over 64 bits", 8 + 4 + 6 + 10, 0);
    // An 11th byte (continuation bit set on the 10th).
    start_v2(43, 1, VGACAP_CLOCK_BIT_NONE, 0);
    const uint8_t eleven[11] = { 0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0x81, 0x01 };
    put_runs1(1, 6, eleven, 11);
    expect_runs_skip("RUNS run over 64 bits", 8 + 4 + 6 + 11, 0);
}

TEST(runs_reject_padding_bits) {
    start_v2(43, 1, VGACAP_CLOCK_BIT_NONE, 0);
    const uint8_t one = 1;
    put_runs1(1ull << 43, 6, &one, 1);
    expect_runs_skip("RUNS value has bits above sample_bits", 8 + 4 + 6 + 1, 0);
    start_v2(12, 1, VGACAP_CLOCK_BIT_NONE, 0);
    put_runs1(1ull << 15, 2, &one, 1);
    expect_runs_skip("RUNS value has bits above sample_bits", 8 + 4 + 2 + 1, 0);
}

TEST(runs_reject_zero_run) {
    start_v2(43, 1, VGACAP_CLOCK_BIT_NONE, 0);
    const uint8_t zero = 0;
    put_runs1(1, 6, &zero, 1);
    expect_runs_skip("RUNS run is zero", 8 + 4 + 6 + 1, 0);
}

// n is a u64: a run whose end would take it past 2^64 - 1 is a RUNS content
// error, skipped by length, never a wrap. A run ending exactly at 2^64 - 1
// is delivered.
TEST(runs_reject_n_past_64_bits) {
    // BLKH puts n at 2^64 - 3: a run of 2 ends at 2^64 - 1, a run of 3 would wrap.
    start_v2(43, 1, VGACAP_CLOCK_BIT_NONE, 0);
    ASSERT_EQ_U(vgacap_writer_block(&W, UINT64_MAX - 2u), 0);
    const uint8_t three = 3;
    put_runs1(1, 6, &three, 1);
    ASSERT_EQ_U(feed_all(), 0);
    // Resync 0 is the BLKH gap report (n was 0), then the RUNS skip.
    ASSERT_EQ_U(count(VGACAP_EV_RUN), 0);
    ASSERT_EQ_U(count(VGACAP_EV_RESYNC), 2);
    ASSERT_TRUE(what_is(nth(VGACAP_EV_RESYNC, 0), "BLKH first_sample does not follow"));
    ASSERT_TRUE(what_is(nth(VGACAP_EV_RESYNC, 1), "RUNS run takes n past 64 bits"));
    ASSERT_EQ_U(nth(VGACAP_EV_RESYNC, 1)->u.resync.skipped, 8 + 4 + 6 + 1);
    ASSERT_EQ_U(rd.sample_pos, UINT64_MAX - 2u);
    start_v2(43, 1, VGACAP_CLOCK_BIT_NONE, 0);
    ASSERT_EQ_U(vgacap_writer_block(&W, UINT64_MAX - 2u), 0);
    writer_runs1(1, 2);
    ASSERT_EQ_U(feed_all(), 0);
    ASSERT_EQ_U(count(VGACAP_EV_RESYNC), 1);   // the BLKH gap report only
    ASSERT_EQ_U(count(VGACAP_EV_RUN), 1);
    ASSERT_EQ_U(rd.sample_pos, UINT64_MAX);
    // Mid-chunk, from n = 0: pair 1 (2^64 - 1 samples) is delivered, pair 2
    // would wrap. The whole chunk is reported; in a clock stream n is then
    // unknown and the next RUNS is discarded until a BLKH.
    start_trace();
    uint64_t values[2] = { 1, 2 }, runs[2] = { UINT64_MAX, 1 };
    ASSERT_EQ_U(vgacap_writer_runs(&W, values, runs, 2), 0);
    writer_runs1(5, 1);
    ASSERT_EQ_U(feed_all(), 0);
    ASSERT_EQ_U(count(VGACAP_EV_RUN), 1);
    ASSERT_EQ_U(nth(VGACAP_EV_RUN, 0)->u.run.run, UINT64_MAX);
    ASSERT_TRUE(what_is(nth(VGACAP_EV_RESYNC, 0), "RUNS run takes n past 64 bits"));
    ASSERT_EQ_U(nth(VGACAP_EV_RESYNC, 0)->u.resync.skipped, 8 + 4 + (6 + 10) + (6 + 1));
    ASSERT_TRUE(what_is(nth(VGACAP_EV_RESYNC, 1), "RUNS discarded: sample position unknown until BLKH"));
}

TEST(runs_length_mismatch) {
    // One byte too many after the last pair.
    start_v2(43, 1, VGACAP_CLOCK_BIT_NONE, 0);
    put_head("RUNS", 4 + 7 + 1); put_u32le(1); put_u64le(0); outlen -= 2; put_u8(1); put_u8(0);
    expect_runs_skip("RUNS pairs do not fill the chunk", 8 + 12, 1);
    // The chunk ends inside a pair (pair_count says 2, one is present).
    start_v2(43, 1, VGACAP_CLOCK_BIT_NONE, 0);
    put_head("RUNS", 4 + 7); put_u32le(2); put_u64le(0); outlen -= 2; put_u8(1);
    expect_runs_skip("RUNS pairs do not fill the chunk", 8 + 11, 0);
    // The chunk ends inside a run's LEB128.
    start_v2(43, 1, VGACAP_CLOCK_BIT_NONE, 0);
    const uint8_t cont = 0x80;
    put_runs1(1, 6, &cont, 1);
    expect_runs_skip("RUNS pairs do not fill the chunk", 8 + 11, 0);
    // Shorter than pair_count itself.
    start_v2(43, 1, VGACAP_CLOCK_BIT_NONE, 0);
    put_head("RUNS", 2); put_u16le(1);
    expect_runs_skip("RUNS pairs do not fill the chunk", 8 + 2, 0);
}

TEST(runs_two_faults_early_bound_first) {
    // pair_count 5 that the length cannot hold, and pair 1's clock bit set:
    // the count is judged first (the Python mirror must agree).
    start_trace();
    put_head("RUNS", 4 + 7); put_u32le(5); put_u64le(CLK42 | 1); outlen -= 2; put_u8(1);
    ASSERT_EQ_U(feed_all(), 0);
    ASSERT_EQ_U(count(VGACAP_EV_RESYNC), 1);
    ASSERT_TRUE(what_is(nth(VGACAP_EV_RESYNC, 0), "RUNS pairs do not fill the chunk"));
}

TEST(runs_reject_no_pairs) {
    start_v2(43, 1, VGACAP_CLOCK_BIT_NONE, 0);
    put_head("RUNS", 4); put_u32le(0);
    expect_runs_skip("RUNS has no pairs", 8 + 4, 0);
}

TEST(runs_adjacent_pairs) {
    // Equal values next to each other inside one chunk are malformed...
    start_v2(8, 1, VGACAP_CLOCK_BIT_NONE, 0);
    put_head("RUNS", 4 + 2 + 2); put_u32le(2); put_u8(9); put_u8(3); put_u8(9); put_u8(4);
    expect_runs_skip("RUNS adjacent pairs have equal values", 8 + 8, 1);
    // ...but allowed across a chunk boundary.
    start_trace();
    writer_runs1(9, 3); writer_runs1(9, 4);
    ASSERT_EQ_U(feed_all(), 0);
    ASSERT_EQ_U(count(VGACAP_EV_RESYNC), 0); ASSERT_EQ_U(count(VGACAP_EV_RUN), 2);
    ASSERT_EQ_U(nth(VGACAP_EV_RUN, 1)->u.run.value, 9 | CLK42);   // n = 3
}

TEST(runs_writer_refuses_bad_pairs) {
    start_trace(); size_t at = outlen;
    uint64_t v[2] = { 1, 1 }, r[2] = { 1, 1 }, zero = 0, one = 1, big = 1ull << 43, clk = CLK42;
    ASSERT_TRUE(vgacap_writer_runs(&W, v, r, 2) != 0);       // adjacent equal
    ASSERT_TRUE(vgacap_writer_runs(&W, &one, &zero, 1) != 0); // run 0
    ASSERT_TRUE(vgacap_writer_runs(&W, &big, &one, 1) != 0);  // above sample_bits
    ASSERT_TRUE(vgacap_writer_runs(&W, &clk, &one, 1) != 0);  // clock bit set
    ASSERT_TRUE(vgacap_writer_runs(&W, v, r, 0) != 0);        // no pairs
    ASSERT_EQ_U(outlen, at);                                  // nothing written
    // RUNS, BLKH and META need a v2 header.
    start_v1(); at = outlen;
    ASSERT_TRUE(vgacap_writer_runs(&W, &one, &one, 1) != 0);
    ASSERT_TRUE(vgacap_writer_block(&W, 0) != 0);
    ASSERT_TRUE(vgacap_writer_meta(&W, "meta=close\n", 11) != 0);
    ASSERT_EQ_U(outlen, at);
}

// ---- which chunks a header allows ------------------------------------------

TEST(runs_need_v2_header) {
    start_v1();
    put_runs1(1, 1, (const uint8_t *)"\x01", 1);
    put_head("BLKH", 8); put_u64le(0);
    put_head("META", 11); put("meta=close\n", 11);
    uint32_t v = 3, n = 2; vgacap_writer_rle(&W, &v, &n, 1);
    ASSERT_EQ_U(feed_all(), 0);
    ASSERT_EQ_U(count(VGACAP_EV_RESYNC), 3);
    for (size_t i = 0; i < 3; i++) ASSERT_TRUE(what_is(nth(VGACAP_EV_RESYNC, i), "chunk needs a version 2 header"));
    ASSERT_EQ_U(nth(VGACAP_EV_RESYNC, 0)->u.resync.skipped, 8 + 6);
    ASSERT_EQ_U(nth(VGACAP_EV_RESYNC, 1)->u.resync.skipped, 8 + 8);
    ASSERT_EQ_U(nth(VGACAP_EV_RESYNC, 2)->u.resync.skipped, 8 + 11);
    ASSERT_EQ_U(count(VGACAP_EV_BLOCK), 0); ASSERT_EQ_U(count(VGACAP_EV_META), 0);
    ASSERT_EQ_U(count(VGACAP_EV_RUN), 1); ASSERT_EQ_U(nth(VGACAP_EV_RUN, 0)->u.run.value, 3);
}

TEST(rle_rejected_in_v2) {
    const uint8_t cbs[2] = { VGACAP_CLOCK_BIT_NONE, 42 };
    for (size_t k = 0; k < 2; k++) {
        if (cbs[k] == 42) start_trace(); else start_v2(8, 1, VGACAP_CLOCK_BIT_NONE, 0);
        put_head("RLE ", 12); put_u32le(1); put_u32le(3); put_u32le(2);
        put_head("TIME", 18); put_u64le(1); put_u32le(0); put_u32le(0); put_u16le(0);
        ASSERT_EQ_U(feed_all(), 0);
        ASSERT_EQ_U(count(VGACAP_EV_RESYNC), 1);
        ASSERT_TRUE(what_is(nth(VGACAP_EV_RESYNC, 0), "RLE  chunk not allowed in version 2"));
        ASSERT_EQ_U(nth(VGACAP_EV_RESYNC, 0)->u.resync.skipped, 8 + 12);
        ASSERT_EQ_U(count(VGACAP_EV_RUN), 0); ASSERT_EQ_U(count(VGACAP_EV_TIME), 1);
    }
}

TEST(clock_stream_rejects_raw) {
    start_trace();
    put_head("RAW ", 8); put_u32le(1); put_u32le(5);
    put_head("FRAM", 16); put_u32le(0); put_u32le(0); put_u32le(0); put_u32le(0);
    put_head("EVNT", 4); put_u32le(0);
    put_head("XYZW", 3); put_u8(1); put_u8(2); put_u8(3);   // unknown tags too
    put_head("RAW ", 2); put_u16le(0);                       // and a short one
    put_head("TIME", 18); put_u64le(1); put_u32le(0); put_u32le(0); put_u16le(0);
    ASSERT_EQ_U(feed_all(), 0);
    ASSERT_EQ_U(count(VGACAP_EV_RESYNC), 5);
    const uint32_t sk[5] = { 16, 24, 12, 11, 10 };
    for (size_t i = 0; i < 5; i++) {
        ASSERT_TRUE(what_is(nth(VGACAP_EV_RESYNC, i), "chunk not allowed with a sample clock"));
        ASSERT_EQ_U(nth(VGACAP_EV_RESYNC, i)->u.resync.skipped, sk[i]);
    }
    ASSERT_EQ_U(count(VGACAP_EV_RUN), 0); ASSERT_EQ_U(count(VGACAP_EV_FRAME_BEGIN), 0);
    ASSERT_EQ_U(count(VGACAP_EV_TIME), 1);
    // Short forbidden chunks are reported by the ban, not by their length.
    start_trace(); put_head("RLE ", 2); put_u16le(0);
    ASSERT_EQ_U(feed_all(), 0);
    ASSERT_TRUE(what_is(nth(VGACAP_EV_RESYNC, 0), "RLE  chunk not allowed in version 2"));
    ASSERT_EQ_U(nth(VGACAP_EV_RESYNC, 0)->u.resync.skipped, 10);
    start_v2(8, 1, VGACAP_CLOCK_BIT_NONE, 0); put_head("RLE ", 2); put_u16le(0);
    ASSERT_EQ_U(feed_all(), 0);
    ASSERT_TRUE(what_is(nth(VGACAP_EV_RESYNC, 0), "RLE  chunk not allowed in version 2"));
    start_v2(43, 1, VGACAP_CLOCK_BIT_NONE, 0); put_head("RAW ", 2); put_u16le(0);
    ASSERT_EQ_U(feed_all(), 0);
    ASSERT_TRUE(what_is(nth(VGACAP_EV_RESYNC, 0), "chunk not allowed with sample_bits over 32"));
    // The clock-bit check applies before the sample_bits one: a 16-bit
    // stream with a clock bit says "sample clock" too.
    start_v2(16, 2, 15, 0);
    put_head("RAW ", 8); put_u32le(1); put_u32le(5);
    ASSERT_EQ_U(feed_all(), 0);
    ASSERT_TRUE(what_is(nth(VGACAP_EV_RESYNC, 0), "chunk not allowed with a sample clock"));
}

TEST(wide_stream_rejects_raw) {
    start_v2(43, 1, VGACAP_CLOCK_BIT_NONE, 0);
    put_head("RAW ", 8); put_u32le(1); put_u32le(5);
    put_head("FRAM", 16); put_u32le(0); put_u32le(0); put_u32le(0); put_u32le(0);
    put_head("EVNT", 4); put_u32le(0);
    put_head("XYZW", 1); put_u8(1);   // unknown tags are still skipped silently here
    writer_runs1(0x55, 4);
    ASSERT_EQ_U(feed_all(), 0);
    ASSERT_EQ_U(count(VGACAP_EV_RESYNC), 3);
    for (size_t i = 0; i < 3; i++)
        ASSERT_TRUE(what_is(nth(VGACAP_EV_RESYNC, i), "chunk not allowed with sample_bits over 32"));
    ASSERT_EQ_U(count(VGACAP_EV_RUN), 1);   // no clock bit: n stays usable
}

TEST(v2_narrow_stream_allows_raw) {
    // v2, 12 bits, no clock: RAW/FRAM/EVNT decode as in v1 and advance n.
    vgacap_header_t h; vgacap_header_init_v2(&h, 12, 1, VGACAP_CLOCK_BIT_NONE, 0); h.samples_per_word = 2;
    outlen = 0; ASSERT_EQ_U(vgacap_writer_init(&W, capture_write, NULL, &h), 0);
    uint32_t s[3] = { 0xABC, 0x123, 0xFFF }, words[2];
    ASSERT_EQ_U(vgacap_writer_raw_samples(&W, s, 3, words, 2), 0);
    uint64_t c[2] = { 0, 10 }; uint32_t ev[2] = { 1, 2 };
    ASSERT_EQ_U(vgacap_writer_events(&W, c, ev, 2), 0);
    uint64_t v = 7, r = 5; ASSERT_EQ_U(vgacap_writer_runs(&W, &v, &r, 1), 0);
    ASSERT_EQ_U(vgacap_writer_block(&W, 19), 0);
    ASSERT_EQ_U(feed_all(), 0);
    ASSERT_EQ_U(count(VGACAP_EV_RESYNC), 0);
    ASSERT_EQ_U(count(VGACAP_EV_RUN), 6);
    ASSERT_EQ_U(nth(VGACAP_EV_RUN, 1)->u.run.value, 0x123);
    ASSERT_EQ_U(nth(VGACAP_EV_BLOCK, 0)->u.block.expected, 3 + 11 + 5);
}

// ---- BLKH and the sample index n --------------------------------------------

TEST(block_sets_position) {
    start_trace();
    uint64_t v[2] = { 1, 2 }, r[2] = { 4, 6 };
    ASSERT_EQ_U(vgacap_writer_runs(&W, v, r, 2), 0);          // samples 0..9
    ASSERT_EQ_U(vgacap_writer_block(&W, 1000), 0);
    uint64_t v2[2] = { 3, 4 }, r2[2] = { 1, 1 };
    ASSERT_EQ_U(vgacap_writer_runs(&W, v2, r2, 2), 0);        // samples 1000, 1001
    ASSERT_EQ_U(vgacap_writer_block(&W, 1002), 0);            // follows: no report
    ASSERT_EQ_U(feed_all(), 0);
    ASSERT_EQ_U(count(VGACAP_EV_BLOCK), 2);
    const vgacap_event_t *b = nth(VGACAP_EV_BLOCK, 0);
    ASSERT_EQ_U(b->u.block.first_sample, 1000); ASSERT_EQ_U(b->u.block.expected, 10);
    // The gap is reported first, with nothing skipped, then adopted.
    ASSERT_EQ_U(count(VGACAP_EV_RESYNC), 1);
    const vgacap_event_t *g = nth(VGACAP_EV_RESYNC, 0);
    ASSERT_TRUE(what_is(g, "BLKH first_sample does not follow")); ASSERT_EQ_U(g->u.resync.skipped, 0);
    ASSERT_TRUE(g < b);
    ASSERT_EQ_U(nth(VGACAP_EV_RUN, 2)->u.run.value, 3);           // n = 1000: clock 0
    ASSERT_EQ_U(nth(VGACAP_EV_RUN, 3)->u.run.value, 4 | CLK42);   // n = 1001: clock 1
    ASSERT_EQ_U(nth(VGACAP_EV_BLOCK, 1)->u.block.expected, 1002);
    ASSERT_EQ_U(rd.sample_pos, 1002);
    // An overlap is reported the same way.
    start_trace(); writer_runs1(1, 10); ASSERT_EQ_U(vgacap_writer_block(&W, 3), 0); writer_runs1(2, 1);
    ASSERT_EQ_U(feed_all(), 0);
    ASSERT_TRUE(what_is(nth(VGACAP_EV_RESYNC, 0), "BLKH first_sample does not follow"));
    ASSERT_EQ_U(nth(VGACAP_EV_RUN, 1)->u.run.value, 2 | CLK42);   // n = 3
}

TEST(bad_blkh_length) {
    start_trace();
    put_head("BLKH", 4); put_u32le(0);
    put_head("BLKH", 9); put_u64le(0); put_u8(0);
    ASSERT_EQ_U(vgacap_writer_block(&W, 0), 0);
    writer_runs1(1, 1);
    ASSERT_EQ_U(feed_all(), 0);
    ASSERT_EQ_U(count(VGACAP_EV_RESYNC), 2);
    ASSERT_TRUE(what_is(nth(VGACAP_EV_RESYNC, 0), "bad BLKH length")); ASSERT_EQ_U(nth(VGACAP_EV_RESYNC, 0)->u.resync.skipped, 12);
    ASSERT_TRUE(what_is(nth(VGACAP_EV_RESYNC, 1), "bad BLKH length")); ASSERT_EQ_U(nth(VGACAP_EV_RESYNC, 1)->u.resync.skipped, 17);
    ASSERT_EQ_U(count(VGACAP_EV_BLOCK), 1);
    ASSERT_EQ_U(count(VGACAP_EV_RUN), 1);
    // Zero-length chunks are skipped and reported too.
    start_trace(); put_head("BLKH", 0); writer_runs1(1, 1);
    ASSERT_EQ_U(feed_all(), 0);
    ASSERT_TRUE(what_is(nth(VGACAP_EV_RESYNC, 0), "bad BLKH length")); ASSERT_EQ_U(nth(VGACAP_EV_RESYNC, 0)->u.resync.skipped, 8);
}

TEST(clock_loss_discards_until_blkh) {
    start_trace();
    writer_runs1(1, 5);                                    // n 0..4, delivered
    put_runs1(2, 6, (const uint8_t *)"\x00", 1);           // run 0: a loss
    writer_runs1(3, 7);                                    // n unknown: discarded
    writer_runs1(4, 2);                                    // discarded
    put_head("TIME", 18); put_u64le(1); put_u32le(0); put_u32le(0); put_u16le(0);   // not a run: delivered
    ASSERT_EQ_U(vgacap_writer_block(&W, 101), 0);          // n known again, no gap report
    writer_runs1(5, 1);                                    // n = 101: clock 1
    ASSERT_EQ_U(feed_all(), 0);
    ASSERT_EQ_U(count(VGACAP_EV_RUN), 2);
    ASSERT_EQ_U(nth(VGACAP_EV_RUN, 0)->u.run.value, 1);
    ASSERT_EQ_U(nth(VGACAP_EV_RUN, 1)->u.run.value, 5 | CLK42);
    ASSERT_EQ_U(count(VGACAP_EV_RESYNC), 3);
    ASSERT_TRUE(what_is(nth(VGACAP_EV_RESYNC, 0), "RUNS run is zero"));
    // The discarded chunks are counted in the loss report.
    ASSERT_TRUE(what_is(nth(VGACAP_EV_RESYNC, 1), "RUNS discarded: sample position unknown until BLKH"));
    ASSERT_EQ_U(nth(VGACAP_EV_RESYNC, 1)->u.resync.skipped, 8 + 4 + 7);
    ASSERT_TRUE(what_is(nth(VGACAP_EV_RESYNC, 2), "RUNS discarded: sample position unknown until BLKH"));
    ASSERT_EQ_U(count(VGACAP_EV_TIME), 1); ASSERT_EQ_U(count(VGACAP_EV_BLOCK), 1);
    ASSERT_EQ_U(rd.sample_pos, 102);

    // A byte-wise framing loss does the same.
    start_trace();
    writer_runs1(1, 5);
    put_head("RUNS", 0x7FFFFFFFu);                         // implausible length
    writer_runs1(3, 7);
    ASSERT_EQ_U(vgacap_writer_block(&W, 6), 0);
    writer_runs1(5, 1);
    ASSERT_EQ_U(feed_all(), 0);
    ASSERT_EQ_U(count(VGACAP_EV_RUN), 2);
    ASSERT_EQ_U(nth(VGACAP_EV_RUN, 1)->u.run.value, 5);    // n = 6: clock 0
    ASSERT_TRUE(what_is(nth(VGACAP_EV_RESYNC, 0), "chunk length too large"));
    ASSERT_TRUE(what_is(nth(VGACAP_EV_RESYNC, 1), "RUNS discarded: sample position unknown until BLKH"));
    ASSERT_EQ_U(count(VGACAP_EV_RESYNC), 2);

    // A stream with no clock bit keeps delivering.
    start_v2(43, 1, VGACAP_CLOCK_BIT_NONE, 0);
    put_runs1(2, 6, (const uint8_t *)"\x00", 1);
    writer_runs1(3, 7);
    ASSERT_EQ_U(feed_all(), 0);
    ASSERT_EQ_U(count(VGACAP_EV_RUN), 1); ASSERT_EQ_U(count(VGACAP_EV_RESYNC), 1);
}

// Ruling K: in a clock stream n becomes unknown only after a loss that could
// have carried samples. META and TIME losses leave it known.
TEST(clock_n_after_non_runs_skips) {
    // A bad META (text, then length): every later RUNS is delivered.
    start_trace();
    writer_runs1(1, 5);
    put_meta("meta=open\n");                               // bad META text
    writer_runs1(2, 7);
    put_head("META", 0);                                    // bad META length
    writer_runs1(3, 9);
    ASSERT_EQ_U(feed_all(), 0);
    ASSERT_EQ_U(count(VGACAP_EV_RESYNC), 2);
    ASSERT_TRUE(what_is(nth(VGACAP_EV_RESYNC, 0), "bad META text"));
    ASSERT_TRUE(what_is(nth(VGACAP_EV_RESYNC, 1), "bad META length"));
    ASSERT_EQ_U(count(VGACAP_EV_RUN), 3);
    ASSERT_EQ_U(nth(VGACAP_EV_RUN, 1)->u.run.value, 2 | CLK42);   // n = 5
    ASSERT_EQ_U(nth(VGACAP_EV_RUN, 2)->u.run.value, 3);           // n = 12: clock 0
    ASSERT_EQ_U(rd.sample_pos, 21);
    // A TIME whose msg_len is wrong: a byte-wise resync that regains framing
    // exactly at the TIME chunk's end lost only the TIME, so n stays known.
    start_trace();
    writer_runs1(1, 5);
    put_head("TIME", 18); put_u64le(1); put_u32le(0); put_u32le(0); put_u16le(20);
    writer_runs1(2, 7);
    ASSERT_EQ_U(feed_all(), 0);
    ASSERT_EQ_U(count(VGACAP_EV_RESYNC), 1);
    ASSERT_TRUE(what_is(nth(VGACAP_EV_RESYNC, 0), "TIME msg_len does not match chunk length"));
    ASSERT_EQ_U(count(VGACAP_EV_RUN), 2);
    ASSERT_EQ_U(rd.sample_pos, 12);
    // A forbidden chunk: later RUNS are discarded until the next BLKH.
    const char *forbid[3] = { "RAW ", "RLE ", "XYZW" };
    for (size_t k = 0; k < 3; k++) {
        start_trace();
        writer_runs1(1, 5);
        put_head(forbid[k], 8); put_u32le(1); put_u32le(5);
        writer_runs1(2, 7);
        ASSERT_EQ_U(vgacap_writer_block(&W, 12), 0);
        writer_runs1(3, 1);
        ASSERT_EQ_U(feed_all(), 0);
        ASSERT_EQ_U(count(VGACAP_EV_RUN), 2);
        ASSERT_TRUE(what_is(nth(VGACAP_EV_RESYNC, 1), "RUNS discarded: sample position unknown until BLKH"));
        ASSERT_EQ_U(count(VGACAP_EV_RESYNC), 2);   // the ban, the discard; the BLKH is silent
    }
    // A bad BLKH: the same.
    start_trace();
    writer_runs1(1, 5);
    put_head("BLKH", 4); put_u32le(0);
    writer_runs1(2, 7);
    ASSERT_EQ_U(vgacap_writer_block(&W, 12), 0);
    writer_runs1(3, 1);
    ASSERT_EQ_U(feed_all(), 0);
    ASSERT_EQ_U(count(VGACAP_EV_RUN), 2);
    ASSERT_TRUE(what_is(nth(VGACAP_EV_RESYNC, 0), "bad BLKH length"));
    ASSERT_TRUE(what_is(nth(VGACAP_EV_RESYNC, 1), "RUNS discarded: sample position unknown until BLKH"));
    ASSERT_EQ_U(count(VGACAP_EV_RESYNC), 2);
}

TEST(init_header_decodes_block) {
    // Block 1 of a trace, decoded on its own: BLKH + RUNS, no VGCH.
    vgacap_header_t h; vgacap_header_init_v2(&h, 43, 2, 42, 0);
    start_trace(); size_t hl = outlen;
    ASSERT_EQ_U(vgacap_writer_block(&W, 777), 0);
    uint64_t v[2] = { 0x10, 0x20 }, r[2] = { 2, 1 };
    ASSERT_EQ_U(vgacap_writer_runs(&W, v, r, 2), 0);
    reset_events(); vgacap_reader_init_header(&rd, on_event, NULL, &h);
    ASSERT_EQ_U(feed_bytes(&rd, outbuf + hl, outlen - hl), 0);
    ASSERT_EQ_U(count(VGACAP_EV_HEADER), 0);
    ASSERT_EQ_U(count(VGACAP_EV_RESYNC), 0);   // a seek reports nothing
    ASSERT_EQ_U(count(VGACAP_EV_BLOCK), 1); ASSERT_EQ_U(nth(VGACAP_EV_BLOCK, 0)->u.block.first_sample, 777);
    ASSERT_EQ_U(nth(VGACAP_EV_RUN, 0)->u.run.value, 0x10 | CLK42);   // n = 777
    ASSERT_EQ_U(nth(VGACAP_EV_RUN, 1)->u.run.value, 0x20 | CLK42);   // n = 779
    ASSERT_EQ_U(rd.sample_pos, 780);
    // Before its BLKH such a reader does not know n: runs wait for one.
    reset_events(); vgacap_reader_init_header(&rd, on_event, NULL, &h);
    ASSERT_EQ_U(feed_bytes(&rd, outbuf + hl + 16, outlen - hl - 16), 0);
    ASSERT_EQ_U(count(VGACAP_EV_RUN), 0);
    ASSERT_TRUE(what_is(nth(VGACAP_EV_RESYNC, 0), "RUNS discarded: sample position unknown until BLKH"));
}

// ---- META -----------------------------------------------------------------

static const char *OPEN_OK =
    "meta=open\nproject=tt_um_x\ncalib_seconds=0.5\nseconds=2\nlayout=tt-um-43\n";


TEST(meta_event) {
    const char *texts[4] = {
        OPEN_OK,
        "meta=open\nproject=x y=z\ncalib_seconds=-1.5e-3\nseconds=.25\nlayout=tt-um-43\n"
        "override=\nsimulator=verilator 5\nharness=h\nfuture_key=anything at all\n",
        "meta=close\ncapture_after_clock=123456789012\nstatus=ok\nframes=0\n",
        "meta=close\nstatus=sim-timeout\nframes=3\ncapture_after_clock=0\n",
    };
    start_trace();
    for (size_t i = 0; i < 4; i++) ASSERT_EQ_U(vgacap_writer_meta(&W, texts[i], strlen(texts[i])), 0);
    ASSERT_EQ_U(feed_all(), 0);
    ASSERT_EQ_U(count(VGACAP_EV_RESYNC), 0);
    ASSERT_EQ_U(count(VGACAP_EV_META), 4);
    for (size_t i = 0; i < 4; i++) {
        ASSERT_EQ_U(nth(VGACAP_EV_META, i)->u.meta.len, strlen(texts[i]));
        ASSERT_TRUE(strcmp(nth(VGACAP_EV_META, i)->u.meta.text, texts[i]) == 0);
    }
    // 4096 bytes is the most; 0 and 4097 are "bad META length".
    static char big[VGACAP_META_MAX + 2];
    strcpy(big, "meta=close\nstatus=ok\nframes=1\ncapture_after_clock=1\nproject=");
    size_t n = strlen(big); memset(big + n, 'a', VGACAP_META_MAX - n - 1); big[VGACAP_META_MAX - 1] = '\n';
    start_trace();
    ASSERT_EQ_U(vgacap_writer_meta(&W, big, VGACAP_META_MAX), 0);
    put_head("META", 0);
    put_head("META", VGACAP_META_MAX + 1); put(big, VGACAP_META_MAX); put_u8('\n');
    ASSERT_TRUE(vgacap_writer_meta(&W, big, 0) != 0);
    ASSERT_TRUE(vgacap_writer_meta(&W, big, VGACAP_META_MAX + 1) != 0);
    ASSERT_EQ_U(feed_all(), 0);
    ASSERT_EQ_U(count(VGACAP_EV_META), 1); ASSERT_EQ_U(nth(VGACAP_EV_META, 0)->u.meta.len, VGACAP_META_MAX);
    ASSERT_EQ_U(count(VGACAP_EV_RESYNC), 2);
    ASSERT_TRUE(what_is(nth(VGACAP_EV_RESYNC, 0), "bad META length")); ASSERT_EQ_U(nth(VGACAP_EV_RESYNC, 0)->u.resync.skipped, 8);
    ASSERT_TRUE(what_is(nth(VGACAP_EV_RESYNC, 1), "bad META length")); ASSERT_EQ_U(nth(VGACAP_EV_RESYNC, 1)->u.resync.skipped, 8 + 4097);
}

TEST(meta_bad_text) {
    static const char *bad[] = {
        "meta=open\nproject=x\ncalib_seconds=1\nseconds=2\nlayout=tt-um-43",      // no final \n
        "project=x\nmeta=open\ncalib_seconds=1\nseconds=2\nlayout=tt-um-43\n",    // first line
        "meta=opened\nproject=x\ncalib_seconds=1\nseconds=2\nlayout=tt-um-43\n",
        "meta=open\nproject=x\ncalib_seconds=1\nseconds=2\nlayout=tt-um-43\nnoequals\n",
        "meta=open\nproject=x\ncalib_seconds=1\nseconds=2\nlayout=tt-um-43\nproject=y\n", // repeat
        "meta=open\nproject=x\ncalib_seconds=1\nseconds=2\nlayout=tt-um-43\nmeta=close\n",
        "meta=open\nProject=x\ncalib_seconds=1\nseconds=2\nlayout=tt-um-43\n",    // key syntax
        "meta=open\nproject=x\ncalib_seconds=1\nseconds=2\nlayout=tt-um-43\n1x=2\n",
        "meta=open\nproject=x\ncalib_seconds=1\nseconds=2\nlayout=tt-um-43\n=2\n",
        "meta=open\nproject=x\ncalib_seconds=1\nseconds=2\nlayout=tt-um-43\nk-y=2\n",
        "meta=open\nproject=\xff\ncalib_seconds=1\nseconds=2\nlayout=tt-um-43\n",  // not UTF-8
        "meta=open\nproject=\xc0\xaf\ncalib_seconds=1\nseconds=2\nlayout=tt-um-43\n", // overlong
        "meta=open\nproject=\xed\xa0\x80\ncalib_seconds=1\nseconds=2\nlayout=tt-um-43\n", // surrogate
        "meta=open\ncalib_seconds=1\nseconds=2\nlayout=tt-um-43\n",               // missing project
        "meta=open\nproject=x\nseconds=2\nlayout=tt-um-43\n",                     // missing calib_seconds
        "meta=open\nproject=x\ncalib_seconds=1\nlayout=tt-um-43\n",               // missing seconds
        "meta=open\nproject=x\ncalib_seconds=1\nseconds=2\n",                     // missing layout
        "meta=close\nstatus=ok\nframes=1\n",                                      // missing capture_after_clock
        "meta=close\ncapture_after_clock=1\nframes=1\n",                          // missing status
        "meta=close\ncapture_after_clock=1\nstatus=ok\n",                         // missing frames
        "meta=open\nproject=x\ncalib_seconds=1\nseconds=2\nlayout=tt-um-44\n",    // layout value
        "meta=open\nproject=x\ncalib_seconds=1\nseconds=\nlayout=tt-um-43\n",     // decimal values
        "meta=open\nproject=x\ncalib_seconds=1\nseconds=1.2.3\nlayout=tt-um-43\n",
        "meta=open\nproject=x\ncalib_seconds=1\nseconds=0x10\nlayout=tt-um-43\n",
        "meta=open\nproject=x\ncalib_seconds=1\nseconds=inf\nlayout=tt-um-43\n",
        "meta=open\nproject=x\ncalib_seconds=1\nseconds= 2\nlayout=tt-um-43\n",
        "meta=open\nproject=x\ncalib_seconds=1e\nseconds=2\nlayout=tt-um-43\n",
        "meta=open\nproject=x\ncalib_seconds=.\nseconds=2\nlayout=tt-um-43\n",
        "meta=close\ncapture_after_clock=-1\nstatus=ok\nframes=1\n",            // integer values
        "meta=close\ncapture_after_clock=1\nstatus=ok\nframes=\n",
        "meta=close\ncapture_after_clock=18446744073709551616\nstatus=ok\nframes=1\n",
        "meta=close\ncapture_after_clock=1\nstatus=ok\nframes=+1\n",
        "meta=close\ncapture_after_clock=1\nstatus=truncated\nframes=1\n",      // status value
        "meta=close\ncapture_after_clock=1\nstatus=OK\nframes=1\n",
    };
    for (size_t i = 0; i < sizeof bad / sizeof bad[0]; i++) {
        start_trace(); put_meta(bad[i]); ASSERT_EQ_U(vgacap_writer_meta(&W, OPEN_OK, strlen(OPEN_OK)), 0);
        ASSERT_EQ_U(feed_all(), 0);
        if (count(VGACAP_EV_RESYNC) != 1 || !what_is(nth(VGACAP_EV_RESYNC, 0), "bad META text"))
            fprintf(stderr, "  bad META text case %zu accepted\n", i);
        ASSERT_EQ_U(count(VGACAP_EV_RESYNC), 1);
        ASSERT_TRUE(what_is(nth(VGACAP_EV_RESYNC, 0), "bad META text"));
        ASSERT_EQ_U(nth(VGACAP_EV_RESYNC, 0)->u.resync.skipped, 8 + strlen(bad[i]));
        ASSERT_EQ_U(count(VGACAP_EV_META), 1);   // the good one after it
    }
    // Every status value the note lists is accepted.
    static const char *st[5] = { "ok", "sim-timeout", "unstable-sync", "no-sync", "bad-timing" };
    for (size_t i = 0; i < 5; i++) {
        char t[128]; snprintf(t, sizeof t, "meta=close\ncapture_after_clock=1\nstatus=%s\nframes=1\n", st[i]);
        start_trace(); put_meta(t);
        ASSERT_EQ_U(feed_all(), 0); ASSERT_EQ_U(count(VGACAP_EV_META), 1); ASSERT_EQ_U(count(VGACAP_EV_RESYNC), 0);
    }
    // Decimal forms strtod reads: sign, no integer part, no fraction, exponent.
    static const char *dec[6] = { "0", "+3", "-.5", "2.", "1E+9", "12.5e-03" };
    for (size_t i = 0; i < 6; i++) {
        char t[160]; snprintf(t, sizeof t, "meta=open\nproject=x\ncalib_seconds=%s\nseconds=1\nlayout=tt-um-43\n", dec[i]);
        start_trace(); put_meta(t);
        ASSERT_EQ_U(feed_all(), 0); ASSERT_EQ_U(count(VGACAP_EV_META), 1);
    }
    // UTF-8 free text and the largest u64 are fine.
    start_trace(); put_meta("meta=close\ncapture_after_clock=18446744073709551615\nstatus=ok\nframes=1\nproject=\xc3\xa9\xe2\x82\xac\xf0\x9f\x98\x80\n");
    ASSERT_EQ_U(feed_all(), 0); ASSERT_EQ_U(count(VGACAP_EV_META), 1);
}

// ---- version 1 ------------------------------------------------------------

TEST(v1_streams_unchanged) {
    // Every v1 chunk kind (the vectors of test_stream_chunks.c) decodes as
    // before: same runs, no BLOCK/META events, no reports, and the clock
    // regeneration never touches a v1 value.
    start_v1();
    uint32_t v[3] = { 0x88, 0x08, 0x3F }, n[3] = { 96, 48, 640 };
    ASSERT_EQ_U(vgacap_writer_rle(&W, v, n, 3), 0);
    uint32_t s[10] = { 1,2,3,4,5,6,7,8,9,10 }, words[3]; vgacap_pack_samples(&W.header, s, 10, words);
    ASSERT_EQ_U(vgacap_writer_frame(&W, 42, 100, 2, 5, words, 10), 0);
    uint64_t c[4] = { 0, 10, 15, 100 }; uint32_t ev[4] = { 0x80, 0x00, 0x80, 0x3F };
    ASSERT_EQ_U(vgacap_writer_events(&W, c, ev, 4), 0);
    ASSERT_EQ_U(vgacap_writer_raw(&W, words, 10), 0);
    ASSERT_EQ_U(vgacap_writer_time(&W, 1234567890123ull, 500000, 7, "fifo overrun"), 0);
    const uint8_t junk[12] = { 'X','Y','Z','W', 4,0,0,0, 1,2,3,4 }; put(junk, 12);
    uint32_t rv = 0xFFFFFFFFu, rn = 0xFFFFFFFFu;   // the largest v1 run and value
    ASSERT_EQ_U(vgacap_writer_rle(&W, &rv, &rn, 1), 0);
    ASSERT_EQ_U(feed_all(), 0);
    ASSERT_EQ_U(count(VGACAP_EV_RESYNC), 0); ASSERT_EQ_U(count(VGACAP_EV_BLOCK), 0); ASSERT_EQ_U(count(VGACAP_EV_META), 0);
    ASSERT_EQ_U(count(VGACAP_EV_FRAME_BEGIN), 1); ASSERT_EQ_U(count(VGACAP_EV_TIME), 1);
    const uint64_t want_v[] = { 0x88, 0x08, 0x3F, 1,2,3,4,5,6,7,8,9,10, 0x80, 0x00, 0x80, 0x3F, 1,2,3,4,5,6,7,8,9,10, 0xFFFFFFFFu };
    const uint64_t want_r[] = { 96, 48, 640, 1,1,1,1,1,1,1,1,1,1, 10, 5, 85, 1, 1,1,1,1,1,1,1,1,1,1, 0xFFFFFFFFu };
    ASSERT_EQ_U(count(VGACAP_EV_RUN), sizeof want_v / sizeof want_v[0]);
    uint64_t total = 0;
    for (size_t i = 0; i < sizeof want_v / sizeof want_v[0]; i++) {
        ASSERT_EQ_U(nth(VGACAP_EV_RUN, i)->u.run.value, want_v[i]);
        ASSERT_EQ_U(nth(VGACAP_EV_RUN, i)->u.run.run, want_r[i]);
        total += want_r[i];
    }
    ASSERT_EQ_U(rd.sample_pos, total);
}

int main(void) {
    RUN(leb128_vectors); RUN(header_v2_round_trip); RUN(worked_example_bytes); RUN(header_v2_rules);
    RUN(header_v1_defaults); RUN(runs_round_trip_43bit); RUN(runs_clock_first_one); RUN(runs_other_widths);
    RUN(runs_reject_clock_bit_set); RUN(runs_reject_overlong_leb); RUN(runs_reject_padding_bits);
    RUN(runs_reject_zero_run); RUN(runs_reject_n_past_64_bits); RUN(runs_length_mismatch); RUN(runs_two_faults_early_bound_first); RUN(runs_reject_no_pairs); RUN(runs_adjacent_pairs);
    RUN(runs_writer_refuses_bad_pairs); RUN(runs_need_v2_header); RUN(rle_rejected_in_v2);
    RUN(clock_stream_rejects_raw); RUN(wide_stream_rejects_raw); RUN(v2_narrow_stream_allows_raw);
    RUN(block_sets_position); RUN(bad_blkh_length); RUN(clock_loss_discards_until_blkh);
    RUN(clock_n_after_non_runs_skips); RUN(init_header_decodes_block); RUN(meta_event); RUN(meta_bad_text); RUN(v1_streams_unchanged);
    RUN_TESTS_END();
}
