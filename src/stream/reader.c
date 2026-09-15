// SPDX-License-Identifier: Apache-2.0
#include "vgacap/stream.h"
#include <string.h>

static uint16_t get_u16(const uint8_t *p) { return (uint16_t)(p[0] | (uint16_t)(p[1] << 8)); }
static uint32_t get_u32(const uint8_t *p) {
    return (uint32_t)p[0] | ((uint32_t)p[1] << 8) | ((uint32_t)p[2] << 16) | ((uint32_t)p[3] << 24);
}

static uint32_t sample_mask(uint8_t bits) { return bits == 32 ? 0xFFFFFFFFu : ((1u << bits) - 1u); }

void vgacap_reader_init(vgacap_reader_t *r, vgacap_event_fn cb, void *user) {
    memset(r, 0, sizeof *r); r->cb = cb; r->user = user;
}

static int fail(vgacap_reader_t *r, const char *what) {
    vgacap_event_t ev; ev.type = VGACAP_EV_ERROR; ev.u.error.what = what; r->cb(r->user, &ev);
    r->in_payload = 0; r->hdrfill = 0; r->failed = 1; return -1;
}

static int tag_is(const vgacap_reader_t *r, const char *t) { return memcmp(r->tag, t, 4) == 0; }

static int begin_payload(vgacap_reader_t *r) {
    r->consumed = 0; r->pfill = 0; r->sample_index = 0; r->remaining_items = 0;
    if (tag_is(r, VGACAP_TAG_HEADER)) {
        if (r->length < 20 || r->length > 20u + VGACAP_DESC_MAX) return fail(r, "bad header length");
        return 0;
    }
    if (!r->have_header) return fail(r, "chunk before header");
    return 0; // other tags handled below; unknown tags are skipped
}

static int end_payload(vgacap_reader_t *r) {
    if (tag_is(r, VGACAP_TAG_HEADER)) {
        vgacap_event_t ev; ev.type = VGACAP_EV_HEADER; ev.u.header = &r->header; r->have_header = 1;
        r->cb(r->user, &ev);
    }
    r->in_payload = 0; r->hdrfill = 0; return 0;
}

// Header payload is small; accumulate the fixed 20 bytes in msgbuf, then the desc.
static int header_byte(vgacap_reader_t *r, uint8_t b) {
    uint32_t i = r->consumed;
    if (i < 20) {
        r->msgbuf[i] = (char)b;
        if (i == 19) {
            const uint8_t *p = (const uint8_t *)r->msgbuf;
            r->header.version = get_u16(p);
            if (r->header.version != 1) return fail(r, "unsupported version");
            r->header.sample_bits = p[2]; r->header.mode = p[3]; r->header.clock_hz = get_u32(p + 4);
            memcpy(r->header.signal_map, p + 8, VGACAP_SIG_COUNT);
            r->header.samples_per_word = p[16]; r->header.flags = p[17];
            uint16_t dl = get_u16(p + 18);
            if (dl != r->length - 20) return fail(r, "desc length mismatch");
            r->header.desc_len = (uint8_t)dl; r->header.desc[dl] = 0;
            if (!(r->header.sample_bits == 8 || r->header.sample_bits == 12 ||
                  r->header.sample_bits == 16 || r->header.sample_bits == 32))
                return fail(r, "bad sample_bits");
            uint8_t spw = r->header.samples_per_word;
            if (!(spw == 1 || spw == 2 || spw == 4 || spw == 8) ||
                (uint32_t)spw * r->header.sample_bits > 32)
                return fail(r, "bad samples_per_word");
        }
    } else {
        r->header.desc[i - 20] = (char)b;
    }
    return 0;
}

static void emit_run(vgacap_reader_t *r, uint32_t value, uint32_t run) {
    vgacap_event_t ev; ev.type = VGACAP_EV_RUN; ev.u.run.value = value; ev.u.run.run = run; r->cb(r->user, &ev);
}

static void unpack_word(vgacap_reader_t *r, uint32_t word) {
    uint32_t spw = r->header.samples_per_word, bits = r->header.sample_bits;
    uint32_t mask = sample_mask((uint8_t)bits);
    for (uint32_t s = 0; s < spw && r->sample_index < r->remaining_items; s++, r->sample_index++) {
        uint32_t slot = (r->header.flags & VGACAP_FLAG_FIRST_SAMPLE_MSB) ? spw - 1 - s : s;
        emit_run(r, (word >> (slot * bits)) & mask, 1);
    }
}

static int raw_byte(vgacap_reader_t *r, uint8_t b) {
    r->pbuf[r->pfill++] = b;
    if (r->consumed < 4) { if (r->pfill == 4) { r->remaining_items = get_u32(r->pbuf); r->pfill = 0; } return 0; }
    if (r->pfill == 4) { unpack_word(r, get_u32(r->pbuf)); r->pfill = 0; }
    return 0;
}

static int payload_byte(vgacap_reader_t *r, uint8_t b) {
    if (tag_is(r, VGACAP_TAG_HEADER)) return header_byte(r, b);
    if (tag_is(r, VGACAP_TAG_RAW)) return raw_byte(r, b);
    return 0; // skip unknown
}

int vgacap_reader_feed(vgacap_reader_t *r, const uint8_t *buf, size_t len) {
    if (r->failed) return -1;
    for (size_t i = 0; i < len; i++) {
        if (!r->in_payload) {
            r->hdrbuf[r->hdrfill++] = buf[i];
            if (r->hdrfill == 8) {
                memcpy(r->tag, r->hdrbuf, 4); r->length = get_u32(r->hdrbuf + 4); r->in_payload = 1;
                if (begin_payload(r)) return -1;
                if (r->length == 0 && end_payload(r)) return -1;
            }
        } else {
            if (payload_byte(r, buf[i])) return -1;
            r->consumed++;
            if (r->consumed == r->length && end_payload(r)) return -1;
        }
    }
    return 0;
}
