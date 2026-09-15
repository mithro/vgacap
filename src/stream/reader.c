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

// Framing error: stop parsing this chunk and hunt for the next plausible
// chunk header. `already` seeds the skip count with the bytes of the current
// (bad) chunk that are known to be lost. Returns 1, the "framing error"
// sentinel the payload handlers propagate.
static int resync(vgacap_reader_t *r, const char *what, uint32_t already) {
    r->scanning = 1; r->skipped = already; r->resync_what = what;
    r->scanfill = 0; r->in_payload = 0; r->hdrfill = 0; r->pfill = 0;
    r->consumed = 0; r->remaining_items = 0; r->sample_index = 0; r->have_last_event = 0;
    return 1;
}

// Framing error detected part-way through a payload: the 8 header bytes plus
// everything consumed so far, including the byte being handled, are lost.
static int resync_here(vgacap_reader_t *r, const char *what) {
    return resync(r, what, 8u + r->consumed + 1u);
}

static int tag_is(const vgacap_reader_t *r, const char *t) { return memcmp(r->tag, t, 4) == 0; }

static int tag_known(const uint8_t *t) {
    return memcmp(t, VGACAP_TAG_HEADER, 4) == 0 || memcmp(t, VGACAP_TAG_RAW, 4) == 0 ||
           memcmp(t, VGACAP_TAG_RLE, 4) == 0 || memcmp(t, VGACAP_TAG_FRAME, 4) == 0 ||
           memcmp(t, VGACAP_TAG_EVENT, 4) == 0 || memcmp(t, VGACAP_TAG_TIME, 4) == 0;
}

static int tag_printable(const uint8_t *t) {
    for (int i = 0; i < 4; i++) if (t[i] < 0x20 || t[i] > 0x7E) return 0;
    return 1;
}

// Everything about a (tag, length) pair that can be judged before any payload
// byte arrives. NULL = plausible. An unknown but printable tag with a sane
// length is plausible: unknown chunks are skipped, not resynced.
static const char *header_problem(const vgacap_reader_t *r, const uint8_t *tag, uint32_t length) {
    if (!tag_printable(tag)) return "non-printable chunk tag";
    if (length > VGACAP_MAX_CHUNK_LEN) return "chunk length too large";
    if (memcmp(tag, VGACAP_TAG_HEADER, 4) == 0)
        return (length < 20 || length > 20u + VGACAP_DESC_MAX) ? "bad VGCH length" : NULL;
    if (!r->have_header) return "chunk before header";
    if (memcmp(tag, VGACAP_TAG_RAW, 4) == 0 && length < 4) return "bad RAW length";
    if (memcmp(tag, VGACAP_TAG_RLE, 4) == 0 && length < 4) return "bad RLE length";
    if (memcmp(tag, VGACAP_TAG_FRAME, 4) == 0 && length < 16) return "bad FRAM length";
    if (memcmp(tag, VGACAP_TAG_EVENT, 4) == 0 && length < 4) return "bad EVNT length";
    if (memcmp(tag, VGACAP_TAG_TIME, 4) == 0 && length < 18) return "bad TIME length";
    return NULL;
}

static void begin_payload(vgacap_reader_t *r) {
    r->consumed = 0; r->pfill = 0; r->sample_index = 0; r->remaining_items = 0;
}

// The payload of a RAW/FRAM chunk is `head` bytes of fixed fields followed by
// ceil(count / samples_per_word) 32-bit words. Checked without ever forming
// 4 * words, which can overflow for a hostile count.
static int packed_count_ok(const vgacap_reader_t *r, uint32_t count, uint32_t head) {
    uint32_t body = r->length - head, spw = r->header.samples_per_word;
    if (!spw || (body & 3u)) return 0;
    return count / spw + (count % spw ? 1u : 0u) == body / 4u;
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
        // time_byte rejected the chunk unless msg_len == length - 18 <= 255,
        // so every one of these bytes was actually present in the payload.
        uint16_t ml = get_u16(r->pbuf + 16);
        ev.u.time.msg_len = ml; ev.u.time.msg = r->msgbuf;
        r->msgbuf[ml] = 0;
        r->cb(r->user, &ev);
    }
    r->in_payload = 0; r->hdrfill = 0; return 0;
}

