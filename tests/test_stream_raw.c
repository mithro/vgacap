// SPDX-License-Identifier: Apache-2.0
#include "harness.h"
#include "vgacap/stream.h"

static uint8_t outbuf[65536]; static size_t outlen;
static int capture_write(void *u, const uint8_t *b, size_t n) { (void)u; memcpy(outbuf + outlen, b, n); outlen += n; return 0; }
static uint32_t got[4096]; static size_t ngot;
static void on_event(void *u, const vgacap_event_t *ev) { (void)u;
    if (ev->type == VGACAP_EV_RUN) { for (uint32_t i = 0; i < ev->u.run.run; i++) got[ngot++] = ev->u.run.value; } }

static void roundtrip(uint8_t bits, uint8_t spw, uint8_t flags, uint32_t n) {
    vgacap_header_t h; vgacap_header_init_tinyvga(&h, bits, spw, flags);
    vgacap_writer_t w; outlen = 0; vgacap_writer_init(&w, capture_write, NULL, &h);
    uint32_t samples[1000], words[1000];
    uint32_t mask = bits == 32 ? 0xFFFFFFFFu : ((1u << bits) - 1u);
    for (uint32_t i = 0; i < n; i++) samples[i] = (i * 2654435761u) & mask;
    ASSERT_EQ_U(vgacap_writer_raw_samples(&w, samples, n, words, 1000), 0);
    vgacap_reader_t r; ngot = 0; vgacap_reader_init(&r, on_event, NULL);
    // feed in odd-sized pieces
    size_t pos = 0; while (pos < outlen) { size_t k = outlen - pos < 7 ? outlen - pos : 7; vgacap_reader_feed(&r, outbuf + pos, k); pos += k; }
    ASSERT_EQ_U(ngot, n);
    for (uint32_t i = 0; i < n; i++) ASSERT_EQ_U(got[i], samples[i]);
}

TEST(raw_8bit_4_per_word_lsb_first) { roundtrip(8, 4, 0, 1000); }
TEST(raw_8bit_4_per_word_msb_first) { roundtrip(8, 4, VGACAP_FLAG_FIRST_SAMPLE_MSB, 999); }
TEST(raw_12bit_2_per_word) { roundtrip(12, 2, 0, 501); }
TEST(raw_12bit_1_per_word) { roundtrip(12, 1, 0, 33); }
TEST(raw_16bit_2_per_word_msb) { roundtrip(16, 2, VGACAP_FLAG_FIRST_SAMPLE_MSB, 100); }
TEST(raw_32bit) { roundtrip(32, 1, 0, 64); }

TEST(raw_wire_layout_lsb_first) {
    vgacap_header_t h; vgacap_header_init_tinyvga(&h, 8, 4, 0);
    vgacap_writer_t w; outlen = 0; vgacap_writer_init(&w, capture_write, NULL, &h);
    uint32_t s[5] = { 0x11, 0x22, 0x33, 0x44, 0x55 }, words[2];
    size_t start = outlen;
    ASSERT_EQ_U(vgacap_writer_raw_samples(&w, s, 5, words, 2), 0);
    ASSERT_EQ_MEM(outbuf + start, "RAW ", 4);
    ASSERT_EQ_U(outbuf[start + 4], 4 + 8);              // length: count + 2 words
    ASSERT_EQ_U(outbuf[start + 8], 5);                  // sample_count
    const uint8_t expect[8] = { 0x11, 0x22, 0x33, 0x44, 0x55, 0, 0, 0 };
    ASSERT_EQ_MEM(outbuf + start + 12, expect, 8);
}

int main(void) { RUN(raw_8bit_4_per_word_lsb_first); RUN(raw_8bit_4_per_word_msb_first); RUN(raw_12bit_2_per_word);
    RUN(raw_12bit_1_per_word); RUN(raw_16bit_2_per_word_msb); RUN(raw_32bit); RUN(raw_wire_layout_lsb_first); RUN_TESTS_END(); }
