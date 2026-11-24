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

static void started(void *user) {
    reply("ok %s", (const char *)user);
}

// The end of a capture's binary output: an 8-byte chunk header that is not
// a vgacap tag, so the host can tell it apart at a chunk boundary and never
// forwards it (PROTOCOL.md, "Capture output").
static void done(const fw_capture_result_t *res) {
    static const uint8_t marker[8] = { 'D', 'O', 'N', 'E', 0, 0, 0, 0 };
    if (!res->lost) hal_write(marker, sizeof marker);
    hal_flush();
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
    size_t arena_bytes;
    uint32_t *arena = hal_arena(&arena_bytes);
    fw_stream_args_t a;
    char why[FW_WHY_MAX];
    if (fw_stream_parse(args, arena_bytes, &a, why, sizeof why)) {
        reply("err %s", why);
        return;
    }
    if (!S.clock_on) {
        reply("err the project clock is stopped: send 'clock hz=N' first");
        return;
    }
    fw_capture_result_t res;
    if (fw_stream_run(&a, arena, arena_bytes, started, (void *)"stream", &res, why, sizeof why)) {
        reply("err %s", why);
        return;
    }
    done(&res);
}

static void cmd_frame(const char *args) {
    size_t arena_bytes;
    uint32_t *arena = hal_arena(&arena_bytes);
    fw_frame_args_t a;
    char why[FW_WHY_MAX];
    if (fw_frame_parse(args, 0, &a, why, sizeof why)) {
        reply("err %s", why);
        return;
    }
    a.clock_hz = S.clock_on ? S.clock.clock_hz : 0u;
    fw_capture_result_t res;
    fw_span_t buf = { arena, (uint32_t)(arena_bytes / 4u) };
    if (fw_frame_run(&a, &buf, 1, started, (void *)"frame", &res, why, sizeof why)) {
        reply("err %s", why);
        return;
    }
    done(&res);
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
