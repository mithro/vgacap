// SPDX-License-Identifier: Apache-2.0
// The capture firmware's command loop and capture engines (firmware/core)
// on the simulated board (firmware/sim), with the host link in memory: the
// replies and the chunk bytes are checked exactly.
#include "harness.h"
#include "fw.h"
#include "fw_capture.h"
#include "fw_hal.h"
#include "sim.h"
#include "vgacap/stream.h"

static uint8_t uo[4096];

static void boot(size_t uo_len, double seconds_per_byte) {
    sim_config_t cfg = { .uo = uo, .uo_len = uo_len, .seconds_per_byte = seconds_per_byte, .arena_bytes = 0 };
    sim_init(&cfg);
    sim_io_memory();
    fw_init();
}

static void send_at(const char *text, double at) { sim_input((const uint8_t *)text, strlen(text), at); }

static void run(void) {
    for (int guard = 0; guard < 100000; guard++) {
        fw_poll();
        if (fw_reboot_requested() || sim_input_closed()) return;
        hal_wait_input();
    }
}

// The output from `pos` on: one text line.
static size_t pos;
static const char *line(void) {
    static char buf[512];
    size_t len;
    const uint8_t *out = sim_output(&len);
    size_t i = 0;
    while (pos < len && out[pos] != '\n' && i < sizeof buf - 1) buf[i++] = (char)out[pos++];
    if (pos < len) pos++;
    buf[i] = 0;
    return buf;
}

// The output from `pos` on: one chunk. Returns its tag, sets payload/len.
static const uint8_t *payload;
static uint32_t paylen;
static const char *chunk(void) {
    static char tag[5];
    size_t len;
    const uint8_t *out = sim_output(&len);
    if (pos + 8 > len) return "";
    memcpy(tag, out + pos, 4);
    tag[4] = 0;
    paylen = (uint32_t)out[pos + 4] | (uint32_t)out[pos + 5] << 8 | (uint32_t)out[pos + 6] << 16 |
             (uint32_t)out[pos + 7] << 24;
    payload = out + pos + 8;
    pos += 8u + paylen;
    return tag;
}

static uint32_t u32_at(const uint8_t *p) {
    return (uint32_t)p[0] | (uint32_t)p[1] << 8 | (uint32_t)p[2] << 16 | (uint32_t)p[3] << 24;
}

static const char *time_msg(void) {
    static char msg[256];
    uint32_t n = (uint32_t)payload[16] | (uint32_t)payload[17] << 8;
    memcpy(msg, payload + 18, n);
    msg[n] = 0;
    return msg;
}

static int streq(const char *a, const char *b) {
    if (strcmp(a, b)) fprintf(stderr, "    got '%s'\n    want '%s'\n", a, b);
    return strcmp(a, b) == 0;
}

// ---- commands -------------------------------------------------------------------

TEST(hello_status_and_errors) {
    boot(1, 1e-6);
    pos = 0;
    send_at("hello\r\nstatus\n\x03\nbogus 1\nhello there=1\nclock hz=2\nclock hz=125000001\nclock\n", 0);
    send_at("reset hold=1\nui_in value=200\nui_in value=-1\nstatus\n", 0);
    run();
    ASSERT_TRUE(streq(line(), "ok vgacap-fw proto=1 chip=rp2350 sysclk_hz=150000000 arena_bytes=450560"));
    ASSERT_TRUE(streq(line(), "ok clock_hz=0 sysclk_hz=150000000 reset=0 ui_in=-1"));
    ASSERT_TRUE(streq(line(), "err unknown command 'bogus'"));
    ASSERT_TRUE(streq(line(), "err hello takes no arguments: unknown key 'there'"));
    ASSERT_TRUE(streq(line(), "err clock 2: no system clock for that project clock (3 Hz .. 125 MHz)"));
    ASSERT_TRUE(streq(line(), "err hz must be an integer 0..125000000, got '125000001'"));
    ASSERT_TRUE(streq(line(), "err missing key 'hz'"));
    ASSERT_TRUE(streq(line(), "ok reset=1"));
    ASSERT_TRUE(streq(line(), "ok ui_in=200"));
    ASSERT_TRUE(streq(line(), "ok ui_in=-1"));
    ASSERT_TRUE(streq(line(), "ok clock_hz=0 sysclk_hz=150000000 reset=1 ui_in=-1"));
    size_t len;
    sim_output(&len);
    ASSERT_EQ_U(pos, len);
}

