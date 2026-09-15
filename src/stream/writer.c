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
