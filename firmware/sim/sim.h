// SPDX-License-Identifier: Apache-2.0
// The host simulator's controls (firmware/sim/hal_sim.c).
//
// The simulated board runs the firmware core unchanged; only the machine
// under it is modelled. The design is a `uo_out` value per project clock,
// repeated for ever (one or more whole frames make a steady picture), and
// time is a double count of seconds that moves only when the firmware
// writes to the host (USB costs `seconds_per_byte`), waits on purpose
// (hal_delay_us), or idles until the next thing it is waiting for (a DMA
// completion, a byte from the host). Nothing is random: a run is a pure
// function of its inputs, which is what lets the tests assert exact bytes.
//
// What the PIO programs and DMA would do is modelled at the level of what
// they deliver -- one sample per project clock, a window starting at the
// vsync/hsync edges the trigger program waits for -- not instruction by
// instruction; the instruction words themselves are checked against
// MicroPython's assembler by python/tests/test_fw_sim.py, and
// ttcap.emu runs those words cycle by cycle. One consequence: here a frame
// window's first sample is the clock on which the hsync edge appears,
// while in ttcap.emu the PIO's is 1-2 clocks later, depending on the clock
// duty and the design's output delay (on the board, on the input
// synchroniser too). libvgaframe re-places x at every hsync, so the
// pictures are the same.
#ifndef VGACAP_FW_SIM_H
#define VGACAP_FW_SIM_H

#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

typedef struct sim_config {
    const uint8_t *uo;        // uo_out per project clock, repeated
    size_t uo_len;
    double seconds_per_byte;  // USB cost of each byte sent to the host
    size_t arena_bytes;       // the capture buffer (the firmware's is ~440 KB)
    // An ASIC carrier: 0 for none (the design is always on the pins, as on
    // an FPGA board), else 1 + the mux index the design sits at; uo_out is
    // then 0 unless the mux has been reset, clocked to that index and enabled.
    int asic_index_plus1;
    // A host that stops reading once: the first write at or after
    // `stall_at` seconds waits `stall_seconds` first (0: never).
    double stall_at, stall_seconds;
    // A host that drops DTR (closes the port) at `host_gone_at` seconds (0:
    // never): from then on hal_host_gone() is true and writes fail, as
    // tud_cdc_connected() makes them on the board.
    double host_gone_at;
} sim_config_t;

// (Re)start the simulated board: boot state, time 0, empty I/O.
void sim_init(const sim_config_t *cfg);

// Host link through file descriptors (the vgacap-fw-sim process: stdin and
// stdout), or through memory (the C tests). With memory input, `at` is the
// simulated time from which bytes are visible to the firmware -- a stop
// byte "sent" part way through a capture.
void sim_io_fds(int in_fd, int out_fd);
void sim_io_memory(void);
void sim_input(const uint8_t *bytes, size_t len, double at);
// Bytes the firmware has written so far (memory mode).
const uint8_t *sim_output(size_t *len);
// True once memory input is exhausted and nothing more can arrive, or the
// fd reached end of file.
int sim_input_closed(void);

// What a front end with its own link (VGACAP_SIM_NO_LINK) has just sent the
// host: the simulated USB time it costs, as the built-in link charges it.
void sim_link_sent(size_t len);

double sim_now(void);
// Force the next frame capture's RXSTALL (tests of the rxstall reports).
void sim_force_rxstall(int on);
// How many buffers the last frame capture was armed with (hal_frame_arm's
// spans): tests of chained buffers.
unsigned sim_frame_spans(void);
// Make hal_clock() fail, as set_sys_clock_hz() can, for any plan that
// needs a different system clock.
void sim_refuse_sysclk(int on);

#ifdef __cplusplus
}
#endif
#endif