TEST(clock_reports_its_plan) {
    boot(1, 1e-6);
    pos = 0;
    send_at("clock hz=25000000\nclock hz=100000\nclock hz=0\n", 0);
    run();
    ASSERT_TRUE(streq(line(), "ok clock_hz=25000000 actual_hz=25000000 sysclk_hz=100000000 period=4"));
    ASSERT_TRUE(streq(line(), "ok clock_hz=100000 actual_hz=100000 sysclk_hz=133000000 period=0"));
    ASSERT_TRUE(streq(line(), "ok clock_hz=0 sysclk_hz=133000000"));
}

TEST(overlong_line_is_refused_whole) {
    boot(1, 1e-6);
    pos = 0;
    char big[400];
    memset(big, 'x', sizeof big - 2);
    big[sizeof big - 2] = '\n';
    big[sizeof big - 1] = 0;
    send_at(big, 0);
    send_at("hello\n", 0);
    run();
    ASSERT_TRUE(streq(line(), "err line longer than 256 bytes"));
    ASSERT_TRUE(strncmp(line(), "ok vgacap-fw", 12) == 0);
}

TEST(program_commands_print_the_words) {
    boot(1, 1e-6);
    pos = 0;
    send_at("program_stream edge=falling\n", 0);
    send_at("program_frame edge=rising hsync_active=0 vsync_active=1 sample_delay=3 period=4\n", 0);
    run();
    ASSERT_TRUE(streq(line(), "ok words=2090,2010,4008 wrap_target=0 wrap=2 shiftctrl=0x80010000 execctrl=0x00002000"));
    ASSERT_TRUE(streq(line(), "ok words=80a0,a027,2004,2084,0046,0009,2088,2008,0004,2010,2390,4308 "
                              "wrap_target=11 wrap=11 shiftctrl=0x00010000 execctrl=0x0000b580"));
}

TEST(reboot_answers_then_stops) {
    boot(1, 1e-6);
    pos = 0;
    send_at("reboot\nhello\n", 0);
    run();
    ASSERT_TRUE(fw_reboot_requested());
    ASSERT_TRUE(streq(line(), "ok reboot"));
    size_t len;
    sim_output(&len);
    ASSERT_EQ_U(pos, len);  // nothing after: the board is gone
}

// ---- streaming --------------------------------------------------------------------

static void ramp(size_t n) {
    for (size_t i = 0; i < n; i++) uo[i] = (uint8_t)(i * 7u + 3u);
}

TEST(stream_needs_a_clock) {
    boot(1, 1e-6);
    pos = 0;
    send_at("stream buf_words=16 max_bytes=0 edge=falling pio=1 sm=0\n", 0);
    run();
    ASSERT_TRUE(streq(line(), "err the project clock is stopped: send 'clock hz=N' first"));
}

TEST(stream_rejects_a_buffer_the_arena_cannot_hold_twice) {
    boot(1, 1e-6);
    pos = 0;
    send_at("clock hz=100000\nstream buf_words=56321 max_bytes=0 edge=falling pio=1 sm=0\n", 0);
    run();
    line();
    ASSERT_TRUE(streq(line(), "err buf_words must be an integer 1..56320, got '56321'"));
}

// Chunks are what capture_rp2.py writes: RAW chunks of whole buffers of
// consecutive samples, max_bytes counted over them, and the closing TIME.
TEST(stream_to_max_bytes) {
    ramp(256);
    boot(256, 1e-6);
    pos = 0;
    send_at("clock hz=100000\nstream buf_words=16 max_bytes=200 edge=falling pio=1 sm=0\n", 0);
    run();
    line();
    ASSERT_TRUE(streq(line(), "ok stream"));
    uint32_t expect_next = 0;
    for (int k = 0; k < 3; k++) {  // 76, 152, 228 >= 200
        ASSERT_TRUE(streq(chunk(), "RAW "));
        ASSERT_EQ_U(paylen, 4 + 64);
        ASSERT_EQ_U(u32_at(payload), 64);
        // Samples are consecutive clocks, packed first-sample-MSB.
        uint32_t first = payload[4 + 3];
        if (k == 0) expect_next = first;
        for (uint32_t s = 0; s < 64; s++) {
            uint8_t got = payload[4 + 4 * (s / 4) + (3 - s % 4)];
            uint8_t want = 0;
            for (size_t i = 0; i < 256; i++) if (uo[i] == (uint8_t)expect_next) { want = uo[(i + s) % 256]; break; }
            ASSERT_EQ_U(got, want);
        }
        size_t at = 0;
        while (uo[at] != (uint8_t)expect_next) at++;
        expect_next = uo[(at + 64) % 256];
    }
    ASSERT_TRUE(streq(chunk(), "TIME"));
    ASSERT_TRUE(streq(time_msg(), "overruns=0 rxstall=0 sysclk_hz=133000000"));
    ASSERT_EQ_U(u32_at(payload + 12), 0);
    ASSERT_TRUE(streq(chunk(), "DONE"));
    ASSERT_EQ_U(paylen, 0);
    size_t len;
    sim_output(&len);
    ASSERT_EQ_U(pos, len);
}

