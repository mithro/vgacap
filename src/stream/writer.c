// SPDX-License-Identifier: Apache-2.0
#include "vgacap/stream.h"
#include <string.h>

static void put_u16(uint8_t *p, uint16_t v) { p[0] = (uint8_t)v; p[1] = (uint8_t)(v >> 8); }
static void put_u32(uint8_t *p, uint32_t v) {
    p[0] = (uint8_t)v; p[1] = (uint8_t)(v >> 8); p[2] = (uint8_t)(v >> 16); p[3] = (uint8_t)(v >> 24);
}

void vgacap_header_init_tinyvga(vgacap_header_t *h, uint8_t sample_bits, uint8_t samples_per_word, uint8_t flags) {
    static const uint8_t tinyvga[VGACAP_SIG_COUNT] = { 7, 3, 0, 4, 1, 5, 2, 6 };
    memset(h, 0, sizeof *h);
    h->version = 1; h->sample_bits = sample_bits; h->mode = VGACAP_MODE_UNKNOWN;
    memcpy(h->signal_map, tinyvga, sizeof tinyvga);
    h->samples_per_word = samples_per_word; h->flags = flags;
    h->samples_per_clock = 1; h->clock_bit = VGACAP_CLOCK_BIT_NONE; h->clock_first = 0;
}

void vgacap_header_init_v2(vgacap_header_t *h, uint8_t sample_bits, uint8_t samples_per_clock,
                           uint8_t clock_bit, uint8_t clock_first) {
    vgacap_header_init_tinyvga(h, sample_bits, sample_bits > 32 ? 0 : 1, 0);
    h->version = 2;
    h->samples_per_clock = samples_per_clock; h->clock_bit = clock_bit; h->clock_first = clock_first;
}

size_t vgacap_leb128_put(uint8_t *p, uint64_t v) { return vgacap_leb128_put_inline(p, v); }

size_t vgacap_run_pair_put(uint8_t *p, uint64_t value, unsigned vbytes, uint64_t run) {
    return vgacap_run_pair_put_inline(p, value, vbytes, run);
}

static int write_chunk_head(vgacap_writer_t *w, const char tag[4], uint32_t length) {
    uint8_t head[8]; memcpy(head, tag, 4); put_u32(head + 4, length);
    return w->write(w->user, head, 8);
}

void vgacap_writer_attach(vgacap_writer_t *w, vgacap_write_fn write, void *user, const vgacap_header_t *h) {
    memset(w, 0, sizeof *w); w->write = write; w->user = user; w->header = *h;
}

// Version 2's fixed part is 24 bytes: version 1's 20, then
// samples_per_clock, clock_bit, clock_first and a reserved 0.
int vgacap_writer_init(vgacap_writer_t *w, vgacap_write_fn write, void *user, const vgacap_header_t *h) {
    vgacap_writer_attach(w, write, user, h);
    uint8_t *p = w->scratch;
    uint32_t fixed = h->version == 2 ? 24u : 20u;
    if (h->version == 2 && h->desc_len > VGACAP_DESC_MAX_V2) return -1;
    put_u16(p + 0, h->version); p[2] = h->sample_bits; p[3] = h->mode; put_u32(p + 4, h->clock_hz);
    memcpy(p + 8, h->signal_map, VGACAP_SIG_COUNT); p[16] = h->samples_per_word; p[17] = h->flags;
    put_u16(p + 18, h->desc_len);
    p[20] = h->samples_per_clock; p[21] = h->clock_bit; p[22] = h->clock_first; p[23] = 0;
    if (write_chunk_head(w, VGACAP_TAG_HEADER, fixed + h->desc_len)) return -1;
    if (w->write(w->user, p, fixed)) return -1;
    if (h->desc_len && w->write(w->user, (const uint8_t *)h->desc, h->desc_len)) return -1;
    return 0;
}

