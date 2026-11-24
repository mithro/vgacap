// SPDX-License-Identifier: Apache-2.0
// The firmware HAL on a host: a simulated demo board (see sim.h).
#define _POSIX_C_SOURCE 200809L
#include <errno.h>
#include <math.h>
#include <poll.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

#include "fw_board.h"
#include "fw_hal.h"
#include "sim.h"

#define SPW 4u  // samples per FIFO word (8-bit samples, autopush at 32)

typedef struct in_byte {
    uint8_t b;
    double at;
} in_byte_t;

static struct {
    sim_config_t cfg;
    double now;
    // host link
    int fds;
    int in_fd, out_fd;
    int in_eof;
    uint8_t fdbuf[4096];
    size_t fdbuf_len, fdbuf_pos;
    in_byte_t *in;
    size_t in_len, in_pos, in_cap;
    uint8_t *out;
    size_t out_len, out_cap;
    // board
    uint32_t sysclk_hz;
    int clock_on;
    double clock_hz;
    double clock_t0;
    uint32_t *arena;
    size_t arena_bytes;
    int force_rxstall;
    // streaming
    int streaming;
    uint32_t *bufs[2];
    uint32_t buf_words;
    uint64_t first_clock;
    uint64_t fills;
    volatile int full[2];
    uint32_t overruns;
    // frame
    int framing;
    unsigned hs_bit, vs_bit, hs_act, vs_act;
    double done_at;
} S;

// ---- control ------------------------------------------------------------------

void sim_init(const sim_config_t *cfg) {
    free(S.in);
    free(S.out);
    free(S.arena);
    memset(&S, 0, sizeof S);
    S.cfg = *cfg;
    if (!S.cfg.arena_bytes) S.cfg.arena_bytes = FW_ARENA_BYTES;
    S.arena_bytes = S.cfg.arena_bytes;
    S.arena = calloc(S.arena_bytes / 4u, 4u);
    S.sysclk_hz = 150000000u;  // pico-sdk's RP2350 boot clock
    S.in_fd = S.out_fd = -1;
    if (!S.arena) {
        fprintf(stderr, "sim: out of memory\n");
        exit(2);
    }
}

void sim_io_fds(int in_fd, int out_fd) {
    S.fds = 1;
    S.in_fd = in_fd;
    S.out_fd = out_fd;
}

void sim_io_memory(void) { S.fds = 0; }

void sim_input(const uint8_t *bytes, size_t len, double at) {
    if (S.in_len + len > S.in_cap) {
        S.in_cap = (S.in_len + len) * 2u + 64u;
        S.in = realloc(S.in, S.in_cap * sizeof *S.in);
        if (!S.in) exit(2);
    }
    for (size_t i = 0; i < len; i++) S.in[S.in_len++] = (in_byte_t){ bytes[i], at };
}

const uint8_t *sim_output(size_t *len) {
    *len = S.out_len;
    return S.out;
}

int sim_input_closed(void) {
    if (S.fds) return S.in_eof && S.fdbuf_pos == S.fdbuf_len;
    return S.in_pos == S.in_len;
}

double sim_now(void) { return S.now; }
void sim_force_rxstall(int on) { S.force_rxstall = on; }

// ---- the design ---------------------------------------------------------------

// Project clocks since the clock started, at time t (the clock whose
// uo_out is on the pins).
static uint64_t clock_at(double t) {
    if (!S.clock_on || t <= S.clock_t0) return 0;
    return (uint64_t)floor((t - S.clock_t0) * S.clock_hz);
}

static double time_of_clock(uint64_t c) { return S.clock_t0 + (double)c / S.clock_hz; }

static uint8_t uo_at(uint64_t c) { return S.cfg.uo[c % S.cfg.uo_len]; }

// Pack `words` words of samples starting at clock `c`, as the sampler's
// left-shifting ISR does: the first sample in the most significant byte.
static void pack(uint32_t *dst, uint32_t words, uint64_t c) {
    for (uint32_t i = 0; i < words; i++, c += SPW) {
        dst[i] = ((uint32_t)uo_at(c) << 24) | ((uint32_t)uo_at(c + 1) << 16) |
                 ((uint32_t)uo_at(c + 2) << 8) | (uint32_t)uo_at(c + 3);
    }
}

