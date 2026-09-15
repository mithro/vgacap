// SPDX-License-Identifier: Apache-2.0
#include "vgacap/stream.h"
#include <string.h>

static uint16_t get_u16(const uint8_t *p) { return (uint16_t)(p[0] | (uint16_t)(p[1] << 8)); }
static uint32_t get_u32(const uint8_t *p) {
    return (uint32_t)p[0] | ((uint32_t)p[1] << 8) | ((uint32_t)p[2] << 16) | ((uint32_t)p[3] << 24);
}

static uint64_t get_u64(const uint8_t *p) { return (uint64_t)get_u32(p) | ((uint64_t)get_u32(p + 4) << 32); }

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
    if (tag_is(r, VGACAP_TAG_RLE) && r->length < 4) return fail(r, "bad RLE length");
    if (tag_is(r, VGACAP_TAG_FRAME) && r->length < 16) return fail(r, "bad FRAM length");
    if (tag_is(r, VGACAP_TAG_EVENT) && r->length < 4) return fail(r, "bad EVNT length");
    if (tag_is(r, VGACAP_TAG_TIME) && r->length < 18) return fail(r, "bad TIME length");
    return 0; // other tags handled below; unknown tags are skipped
}

static void emit_run(vgacap_reader_t *r, uint32_t value, uint32_t run);

static int end_payload(vgacap_reader_t *r) {
    if (tag_is(r, VGACAP_TAG_HEADER)) {
        vgacap_event_t ev; ev.type = VGACAP_EV_HEADER; ev.u.header = &r->header; r->have_header = 1;
        r->cb(r->user, &ev);
    } else if (tag_is(r, VGACAP_TAG_EVENT)) {
        if (r->have_last_event) emit_run(r, r->last_event_value, 1);
    } else if (tag_is(r, VGACAP_TAG_TIME)) {
        vgacap_event_t ev; ev.type = VGACAP_EV_TIME;
        ev.u.time.host_time_ns = get_u64(r->pbuf);
        ev.u.time.clock_hz = get_u32(r->pbuf + 8);
        ev.u.time.dropped_samples = get_u32(r->pbuf + 12);
        uint16_t ml = get_u16(r->pbuf + 16); if (ml > 255) ml = 255;
        ev.u.time.msg_len = ml; ev.u.time.msg = r->msgbuf;
        r->msgbuf[ml] = 0;
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

static int rle_byte(vgacap_reader_t *r, uint8_t b) {
    r->pbuf[r->pfill++] = b;
    if (r->consumed < 4) { if (r->pfill == 4) { r->remaining_items = get_u32(r->pbuf); r->pfill = 0; } return 0; }
    if (r->pfill == 8) { emit_run(r, get_u32(r->pbuf), get_u32(r->pbuf + 4)); r->pfill = 0; }
    return 0;
}

static int frame_byte(vgacap_reader_t *r, uint8_t b) {
    r->pbuf[r->pfill++] = b;
    if (r->consumed < 16) {
        if (r->pfill == 16) {
            vgacap_event_t ev; ev.type = VGACAP_EV_FRAME_BEGIN;
            ev.u.frame.frame_counter = get_u32(r->pbuf); ev.u.frame.first_line = get_u16(r->pbuf + 4);
            ev.u.frame.line_count = get_u16(r->pbuf + 6); ev.u.frame.clocks_per_line = get_u32(r->pbuf + 8);
            ev.u.frame.sample_count = get_u32(r->pbuf + 12); r->remaining_items = ev.u.frame.sample_count;
            r->cb(r->user, &ev); r->pfill = 0;
        }
        return 0;
    }
    if (r->pfill == 4) { unpack_word(r, get_u32(r->pbuf)); r->pfill = 0; }
    return 0;
}

static int event_byte(vgacap_reader_t *r, uint8_t b) {
    r->pbuf[r->pfill++] = b;
    if (r->consumed < 4) {
        if (r->pfill == 4) { r->remaining_items = get_u32(r->pbuf); r->pfill = 0; r->have_last_event = 0; }
        return 0;
    }
    if (r->pfill == 12) {
        uint64_t clk = get_u64(r->pbuf); uint32_t val = get_u32(r->pbuf + 8); r->pfill = 0;
        if (r->have_last_event) {
            if (clk <= r->last_event_clock) return fail(r, "event clock not increasing");
            emit_run(r, r->last_event_value, (uint32_t)(clk - r->last_event_clock));
        }
        r->last_event_clock = clk; r->last_event_value = val; r->have_last_event = 1;
    }
    return 0;
}

static int time_byte(vgacap_reader_t *r, uint8_t b) {
    if (r->consumed < 18) { r->pbuf[r->consumed] = b; return 0; }
    uint32_t mi = r->consumed - 18;
    if (mi < sizeof r->msgbuf - 1) r->msgbuf[mi] = (char)b;
    return 0;
}

static int payload_byte(vgacap_reader_t *r, uint8_t b) {
    if (tag_is(r, VGACAP_TAG_HEADER)) return header_byte(r, b);
    if (tag_is(r, VGACAP_TAG_RAW)) return raw_byte(r, b);
    if (tag_is(r, VGACAP_TAG_RLE)) return rle_byte(r, b);
    if (tag_is(r, VGACAP_TAG_FRAME)) return frame_byte(r, b);
    if (tag_is(r, VGACAP_TAG_EVENT)) return event_byte(r, b);
    if (tag_is(r, VGACAP_TAG_TIME)) return time_byte(r, b);
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
