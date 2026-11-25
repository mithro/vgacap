// SPDX-License-Identifier: Apache-2.0
#include "fw_capture.h"

#include <stdarg.h>
#include <stdio.h>
#include <string.h>

#include "fw_args.h"
#include "fw_clock.h"
#include "fw_hal.h"
#include "fw_pio.h"
#include "vgacap/stream.h"

// Reads of a sync line used to find its idle level, and the gap between
// them: 2000 x 10 us = 20 ms, longer than a 60 Hz frame, so even the vsync
// pulse (2 of 525 lines) is a small minority whichever frame it falls in.
// frame_rp2.py takes its 2000 reads back to back, which on MicroPython
// is ~2 ms; at C speed back-to-back reads would span a few microseconds,
// short enough to land inside the vsync pulse and read it as idle.
#define POLARITY_READS 2000u
#define POLARITY_READ_GAP_US 10u

static const char *const EDGES[] = { "falling", "rising", NULL };

static int refuse(char *why, size_t whylen, const char *fmt, ...) {
    va_list ap;
    va_start(ap, fmt);
    vsnprintf(why, whylen, fmt, ap);
    va_end(ap);
    return -1;
}

// ---- arguments ---------------------------------------------------------------------

int fw_stream_parse(const char *text, size_t arena_bytes, fw_stream_args_t *a, char *why,
                    size_t whylen) {
    const fw_board_t *b = hal_board();
    const fw_arg_spec_t specs[] = {
        { "buf_words", 1, (int64_t)(arena_bytes / 8u), NULL },
        { "max_bytes", 0, UINT32_MAX, NULL },
        { "edge", 0, 0, EDGES },
        { "pio", 0, (int64_t)b->pio_count - 1, NULL },
        { "sm", 0, 3, NULL },
    };
    int64_t v[5];
    if (fw_args_parse(text, specs, sizeof specs / sizeof specs[0], v, why, whylen)) return -1;
    a->buf_words = (uint32_t)v[0];
    a->max_bytes = (uint32_t)v[1];
    a->rising = (unsigned)v[2];
    a->pio = (unsigned)v[3];
    a->sm = (unsigned)v[4];
    return 0;
}

int fw_frame_parse(const char *text, int with_clock_hz, fw_frame_args_t *a, char *why,
                   size_t whylen) {
    const fw_board_t *b = hal_board();
    const fw_arg_spec_t specs[] = {
        { "cpl", 1, 65535, NULL },          { "region_first", 0, 65535, NULL },
        { "region_lines", 1, 65535, NULL }, { "window_lines", 0, 65535, NULL },
        { "hsync_active", -1, 1, NULL },    { "vsync_active", -1, 1, NULL },
        { "max_frames", 0, UINT32_MAX, NULL }, { "max_bytes", 0, UINT32_MAX, NULL },
        { "edge", 0, 0, EDGES },            { "sample_delay", 0, 31, NULL },
        { "pio", 0, (int64_t)b->pio_count - 1, NULL }, { "sm", 0, 3, NULL },
        { "clock_hz", 1, 125000000, NULL },  // the clock command's range
    };
    int64_t v[13];
    size_t n = sizeof specs / sizeof specs[0] - (with_clock_hz ? 0u : 1u);
    if (fw_args_parse(text, specs, n, v, why, whylen)) return -1;
    a->cpl = (uint32_t)v[0];
    a->region_first = (uint32_t)v[1];
    a->region_lines = (uint32_t)v[2];
    a->window_lines = (uint32_t)v[3];
    a->hsync_active = (int)v[4];
    a->vsync_active = (int)v[5];
    a->max_frames = (uint32_t)v[6];
    a->max_bytes = (uint32_t)v[7];
    a->rising = (unsigned)v[8];
    a->sample_delay = (unsigned)v[9];
    a->pio = (unsigned)v[10];
    a->sm = (unsigned)v[11];
    a->clock_hz = with_clock_hz ? (uint32_t)v[12] : 0u;
    return 0;
}

size_t fw_frame_wanted_bytes(const fw_frame_args_t *a) {
    const fw_board_t *b = hal_board();
    uint32_t spw = b->push_thresh / b->in_count;
    if (!a->cpl || a->cpl % spw) return 0;
    uint32_t lines = a->region_lines;
    if (a->window_lines && a->window_lines < lines) lines = a->window_lines;
    return (size_t)lines * (a->cpl / spw) * 4u;
}

// ---- capture output -------------------------------------------------------------------

typedef struct out {
    vgacap_writer_t w;
    uint64_t sent;  // chunk bytes, as capture_rp2.py / frame_rp2.py count `sent`
    int lost;       // the host went away: stop, and write nothing more
} out_t;

static int out_write(void *user, const uint8_t *buf, size_t len) {
    out_t *o = user;
    if (o->lost) return -1;
    if (hal_write(buf, len)) {
        o->lost = 1;
        return -1;
    }
    o->sent += len;
    return 0;
}

