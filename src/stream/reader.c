// SPDX-License-Identifier: Apache-2.0
#include "vgacap/stream.h"
#include <string.h>

static uint16_t get_u16(const uint8_t *p) { return (uint16_t)(p[0] | (uint16_t)(p[1] << 8)); }
static uint32_t get_u32(const uint8_t *p) {
    return (uint32_t)p[0] | ((uint32_t)p[1] << 8) | ((uint32_t)p[2] << 16) | ((uint32_t)p[3] << 24);
}

static uint64_t get_u64(const uint8_t *p) { return (uint64_t)get_u32(p) | ((uint64_t)get_u32(p + 4) << 32); }

static uint32_t sample_mask(uint8_t bits) { return bits == 32 ? 0xFFFFFFFFu : ((1u << bits) - 1u); }
static uint64_t sample_mask64(uint8_t bits) { return bits >= 64 ? ~(uint64_t)0 : (((uint64_t)1 << bits) - 1u); }

void vgacap_reader_init(vgacap_reader_t *r, vgacap_event_fn cb, void *user) {
    memset(r, 0, sizeof *r); r->cb = cb; r->user = user;
}

void vgacap_reader_init_header(vgacap_reader_t *r, vgacap_event_fn cb, void *user, const vgacap_header_t *h) {
    vgacap_reader_init(r, cb, user);
    r->header = *h; r->have_header = 1;
    r->pos_known = 0;   // positioned by a seek: the block's BLKH gives n, silently
}

static int has_clock(const vgacap_reader_t *r) {
    return r->have_header && r->header.clock_bit != VGACAP_CLOCK_BIT_NONE;
}

static int fail(vgacap_reader_t *r, const char *what) {
    vgacap_event_t ev; ev.type = VGACAP_EV_ERROR; ev.u.error.what = what; r->cb(r->user, &ev);
    r->in_payload = 0; r->hdrfill = 0; r->failed = 1; return -1;
}

// Framing error: stop parsing this chunk and hunt for the next plausible
// chunk header. `already` seeds the skip count with the bytes of the current
// (bad) chunk that are known to be lost. Returns 1, the "framing error"
// sentinel the payload handlers propagate. Any loss in a stream with a clock
// bit makes the sample index unknown until the next BLKH.
static int resync(vgacap_reader_t *r, const char *what, uint32_t already) {
    r->scanning = 1; r->skipped = already; r->resync_what = what;
    r->scanfill = 0; r->in_payload = 0; r->hdrfill = 0; r->pfill = 0;
    r->consumed = 0; r->remaining_items = 0; r->sample_index = 0; r->have_last_event = 0;
    r->skip_what = NULL;
    if (has_clock(r)) r->pos_known = 0;
    return 1;
}

// Framing error detected part-way through a payload: the 8 header bytes plus
// everything consumed so far, including the byte being handled, are lost.
static int resync_here(vgacap_reader_t *r, const char *what) {
    return resync(r, what, 8u + r->consumed + 1u);
}

// Version 2's rule for well-framed chunks: a content error skips the rest of
// the chunk by its length (never a byte-wise search) and is reported, as a
// RESYNC, when the chunk ends. Returns 0: the caller keeps consuming bytes.
static int skip_rest(vgacap_reader_t *r, const char *what) {
    if (!r->skip_what) r->skip_what = what;
    return 0;
}

static int tag_is(const vgacap_reader_t *r, const char *t) { return memcmp(r->tag, t, 4) == 0; }
static int is_tag(const uint8_t *t, const char *name) { return memcmp(t, name, 4) == 0; }

static int tag_known(const uint8_t *t) {
    return is_tag(t, VGACAP_TAG_HEADER) || is_tag(t, VGACAP_TAG_RAW) || is_tag(t, VGACAP_TAG_RLE) ||
           is_tag(t, VGACAP_TAG_FRAME) || is_tag(t, VGACAP_TAG_EVENT) || is_tag(t, VGACAP_TAG_TIME) ||
           is_tag(t, VGACAP_TAG_RUNS) || is_tag(t, VGACAP_TAG_BLOCK) || is_tag(t, VGACAP_TAG_META);
}

