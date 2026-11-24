// SPDX-License-Identifier: Apache-2.0
// Project clock planning: the system clock and the PWM settings that make
// a requested project clock, chosen exactly as the stock firmware chooses
// them, so a capture taken with this firmware runs the design at the same
// clock -- and samples it with the same system-clock ratio -- as one taken
// through the MicroPython scripts.
//
// Portable: no pico-sdk. firmware/pico/hal_pico.c applies a plan, the
// host simulator (firmware/sim) only reads it.
#ifndef VGACAP_FW_CLOCK_H
#define VGACAP_FW_CLOCK_H

#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

typedef struct fw_clock_plan {
    uint32_t clock_hz;   // what was asked for
    uint32_t sysclk_hz;  // system clock to run at
    uint32_t div16;      // PWM divider, 8.4 fixed point (16 = divide by 1)
    uint32_t top;        // PWM wrap: the counter runs 0..top
    uint32_t level;      // PWM compare: the output is high while counter < level
    uint32_t actual_hz;  // the PWM frequency this produces, rounded
} fw_clock_plan_t;

// The stock firmware's system clock for a project clock of `freq` Hz:
// ttboard `DemoBoard._get_best_rp2040_freq(freq, max)` (tt-micropython-
// firmware src/ttboard/demoboard.py), including how `clock_project_PWM()`
// raises the 133 MHz cap to 2 x freq for fast clocks. Scans down from the
// cap in 1 MHz steps (2 MHz above 136 MHz) for a frequency an even PWM
// divider takes exactly to `freq`; failing that, the closest. Returns 0 and
// sets *sysclk_hz, or -1 for a clock outside 3 Hz .. cap / 2.
int fw_clock_best_sysclk(uint32_t freq, uint32_t *sysclk_hz);

// The full plan for `freq`: the system clock above, then MicroPython's
// `PWM.freq(freq)` and `PWM.duty_u16(32767)` arithmetic (ports/rp2/
// machine_pwm.c, v1.24.0) at that system clock -- divider 1 and
// top = round(sysclk / freq) - 1 whenever that fits, and a 50% compare
// rounded the way MicroPython rounds it. Returns 0, or -1 with *why set.
int fw_clock_plan(uint32_t freq, fw_clock_plan_t *plan, const char **why);

// System clocks per project clock if that is a whole number the frame
// sampler can use (2..32, within 0.5%, as frame_rp2.py checks), else 0.
uint32_t fw_clock_period(uint32_t sysclk_hz, uint32_t clock_hz);

#ifdef __cplusplus
}
#endif
#endif
