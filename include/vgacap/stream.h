// SPDX-License-Identifier: Apache-2.0
// vgacap capture stream: a sequence of self-describing chunks.
//
// Chunk = tag[4] ASCII, u32 little-endian payload length, payload.
// The first chunk is always the VGCH header. See docs in mithro/tt-vga-capture
// (docs/superpowers/plans/2026-09-15-m2-stream-and-frame.md, "Stream format").
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

#define VGACAP_DESC_MAX 255
#define VGACAP_FLAG_FIRST_SAMPLE_MSB 0x01

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
    uint16_t version;                       // 1
    uint8_t  sample_bits;                   // 8, 12, 16 or 32
    uint8_t  mode;                          // enum vgacap_mode
    uint32_t clock_hz;                      // nominal project clock, 0 = unknown
    uint8_t  signal_map[VGACAP_SIG_COUNT];  // sample bit index per signal, or VGACAP_SIG_ABSENT
    uint8_t  samples_per_word;              // 1, 2, 4 or 8 samples per 32-bit word in RAW/FRAM
    uint8_t  flags;                         // VGACAP_FLAG_*
    uint8_t  desc_len;
    char     desc[VGACAP_DESC_MAX + 1];     // NUL-terminated free text
} vgacap_header_t;

// Fill a header for the Tiny VGA Pmod: signal_map = {7,3,0,4,1,5,2,6},
// version 1, mode UNKNOWN, clock 0, empty desc.
void vgacap_header_init_tinyvga(vgacap_header_t *h, uint8_t sample_bits,
                                uint8_t samples_per_word, uint8_t flags);

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
int vgacap_writer_events(vgacap_writer_t *w, const uint64_t *clocks, const uint32_t *values,
                         uint32_t count);
int vgacap_writer_time(vgacap_writer_t *w, uint64_t host_time_ns, uint32_t clock_hz,
                       uint32_t dropped, const char *msg);

// ---- reader -------------------------------------------------------------

typedef enum vgacap_event_type {
    VGACAP_EV_HEADER,       // u.header
    VGACAP_EV_RUN,          // u.run: `run` consecutive clocks of `value`
    VGACAP_EV_FRAME_BEGIN,  // u.frame: a FRAM chunk starts; its samples follow as RUNs
    VGACAP_EV_TIME,         // u.time
    VGACAP_EV_ERROR         // u.error; the reader stops accepting input
} vgacap_event_type_t;

typedef struct vgacap_event {
    vgacap_event_type_t type;
    union {
        const vgacap_header_t *header;
        struct { uint32_t value; uint32_t run; } run;
        struct {
            uint32_t frame_counter; uint16_t first_line; uint16_t line_count;
            uint32_t clocks_per_line; uint32_t sample_count;
        } frame;
        struct {
            uint64_t host_time_ns; uint32_t clock_hz; uint32_t dropped_samples;
            const char *msg; uint16_t msg_len;   // msg valid only during the callback
        } time;
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
} vgacap_reader_t;

void vgacap_reader_init(vgacap_reader_t *r, vgacap_event_fn cb, void *user);
// Feed any number of bytes; events are delivered from inside this call.
// Returns 0, or -1 after an ERROR event (further input is ignored).
int vgacap_reader_feed(vgacap_reader_t *r, const uint8_t *buf, size_t len);

#ifdef __cplusplus
}
#endif
#endif