static int tag_printable(const uint8_t *t) {
    for (int i = 0; i < 4; i++) if (t[i] < 0x20 || t[i] > 0x7E) return 0;
    return 1;
}

// The chunk types the current header forbids (the note's "Which chunks a
// header allows"). NULL = allowed. A forbidden chunk with a plausible length
// is skipped by that length and reported.
static const char *forbidden(const vgacap_reader_t *r, const uint8_t *tag) {
    if (is_tag(tag, VGACAP_TAG_HEADER)) return NULL;
    int v2_only = is_tag(tag, VGACAP_TAG_RUNS) || is_tag(tag, VGACAP_TAG_BLOCK) || is_tag(tag, VGACAP_TAG_META);
    if (r->header.version != 2) return v2_only ? "chunk needs a version 2 header" : NULL;
    if (is_tag(tag, VGACAP_TAG_RLE)) return "RLE  chunk not allowed in version 2";
    if (has_clock(r)) return (v2_only || is_tag(tag, VGACAP_TAG_TIME)) ? NULL : "chunk not allowed with a sample clock";
    if (r->header.sample_bits > 32 &&
        (is_tag(tag, VGACAP_TAG_RAW) || is_tag(tag, VGACAP_TAG_FRAME) || is_tag(tag, VGACAP_TAG_EVENT)))
        return "chunk not allowed with sample_bits over 32";
    return NULL;
}

// Everything about a (tag, length) pair that can be judged before any payload
// byte arrives. NULL = plausible. An unknown but printable tag with a sane
// length is plausible: unknown chunks are skipped, not resynced. So is any
// chunk the version 2 rules skip by length (forbidden ones, and RUNS, BLKH
// and META, whose lengths are checked when the chunk starts).
static const char *header_problem(const vgacap_reader_t *r, const uint8_t *tag, uint32_t length) {
    if (!tag_printable(tag)) return "non-printable chunk tag";
    if (length > VGACAP_MAX_CHUNK_LEN) return "chunk length too large";
    if (is_tag(tag, VGACAP_TAG_HEADER))
        return (length < 20 || length > 20u + VGACAP_DESC_MAX) ? "bad VGCH length" : NULL;
    if (!r->have_header) return "chunk before header";
    if (forbidden(r, tag)) return NULL;
    if (is_tag(tag, VGACAP_TAG_RAW) && length < 4) return "bad RAW length";
    if (is_tag(tag, VGACAP_TAG_RLE) && length < 4) return "bad RLE length";
    if (is_tag(tag, VGACAP_TAG_FRAME) && length < 16) return "bad FRAM length";
    if (is_tag(tag, VGACAP_TAG_EVENT) && length < 4) return "bad EVNT length";
    if (is_tag(tag, VGACAP_TAG_TIME) && length < 18) return "bad TIME length";
    return NULL;
}

// Why a chunk that has just started is to be skipped by its length, or NULL.
static const char *chunk_rule(const vgacap_reader_t *r) {
    const char *f = forbidden(r, r->tag);
    if (f) return f;
    if (tag_is(r, VGACAP_TAG_RUNS) && r->length < 4) return "RUNS pairs do not fill the chunk";
    if (tag_is(r, VGACAP_TAG_BLOCK) && r->length != 8) return "bad BLKH length";
    if (tag_is(r, VGACAP_TAG_META) && (r->length == 0 || r->length > VGACAP_META_MAX)) return "bad META length";
    if (tag_is(r, VGACAP_TAG_RUNS) && has_clock(r) && !r->pos_known)
        return "RUNS discarded: sample position unknown until BLKH";
    return NULL;
}

static void begin_payload(vgacap_reader_t *r) {
    r->consumed = 0; r->pfill = 0; r->sample_index = 0; r->remaining_items = 0;
    r->vfill = 0; r->lebcount = 0; r->have_prev = 0; r->pair_value = 0; r->pair_run = 0;
    r->skip_what = chunk_rule(r);
}

