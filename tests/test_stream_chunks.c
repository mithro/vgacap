// SPDX-License-Identifier: Apache-2.0
#include "harness.h"
#include "vgacap/stream.h"

static uint8_t outbuf[65536]; static size_t outlen;
static int capture_write(void *u, const uint8_t *b, size_t n) { (void)u; memcpy(outbuf + outlen, b, n); outlen += n; return 0; }
static vgacap_event_t evs[4096]; static char msgs[4096][64]; static size_t nev;
static void on_event(void *u, const vgacap_event_t *ev) { (void)u; evs[nev] = *ev;
    if (ev->type == VGACAP_EV_TIME) { memcpy(msgs[nev], ev->u.time.msg, ev->u.time.msg_len); msgs[nev][ev->u.time.msg_len] = 0; evs[nev].u.time.msg = msgs[nev]; }
    nev++; }
static void feed_all(void) { vgacap_reader_t r; nev = 0; vgacap_reader_init(&r, on_event, NULL);
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

int main(void) { RUN(rle_roundtrip); RUN(frame_roundtrip); RUN(events_expand_to_runs); RUN(time_roundtrip); RUN(unknown_chunk_is_skipped); RUN_TESTS_END(); }