// DMA completions due by now: each ping-pong buffer is filled when its last
// sample has been taken; one that is still unsent is overwritten and
// counted, as capture_rp2.py's interrupt handlers count it.
static double next_fill_time(void) {
    uint64_t samples = (uint64_t)S.buf_words * SPW;
    return time_of_clock(S.first_clock + (S.fills + 1u) * samples);
}

static void process(void) {
    while (S.streaming && next_fill_time() <= S.now) {
        uint64_t samples = (uint64_t)S.buf_words * SPW;
        int i = (int)(S.fills & 1u);
        pack(S.bufs[i], S.buf_words, S.first_clock + S.fills * samples);
        if (S.full[i]) S.overruns++;
        S.full[i] = 1;
        S.fills++;
    }
}

static void advance(double seconds) {
    S.now += seconds;
    process();
}

// ---- the host link --------------------------------------------------------------

static int fd_fill(int timeout_ms) {
    if (S.fdbuf_pos < S.fdbuf_len) return 1;
    if (S.in_eof) return 0;
    struct pollfd p = { .fd = S.in_fd, .events = POLLIN };
    int r = poll(&p, 1, timeout_ms);
    if (r <= 0) return 0;
    ssize_t n = read(S.in_fd, S.fdbuf, sizeof S.fdbuf);
    if (n <= 0) {
        if (n < 0 && (errno == EINTR || errno == EAGAIN)) return 0;
        S.in_eof = 1;
        return 0;
    }
    S.fdbuf_len = (size_t)n;
    S.fdbuf_pos = 0;
    return 1;
}

int hal_read_byte(void) {
    if (S.fds) {
        if (!fd_fill(0)) return -1;
        return S.fdbuf[S.fdbuf_pos++];
    }
    if (S.in_pos < S.in_len && S.in[S.in_pos].at <= S.now) return S.in[S.in_pos++].b;
    return -1;
}

void hal_wait_input(void) {
    if (S.fds) {
        fd_fill(-1);
        return;
    }
    if (S.in_pos < S.in_len && S.in[S.in_pos].at > S.now) advance(S.in[S.in_pos].at - S.now);
}

int hal_write(const uint8_t *buf, size_t len) {
    if (S.fds) {
        size_t done = 0;
        while (done < len) {
            ssize_t n = write(S.out_fd, buf + done, len - done);
            if (n < 0) {
                if (errno == EINTR) continue;
                return -1;  // EPIPE: the host went away
            }
            done += (size_t)n;
        }
    } else {
        if (S.out_len + len > S.out_cap) {
            S.out_cap = (S.out_len + len) * 2u + 4096u;
            S.out = realloc(S.out, S.out_cap);
            if (!S.out) exit(2);
        }
        memcpy(S.out + S.out_len, buf, len);
        S.out_len += len;
    }
    advance((double)len * S.cfg.seconds_per_byte);
    return 0;
}

void hal_flush(void) {}

// ---- the board ------------------------------------------------------------------

const fw_board_t *hal_board(void) { return &fw_board_dbv3; }
uint32_t hal_sysclk_hz(void) { return S.sysclk_hz; }

int hal_clock(const fw_clock_plan_t *plan, const char **why) {
    (void)why;
    if (!plan) {
        S.clock_on = 0;
        return 0;
    }
    S.sysclk_hz = plan->sysclk_hz;
    S.clock_hz = (double)plan->sysclk_hz * 16.0 / ((double)plan->div16 * (double)(plan->top + 1u));
    S.clock_on = 1;
    S.clock_t0 = S.now;
    return 0;
}

void hal_reset(int hold) { (void)hold; }
void hal_ui_in(int value) { (void)value; }

int hal_gpio_get(unsigned gpio) {
    const fw_board_t *b = hal_board();
    if (gpio >= b->uo_gpio && gpio < b->uo_gpio + 8u) return (uo_at(clock_at(S.now)) >> (gpio - b->uo_gpio)) & 1;
    return 0;
}

void hal_delay_us(uint32_t us) { advance((double)us * 1e-6); }

