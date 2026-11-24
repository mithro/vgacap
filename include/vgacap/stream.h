// SPDX-License-Identifier: Apache-2.0
// vgacap capture stream: a sequence of self-describing chunks.
//
// Chunk = tag[4] ASCII, u32 little-endian payload length, payload.
// The first chunk is always the VGCH header. See docs in mithro/tt-vga-capture
// (docs/superpowers/plans/2026-09-15-m2-stream-and-frame.md, "Stream format";
// version 2 -- wide samples, RUNS, BLKH and META -- is
// docs/design/2026-11-25-format-v2-runs.md).
#ifndef VGACAP_STREAM_H
#define VGACAP_STREAM_H

#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

#define VGACAP_TAG_HEADER "VGCH"
#define VGACAP_TAG_RAW    "RAW "
#define VGACAP_TAG_RLE    "RLE "
#define VGACAP_TAG_FRAME  "FRAM"
#define VGACAP_TAG_EVENT  "EVNT"
#define VGACAP_TAG_TIME   "TIME"
// Version 2 only (a version 1 stream never carries them).
#define VGACAP_TAG_RUNS   "RUNS"   // u32 pair_count, then (vbytes value, LEB128 run) pairs
#define VGACAP_TAG_BLOCK  "BLKH"   // u64 first_sample: sets the sample index n
#define VGACAP_TAG_META   "META"   // 1..VGACAP_META_MAX bytes of key=value lines

#define VGACAP_META_MAX 4096
#define VGACAP_CLOCK_BIT_NONE 0xFF
// Version 2 keeps the whole VGCH chunk within version 1's 20 + 255 bytes,
// so a version 1 reader always reaches its "unsupported version" error.
#define VGACAP_DESC_MAX_V2 251

#define VGACAP_DESC_MAX 255
#define VGACAP_FLAG_FIRST_SAMPLE_MSB 0x01

// Largest payload a chunk may declare. The format has no sync word, so a
// plausibility bound on the length is half of what lets the reader tell a
// real chunk header from four arbitrary bytes (the other half is the tag
// whitelist); it is also what stops a corrupt length from swallowing the
// rest of a capture. 16 MiB is ~2 s of uncompressed 640x480@60 samples,
// far more than any writer here emits in one chunk.
#define VGACAP_MAX_CHUNK_LEN (16u * 1024u * 1024u)

enum vgacap_mode {
    VGACAP_MODE_EXTCLK = 0,   // sampled on the project's external clock edge
    VGACAP_MODE_SELFCLK = 1,  // the capture hardware generated the clock
    VGACAP_MODE_EVENT = 2,    // change events from a simulator or analyser
    VGACAP_MODE_UNKNOWN = 3
};

// Index into vgacap_header_t::signal_map.
enum vgacap_signal {
    VGACAP_SIG_HSYNC = 0,
    VGACAP_SIG_VSYNC,
    VGACAP_SIG_R1,
    VGACAP_SIG_R0,
    VGACAP_SIG_G1,
    VGACAP_SIG_G0,
    VGACAP_SIG_B1,
    VGACAP_SIG_B0,
    VGACAP_SIG_COUNT
};
#define VGACAP_SIG_ABSENT 0xFF

typedef struct vgacap_header {
    uint16_t version;                       // 1 or 2
    uint8_t  sample_bits;                   // v1: 8, 12, 16 or 32; v2: 1..64
    uint8_t  mode;                          // enum vgacap_mode
    uint32_t clock_hz;                      // nominal project clock, 0 = unknown
    uint8_t  signal_map[VGACAP_SIG_COUNT];  // sample bit index per signal, or VGACAP_SIG_ABSENT
    uint8_t  samples_per_word;              // 1, 2, 4 or 8 samples per 32-bit word in RAW/FRAM;
                                            // v2: 0 when sample_bits > 32
    uint8_t  flags;                         // VGACAP_FLAG_*
    uint8_t  desc_len;                      // v2: at most VGACAP_DESC_MAX_V2
    char     desc[VGACAP_DESC_MAX + 1];     // NUL-terminated free text
    // Version 2 fields; a version 1 header reads as 1, NONE, 0.
    uint8_t  samples_per_clock;             // 1, or 2 exactly when there is a clock bit
    uint8_t  clock_bit;                     // 0..sample_bits-1, or VGACAP_CLOCK_BIT_NONE
    uint8_t  clock_first;                   // the clock bit's value in sample 0 (0 with no clock bit)
} vgacap_header_t;