// Header payload is small; accumulate the fixed 20 bytes in pbuf, then the
// desc. (pbuf, not msgbuf: sharing the scratch with TIME's message buffer
// used to let a malformed TIME chunk report the parsed VGCH bytes as its
// message.) Nothing is committed to r->header until every field validates,
// so a corrupt VGCH cannot replace a good one already in force.
static int header_byte(vgacap_reader_t *r, uint8_t b) {
    uint32_t i = r->consumed;
    if (i < 20) {
        r->pbuf[i] = b;
        if (i == 19) {
            const uint8_t *p = r->pbuf;
            uint16_t version = get_u16(p);
            if (version != 1) return fail(r, "unsupported version");
            uint8_t bits = p[2], spw = p[16];
            uint16_t dl = get_u16(p + 18);
            if (dl != r->length - 20) return resync_here(r, "VGCH desc length mismatch");
            if (!(bits == 8 || bits == 12 || bits == 16 || bits == 32))
                return resync_here(r, "bad sample_bits");
            if (!(spw == 1 || spw == 2 || spw == 4 || spw == 8) || (uint32_t)spw * bits > 32)
                return resync_here(r, "bad samples_per_word");
            r->header.version = version;
            r->header.sample_bits = bits; r->header.mode = p[3]; r->header.clock_hz = get_u32(p + 4);
            memcpy(r->header.signal_map, p + 8, VGACAP_SIG_COUNT);
            r->header.samples_per_word = spw; r->header.flags = p[17];
            r->header.desc_len = (uint8_t)dl; r->header.desc[dl] = 0;
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
    if (r->consumed < 4) {
        if (r->pfill == 4) {
            r->remaining_items = get_u32(r->pbuf); r->pfill = 0;
            if (!packed_count_ok(r, r->remaining_items, 4))
                return resync_here(r, "RAW sample count does not match chunk length");
        }
        return 0;
    }
    if (r->pfill == 4) { unpack_word(r, get_u32(r->pbuf)); r->pfill = 0; }
    return 0;
}

static int rle_byte(vgacap_reader_t *r, uint8_t b) {
    r->pbuf[r->pfill++] = b;
    if (r->consumed < 4) {
        if (r->pfill == 4) {
            r->remaining_items = get_u32(r->pbuf); r->pfill = 0;
            uint32_t body = r->length - 4;
            if ((body % 8u) != 0 || body / 8u != r->remaining_items)
                return resync_here(r, "RLE pair count does not match chunk length");
        }
        return 0;
    }
    if (r->pfill == 8) { emit_run(r, get_u32(r->pbuf), get_u32(r->pbuf + 4)); r->pfill = 0; }
    return 0;
}

static int frame_byte(vgacap_reader_t *r, uint8_t b) {
    r->pbuf[r->pfill++] = b;
    if (r->consumed < 16) {
        if (r->pfill == 16) {
            uint32_t sample_count = get_u32(r->pbuf + 12);
            if (!packed_count_ok(r, sample_count, 16))
                return resync_here(r, "FRAM sample count does not match chunk length");
            vgacap_event_t ev; ev.type = VGACAP_EV_FRAME_BEGIN;
            ev.u.frame.frame_counter = get_u32(r->pbuf); ev.u.frame.first_line = get_u16(r->pbuf + 4);
            ev.u.frame.line_count = get_u16(r->pbuf + 6); ev.u.frame.clocks_per_line = get_u32(r->pbuf + 8);
            ev.u.frame.sample_count = sample_count; r->remaining_items = sample_count;
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
        if (r->pfill == 4) {
            r->remaining_items = get_u32(r->pbuf); r->pfill = 0; r->have_last_event = 0;
            uint32_t body = r->length - 4;
            if ((body % 12u) != 0 || body / 12u != r->remaining_items)
                return resync_here(r, "EVNT event count does not match chunk length");
        }
        return 0;
    }
    if (r->pfill == 12) {
        uint64_t clk = get_u64(r->pbuf); uint32_t val = get_u32(r->pbuf + 8); r->pfill = 0;
        if (r->have_last_event) {
            if (clk <= r->last_event_clock) return resync_here(r, "EVNT clock not increasing");
            emit_run(r, r->last_event_value, (uint32_t)(clk - r->last_event_clock));
        }
        r->last_event_clock = clk; r->last_event_value = val; r->have_last_event = 1;
    }
    return 0;
}

static int time_byte(vgacap_reader_t *r, uint8_t b) {
    if (r->consumed < 18) {
        r->pbuf[r->consumed] = b;
        if (r->consumed == 17) {
            uint16_t ml = get_u16(r->pbuf + 16);
            // msg_len must name bytes that are actually in this payload;
            // otherwise the message would be whatever the scratch last held.
            if (ml > 255 || r->length - 18u != ml)
                return resync_here(r, "TIME msg_len does not match chunk length");
        }
        return 0;
    }
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

// Scan state: a rolling 8-byte window over the input, looking for a *known*
// tag followed by a plausible length. Unknown tags are deliberately not
// accepted here - any four printable bytes would match, which is exactly the
// hole that let a mis-framed stream parse as "success". Every byte that
// falls out of the window without matching is a byte lost to the corruption.
static void scan_byte(vgacap_reader_t *r, uint8_t b) {
    if (r->scanfill == 8) { memmove(r->scanbuf, r->scanbuf + 1, 7); r->scanfill = 7; r->skipped++; }
    r->scanbuf[r->scanfill++] = b;
    if (r->scanfill < 8) return;
    uint32_t length = get_u32(r->scanbuf + 4);
    if (!tag_known(r->scanbuf) || header_problem(r, r->scanbuf, length)) return;
    memcpy(r->tag, r->scanbuf, 4); r->length = length;
    r->scanning = 0; r->scanfill = 0;
    vgacap_event_t ev; ev.type = VGACAP_EV_RESYNC;
    ev.u.resync.skipped = r->skipped; ev.u.resync.what = r->resync_what;
    r->cb(r->user, &ev);
    begin_payload(r);
    r->in_payload = 1;   // every known tag has a non-zero minimum length
}

int vgacap_reader_feed(vgacap_reader_t *r, const uint8_t *buf, size_t len) {
    if (r->failed) return -1;
    for (size_t i = 0; i < len; i++) {
        if (r->scanning) { scan_byte(r, buf[i]); continue; }
        if (!r->in_payload) {
            r->hdrbuf[r->hdrfill++] = buf[i];
            if (r->hdrfill == 8) {
                memcpy(r->tag, r->hdrbuf, 4); r->length = get_u32(r->hdrbuf + 4);
                const char *bad = header_problem(r, r->tag, r->length);
                if (bad) {
                    // The real header may start inside these eight bytes (a
                    // deleted byte shifts everything left), so re-examine
                    // them byte-wise rather than discarding them wholesale.
                    uint8_t saved[8]; memcpy(saved, r->hdrbuf, 8);
                    resync(r, bad, 0);
                    for (int k = 0; k < 8; k++) scan_byte(r, saved[k]);
                    continue;
                }
                r->in_payload = 1;
                begin_payload(r);
                if (r->length == 0 && end_payload(r)) return -1;
            }
        } else {
            int rc = payload_byte(r, buf[i]);
            if (rc < 0) return -1;
            if (rc > 0) continue;   // framing error; resync() already armed the scan
            r->consumed++;
            if (r->consumed == r->length && end_payload(r)) return -1;
        }
    }
    return 0;
}