// Packed words as little-endian bytes. On a little-endian machine (every
// RP2 and every host this builds for) that is the words' own memory, sent in
// one call -- a capture board writes 16 KB buffers through this, and a call
// per word would cost it CPU time it needs for the USB link.
static int write_words(vgacap_writer_t *w, const uint32_t *words, uint32_t nwords) {
#if defined(__BYTE_ORDER__) && __BYTE_ORDER__ == __ORDER_LITTLE_ENDIAN__
    return nwords ? w->write(w->user, (const uint8_t *)words, 4u * (size_t)nwords) : 0;
#else
    uint8_t c[4];
    for (uint32_t i = 0; i < nwords; i++) { put_u32(c, words[i]); if (w->write(w->user, c, 4)) return -1; }
    return 0;
#endif
}

static uint32_t sample_mask(uint8_t bits) { return bits == 32 ? 0xFFFFFFFFu : ((1u << bits) - 1u); }

int vgacap_writer_raw(vgacap_writer_t *w, const uint32_t *words, uint32_t sample_count) {
    uint32_t spw = w->header.samples_per_word;
    uint32_t nwords = (sample_count + spw - 1) / spw;
    if (write_chunk_head(w, VGACAP_TAG_RAW, 4u + 4u * nwords)) return -1;
    uint8_t c[4]; put_u32(c, sample_count); if (w->write(w->user, c, 4)) return -1;
    return write_words(w, words, nwords);
}

uint32_t vgacap_pack_samples(const vgacap_header_t *h, const uint32_t *samples, uint32_t n, uint32_t *words) {
    uint32_t spw = h->samples_per_word, bits = h->sample_bits, mask = sample_mask(h->sample_bits);
    uint32_t nwords = (n + spw - 1) / spw;
    for (uint32_t i = 0; i < nwords; i++) words[i] = 0;
    for (uint32_t i = 0; i < n; i++) {
        uint32_t slot = i % spw; if (h->flags & VGACAP_FLAG_FIRST_SAMPLE_MSB) slot = spw - 1 - slot;
        uint32_t shift = slot * bits; words[i / spw] |= (samples[i] & mask) << shift;
    }
    return nwords;
}

int vgacap_writer_raw_samples(vgacap_writer_t *w, const uint32_t *samples, uint32_t n, uint32_t *wordbuf, size_t wordbuf_len) {
    uint32_t spw = w->header.samples_per_word;
    if ((size_t)((n + spw - 1) / spw) > wordbuf_len) return -1;
    vgacap_pack_samples(&w->header, samples, n, wordbuf);
    return vgacap_writer_raw(w, wordbuf, n);
}

int vgacap_writer_rle(vgacap_writer_t *w, const uint32_t *values, const uint32_t *runs, uint32_t pair_count) {
    if (write_chunk_head(w, VGACAP_TAG_RLE, 4u + 8u * pair_count)) return -1;
    uint8_t c[8]; put_u32(c, pair_count); if (w->write(w->user, c, 4)) return -1;
    for (uint32_t i = 0; i < pair_count; i++) {
        put_u32(c, values[i]); put_u32(c + 4, runs[i]); if (w->write(w->user, c, 8)) return -1;
    }
    return 0;
}

int vgacap_writer_frame_begin(vgacap_writer_t *w, uint32_t frame_counter, uint16_t first_line,
                              uint16_t line_count, uint32_t clocks_per_line, uint32_t sample_count) {
    uint32_t spw = w->header.samples_per_word, nwords = (sample_count + spw - 1) / spw;
    if (write_chunk_head(w, VGACAP_TAG_FRAME, 16u + 4u * nwords)) return -1;
    uint8_t c[16]; put_u32(c, frame_counter); put_u16(c + 4, first_line); put_u16(c + 6, line_count);
    put_u32(c + 8, clocks_per_line); put_u32(c + 12, sample_count);
    return w->write(w->user, c, 16);
}

int vgacap_writer_frame_words(vgacap_writer_t *w, const uint32_t *words, uint32_t nwords) {
    return write_words(w, words, nwords);
}

int vgacap_writer_frame(vgacap_writer_t *w, uint32_t frame_counter, uint16_t first_line,
                        uint16_t line_count, uint32_t clocks_per_line,
                        const uint32_t *words, uint32_t sample_count) {
    uint32_t spw = w->header.samples_per_word, nwords = (sample_count + spw - 1) / spw;
    if (vgacap_writer_frame_begin(w, frame_counter, first_line, line_count, clocks_per_line, sample_count)) return -1;
    return write_words(w, words, nwords);
}