// Fill a header for the Tiny VGA Pmod: signal_map = {7,3,0,4,1,5,2,6},
// version 1, mode UNKNOWN, clock 0, empty desc, one sample a clock, no
// clock bit.
void vgacap_header_init_tinyvga(vgacap_header_t *h, uint8_t sample_bits,
                                uint8_t samples_per_word, uint8_t flags);
// A version 2 header: the Tiny VGA map on bits 0-7, mode UNKNOWN, clock 0,
// empty desc, samples_per_word 0 when sample_bits > 32 and 1 otherwise.
void vgacap_header_init_v2(vgacap_header_t *h, uint8_t sample_bits, uint8_t samples_per_clock,
                           uint8_t clock_bit, uint8_t clock_first);

// LEB128 of v into p (at most 10 bytes); returns the bytes written, 1..10.
size_t vgacap_leb128_put(uint8_t *p, uint64_t v);
// One RUNS pair: `vbytes` = (sample_bits + 7) / 8 little-endian value bytes,
// then the LEB128 run. Returns the bytes written (vbytes + 1..10).
size_t vgacap_run_pair_put(uint8_t *p, uint64_t value, unsigned vbytes, uint64_t run);
// The same two encoders inline, for a caller whose loop is the hot path (the
// trace writer, a pair per run). vgacap_leb128_put and vgacap_run_pair_put
// are these; there is one implementation.
static inline size_t vgacap_leb128_put_inline(uint8_t *p, uint64_t v) {
    size_t n = 0;
    while (v >= 0x80u) { p[n++] = (uint8_t)(v | 0x80u); v >>= 7; }
    p[n++] = (uint8_t)v;
    return n;
}
static inline size_t vgacap_run_pair_put_inline(uint8_t *p, uint64_t value, unsigned vbytes, uint64_t run) {
    for (unsigned i = 0; i < vbytes; i++) p[i] = (uint8_t)(value >> (8u * i));
    return vbytes + vgacap_leb128_put_inline(p + vbytes, run);
}

// Pack n samples into 32-bit words per the header's packing; words must hold
// ceil(n / samples_per_word) entries. Returns the number of words written.
uint32_t vgacap_pack_samples(const vgacap_header_t *h, const uint32_t *samples,
                             uint32_t n, uint32_t *words);

// ---- writer -------------------------------------------------------------

typedef int (*vgacap_write_fn)(void *user, const uint8_t *buf, size_t len); // 0 = ok

typedef struct vgacap_writer {
    vgacap_write_fn write;
    void *user;
    vgacap_header_t header;
    uint8_t scratch[64];
} vgacap_writer_t;

// Writes the VGCH chunk immediately. Returns 0 on success.
int vgacap_writer_init(vgacap_writer_t *w, vgacap_write_fn write, void *user,
                       const vgacap_header_t *h);
// The same writer for a stream whose VGCH chunk somebody else has written:
// nothing is emitted, `h` only tells the chunk writers how samples pack.
// This is the capture board's case -- the host knows the signal map, mode
// and clock and writes the header, the board appends its chunks after it
// (firmware/PROTOCOL.md).
void vgacap_writer_attach(vgacap_writer_t *w, vgacap_write_fn write, void *user,
                          const vgacap_header_t *h);