// The payload of a RAW/FRAM chunk is `head` bytes of fixed fields followed by
// ceil(count / samples_per_word) 32-bit words. Checked without ever forming
// 4 * words, which can overflow for a hostile count.
static int packed_count_ok(const vgacap_reader_t *r, uint32_t count, uint32_t head) {
    uint32_t body = r->length - head, spw = r->header.samples_per_word;
    if (!spw || (body & 3u)) return 0;
    return count / spw + (count % spw ? 1u : 0u) == body / 4u;
}

// Every delivered sample advances n. With a clock bit, the run's value gets
// the clock bit of its first sample, clock_first ^ (n & 1).
static void emit_run(vgacap_reader_t *r, uint64_t value, uint64_t run) {
    if (has_clock(r)) {
        uint64_t bit = (uint64_t)1 << r->header.clock_bit;
        value &= ~bit;
        if ((r->header.clock_first ^ (r->sample_pos & 1u)) & 1u) value |= bit;
    }
    vgacap_event_t ev; ev.type = VGACAP_EV_RUN; ev.u.run.value = value; ev.u.run.run = run;
    r->sample_pos += run;
    r->cb(r->user, &ev);
}

static void report(vgacap_reader_t *r, uint32_t skipped, const char *what) {
    vgacap_event_t ev; ev.type = VGACAP_EV_RESYNC; ev.u.resync.skipped = skipped; ev.u.resync.what = what;
    r->cb(r->user, &ev);
}

// ---- META text ----------------------------------------------------------

// Strict UTF-8: no overlong forms, no surrogates, nothing above U+10FFFF.
static int utf8_ok(const uint8_t *s, size_t n) {
    size_t i = 0;
    while (i < n) {
        uint8_t c = s[i];
        size_t k; uint32_t cp, min;
        if (c < 0x80) { i++; continue; }
        else if ((c & 0xE0) == 0xC0) { k = 1; cp = c & 0x1Fu; min = 0x80; }
        else if ((c & 0xF0) == 0xE0) { k = 2; cp = c & 0x0Fu; min = 0x800; }
        else if ((c & 0xF8) == 0xF0) { k = 3; cp = c & 0x07u; min = 0x10000; }
        else return 0;
        if (n - i <= k) return 0;
        for (size_t j = 1; j <= k; j++) {
            if ((s[i + j] & 0xC0) != 0x80) return 0;
            cp = (cp << 6) | (s[i + j] & 0x3Fu);
        }
        if (cp < min || cp > 0x10FFFF || (cp >= 0xD800 && cp <= 0xDFFF)) return 0;
        i += k + 1;
    }
    return 1;
}

static int span_is(const char *s, size_t n, const char *lit) { return strlen(lit) == n && memcmp(s, lit, n) == 0; }

static int key_ok(const char *k, size_t n) {
    if (n == 0 || k[0] < 'a' || k[0] > 'z') return 0;
    for (size_t i = 1; i < n; i++)
        if (!((k[i] >= 'a' && k[i] <= 'z') || (k[i] >= '0' && k[i] <= '9') || k[i] == '_')) return 0;
    return 1;
}

static int is_digit(char c) { return c >= '0' && c <= '9'; }

// An unsigned decimal integer that fits in 64 bits.
static int uint_ok(const char *v, size_t n) {
    uint64_t x = 0;
    if (n == 0) return 0;
    for (size_t i = 0; i < n; i++) {
        if (!is_digit(v[i])) return 0;
        unsigned d = (unsigned)(v[i] - '0');
        if (x > (~(uint64_t)0 - d) / 10u) return 0;
        x = x * 10u + d;
    }
    return 1;
}

// A decimal number in the form C strtod reads in the C locale: optional
// sign, digits with an optional '.', at least one digit, optional exponent.
// (No leading space, hex, inf or nan: the value is the whole of it.)
static int decimal_ok(const char *v, size_t n) {
    size_t i = 0, digits = 0;
    if (i < n && (v[i] == '+' || v[i] == '-')) i++;
    while (i < n && is_digit(v[i])) { i++; digits++; }
    if (i < n && v[i] == '.') { i++; while (i < n && is_digit(v[i])) { i++; digits++; } }
    if (!digits) return 0;
    if (i < n && (v[i] == 'e' || v[i] == 'E')) {
        size_t e = 0; i++;
        if (i < n && (v[i] == '+' || v[i] == '-')) i++;
        while (i < n && is_digit(v[i])) { i++; e++; }
        if (!e) return 0;
    }
    return i == n;
}