static void out_init(out_t *o) {
    const fw_board_t *b = hal_board();
    vgacap_header_t h;
    // Only the packing matters to the chunk writers: the host writes the
    // real VGCH header, with the signal map, mode and clock.
    vgacap_header_init_tinyvga(&h, (uint8_t)b->in_count, (uint8_t)(b->push_thresh / b->in_count),
                               VGACAP_FLAG_FIRST_SAMPLE_MSB);
    memset(o, 0, sizeof *o);
    vgacap_writer_attach(&o->w, out_write, o, &h);
}

static void out_time(out_t *o, uint32_t dropped, const char *msg) {
    vgacap_writer_time(&o->w, 0, 0, dropped, msg);
    hal_flush();
}

// One byte per call, as prelude.py's stop_requested() reads one: a stop
// byte ends the capture, anything else is dropped. Never blocks.
static int stop_requested(out_t *o) {
    if (o->lost) return 1;
    if (hal_host_gone()) {
        o->lost = 1;
        return 1;
    }
    int c = hal_read_byte();
    return c == FW_STOP_BYTE || c == FW_STOP_BYTE_HUMAN;
}

// ---- streaming (capture_rp2.py main) ------------------------------------------------------

int fw_stream_run(const fw_stream_args_t *a, uint32_t *arena, size_t arena_bytes,
                  fw_started_fn started, void *user, fw_capture_result_t *res, char *why,
                  size_t whylen) {
    const fw_board_t *b = hal_board();
    res->lost = 0;
    uint32_t buf_words = a->buf_words;
    if (!buf_words || (uint64_t)buf_words * 8u > arena_bytes) {
        return refuse(why, whylen, "buf_words %lu needs two buffers of %lu bytes, have %lu in all",
                      (unsigned long)buf_words, (unsigned long)buf_words * 4ul,
                      (unsigned long)arena_bytes);
    }
    uint64_t max_bytes = a->max_bytes;
    uint32_t spc = buf_words * (b->push_thresh / b->in_count);  // SAMPLES_PER_CHUNK
    uint32_t *bufs[2] = { arena, arena + buf_words };

    fw_pio_program_t prog;
    fw_pio_sampler(&prog, b->clk_gpio, b->in_count, b->push_thresh, a->rising != 0);
    const char *hal_why = "";
    if (hal_stream_start(&prog, a->pio, a->sm, bufs[0], bufs[1], buf_words, &hal_why)) {
        return refuse(why, whylen, "%s", hal_why);
    }
    if (started) started(user);

    out_t o;
    out_init(&o);
    int index = 0, stopping = 0;
    for (;;) {
        while (!hal_stream_full(index)) {
            if (stop_requested(&o)) {
                stopping = 1;
                break;
            }
            hal_idle();
        }
        if (stopping) break;
        vgacap_writer_raw(&o.w, bufs[index], spc);
        hal_flush();  // a whole chunk reaches the host now, not with the next one
        // Released first: once the buffer is not full no fill can tear it,
        // so a tear during the send above cannot be missed here. A torn
        // chunk is never followed by another without its report.
        hal_stream_release(index);
        if (hal_stream_take_torn(index)) {
            // Cumulative, never an increment: see prelude.py write_time_chunk().
            out_time(&o, hal_stream_overruns() * spc, "overrun");
        }
        index = 1 - index;
        // Both stop checks between whole chunks: max_bytes so the board
        // finishes on its own, the stop byte for when the host asks.
        if (max_bytes && o.sent >= max_bytes) break;
        if (stop_requested(&o)) break;
    }
    uint32_t overruns;
    int rxstall;
    hal_stream_stop(&overruns, &rxstall);
    char summary[96];
    snprintf(summary, sizeof summary, "overruns=%lu rxstall=%d sysclk_hz=%lu", (unsigned long)overruns,
             rxstall, (unsigned long)hal_sysclk_hz());
    out_time(&o, overruns * spc, summary);
    res->lost = o.lost;
    return 0;
}

// ---- frame / window (frame_rp2.py main) -----------------------------------------------------

static int idle_level(unsigned gpio) {
    unsigned ones = 0;
    for (unsigned i = 0; i < POLARITY_READS; i++) {
        ones += (unsigned)hal_gpio_get(gpio);
        hal_delay_us(POLARITY_READ_GAP_US);
    }
    return 2u * ones > POLARITY_READS ? 1 : 0;
}

// The spans one capture of `lines` lines fills: the first buffers' whole
// lines, in order, the last one cut short (frame_rp2.py main's arm loop).
static unsigned window_spans(const fw_span_t *bufs, unsigned count, uint32_t line_words,
                             uint32_t lines, fw_span_t *out) {
    unsigned n = 0;
    for (unsigned i = 0; i < count && lines; i++) {
        uint32_t take = bufs[i].words / line_words;
        if (!take) continue;
        if (take > lines) take = lines;
        out[n].buf = bufs[i].buf;
        out[n].words = take * line_words;
        n++;
        lines -= take;
    }
    return n;
}

// The spans of the capture running now (static: FW_FRAME_MAX_BUFFERS of
// them is 1 KB, too much for a MicroPython caller's stack).
static fw_span_t spans[FW_FRAME_MAX_BUFFERS];

