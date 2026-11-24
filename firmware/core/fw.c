// SPDX-License-Identifier: Apache-2.0
#include "fw.h"

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

static struct {
    char line[FW_LINE_MAX + 1];
    size_t len;
    int overlong;
    fw_clock_plan_t clock;
    int clock_on;
    int reset_held;
    int ui_in;
    int reboot;
} S;

static void reply(const char *fmt, ...) {
    char buf[FW_LINE_MAX + 64];
    va_list ap;
    va_start(ap, fmt);
    int n = vsnprintf(buf, sizeof buf - 1, fmt, ap);
    va_end(ap);
    if (n < 0) n = 0;
    if ((size_t)n > sizeof buf - 2) n = (int)(sizeof buf - 2);
    buf[n++] = '\n';
    hal_write((const uint8_t *)buf, (size_t)n);
    hal_flush();
}

void fw_init(void) {
    memset(&S, 0, sizeof S);
    S.ui_in = -1;
}

int fw_reboot_requested(void) { return S.reboot; }

// ---- capture output ---------------------------------------------------------

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

// The end of a capture's binary output: an 8-byte chunk header that is not
// a vgacap tag, so the host can tell it apart at a chunk boundary and never
// forwards it (PROTOCOL.md, "Capture output").
static void out_done(out_t *o) {
    static const uint8_t done[8] = { 'D', 'O', 'N', 'E', 0, 0, 0, 0 };
    if (!o->lost) hal_write(done, sizeof done);
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

// ---- commands -----------------------------------------------------------------

#define PARSE(text, specs, values)                                                        \
    do {                                                                                  \
        char err_[160];                                                                   \
        if (fw_args_parse((text), (specs), sizeof(specs) / sizeof((specs)[0]), (values),  \
                          err_, sizeof err_)) {                                           \
            reply("err %s", err_);                                                        \
            return;                                                                       \
        }                                                                                 \
    } while (0)

static void no_args(const char *text, const char *cmd, int *ok) {
    char err[160];
    *ok = 0;
    if (fw_args_parse(text, NULL, 0, NULL, err, sizeof err)) {
        reply("err %s takes no arguments: %s", cmd, err);
        return;
    }
    *ok = 1;
}

static void cmd_hello(const char *args) {
    int ok;
    no_args(args, "hello", &ok);
    if (!ok) return;
    size_t arena;
    hal_arena(&arena);
    reply("ok vgacap-fw proto=%d chip=%s sysclk_hz=%lu arena_bytes=%lu", FW_PROTOCOL_VERSION,
          hal_board()->chip, (unsigned long)hal_sysclk_hz(), (unsigned long)arena);
}

static void cmd_status(const char *args) {
    int ok;
    no_args(args, "status", &ok);
    if (!ok) return;
    reply("ok clock_hz=%lu sysclk_hz=%lu reset=%d ui_in=%d",
          (unsigned long)(S.clock_on ? S.clock.clock_hz : 0u), (unsigned long)hal_sysclk_hz(),
          S.reset_held, S.ui_in);
}

static void cmd_clock(const char *args) {
    static const fw_arg_spec_t specs[] = { { "hz", 0, 125000000, NULL } };
    int64_t v[1];
    PARSE(args, specs, v);
    const char *why = "";
    if (v[0] == 0) {
        hal_clock(NULL, &why);
        S.clock_on = 0;
        reply("ok clock_hz=0 sysclk_hz=%lu", (unsigned long)hal_sysclk_hz());
        return;
    }
    fw_clock_plan_t plan;
    if (fw_clock_plan((uint32_t)v[0], &plan, &why) || hal_clock(&plan, &why)) {
        reply("err clock %lu: %s", (unsigned long)v[0], why);
        return;
    }
    S.clock = plan;
    S.clock_on = 1;
    reply("ok clock_hz=%lu actual_hz=%lu sysclk_hz=%lu period=%lu", (unsigned long)plan.clock_hz,
          (unsigned long)plan.actual_hz, (unsigned long)hal_sysclk_hz(),
          (unsigned long)fw_clock_period(hal_sysclk_hz(), plan.clock_hz));
}

static void cmd_reset(const char *args) {
    static const fw_arg_spec_t specs[] = { { "hold", 0, 1, NULL } };
    int64_t v[1];
    PARSE(args, specs, v);
    S.reset_held = (int)v[0];
    hal_reset(S.reset_held);
    reply("ok reset=%d", S.reset_held);
}

// The stock firmware's ProjectMux.reset_and_clock_mux() (tt-micropython-
// firmware src/ttboard/project_mux.py): reset the mux (inc, nrst, ena all
// low, 10 ms, nrst high, 10 ms), `index` pulses on inc (1 ms high, 1 ms
// low), then ena high. Running in place of the stock firmware, this is the
// only way an ASIC board's project survives the switch: the mux lines float
// while the RP2350 is in its bootloader (measured on tt09, 2026-11-24: no
// sync at all until the project was selected again).
static void cmd_asic_select(const char *args) {
    static const fw_arg_spec_t specs[] = { { "index", 0, 1023, NULL } };
    int64_t v[1];
    PARSE(args, specs, v);
    hal_asic_ctrl(0, 0, 0);
    hal_delay_us(10000);
    hal_asic_ctrl(0, 1, 0);
    hal_delay_us(10000);
    for (int64_t i = 0; i < v[0]; i++) {
        hal_asic_ctrl(0, 1, 1);
        hal_delay_us(1000);
        hal_asic_ctrl(0, 1, 0);
        hal_delay_us(1000);
    }
    hal_asic_ctrl(1, 1, 0);
    reply("ok asic_select index=%d", (int)v[0]);
}

static void cmd_ui_in(const char *args) {
    static const fw_arg_spec_t specs[] = { { "value", -1, 255, NULL } };
    int64_t v[1];
    PARSE(args, specs, v);
    S.ui_in = (int)v[0];
    hal_ui_in(S.ui_in);
    reply("ok ui_in=%d", S.ui_in);
}

static void reply_program(const fw_pio_program_t *p) {
    char words[FW_PIO_MAX_WORDS * 5 + 1] = "";
    for (unsigned i = 0; i < p->length; i++) {
        size_t used = strlen(words);
        snprintf(words + used, sizeof words - used, "%s%04x", i ? "," : "", p->words[i]);
    }
    reply("ok words=%s wrap_target=%u wrap=%u shiftctrl=0x%08lx execctrl=0x%08lx", words,
          p->wrap_target, p->wrap, (unsigned long)p->shiftctrl, (unsigned long)p->execctrl);
}

static void cmd_program_stream(const char *args) {
    static const fw_arg_spec_t specs[] = { { "edge", 0, 0, EDGES } };
    int64_t v[1];
    PARSE(args, specs, v);
    const fw_board_t *b = hal_board();
    fw_pio_program_t p;
    fw_pio_sampler(&p, b->clk_gpio, b->in_count, b->push_thresh, v[0] == 1);
    reply_program(&p);
}

static void cmd_program_frame(const char *args) {
    static const fw_arg_spec_t specs[] = {
        { "edge", 0, 0, EDGES },        { "hsync_active", 0, 1, NULL }, { "vsync_active", 0, 1, NULL },
        { "sample_delay", 0, 31, NULL }, { "period", 2, 32, NULL },
    };
    int64_t v[5];
    PARSE(args, specs, v);
    const fw_board_t *b = hal_board();
    fw_pio_program_t p;
    fw_pio_frame(&p, b->clk_gpio, b->uo_gpio + 7u, b->uo_gpio + 3u, (unsigned)v[1], (unsigned)v[2],
                 b->in_count, b->push_thresh, (unsigned)v[0], (unsigned)v[3], (unsigned)v[4]);
    reply_program(&p);
}

static void cmd_stream(const char *args) {
    const fw_board_t *b = hal_board();
    size_t arena_bytes;
    uint32_t *arena = hal_arena(&arena_bytes);
    const fw_arg_spec_t specs[] = {
        { "buf_words", 1, (int64_t)(arena_bytes / 8u), NULL },
        { "max_bytes", 0, UINT32_MAX, NULL },
        { "edge", 0, 0, EDGES },
        { "pio", 0, (int64_t)b->pio_count - 1, NULL },
        { "sm", 0, 3, NULL },
    };
    int64_t v[5];
    PARSE(args, specs, v);
    if (!S.clock_on) {
        reply("err the project clock is stopped: send 'clock hz=N' first");
        return;
    }
    uint32_t buf_words = (uint32_t)v[0];
    uint64_t max_bytes = (uint64_t)v[1];
    uint32_t spc = buf_words * (b->push_thresh / b->in_count);  // SAMPLES_PER_CHUNK
    uint32_t *bufs[2] = { arena, arena + buf_words };

    fw_pio_program_t prog;
    fw_pio_sampler(&prog, b->clk_gpio, b->in_count, b->push_thresh, v[2] == 1);
    const char *why = "";
    if (hal_stream_start(&prog, (unsigned)v[3], (unsigned)v[4], bufs[0], bufs[1], buf_words, &why)) {
        reply("err %s", why);
        return;
    }
    reply("ok stream");

    out_t o;
    out_init(&o);
    uint32_t reported = 0;
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
        hal_stream_release(index);
        uint32_t overruns = hal_stream_overruns();
        if (overruns > reported) {
            // Cumulative, never an increment: see prelude.py write_time_chunk().
            out_time(&o, overruns * spc, "overrun");
            reported = overruns;
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
    out_done(&o);
}

static int idle_level(unsigned gpio) {
    unsigned ones = 0;
    for (unsigned i = 0; i < POLARITY_READS; i++) {
        ones += (unsigned)hal_gpio_get(gpio);
        hal_delay_us(POLARITY_READ_GAP_US);
    }
    return 2u * ones > POLARITY_READS ? 1 : 0;
}

static void cmd_frame(const char *args) {
    const fw_board_t *b = hal_board();
    size_t arena_bytes;
    uint32_t *arena = hal_arena(&arena_bytes);
    const fw_arg_spec_t specs[] = {
        { "cpl", 1, 65535, NULL },          { "region_first", 0, 65535, NULL },
        { "region_lines", 1, 65535, NULL }, { "window_lines", 0, 65535, NULL },
        { "hsync_active", -1, 1, NULL },    { "vsync_active", -1, 1, NULL },
        { "max_frames", 0, UINT32_MAX, NULL }, { "max_bytes", 0, UINT32_MAX, NULL },
        { "edge", 0, 0, EDGES },            { "sample_delay", 0, 31, NULL },
        { "pio", 0, (int64_t)b->pio_count - 1, NULL }, { "sm", 0, 3, NULL },
    };
    int64_t v[12];
    PARSE(args, specs, v);
    uint32_t cpl = (uint32_t)v[0], region_first = (uint32_t)v[1], region_lines = (uint32_t)v[2];
    uint32_t window_lines = (uint32_t)v[3];
    int hs_act = (int)v[4], vs_act = (int)v[5];
    uint32_t max_frames = (uint32_t)v[6];
    uint64_t max_bytes = (uint64_t)v[7];
    unsigned clk_act = (unsigned)v[8], delay = (unsigned)v[9];
    uint32_t spw = b->push_thresh / b->in_count;
    if (cpl % spw) {
        reply("err cpl %lu is not a whole number of %lu-sample words", (unsigned long)cpl, (unsigned long)spw);
        return;
    }
    if (region_first + region_lines > 65536u) {
        reply("err region_first + region_lines must not pass line 65535");
        return;
    }
    if (!S.clock_on) {
        reply("err the project clock is stopped: send 'clock hz=N' first");
        return;
    }
    uint32_t sysclk = hal_sysclk_hz();
    uint32_t period = fw_clock_period(sysclk, S.clock.clock_hz);
    if (!period) {
        reply("err sysclk %lu is not 2..32 whole project clocks of %lu", (unsigned long)sysclk,
              (unsigned long)S.clock.clock_hz);
        return;
    }
    uint32_t line_words = cpl / spw;
    uint32_t capacity = (uint32_t)(arena_bytes / 4u / line_words);
    uint32_t window = region_lines;
    if (window_lines && window_lines < window) window = window_lines;
    if (window > capacity) window = capacity;
    if (!window) {
        reply("err not one line of buffer: a line is %lu bytes", (unsigned long)(4u * line_words));
        return;
    }
    unsigned hs_gpio = b->uo_gpio + 7u, vs_gpio = b->uo_gpio + 3u;
    if (hs_act < 0) hs_act = 1 - idle_level(hs_gpio);
    if (vs_act < 0) vs_act = 1 - idle_level(vs_gpio);

    fw_pio_program_t prog;
    fw_pio_frame(&prog, b->clk_gpio, hs_gpio, vs_gpio, (unsigned)hs_act, (unsigned)vs_act, b->in_count,
                 b->push_thresh, clk_act, delay, period);
    const char *why = "";
    if (hal_frame_start(&prog, (unsigned)v[10], (unsigned)v[11], &why)) {
        reply("err %s", why);
        return;
    }
    reply("ok frame");

    out_t o;
    out_init(&o);
    char msg[96];
    snprintf(msg, sizeof msg, "frame window=%lu buffers=1 hsync_active=%d vsync_active=%d",
             (unsigned long)window, hs_act, vs_act);
    out_time(&o, 0, msg);

    uint32_t cursor = 0, picture = 0, frames = 0, stalls = 0;
    int stopping = 0;
    while (!stopping) {
        uint32_t lines_now = window;
        if (lines_now > region_lines - cursor) lines_now = region_lines - cursor;
        uint32_t first = region_first + cursor;
        hal_frame_arm(first, arena, lines_now * line_words);
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
        vgacap_writer_frame(&o.w, picture, (uint16_t)first, (uint16_t)lines_now, cpl, arena,
                            lines_now * cpl);
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
    out_done(&o);
}

static void cmd_reboot(const char *args, int bootsel) {
    int ok;
    no_args(args, bootsel ? "bootsel" : "reboot", &ok);
    if (!ok) return;
    reply("ok %s", bootsel ? "bootsel" : "reboot");
    S.reboot = 1;
    hal_reboot(bootsel);
}

static void cmd_reboot_flash(const char *args) { cmd_reboot(args, 0); }
static void cmd_reboot_bootsel(const char *args) { cmd_reboot(args, 1); }

static const struct {
    const char *name;
    void (*run)(const char *args);
} COMMANDS[] = {
    { "hello", cmd_hello },       { "status", cmd_status },
    { "clock", cmd_clock },       { "reset", cmd_reset },
    { "ui_in", cmd_ui_in },       { "asic_select", cmd_asic_select },       { "stream", cmd_stream },
    { "frame", cmd_frame },       { "program_stream", cmd_program_stream },
    { "program_frame", cmd_program_frame }, { "reboot", cmd_reboot_flash },
    { "bootsel", cmd_reboot_bootsel },
};

void fw_execute(const char *line) {
    while (*line == ' ' || *line == '\t') line++;
    const char *end = line;
    while (*end && *end != ' ' && *end != '\t') end++;
    size_t n = (size_t)(end - line);
    if (!n) return;
    for (size_t i = 0; i < sizeof COMMANDS / sizeof COMMANDS[0]; i++) {
        if (strlen(COMMANDS[i].name) == n && memcmp(COMMANDS[i].name, line, n) == 0) {
            COMMANDS[i].run(end);
            return;
        }
    }
    reply("err unknown command '%.*s'", (int)n, line);
}

void fw_poll(void) {
    int c;
    while (!S.reboot && (c = hal_read_byte()) >= 0) {
        if (c == '\n') {
            if (S.overlong) {
                reply("err line longer than %d bytes", FW_LINE_MAX);
            } else {
                S.line[S.len] = 0;
                fw_execute(S.line);
            }
            S.len = 0;
            S.overlong = 0;
            continue;
        }
        // '\r', and a stop byte that arrives between captures, are dropped.
        if (c < 0x20 || c == 0x7F) continue;
        if (S.len < FW_LINE_MAX) S.line[S.len++] = (char)c;
        else S.overlong = 1;
    }
}