// RAW chunk from already-packed words (ceil(sample_count / spw) of them).
int vgacap_writer_raw(vgacap_writer_t *w, const uint32_t *words, uint32_t sample_count);
// RAW chunk from unpacked samples; wordbuf is scratch, -1 if too small.
int vgacap_writer_raw_samples(vgacap_writer_t *w, const uint32_t *samples, uint32_t sample_count,
                              uint32_t *wordbuf, size_t wordbuf_len);
int vgacap_writer_rle(vgacap_writer_t *w, const uint32_t *values, const uint32_t *runs,
                      uint32_t pair_count);
int vgacap_writer_frame(vgacap_writer_t *w, uint32_t frame_counter, uint16_t first_line,
                        uint16_t line_count, uint32_t clocks_per_line,
                        const uint32_t *words, uint32_t sample_count);
// The same FRAM chunk with its packed words in several pieces, in order (a
// capture into chained buffers): the head, which declares all
// ceil(sample_count / spw) words, then each piece through
// vgacap_writer_frame_words(). The caller writes exactly that many words.
int vgacap_writer_frame_begin(vgacap_writer_t *w, uint32_t frame_counter, uint16_t first_line,
                              uint16_t line_count, uint32_t clocks_per_line, uint32_t sample_count);
int vgacap_writer_frame_words(vgacap_writer_t *w, const uint32_t *words, uint32_t nwords);
int vgacap_writer_events(vgacap_writer_t *w, const uint64_t *clocks, const uint32_t *values,
                         uint32_t count);
int vgacap_writer_time(vgacap_writer_t *w, uint64_t host_time_ns, uint32_t clock_hz,
                       uint32_t dropped, const char *msg);
// Version 2 chunks; each returns -1, writing nothing, under a version 1
// header. RUNS: values with the clock bit stored as 0 and no bits at or
// above sample_bits, runs >= 1, adjacent values different, 1 <= pair_count
// and the chunk within VGACAP_MAX_CHUNK_LEN; anything else is -1, writing
// nothing.
int vgacap_writer_runs(vgacap_writer_t *w, const uint64_t *values, const uint64_t *runs,
                       uint32_t pair_count);
int vgacap_writer_block(vgacap_writer_t *w, uint64_t first_sample);
// META: 1..VGACAP_META_MAX bytes, written as given (the reader checks the text).
int vgacap_writer_meta(vgacap_writer_t *w, const char *text, size_t len);

// ---- reader -------------------------------------------------------------

typedef enum vgacap_event_type {
    VGACAP_EV_HEADER,       // u.header
    // u.run: `run` consecutive samples (not clocks: with samples_per_clock
    // 2 a clock is two samples). `value` is the run's first sample, its
    // clock bit (if any) regenerated as clock_first ^ (n & 1); the clock
    // bit of each later sample in the run toggles.
    VGACAP_EV_RUN,
    VGACAP_EV_FRAME_BEGIN,  // u.frame: a FRAM chunk starts; its samples follow as RUNs
    VGACAP_EV_TIME,         // u.time
    VGACAP_EV_RESYNC,       // u.resync; non-fatal, see below
    VGACAP_EV_ERROR,        // u.error; the reader stops accepting input
    VGACAP_EV_BLOCK,        // u.block: a BLKH chunk
    VGACAP_EV_META          // u.meta: a META chunk whose text is well formed
} vgacap_event_type_t;

typedef struct vgacap_event {
    vgacap_event_type_t type;
    union {
        const vgacap_header_t *header;
        struct { uint64_t value; uint64_t run; } run;
        struct {
            uint32_t frame_counter; uint16_t first_line; uint16_t line_count;
            uint32_t clocks_per_line; uint32_t sample_count;
        } frame;
        struct {
            uint64_t host_time_ns; uint32_t clock_hz; uint32_t dropped_samples;
            const char *msg; uint16_t msg_len;   // msg valid only during the callback
        } time;
        // Data was lost. Either chunk framing was lost and has now been
        // regained: `skipped` bytes of input were discarded, `what` says what
        // first looked wrong, delivered once per episode at the moment the
        // next plausible chunk header is found. Or ("data lost, framing
        // kept") a well-framed chunk was skipped by its length, a v2 rule:
        // a RUNS/BLKH/META content error, a chunk the header forbids, or a
        // RUNS chunk discarded while the sample index is unknown; then
        // `skipped` is the whole chunk (8 + length), reported at its end.
        // A BLKH whose first_sample is not the running n is reported with
        // skipped = 0 just before its BLOCK event.
        struct { uint32_t skipped; const char *what; } resync;
        // `expected` is the reader's running sample index before the chunk.
        struct { uint64_t first_sample; uint64_t expected; } block;
        struct { const char *text; uint16_t len; } meta;   // text valid only during the callback
        struct { const char *what; } error;
    } u;
} vgacap_event_t;