static int value_ok(const char *k, size_t kn, const char *v, size_t vn) {
    if (span_is(k, kn, "capture_after_clock") || span_is(k, kn, "frames")) return uint_ok(v, vn);
    if (span_is(k, kn, "calib_seconds") || span_is(k, kn, "seconds")) return decimal_ok(v, vn);
    if (span_is(k, kn, "status"))
        return span_is(v, vn, "ok") || span_is(v, vn, "sim-timeout") || span_is(v, vn, "unstable-sync") ||
               span_is(v, vn, "no-sync") || span_is(v, vn, "bad-timing");
    if (span_is(k, kn, "layout")) return span_is(v, vn, "tt-um-43");
    return 1;   // project, override, simulator, harness: free text; unknown keys: ignored
}

// The required keys, by bit: meta=open's four, then meta=close's three.
static const char *const meta_required[7] = {
    "project", "calib_seconds", "seconds", "layout", "capture_after_clock", "status", "frames",
};

static int meta_text_ok(const char *s, size_t n) {
    if (!utf8_ok((const uint8_t *)s, n) || n == 0 || s[n - 1] != '\n') return 0;
    unsigned seen = 0, need = 0;
    for (size_t line = 0; line < n;) {
        size_t end = line; while (s[end] != '\n') end++;
        const char *eq = memchr(s + line, '=', end - line);
        if (!eq) return 0;
        const char *k = s + line, *v = eq + 1;
        size_t kn = (size_t)(eq - k), vn = (size_t)(s + end - v);
        if (!key_ok(k, kn)) return 0;
        if (line == 0) {
            if (!span_is(k, kn, "meta")) return 0;
            if (span_is(v, vn, "open")) need = 0x0Fu;
            else if (span_is(v, vn, "close")) need = 0x70u;
            else return 0;
        }
        // A repeated key: compare with every earlier line's key.
        for (size_t p = 0; p < line;) {
            size_t pe = p; while (s[pe] != '=') pe++;
            if (pe - p == kn && memcmp(s + p, k, kn) == 0) return 0;
            while (s[p] != '\n') p++;
            p++;
        }
        if (!value_ok(k, kn, v, vn)) return 0;
        for (unsigned b = 0; b < 7; b++) if (span_is(k, kn, meta_required[b])) seen |= 1u << b;
        line = end + 1;
    }
    return (seen & need) == need;
}

// ---- chunk ends -----------------------------------------------------------

static int end_payload(vgacap_reader_t *r) {
    if (!r->skip_what && tag_is(r, VGACAP_TAG_RUNS) && (r->remaining_items || r->vfill || r->lebcount))
        r->skip_what = "RUNS pairs do not fill the chunk";
    if (r->skip_what) {
        // "Data lost, framing kept": the whole chunk is reported.
        report(r, 8u + r->length, r->skip_what);
        r->skip_what = NULL;
        if (has_clock(r)) r->pos_known = 0;
    } else if (tag_is(r, VGACAP_TAG_HEADER)) {
        vgacap_event_t ev; ev.type = VGACAP_EV_HEADER; ev.u.header = &r->header; r->have_header = 1;
        r->sample_pos = 0; r->pos_known = 1;
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
    } else if (tag_is(r, VGACAP_TAG_BLOCK)) {
        uint64_t first = get_u64(r->pbuf);
        // A gap or an overlap, found reading sequentially, is reported and
        // then adopted. With n unknown (after a loss, or a reader positioned
        // by a seek) the BLKH simply gives n.
        if (r->pos_known && first != r->sample_pos) report(r, 0, "BLKH first_sample does not follow");
        vgacap_event_t ev; ev.type = VGACAP_EV_BLOCK;
        ev.u.block.first_sample = first; ev.u.block.expected = r->sample_pos;
        r->sample_pos = first; r->pos_known = 1;
        r->cb(r->user, &ev);
    } else if (tag_is(r, VGACAP_TAG_META)) {
        r->metabuf[r->length] = 0;
        if (!meta_text_ok(r->metabuf, r->length)) {
            report(r, 8u + r->length, "bad META text");
            if (has_clock(r)) r->pos_known = 0;
        } else {
            vgacap_event_t ev; ev.type = VGACAP_EV_META;
            ev.u.meta.text = r->metabuf; ev.u.meta.len = (uint16_t)r->length;
            r->cb(r->user, &ev);
        }
    }
    r->in_payload = 0; r->hdrfill = 0; return 0;
}