// A host that reads too slowly: every buffer the DMA comes back to before
// it was sent is an overrun, reported as it happens with the CUMULATIVE
// dropped count, and again in the summary.
TEST(stream_overruns_are_cumulative) {
    ramp(256);
    boot(256, 100e-6);  // 10 KB/s: a 76-byte chunk takes 7.6 ms, a buffer fills in 0.64 ms
    pos = 0;
    send_at("clock hz=100000\nstream buf_words=16 max_bytes=1000 edge=falling pio=1 sm=0\n", 0);
    run();
    line();
    ASSERT_TRUE(streq(line(), "ok stream"));
    uint32_t last_dropped = 0, raws = 0, reports = 0;
    const char *tag;
    while (*(tag = chunk()) && strcmp(tag, "DONE")) {
        if (!strcmp(tag, "RAW ")) {
            raws++;
            continue;
        }
        ASSERT_TRUE(!strcmp(tag, "TIME"));
        uint32_t dropped = u32_at(payload + 12);
        ASSERT_TRUE(dropped >= last_dropped);
        ASSERT_EQ_U(dropped % 64, 0);
        last_dropped = dropped;
        if (!strcmp(time_msg(), "overrun")) {
            reports++;
        } else {
            char want[96];
            snprintf(want, sizeof want, "overruns=%u rxstall=0 sysclk_hz=133000000", dropped / 64);
            ASSERT_TRUE(streq(time_msg(), want));
        }
    }
    ASSERT_TRUE(raws >= 2);
    ASSERT_TRUE(reports >= 1);
    ASSERT_TRUE(last_dropped > 0);
}

// The stop byte is noticed between chunks, never inside one, and the
// trailer still follows.
TEST(stream_stops_on_the_stop_byte) {
    ramp(256);
    boot(256, 1e-6);
    pos = 0;
    send_at("clock hz=100000\nstream buf_words=16 max_bytes=0 edge=rising pio=2 sm=3\n", 0);
    send_at("\x03", 0.0100);  // ~15 buffers of 0.64 ms in
    run();
    line();
    ASSERT_TRUE(streq(line(), "ok stream"));
    uint32_t raws = 0;
    while (!strcmp(chunk(), "RAW ")) raws++;
    ASSERT_TRUE(raws >= 14 && raws <= 17);
    ASSERT_TRUE(streq(time_msg(), "overruns=0 rxstall=0 sysclk_hz=133000000"));
    ASSERT_TRUE(streq(chunk(), "DONE"));
}

// ---- frame / window ---------------------------------------------------------------

// A tiny VGA-shaped signal: 16 clocks a line, 10 lines a frame; hsync high
// for the first 2 clocks of a line, vsync high for lines 0-1 (both active
// high); the colour bits carry the line number and clock.
#define CPL 16u
#define LPF 10u
static uint8_t tiny(uint32_t line_no, uint32_t clk) {
    uint8_t hs = clk < 2u, vs = line_no < 2u;
    uint8_t colour = (uint8_t)(((line_no & 7u) << 4) | (clk & 7u));  // bits 0-2 and 4-6
    return (uint8_t)((hs << 7) | (vs << 3) | (colour & 0x77u));
}

static void tiny_frames(void) {
    for (uint32_t l = 0; l < LPF; l++)
        for (uint32_t c = 0; c < CPL; c++) uo[l * CPL + c] = tiny(l, c);
}

// A FRAM chunk holds `lines` whole lines from `first`, each starting at its
// hsync leading edge.
static int fram_is(uint32_t picture, uint32_t first, uint32_t lines) {
    if (u32_at(payload) != picture) return 0;
    if ((payload[4] | payload[5] << 8) != (int)first || (payload[6] | payload[7] << 8) != (int)lines) return 0;
    if (u32_at(payload + 8) != CPL || u32_at(payload + 12) != lines * CPL) return 0;
    if (paylen != 16u + lines * CPL) return 0;
    for (uint32_t s = 0; s < lines * CPL; s++) {
        uint8_t got = payload[16 + 4 * (s / 4) + (3 - s % 4)];
        if (got != tiny((first + s / CPL) % LPF, s % CPL)) return 0;
    }
    return 1;
}

