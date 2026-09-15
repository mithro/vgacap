// SPDX-License-Identifier: Apache-2.0
#include "harness.h"
#include "vgacap/stream.h"

static uint8_t outbuf[65536]; static size_t outlen;
static int capture_write(void *u, const uint8_t *b, size_t n) { (void)u; memcpy(outbuf + outlen, b, n); outlen += n; return 0; }
static vgacap_event_t evs[4096]; static char msgs[4096][64]; static size_t nev;
static size_t nresync, ntime;
static void on_event(void *u, const vgacap_event_t *ev) { (void)u; evs[nev] = *ev;
    if (ev->type == VGACAP_EV_TIME) { memcpy(msgs[nev], ev->u.time.msg, ev->u.time.msg_len); msgs[nev][ev->u.time.msg_len] = 0; evs[nev].u.time.msg = msgs[nev]; ntime++; }
    if (ev->type == VGACAP_EV_RESYNC) nresync++;
    nev++; }
static void feed_all(void) { vgacap_reader_t r; nev = nresync = ntime = 0; vgacap_reader_init(&r, on_event, NULL);
    for (size_t i = 0; i < outlen; i++) vgacap_reader_feed(&r, outbuf + i, 1); }
static void start(uint8_t bits, uint8_t spw, vgacap_writer_t *w) { vgacap_header_t h; vgacap_header_init_tinyvga(&h, bits, spw, 0);
    outlen = 0; vgacap_writer_init(w, capture_write, NULL, &h); }

TEST(rle_roundtrip) {
    vgacap_writer_t w; start(8, 4, &w);
    uint32_t v[3] = { 0x88, 0x08, 0x3F }, n[3] = { 96, 48, 640 };
    ASSERT_EQ_U(vgacap_writer_rle(&w, v, n, 3), 0);
    feed_all();
    ASSERT_EQ_U(nev, 4); // header + 3 runs
    ASSERT_EQ_U(evs[1].type, VGACAP_EV_RUN); ASSERT_EQ_U(evs[1].u.run.value, 0x88); ASSERT_EQ_U(evs[1].u.run.run, 96);
    ASSERT_EQ_U(evs[3].u.run.value, 0x3F); ASSERT_EQ_U(evs[3].u.run.run, 640);
}

TEST(frame_roundtrip) {
    vgacap_writer_t w; start(8, 4, &w);
    uint32_t s[10] = { 1,2,3,4,5,6,7,8,9,10 }, words[3]; vgacap_pack_samples(&w.header, s, 10, words);
    ASSERT_EQ_U(vgacap_writer_frame(&w, 42, 100, 2, 5, words, 10), 0);
    feed_all();
    ASSERT_EQ_U(nev, 12);
    ASSERT_EQ_U(evs[1].type, VGACAP_EV_FRAME_BEGIN);
    ASSERT_EQ_U(evs[1].u.frame.frame_counter, 42); ASSERT_EQ_U(evs[1].u.frame.first_line, 100);
    ASSERT_EQ_U(evs[1].u.frame.line_count, 2); ASSERT_EQ_U(evs[1].u.frame.clocks_per_line, 5); ASSERT_EQ_U(evs[1].u.frame.sample_count, 10);
    for (int i = 0; i < 10; i++) { ASSERT_EQ_U(evs[2 + i].type, VGACAP_EV_RUN); ASSERT_EQ_U(evs[2 + i].u.run.value, (unsigned)(i + 1)); }
}

TEST(events_expand_to_runs) {
    vgacap_writer_t w; start(8, 1, &w);
    uint64_t c[4] = { 0, 10, 15, 100 }; uint32_t v[4] = { 0x80, 0x00, 0x80, 0x3F };
    ASSERT_EQ_U(vgacap_writer_events(&w, c, v, 4), 0);
    feed_all();
    ASSERT_EQ_U(nev, 5);
    ASSERT_EQ_U(evs[1].u.run.value, 0x80); ASSERT_EQ_U(evs[1].u.run.run, 10);
    ASSERT_EQ_U(evs[2].u.run.value, 0x00); ASSERT_EQ_U(evs[2].u.run.run, 5);
    ASSERT_EQ_U(evs[3].u.run.value, 0x80); ASSERT_EQ_U(evs[3].u.run.run, 85);
    ASSERT_EQ_U(evs[4].u.run.value, 0x3F); ASSERT_EQ_U(evs[4].u.run.run, 1);
}