typedef void (*vgacap_event_fn)(void *user, const vgacap_event_t *ev);

typedef struct vgacap_reader {
    vgacap_event_fn cb;
    void *user;
    vgacap_header_t header;
    int have_header;
    int failed;
    // chunk framing
    uint8_t  tag[4];
    uint32_t length;
    uint32_t consumed;
    int      in_payload;
    uint8_t  hdrbuf[8];
    uint8_t  hdrfill;
    // payload assembly
    uint8_t  pbuf[24];
    uint8_t  pfill;
    uint32_t remaining_items;
    uint32_t sample_index;
    uint64_t last_event_clock;
    uint32_t last_event_value;
    int      have_last_event;
    char     msgbuf[256];
    // Resynchronisation. On a framing error the reader does not stop: it
    // scans the input byte-wise for the next plausible chunk header (a known
    // tag plus a length <= VGACAP_MAX_CHUNK_LEN) and reports the loss as a
    // VGACAP_EV_RESYNC event when it finds one.
    int         scanning;      // 1 while hunting for the next chunk header
    uint32_t    skipped;       // bytes discarded in the current episode
    const char *resync_what;   // what first looked wrong
    uint8_t     scanbuf[8];    // rolling tag+length candidate window
    uint8_t     scanfill;
    // Version 2. n, the index of the next sample to deliver: 0 after a VGCH,
    // advanced by every delivered sample, set by each BLKH.
    uint64_t    sample_pos;
    // 0 while n is unknown: after a loss that could have carried samples, in
    // a stream with a clock bit, or before the first BLKH of a reader made by
    // vgacap_reader_init_header. RUNS chunks are then discarded (and
    // reported) until a BLKH. Losses of META or TIME alone leave n known.
    int         pos_known;
    // A byte-wise resync that began on a TIME chunk: 8 + its length. If the
    // scan regains framing after exactly that many bytes, only the TIME was
    // lost and n stays known. 0 otherwise.
    uint32_t    resync_time_len;
    const char *skip_what;     // non-NULL: skipping this chunk by its length, report at its end
    // RUNS pair assembly
    uint8_t     vbytes, vfill, lebcount, have_prev;
    uint64_t    pair_value, pair_run, prev_value;
    char        metabuf[VGACAP_META_MAX + 1];
} vgacap_reader_t;

void vgacap_reader_init(vgacap_reader_t *r, vgacap_event_fn cb, void *user);
// A reader that already knows the header, for decoding a block other than
// block 0 of a trace: no VGCH is expected and no HEADER event is emitted.
// n is unknown until the block's BLKH, which is adopted without a report.
void vgacap_reader_init_header(vgacap_reader_t *r, vgacap_event_fn cb, void *user,
                               const vgacap_header_t *h);
// Feed any number of bytes; events are delivered from inside this call.
// Returns 0, or -1 after an ERROR event (further input is ignored). A
// framing error is not an ERROR: it produces a VGACAP_EV_RESYNC (once sync
// is regained) and feed keeps returning 0. If the input ends while
// `scanning` is still set, framing was never regained and `skipped` bytes
// have been discarded with no event - callers that care should report it.
int vgacap_reader_feed(vgacap_reader_t *r, const uint8_t *buf, size_t len);

#ifdef __cplusplus
}
#endif
#endif