void hal_idle(void) {
    double next = INFINITY;
    if (S.streaming && S.clock_on) next = next_fill_time();
    if (S.framing && S.done_at < next) next = S.done_at;
    if (!S.fds && S.in_pos < S.in_len && S.in[S.in_pos].at < next) next = S.in[S.in_pos].at;
    if (next < INFINITY) {
        if (next > S.now) advance(next - S.now);
        return;
    }
    if (S.fds) {
        fd_fill(100);  // nothing to wait for but the host
        return;
    }
    fprintf(stderr, "sim: the firmware is waiting for something that can never happen\n");
    exit(3);
}

void hal_reboot(int bootsel) { (void)bootsel; }

uint32_t *hal_arena(size_t *bytes) {
    *bytes = S.arena_bytes;
    return S.arena;
}

// ---- streaming --------------------------------------------------------------------

int hal_stream_start(const fw_pio_program_t *prog, unsigned pio, unsigned sm, uint32_t *buf0,
                     uint32_t *buf1, uint32_t buf_words, const char **why) {
    (void)prog;
    (void)pio;
    (void)sm;
    (void)why;
    S.streaming = 1;
    S.bufs[0] = buf0;
    S.bufs[1] = buf1;
    S.buf_words = buf_words;
    S.first_clock = clock_at(S.now) + 1u;
    S.fills = 0;
    S.full[0] = S.full[1] = 0;
    S.overruns = 0;
    return 0;
}

int hal_stream_full(int i) {
    process();
    return S.full[i];
}

void hal_stream_release(int i) { S.full[i] = 0; }
uint32_t hal_stream_overruns(void) { return S.overruns; }

void hal_stream_stop(uint32_t *overruns, int *rxstall) {
    *overruns = S.overruns;
    *rxstall = S.force_rxstall;
    S.streaming = 0;
}

// ---- frame -----------------------------------------------------------------------

// A `wait gpio` index (GPIO mod 32) back to its uo_out bit, for the PIO
// window at 16 that reaches GPIO 16..47.
static unsigned uo_bit_of_wait(uint16_t word) {
    unsigned index = word & 0x1Fu;
    unsigned gpio = index < 16u ? index + 32u : index;
    return gpio - hal_board()->uo_gpio;
}

int hal_frame_start(const fw_pio_program_t *prog, unsigned pio, unsigned sm, const char **why) {
    (void)pio;
    (void)sm;
    // The operands the trigger program was built with: word 3 waits for
    // the vsync leading edge, word 7 for the hsync one (fw_pio_frame()).
    if (prog->length != 12) {
        *why = "sim: not the frame trigger program";
        return -1;
    }
    S.vs_bit = uo_bit_of_wait(prog->words[3]);
    S.vs_act = (prog->words[3] >> 7) & 1u;
    S.hs_bit = uo_bit_of_wait(prog->words[7]);
    S.hs_act = (prog->words[7] >> 7) & 1u;
    S.framing = 1;
    S.done_at = INFINITY;
    return 0;
}

// `wait level` on one uo_out bit from clock *c: the first clock at or after
// it where the bit reads `level`. 0 if it never does.
static int wait_level(uint64_t *c, unsigned bit, unsigned level) {
    for (size_t k = 0; k <= S.cfg.uo_len; k++, (*c)++) {
        if (((uo_at(*c) >> bit) & 1u) == level) return 1;
    }
    return 0;
}

void hal_frame_arm(uint32_t skip_lines, uint32_t *buf, uint32_t words) {
    uint64_t c = clock_at(S.now) + 1u;
    S.done_at = INFINITY;
    if (!S.clock_on) return;
    if (!wait_level(&c, S.vs_bit, 1u - S.vs_act) || !wait_level(&c, S.vs_bit, S.vs_act)) return;
    for (uint32_t i = 0; i < skip_lines; i++) {
        if (!wait_level(&c, S.hs_bit, 1u - S.hs_act) || !wait_level(&c, S.hs_bit, S.hs_act)) return;
    }
    pack(buf, words, c);
    S.done_at = time_of_clock(c + (uint64_t)words * SPW);
}

int hal_frame_done(void) { return S.now >= S.done_at; }
int hal_frame_rxstall(void) { return S.force_rxstall; }

int hal_frame_stop(void) {
    S.framing = 0;
    S.done_at = INFINITY;
    return S.force_rxstall;
}