TEST(time_roundtrip) {
    vgacap_writer_t w; start(8, 4, &w);
    ASSERT_EQ_U(vgacap_writer_time(&w, 1234567890123ull, 500000, 7, "fifo overrun"), 0);
    feed_all();
    ASSERT_EQ_U(nev, 2); ASSERT_EQ_U(evs[1].type, VGACAP_EV_TIME);
    ASSERT_EQ_U(evs[1].u.time.host_time_ns, 1234567890123ull); ASSERT_EQ_U(evs[1].u.time.clock_hz, 500000);
    ASSERT_EQ_U(evs[1].u.time.dropped_samples, 7); ASSERT_TRUE(strcmp(evs[1].u.time.msg, "fifo overrun") == 0);
}

TEST(unknown_chunk_is_skipped) {
    vgacap_writer_t w; start(8, 4, &w);
    const uint8_t junk[12] = { 'X','Y','Z','W', 4,0,0,0, 1,2,3,4 }; capture_write(NULL, junk, 12);
    uint32_t v = 5, n = 2; vgacap_writer_rle(&w, &v, &n, 1);
    feed_all();
    ASSERT_EQ_U(nev, 2); ASSERT_EQ_U(evs[1].u.run.run, 2);
}

// A RAW chunk of 64 8-bit samples at 4 samples/word: 8 header bytes + a
// 4-byte count + 16 words. Used by the resync tests to predict skip counts.
#define RAW64_PAYLOAD 68u
#define RAW64_CHUNK   (8u + RAW64_PAYLOAD)

TEST(dropped_byte_resyncs_and_keeps_going) {
    // One deleted byte shifts every subsequent tag/length pair. The reader
    // used to wander on reporting success (I1); it must now notice, skip to
    // the next real chunk header, say how much it lost, and carry on.
    vgacap_writer_t w; start(8, 4, &w);
    // Every sample stays <= 0x0F, so no payload byte can be mistaken for part
    // of a chunk tag and the resync point is unambiguous.
    uint32_t s[64], words[16]; size_t chunk2 = 0, chunk3 = 0;
    for (int c = 0; c < 4; c++) {
        for (int i = 0; i < 64; i++) s[i] = (uint32_t)((i + c) & 0x0F);
        vgacap_pack_samples(&w.header, s, 64, words);
        if (c == 1) chunk2 = outlen;
        if (c == 2) chunk3 = outlen;
        ASSERT_EQ_U(vgacap_writer_raw(&w, words, 64), 0);
    }
    ASSERT_EQ_U(chunk3 - chunk2, RAW64_CHUNK);
    size_t cut = chunk2 + 8 + 20;                       // mid-payload of chunk 2
    memmove(outbuf + cut, outbuf + cut + 1, outlen - cut - 1); outlen--;
    feed_all();
    ASSERT_EQ_U(nresync, 1);
    // Chunk 2 eats chunk 3's first tag byte, so the scan starts one byte into
    // chunk 3 and runs to chunk 4's header: the whole of chunk 3 bar one byte.
    size_t ri = 0; while (ri < nev && evs[ri].type != VGACAP_EV_RESYNC) ri++;
    ASSERT_EQ_U(evs[ri].u.resync.skipped, RAW64_CHUNK - 1);
    ASSERT_TRUE(evs[ri].u.resync.what != NULL);
    // ...and chunk 4's samples still arrive, in full and in order.
    ASSERT_TRUE(nev >= 64);
    for (size_t i = 0; i < 64; i++) {
        const vgacap_event_t *e = &evs[nev - 64 + i];
        ASSERT_EQ_U(e->type, VGACAP_EV_RUN);
        ASSERT_EQ_U(e->u.run.value, (unsigned)((i + 3) & 0x0F));
    }
}