// A chunk header has been accepted (in sequence, or by the resync scan).
static void open_chunk(vgacap_reader_t *r) {
    r->in_payload = 1;
    begin_payload(r);
    if (r->length == 0) end_payload(r);
}

// ---- payload bytes --------------------------------------------------------

// Header payload is small; accumulate the fixed 20 (v1) or 24 (v2) bytes in
// pbuf, then the desc. (pbuf, not msgbuf: sharing the scratch with TIME's
// message buffer used to let a malformed TIME chunk report the parsed VGCH
// bytes as its message.) Nothing is committed to r->header until every field
// validates, so a corrupt VGCH cannot replace a good one already in force.
static int header_byte(vgacap_reader_t *r, uint8_t b) {
    uint32_t i = r->consumed;
    const uint8_t *p = r->pbuf;
    uint32_t fixed = (i >= 20 && get_u16(p) == 2) ? 24u : 20u;
    if (i < fixed) {
        r->pbuf[i] = b;
        if (i == 19) {
            uint16_t version = get_u16(p);
            if (version != 1 && version != 2) return fail(r, "unsupported version");
            if (version == 2) return r->length < 24 ? resync_here(r, "bad VGCH length") : 0;
            uint8_t bits = p[2], spw = p[16];
            uint16_t dl = get_u16(p + 18);
            if (dl != r->length - 20) return resync_here(r, "VGCH desc length mismatch");
            if (!(bits == 8 || bits == 12 || bits == 16 || bits == 32))
                return resync_here(r, "bad sample_bits");
            if (!(spw == 1 || spw == 2 || spw == 4 || spw == 8) || (uint32_t)spw * bits > 32)
                return resync_here(r, "bad samples_per_word");
        } else if (i == 23) {
            uint8_t bits = p[2], spw = p[16];
            uint16_t dl = get_u16(p + 18);
            if (dl != r->length - 24) return resync_here(r, "VGCH desc length mismatch");
            if (bits < 1 || bits > 64) return resync_here(r, "bad sample_bits");
            if (bits > 32 ? spw != 0 : (!(spw == 1 || spw == 2 || spw == 4 || spw == 8) || (uint32_t)spw * bits > 32))
                return resync_here(r, "bad samples_per_word");
            uint8_t spc = p[20], cb = p[21], cf = p[22];
            int clock = cb != VGACAP_CLOCK_BIT_NONE;
            if (!(spc == 1 || spc == 2) || (clock && cb >= bits) || clock != (spc == 2) || cf > 1 ||
                (!clock && cf) || p[23] != 0)
                return resync_here(r, "bad VGCH v2 clock fields");
        } else {
            return 0;
        }
        // Every field checked: commit.
        r->header.version = get_u16(p);
        r->header.sample_bits = p[2]; r->header.mode = p[3]; r->header.clock_hz = get_u32(p + 4);
        memcpy(r->header.signal_map, p + 8, VGACAP_SIG_COUNT);
        r->header.samples_per_word = p[16]; r->header.flags = p[17];
        uint16_t dl = get_u16(p + 18);
        r->header.desc_len = (uint8_t)dl; r->header.desc[dl] = 0;
        if (fixed == 24) {
            r->header.samples_per_clock = p[20]; r->header.clock_bit = p[21]; r->header.clock_first = p[22];
        } else {
            r->header.samples_per_clock = 1; r->header.clock_bit = VGACAP_CLOCK_BIT_NONE; r->header.clock_first = 0;
        }
    } else {
        r->header.desc[i - fixed] = (char)b;
    }
    return 0;
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
            emit_run(r, r->last_event_value, clk - r->last_event_clock);
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

// RUNS, byte by byte: the u32 pair_count, then per pair `vbytes` value
// bytes and a LEB128 run. Each pair is delivered as soon as its run's last
// byte arrives; nothing is buffered beyond the pair being assembled.
static int runs_byte(vgacap_reader_t *r, uint8_t b) {
    const vgacap_header_t *h = &r->header;
    if (r->consumed < 4) {
        r->pbuf[r->consumed] = b;
        if (r->consumed == 3) {
            uint32_t n = get_u32(r->pbuf);
            r->vbytes = (uint8_t)((h->sample_bits + 7u) / 8u);
            if (n == 0) return skip_rest(r, "RUNS has no pairs");
            // A pair is at least vbytes + 1 bytes: a count the length cannot
            // hold is refused before any of its pairs is delivered. (Too few
            // pairs for the length shows at the first byte past the last.)
            if (r->length - 4u < (uint64_t)n * (r->vbytes + 1u))
                return skip_rest(r, "RUNS pairs do not fill the chunk");
            r->remaining_items = n;
        }
        return 0;
    }
    if (r->remaining_items == 0) return skip_rest(r, "RUNS pairs do not fill the chunk");
    if (r->vfill < r->vbytes) {
        r->pair_value |= (uint64_t)b << (8u * r->vfill);
        if (++r->vfill == r->vbytes) {
            if (has_clock(r) && ((r->pair_value >> h->clock_bit) & 1u))
                return skip_rest(r, "RUNS value has the clock bit set");
            if (r->pair_value & ~sample_mask64(h->sample_bits))
                return skip_rest(r, "RUNS value has bits above sample_bits");
            if (r->have_prev && r->pair_value == r->prev_value)
                return skip_rest(r, "RUNS adjacent pairs have equal values");
        }
        return 0;
    }
    // LEB128: at most 10 bytes, the 10th at most 0x01, minimally encoded.
    if (r->lebcount == 9 && b > 1u) return skip_rest(r, "RUNS run over 64 bits");
    r->pair_run |= (uint64_t)(b & 0x7Fu) << (7u * r->lebcount);
    r->lebcount++;
    if (b & 0x80u) return 0;
    if (b == 0) return skip_rest(r, r->lebcount == 1 ? "RUNS run is zero" : "RUNS run not minimally encoded");
    emit_run(r, r->pair_value, r->pair_run);
    r->prev_value = r->pair_value; r->have_prev = 1;
    r->pair_value = 0; r->pair_run = 0; r->vfill = 0; r->lebcount = 0;
    r->remaining_items--;
    return 0;
}

static int payload_byte(vgacap_reader_t *r, uint8_t b) {
    if (r->skip_what) return 0;   // skipping this chunk by its length
    if (tag_is(r, VGACAP_TAG_HEADER)) return header_byte(r, b);
    if (tag_is(r, VGACAP_TAG_RAW)) return raw_byte(r, b);
    if (tag_is(r, VGACAP_TAG_RLE)) return rle_byte(r, b);
    if (tag_is(r, VGACAP_TAG_FRAME)) return frame_byte(r, b);
    if (tag_is(r, VGACAP_TAG_EVENT)) return event_byte(r, b);
    if (tag_is(r, VGACAP_TAG_TIME)) return time_byte(r, b);
    if (tag_is(r, VGACAP_TAG_RUNS)) return runs_byte(r, b);
    if (tag_is(r, VGACAP_TAG_BLOCK)) { r->pbuf[r->consumed] = b; return 0; }      // length is 8
    if (tag_is(r, VGACAP_TAG_META)) { r->metabuf[r->consumed] = (char)b; return 0; } // length <= META_MAX
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
    report(r, r->skipped, r->resync_what);
    open_chunk(r);
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
                open_chunk(r);
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