TEST(frame_whole_region_measures_polarity) {
    tiny_frames();
    boot(CPL * LPF, 1e-6);
    pos = 0;
    send_at("clock hz=25000000\nframe cpl=16 region_first=3 region_lines=5 window_lines=0 "
            "hsync_active=-1 vsync_active=-1 max_frames=2 max_bytes=0 edge=rising sample_delay=0 pio=1 sm=0\n", 0);
    run();
    line();
    ASSERT_TRUE(streq(line(), "ok frame"));
    ASSERT_TRUE(streq(chunk(), "TIME"));
    ASSERT_TRUE(streq(time_msg(), "frame window=5 buffers=1 hsync_active=1 vsync_active=1"));
    ASSERT_TRUE(streq(chunk(), "FRAM"));
    ASSERT_TRUE(fram_is(0, 3, 5));
    ASSERT_TRUE(streq(chunk(), "FRAM"));
    ASSERT_TRUE(fram_is(1, 3, 5));
    ASSERT_TRUE(streq(chunk(), "TIME"));
    ASSERT_TRUE(streq(time_msg(), "frames=2 stalls=0 rxstall=0 sysclk_hz=100000000"));
    ASSERT_TRUE(streq(chunk(), "DONE"));
}

// Windows of 2 lines sweep the region, all sharing one picture counter,
// the last window short; max_frames counts pictures, not windows.
TEST(frame_windows_sweep_the_region) {
    tiny_frames();
    boot(CPL * LPF, 1e-6);
    pos = 0;
    send_at("clock hz=25000000\nframe cpl=16 region_first=3 region_lines=5 window_lines=2 "
            "hsync_active=1 vsync_active=1 max_frames=2 max_bytes=0 edge=rising sample_delay=0 pio=1 sm=0\n", 0);
    run();
    line();
    ASSERT_TRUE(streq(line(), "ok frame"));
    ASSERT_TRUE(streq(chunk(), "TIME"));
    ASSERT_TRUE(streq(time_msg(), "frame window=2 buffers=1 hsync_active=1 vsync_active=1"));
    for (uint32_t pic = 0; pic < 2; pic++) {
        ASSERT_TRUE(streq(chunk(), "FRAM"));
        ASSERT_TRUE(fram_is(pic, 3, 2));
        ASSERT_TRUE(streq(chunk(), "FRAM"));
        ASSERT_TRUE(fram_is(pic, 5, 2));
        ASSERT_TRUE(streq(chunk(), "FRAM"));
        ASSERT_TRUE(fram_is(pic, 7, 1));
    }
    ASSERT_TRUE(streq(chunk(), "TIME"));
    ASSERT_TRUE(streq(time_msg(), "frames=2 stalls=0 rxstall=0 sysclk_hz=100000000"));
    ASSERT_TRUE(streq(chunk(), "DONE"));
}

