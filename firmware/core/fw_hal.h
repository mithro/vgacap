// SPDX-License-Identifier: Apache-2.0
// What the portable firmware core needs from the machine under it.
//
// Two implementations: firmware/pico/hal_pico.c (pico-sdk: USB CDC, PWM,
// PIO, DMA, watchdog) and firmware/sim/hal_sim.c (a host process reading
// a design's uo_out from a file, for the tests). Everything that decides
// *what* a capture emits -- parsing, validation, the stream and frame
// loops, the chunks, the stop rules -- lives in the core and is the same
// code in both.
#ifndef VGACAP_FW_HAL_H
#define VGACAP_FW_HAL_H

#include <stddef.h>
#include <stdint.h>

#include "fw_clock.h"
#include "fw_pio.h"

#ifdef __cplusplus
extern "C" {
#endif

// ---- the host link ---------------------------------------------------------

// One byte from the host, or -1 if none is waiting. Never blocks.
int hal_read_byte(void);
// Block until the host sends something (or the link needs servicing).
void hal_wait_input(void);
// Send all of `buf`, blocking while the link is full. Returns 0, or -1 if
// the host has gone away (the bytes are dropped; a capture then stops).
int hal_write(const uint8_t *buf, size_t len);
// Push anything buffered towards the host.
void hal_flush(void);

// ---- the board -------------------------------------------------------------

// Fixed facts about the chip and the board.
typedef struct fw_board {
    const char *chip;         // "rp2350"
    unsigned clk_gpio;        // project clock (PWM)
    unsigned rst_n_gpio;      // project reset, active low
    unsigned ui_in_gpio;      // ui_in[0]; ui_in[0..7] are consecutive
    unsigned uo_gpio;         // uo_out[0]; uo_out[0..7] are consecutive
    unsigned in_count;        // bits per sample: the uo_out window
    unsigned push_thresh;     // autopush threshold: 4 samples of 8 bits
    unsigned pio_gpio_base;   // 16: uo_out (GPIO 33..40) is past GPIO 31
    unsigned pio_count;       // PIO blocks
} fw_board_t;
const fw_board_t *hal_board(void);

// The system clock running now.
uint32_t hal_sysclk_hz(void);
// Switch to `plan` (system clock first, then the PWM on the clock pin), or
// stop the clock (pin low and released) when plan is NULL. Returns 0, or
// -1 with *why set.
int hal_clock(const fw_clock_plan_t *plan, const char **why);
// Hold (1) or release (0) the project's reset.
void hal_reset(int hold);
// Drive ui_in with `value`, or release the pins when value < 0.
void hal_ui_in(int value);
// Level of one GPIO, read directly.
int hal_gpio_get(unsigned gpio);
void hal_delay_us(uint32_t us);
// Idle until something might have changed (an interrupt, a byte).
void hal_idle(void);
// Reboot: into the flash firmware, or into the USB bootloader.
void hal_reboot(int bootsel);

// The capture buffer: one static block, all of SRAM the firmware can spare.
uint32_t *hal_arena(size_t *bytes);

// ---- streaming (capture_rp2.py) ----------------------------------------------

// Load `prog` on state machine `sm` of PIO `pio` (inputs from uo_out), and
// ping-pong two DMA channels between `buf0` and `buf1`, `buf_words` each,
// each channel re-armed by its own completion interrupt. Returns 0, or -1
// with *why set (nothing is left running then).
int hal_stream_start(const fw_pio_program_t *prog, unsigned pio, unsigned sm,
                     uint32_t *buf0, uint32_t *buf1, uint32_t buf_words, const char **why);
// Whether buffer `i` holds samples nobody has sent yet; and to say it has
// been sent. A completion that finds its buffer still full is an overrun.
int hal_stream_full(int i);
void hal_stream_release(int i);
uint32_t hal_stream_overruns(void);
// Stop everything; return the overrun count and RXSTALL as they were
// before anything was torn down.
void hal_stream_stop(uint32_t *overruns, int *rxstall);

// ---- frame / window (frame_rp2.py) ------------------------------------------

int hal_frame_start(const fw_pio_program_t *prog, unsigned pio, unsigned sm, const char **why);
// One capture: the state machine restarted with `skip_lines` in its TX
// FIFO, RXSTALL cleared, a DMA of `words` into `buf`, and after it the
// stopper that disables the state machine and raises the interrupt.
void hal_frame_arm(uint32_t skip_lines, uint32_t *buf, uint32_t words);
int hal_frame_done(void);
// After done: whether the RX FIFO overflowed during the window.
int hal_frame_rxstall(void);
// Tear down; returns RXSTALL as it was first.
int hal_frame_stop(void);

#ifdef __cplusplus
}
#endif
#endif