int fw_frame_run(const fw_frame_args_t *a, const fw_span_t *bufs, unsigned count,
                 fw_started_fn started, void *user, fw_capture_result_t *res, char *why,
                 size_t whylen) {
    const fw_board_t *b = hal_board();
    res->lost = 0;
    uint32_t cpl = a->cpl, region_first = a->region_first, region_lines = a->region_lines;
    uint32_t window_lines = a->window_lines;
    int hs_act = a->hsync_active, vs_act = a->vsync_active;
    uint32_t max_frames = a->max_frames;
    uint64_t max_bytes = a->max_bytes;
    uint32_t spw = b->push_thresh / b->in_count;
    if (!cpl || cpl % spw) {
        return refuse(why, whylen, "cpl %lu is not a whole number of %lu-sample words",
                      (unsigned long)cpl, (unsigned long)spw);
    }
    if (region_first + region_lines > 65536u) {
        return refuse(why, whylen, "region_first + region_lines must not pass line 65535");
    }
    if (!a->clock_hz) {
        return refuse(why, whylen, "the project clock is stopped: send 'clock hz=N' first");
    }
    uint32_t sysclk = hal_sysclk_hz();
    uint32_t period = fw_clock_period(sysclk, a->clock_hz);
    if (!period) {
        return refuse(why, whylen, "sysclk %lu is not 2..32 whole project clocks of %lu",
                      (unsigned long)sysclk, (unsigned long)a->clock_hz);
    }
    if (count > FW_FRAME_MAX_BUFFERS) {
        return refuse(why, whylen, "%u buffers, at most %u", count, FW_FRAME_MAX_BUFFERS);
    }
    uint32_t line_words = cpl / spw;
    uint32_t window = region_lines;
    if (window_lines && window_lines < window) window = window_lines;
    uint32_t capacity = 0;
    for (unsigned i = 0; i < count && capacity < window; i++) capacity += bufs[i].words / line_words;
    if (window > capacity) window = capacity;
    if (!window) {
        return refuse(why, whylen, "not one line of buffer: a line is %lu bytes",
                      (unsigned long)(4u * line_words));
    }
    unsigned buffers = window_spans(bufs, count, line_words, window, spans);
    unsigned hs_gpio = b->uo_gpio + 7u, vs_gpio = b->uo_gpio + 3u;
    if (hs_act < 0) hs_act = 1 - idle_level(hs_gpio);
    if (vs_act < 0) vs_act = 1 - idle_level(vs_gpio);

    fw_pio_program_t prog;
    fw_pio_frame(&prog, b->clk_gpio, hs_gpio, vs_gpio, (unsigned)hs_act, (unsigned)vs_act, b->in_count,
                 b->push_thresh, a->rising, a->sample_delay, period);
    const char *hal_why = "";
    if (hal_frame_start(&prog, a->pio, a->sm, &hal_why)) return refuse(why, whylen, "%s", hal_why);
    if (started) started(user);

    out_t o;
    out_init(&o);
    char msg[96];
    snprintf(msg, sizeof msg, "frame window=%lu buffers=%u hsync_active=%d vsync_active=%d",
             (unsigned long)window, buffers, hs_act, vs_act);
    out_time(&o, 0, msg);

    uint32_t cursor = 0, picture = 0, frames = 0, stalls = 0;
    int stopping = 0;
    while (!stopping) {
        uint32_t lines_now = window;
        if (lines_now > region_lines - cursor) lines_now = region_lines - cursor;
        uint32_t first = region_first + cursor;
        unsigned n = window_spans(bufs, count, line_words, lines_now, spans);
        hal_frame_arm(first, spans, n);
        while (!hal_frame_done()) {
            if (stop_requested(&o)) {
                stopping = 1;
                break;
            }
            hal_idle();
        }
        if (stopping) break;
        if (hal_frame_rxstall()) {
            stalls++;
            out_time(&o, 0, "rxstall");
        }
        // One unbroken run of samples, so ONE chunk: a chunk per buffer
        // would restart libvgaframe's line position at every buffer.
        vgacap_writer_frame_begin(&o.w, picture, (uint16_t)first, (uint16_t)lines_now, cpl,
                                  lines_now * cpl);
        for (unsigned i = 0; i < n; i++) vgacap_writer_frame_words(&o.w, spans[i].buf, spans[i].words);
        hal_flush();
        cursor += lines_now;
        if (cursor >= region_lines) {
            cursor = 0;
            picture++;
            frames++;
        }
        if (max_frames && frames >= max_frames) break;
        if (max_bytes && o.sent >= max_bytes) break;
        if (stop_requested(&o)) break;
    }
    int rxstall = hal_frame_stop();
    snprintf(msg, sizeof msg, "frames=%lu stalls=%lu rxstall=%d sysclk_hz=%lu", (unsigned long)frames,
             (unsigned long)stalls, (stalls || rxstall) ? 1 : 0, (unsigned long)hal_sysclk_hz());
    out_time(&o, 0, msg);
    res->lost = o.lost;
    return 0;
}