// Chained buffers, as the MicroPython module fills on a fragmented heap:
// each used in whole lines, in order, a buffer too small for a line
// skipped, and still ONE FRAM chunk per window, byte for byte the arena's.
TEST(frame_into_chained_buffers_is_the_arenas_chunks) {
    tiny_frames();
    boot(CPL * LPF, 1e-6);
    pos = 0;
    send_at("clock hz=25000000\nframe cpl=16 region_first=3 region_lines=5 window_lines=0 "
            "hsync_active=1 vsync_active=1 max_frames=2 max_bytes=0 edge=rising sample_delay=0 pio=1 sm=0\n", 0);
    run();
    line();
    ASSERT_TRUE(streq(line(), "ok frame"));
    ASSERT_TRUE(streq(chunk(), "TIME"));
    size_t len;
    const uint8_t *out = sim_output(&len);
    static uint8_t want[8192];
    size_t want_from = pos, want_len = len - pos - 8u;  // the FRAMs and the summary, not DONE
    ASSERT_TRUE(want_len < sizeof want);
    memcpy(want, out + want_from, want_len);

    // A line is 4 words: 1 line (and 3 words over), none, 2 lines, the rest.
    static uint32_t a[7], b[3], c[10], d[64];
    const fw_span_t bufs[] = { { a, 7 }, { b, 3 }, { c, 10 }, { d, 64 } };
    fw_frame_args_t args;
    char why[FW_WHY_MAX];
    ASSERT_EQ_U(fw_frame_parse(" cpl=16 region_first=3 region_lines=5 window_lines=0 hsync_active=1 "
                               "vsync_active=1 max_frames=2 max_bytes=0 edge=rising sample_delay=0 "
                               "pio=1 sm=0 clock_hz=25000000", 1, &args, why, sizeof why), 0);
    fw_capture_result_t res;
    pos = len;
    ASSERT_EQ_U(fw_frame_run(&args, bufs, 4, NULL, NULL, &res, why, sizeof why), 0);
    ASSERT_EQ_U(sim_frame_spans(), 3);
    ASSERT_TRUE(streq(chunk(), "TIME"));
    ASSERT_TRUE(streq(time_msg(), "frame window=5 buffers=3 hsync_active=1 vsync_active=1"));
    out = sim_output(&len);
    ASSERT_EQ_U(len - pos, want_len);
    ASSERT_EQ_MEM(out + pos, want, want_len);

    // Window captures cut the buffers short: 2 lines are a's and c's first.
    pos = len;
    args.window_lines = 2;
    ASSERT_EQ_U(fw_frame_run(&args, bufs, 4, NULL, NULL, &res, why, sizeof why), 0);
    ASSERT_TRUE(streq(chunk(), "TIME"));
    ASSERT_TRUE(streq(time_msg(), "frame window=2 buffers=2 hsync_active=1 vsync_active=1"));
    ASSERT_TRUE(streq(chunk(), "FRAM"));
    ASSERT_TRUE(fram_is(0, 3, 2));
    ASSERT_TRUE(streq(chunk(), "FRAM"));
    ASSERT_TRUE(fram_is(0, 5, 2));
    ASSERT_TRUE(streq(chunk(), "FRAM"));
    ASSERT_TRUE(fram_is(0, 7, 1));
    ASSERT_EQ_U(sim_frame_spans(), 1);  // the last window: one line, all a's

    // Nothing but buffers too small for a line: the firmware's refusal.
    const fw_span_t tiny_bufs[] = { { b, 3 } };
    ASSERT_TRUE(fw_frame_run(&args, tiny_bufs, 1, NULL, NULL, &res, why, sizeof why) == -1);
    ASSERT_TRUE(streq(why, "not one line of buffer: a line is 16 bytes"));
}

// The arena decides the window when it is smaller than the region.
TEST(frame_window_limited_by_the_arena) {
    tiny_frames();
    sim_config_t cfg = { .uo = uo, .uo_len = CPL * LPF, .seconds_per_byte = 1e-6, .arena_bytes = 3u * CPL };
    sim_init(&cfg);
    sim_io_memory();
    fw_init();
    pos = 0;
    send_at("clock hz=25000000\nframe cpl=16 region_first=3 region_lines=5 window_lines=0 "
            "hsync_active=1 vsync_active=1 max_frames=1 max_bytes=0 edge=rising sample_delay=0 pio=1 sm=0\n", 0);
    run();
    line();
    ASSERT_TRUE(streq(line(), "ok frame"));
    chunk();
    ASSERT_TRUE(streq(time_msg(), "frame window=3 buffers=1 hsync_active=1 vsync_active=1"));
    ASSERT_TRUE(streq(chunk(), "FRAM"));
    ASSERT_TRUE(fram_is(0, 3, 3));
    ASSERT_TRUE(streq(chunk(), "FRAM"));
    ASSERT_TRUE(fram_is(0, 6, 2));
}

TEST(frame_rxstall_is_reported_per_window_and_in_the_summary) {
    tiny_frames();
    boot(CPL * LPF, 1e-6);
    sim_force_rxstall(1);
    pos = 0;
    send_at("clock hz=25000000\nframe cpl=16 region_first=3 region_lines=5 window_lines=0 "
            "hsync_active=1 vsync_active=1 max_frames=1 max_bytes=0 edge=rising sample_delay=0 pio=1 sm=0\n", 0);
    run();
    line();
    line();
    chunk();
    ASSERT_TRUE(streq(chunk(), "TIME"));
    ASSERT_TRUE(streq(time_msg(), "rxstall"));
    ASSERT_TRUE(streq(chunk(), "FRAM"));
    ASSERT_TRUE(streq(chunk(), "TIME"));
    ASSERT_TRUE(streq(time_msg(), "frames=1 stalls=1 rxstall=1 sysclk_hz=100000000"));
}