static void put_u64(uint8_t *p, uint64_t v) { put_u32(p, (uint32_t)v); put_u32(p + 4, (uint32_t)(v >> 32)); }

int vgacap_writer_events(vgacap_writer_t *w, const uint64_t *clocks, const uint32_t *values, uint32_t count) {
    if (write_chunk_head(w, VGACAP_TAG_EVENT, 4u + 12u * count)) return -1;
    uint8_t c[12]; put_u32(c, count); if (w->write(w->user, c, 4)) return -1;
    for (uint32_t i = 0; i < count; i++) {
        put_u64(c, clocks[i]); put_u32(c + 8, values[i]); if (w->write(w->user, c, 12)) return -1;
    }
    return 0;
}

int vgacap_writer_time(vgacap_writer_t *w, uint64_t host_time_ns, uint32_t clock_hz, uint32_t dropped, const char *msg) {
    size_t ml = msg ? strlen(msg) : 0; if (ml > 255) ml = 255;
    if (write_chunk_head(w, VGACAP_TAG_TIME, (uint32_t)(18 + ml))) return -1;
    uint8_t c[18]; put_u64(c, host_time_ns); put_u32(c + 8, clock_hz); put_u32(c + 12, dropped);
    put_u16(c + 16, (uint16_t)ml);
    if (w->write(w->user, c, 18)) return -1;
    if (ml && w->write(w->user, (const uint8_t *)msg, ml)) return -1;
    return 0;
}

// ---- version 2 ------------------------------------------------------------

static uint64_t sample_mask64(uint8_t bits) { return bits >= 64 ? ~(uint64_t)0 : (((uint64_t)1 << bits) - 1u); }

static size_t leb128_len(uint64_t v) { size_t n = 1; while (v >= 0x80u) { v >>= 7; n++; } return n; }

int vgacap_writer_runs(vgacap_writer_t *w, const uint64_t *values, const uint64_t *runs, uint32_t pair_count) {
    const vgacap_header_t *h = &w->header;
    if (h->version != 2 || pair_count == 0 || h->sample_bits == 0 || h->sample_bits > 64) return -1;
    unsigned vbytes = (h->sample_bits + 7u) / 8u;
    uint64_t bad = ~sample_mask64(h->sample_bits);
    if (h->clock_bit != VGACAP_CLOCK_BIT_NONE) {
        if (h->clock_bit >= h->sample_bits || h->clock_bit >= 64) return -1;
        bad |= (uint64_t)1 << h->clock_bit;
    }
    // Check every pair and size the chunk before writing anything.
    uint64_t len = 4;
    for (uint32_t i = 0; i < pair_count; i++) {
        if (runs[i] == 0 || (values[i] & bad) || (i && values[i] == values[i - 1])) return -1;
        len += vbytes + leb128_len(runs[i]);
        if (len > VGACAP_MAX_CHUNK_LEN) return -1;
    }
    if (write_chunk_head(w, VGACAP_TAG_RUNS, (uint32_t)len)) return -1;
    uint8_t *buf = w->scratch; size_t fill = 0;
    put_u32(buf, pair_count); fill = 4;
    // Batch pairs (at most 18 bytes each) through the scratch buffer.
    for (uint32_t i = 0; i < pair_count; i++) {
        if (fill + 18u > sizeof w->scratch) { if (w->write(w->user, buf, fill)) return -1; fill = 0; }
        fill += vgacap_run_pair_put(buf + fill, values[i], vbytes, runs[i]);
    }
    return w->write(w->user, buf, fill);
}

int vgacap_writer_block(vgacap_writer_t *w, uint64_t first_sample) {
    if (w->header.version != 2) return -1;
    uint8_t c[8]; put_u64(c, first_sample);
    if (write_chunk_head(w, VGACAP_TAG_BLOCK, 8)) return -1;
    return w->write(w->user, c, 8);
}

int vgacap_writer_meta(vgacap_writer_t *w, const char *text, size_t len) {
    if (w->header.version != 2 || len == 0 || len > VGACAP_META_MAX) return -1;
    if (write_chunk_head(w, VGACAP_TAG_META, (uint32_t)len)) return -1;
    return w->write(w->user, (const uint8_t *)text, len);
}
