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
}

static int write_chunk_head(vgacap_writer_t *w, const char tag[4], uint32_t length) {
    uint8_t head[8]; memcpy(head, tag, 4); put_u32(head + 4, length);
    return w->write(w->user, head, 8);
}

int vgacap_writer_init(vgacap_writer_t *w, vgacap_write_fn write, void *user, const vgacap_header_t *h) {
    memset(w, 0, sizeof *w); w->write = write; w->user = user; w->header = *h;
    uint8_t *p = w->scratch;
    put_u16(p + 0, h->version); p[2] = h->sample_bits; p[3] = h->mode; put_u32(p + 4, h->clock_hz);
    memcpy(p + 8, h->signal_map, VGACAP_SIG_COUNT); p[16] = h->samples_per_word; p[17] = h->flags;
    put_u16(p + 18, h->desc_len);
    if (write_chunk_head(w, VGACAP_TAG_HEADER, 20u + h->desc_len)) return -1;
    if (w->write(w->user, p, 20)) return -1;
    if (h->desc_len && w->write(w->user, (const uint8_t *)h->desc, h->desc_len)) return -1;
    return 0;
}

static uint32_t sample_mask(uint8_t bits) { return bits == 32 ? 0xFFFFFFFFu : ((1u << bits) - 1u); }

int vgacap_writer_raw(vgacap_writer_t *w, const uint32_t *words, uint32_t sample_count) {
    uint32_t spw = w->header.samples_per_word;
    uint32_t nwords = (sample_count + spw - 1) / spw;
    if (write_chunk_head(w, VGACAP_TAG_RAW, 4u + 4u * nwords)) return -1;
    uint8_t c[4]; put_u32(c, sample_count); if (w->write(w->user, c, 4)) return -1;
    for (uint32_t i = 0; i < nwords; i++) { put_u32(c, words[i]); if (w->write(w->user, c, 4)) return -1; }
    return 0;
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