TEST(frame_refusals) {
    tiny_frames();
    boot(CPL * LPF, 1e-6);
    pos = 0;
    const char *tail = " region_first=3 region_lines=5 window_lines=0 hsync_active=1 vsync_active=1 "
                       "max_frames=1 max_bytes=0 edge=rising sample_delay=0 pio=1 sm=0\n";
    char cmd[300];
    snprintf(cmd, sizeof cmd, "frame cpl=16%s", tail);
    send_at(cmd, 0);
    send_at("clock hz=100000\n", 0);
    send_at(cmd, 0);
    send_at("clock hz=25000000\n", 0);
    snprintf(cmd, sizeof cmd, "frame cpl=18%s", tail);
    send_at(cmd, 0);
    send_at("frame cpl=16 region_first=65535 region_lines=2 window_lines=0 hsync_active=1 vsync_active=1 "
            "max_frames=1 max_bytes=0 edge=rising sample_delay=0 pio=1 sm=0\n", 0);
    send_at("frame cpl=16 region_first=3 region_lines=5 window_lines=0 hsync_active=1 vsync_active=1 "
            "max_frames=1 max_bytes=0 edge=rising sample_delay=32 pio=1 sm=0\n", 0);
    run();
    ASSERT_TRUE(streq(line(), "err the project clock is stopped: send 'clock hz=N' first"));
    line();
    ASSERT_TRUE(streq(line(), "err sysclk 133000000 is not 2..32 whole project clocks of 100000"));
    line();
    ASSERT_TRUE(streq(line(), "err cpl 18 is not a whole number of 4-sample words"));
    ASSERT_TRUE(streq(line(), "err region_first + region_lines must not pass line 65535"));
    ASSERT_TRUE(streq(line(), "err sample_delay must be an integer 0..31, got '32'"));
}

TEST(frame_stops_on_the_stop_byte_and_on_max_bytes) {
    tiny_frames();
    boot(CPL * LPF, 1e-6);
    pos = 0;
    send_at("clock hz=25000000\nframe cpl=16 region_first=3 region_lines=5 window_lines=0 "
            "hsync_active=1 vsync_active=1 max_frames=0 max_bytes=0 edge=rising sample_delay=0 pio=1 sm=0\n", 0);
    send_at("q", 0.002);
    run();
    line();
    line();
    chunk();
    uint32_t frams = 0;
    while (!strcmp(chunk(), "FRAM")) frams++;
    ASSERT_TRUE(frams >= 2);
    ASSERT_TRUE(!strncmp(time_msg(), "frames=", 7));
    ASSERT_TRUE(streq(chunk(), "DONE"));

    boot(CPL * LPF, 1e-6);
    pos = 0;
    send_at("clock hz=25000000\nframe cpl=16 region_first=3 region_lines=5 window_lines=0 "
            "hsync_active=1 vsync_active=1 max_frames=0 max_bytes=150 edge=rising sample_delay=0 pio=1 sm=0\n", 0);
    run();
    line();
    line();
    // The opening TIME counts too (8 + 18 + 54 = 80 bytes), so one FRAM
    // (8 + 16 + 80 = 104) takes the total to 184, past 150.
    ASSERT_TRUE(streq(chunk(), "TIME"));
    ASSERT_EQ_U(paylen, 18 + 54);
    ASSERT_TRUE(streq(chunk(), "FRAM"));
    ASSERT_TRUE(streq(chunk(), "TIME"));
    ASSERT_TRUE(streq(time_msg(), "frames=1 stalls=0 rxstall=0 sysclk_hz=100000000"));
}

int main(void) {
    RUN(hello_status_and_errors);
    RUN(clock_reports_its_plan);
    RUN(overlong_line_is_refused_whole);
    RUN(program_commands_print_the_words);
    RUN(reboot_answers_then_stops);
    RUN(stream_needs_a_clock);
    RUN(stream_rejects_a_buffer_the_arena_cannot_hold_twice);
    RUN(stream_to_max_bytes);
    RUN(stream_overruns_are_cumulative);
    RUN(stream_stops_on_the_stop_byte);
    RUN(frame_whole_region_measures_polarity);
    RUN(frame_windows_sweep_the_region);
    RUN(frame_into_chained_buffers_is_the_arenas_chunks);
    RUN(frame_window_limited_by_the_arena);
    RUN(frame_rxstall_is_reported_per_window_and_in_the_summary);
    RUN(frame_refusals);
    RUN(frame_stops_on_the_stop_byte_and_on_max_bytes);
    RUN_TESTS_END();
}