TEST(time_msg_len_beyond_payload_is_rejected) {
    // msg_len = 20 with no message bytes: the msg used to be returned from
    // stale scratch (I2, deferred minor (a)). It must now be refused outright
    // and never reported.
    vgacap_writer_t w; start(8, 4, &w);
    uint8_t bad[8 + 18] = { 'T','I','M','E', 18,0,0,0 };
    bad[8 + 16] = 20; // msg_len
    capture_write(NULL, bad, sizeof bad);
    uint32_t v = 5, n = 2; vgacap_writer_rle(&w, &v, &n, 1);
    feed_all();
    ASSERT_EQ_U(ntime, 0);
    ASSERT_EQ_U(nresync, 1);
    ASSERT_EQ_U(nev, 3); // header, resync, and the RLE run that follows
    ASSERT_EQ_U(evs[1].type, VGACAP_EV_RESYNC);
    ASSERT_EQ_U(evs[1].u.resync.skipped, 8 + 18); // the whole malformed chunk
    ASSERT_EQ_U(evs[2].u.run.value, 5); ASSERT_EQ_U(evs[2].u.run.run, 2);
}

TEST(rle_pair_count_mismatch_is_rejected) {
    // Declares 5 pairs in a payload that holds 2: a corrupt count must fail
    // the chunk, not silently truncate or pad the sample sequence (I2).
    vgacap_writer_t w; start(8, 4, &w);
    uint8_t bad[8 + 20] = { 'R','L','E',' ', 20,0,0,0, 5,0,0,0 };
    capture_write(NULL, bad, sizeof bad);
    uint32_t v = 9, n = 3; vgacap_writer_rle(&w, &v, &n, 1);
    feed_all();
    ASSERT_EQ_U(nresync, 1);
    ASSERT_EQ_U(nev, 3);
    ASSERT_EQ_U(evs[1].type, VGACAP_EV_RESYNC);
    ASSERT_EQ_U(evs[1].u.resync.skipped, 8 + 20); // header + the whole payload
    ASSERT_EQ_U(evs[2].u.run.value, 9); ASSERT_EQ_U(evs[2].u.run.run, 3);
}

TEST(raw_sample_count_mismatch_is_rejected) {
    // Same for RAW: 4 + 4*ceil(count/spw) must equal the declared length.
    vgacap_writer_t w; start(8, 4, &w);
    uint8_t bad[8 + 12] = { 'R','A','W',' ', 12,0,0,0, 99,0,0,0 }; // 99 samples, 2 words
    capture_write(NULL, bad, sizeof bad);
    uint32_t v = 7, n = 4; vgacap_writer_rle(&w, &v, &n, 1);
    feed_all();
    ASSERT_EQ_U(nresync, 1);
    ASSERT_EQ_U(nev, 3);
    ASSERT_EQ_U(evs[2].u.run.value, 7); ASSERT_EQ_U(evs[2].u.run.run, 4);
}

TEST(oversized_length_is_rejected) {
    // A length beyond VGACAP_MAX_CHUNK_LEN is not a chunk we lost framing
    // inside - it is the evidence that framing is already gone.
    vgacap_writer_t w; start(8, 4, &w);
    const uint8_t bad[8] = { 'R','L','E',' ', 0,0,0,0x40 }; // 1 GiB
    capture_write(NULL, bad, sizeof bad);
    uint32_t v = 3, n = 6; vgacap_writer_rle(&w, &v, &n, 1);
    feed_all();
    ASSERT_EQ_U(nresync, 1);
    ASSERT_EQ_U(nev, 3);
    ASSERT_EQ_U(evs[1].u.resync.skipped, 8); // the bogus header, nothing more
    ASSERT_EQ_U(evs[2].u.run.value, 3); ASSERT_EQ_U(evs[2].u.run.run, 6);
}

int main(void) { RUN(rle_roundtrip); RUN(frame_roundtrip); RUN(events_expand_to_runs); RUN(time_roundtrip); RUN(unknown_chunk_is_skipped);
    RUN(dropped_byte_resyncs_and_keeps_going); RUN(time_msg_len_beyond_payload_is_rejected);
    RUN(rle_pair_count_mismatch_is_rejected); RUN(raw_sample_count_mismatch_is_rejected);
    RUN(oversized_length_is_rejected); RUN_TESTS_END(); }
