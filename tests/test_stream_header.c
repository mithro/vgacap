// SPDX-License-Identifier: Apache-2.0
#include "harness.h"
#include "vgacap/stream.h"

static uint8_t outbuf[4096]; static size_t outlen;
static int capture_write(void *user, const uint8_t *buf, size_t len) {
    (void)user; if (outlen + len > sizeof outbuf) return -1;
    memcpy(outbuf + outlen, buf, len); outlen += len; return 0;
}
static vgacap_header_t seen; static int seen_header;
static void on_event(void *user, const vgacap_event_t *ev) {
    (void)user; if (ev->type == VGACAP_EV_HEADER) { seen = *ev->u.header; seen_header++; }
}

TEST(header_roundtrip) {
    vgacap_header_t h; vgacap_header_init_tinyvga(&h, 12, 2, 0);
    h.clock_hz = 25175000; h.mode = VGACAP_MODE_EXTCLK;
    strcpy(h.desc, "tt07 tt_um_rejunity_vga"); h.desc_len = (uint8_t)strlen(h.desc);
    vgacap_writer_t w; outlen = 0;
    ASSERT_EQ_U(vgacap_writer_init(&w, capture_write, NULL, &h), 0);
    ASSERT_EQ_MEM(outbuf, "VGCH", 4);
    ASSERT_EQ_U(outbuf[4] | (outbuf[5] << 8), 20 + h.desc_len);
    ASSERT_EQ_U(outlen, 8 + 20 + h.desc_len);
    ASSERT_EQ_U(outbuf[8 + 2], 12);           // sample_bits
    ASSERT_EQ_U(outbuf[8 + 8], 7);            // hsync bit
    ASSERT_EQ_U(outbuf[8 + 16], 2);           // samples_per_word

    vgacap_reader_t r; seen_header = 0; vgacap_reader_init(&r, on_event, NULL);
    // feed one byte at a time to prove incremental parsing
    for (size_t i = 0; i < outlen; i++) ASSERT_EQ_U(vgacap_reader_feed(&r, outbuf + i, 1), 0);
    ASSERT_EQ_U(seen_header, 1);
    ASSERT_EQ_U(seen.version, 1);
    ASSERT_EQ_U(seen.sample_bits, 12);
    ASSERT_EQ_U(seen.clock_hz, 25175000);
    ASSERT_EQ_U(seen.signal_map[VGACAP_SIG_B0], 6);
    ASSERT_EQ_U(seen.desc_len, h.desc_len);
    ASSERT_TRUE(strcmp(seen.desc, h.desc) == 0);
}

TEST(header_rejects_bad_version) {
    uint8_t bad[28] = { 'V','G','C','H', 20,0,0,0, 9,0, 8, 3, 0,0,0,0, 7,3,0,4,1,5,2,6, 4, 0, 0,0 };
    vgacap_reader_t r; vgacap_reader_init(&r, on_event, NULL);
    ASSERT_EQ_U(vgacap_reader_feed(&r, bad, sizeof bad), (unsigned long long)-1);
}

int main(void) { RUN(header_roundtrip); RUN(header_rejects_bad_version); RUN_TESTS_END(); }
