// SPDX-License-Identifier: Apache-2.0
#include "fw_clock.h"

// The rp2 MicroPython port computes in SINGLE precision floats, and the
// stock firmware's search below is MicroPython code, so it is ported with
// `float`, not `double`: `133e6 / 4 + 0.5` is not the same number in both,
// and the search compares such values. Every int that meets a float here
// is converted the way MicroPython converts it, (float)int.
#define MIN_SYSCLK 48000000u
#define DEFAULT_MAX_SYSCLK 133000000u
#define FAST_CLOCK_CAP 250000000u

static uint32_t f2i(float f) { return (uint32_t)f; }  // Python int(): truncation
static uint32_t udiff(uint32_t a, uint32_t b) { return a > b ? a - b : b - a; }
static float fabs_f(float f) { return f < 0.0f ? -f : f; }

int fw_clock_best_sysclk(uint32_t freq, uint32_t *sysclk_hz) {
    // clock_project_PWM(freqHz, max_rp2040_freq=0):
    uint32_t max_sys = DEFAULT_MAX_SYSCLK;
    if ((uint64_t)freq * 2u > max_sys && (uint64_t)freq * 2u <= FAST_CLOCK_CAP) max_sys = freq * 2u;
    // _get_best_rp2040_freq(freq, max_sys):
    if (freq > max_sys / 2u) return -1;                   // "Requested frequency too high"
    if (freq <= MIN_SYSCLK / 16777215u) return -1;        // "too low": the SDK uses a PIO clock
    uint32_t best_freq = 0;
    float best_fracdiv = 2000000000.0f;
    uint64_t start = (uint64_t)freq * 16777215u;
    uint32_t sys = start < max_sys ? (uint32_t)start : max_sys;
    if (sys > 136000000u) sys = (sys / 2000000u) * 2000000u;
    else sys = (sys / 1000000u) * 1000000u;
    while (sys >= MIN_SYSCLK && (float)sys >= 1.9f * (float)freq) {
        uint32_t next = sys - 1000000u;
        if (next > 136000000u) next = sys - 2000000u;
        uint32_t div = (sys / (2u * freq)) * 2u;
        if (div < 2u) div = 2u;
        uint32_t near = f2i((float)sys / (float)div + 0.5f);
        uint32_t far = f2i((float)sys / (float)(div + 2u) + 0.5f);
        if (udiff(near, freq) > udiff(far, freq)) div += 2u;
        float fracdiv = fabs_f((float)sys / (float)freq - (float)div);
        if (freq == sys / div) {
            *sysclk_hz = sys;
            return 0;
        }
        if (fracdiv < best_fracdiv) {
            best_fracdiv = fracdiv;
            best_freq = sys;
        }
        sys = next;
    }
    if (!best_freq) return -1;
    *sysclk_hz = best_freq;
    return 0;
}

// MicroPython's get_slice_hz(): floor((16 * sysclk + offset) / div16).
static uint32_t slice_hz(uint32_t sysclk, uint32_t offset, uint64_t div16) {
    return (uint32_t)((16u * (uint64_t)sysclk + offset) / div16);
}

int fw_clock_plan(uint32_t freq, fw_clock_plan_t *plan, const char **why) {
    uint32_t sys;
    if (fw_clock_best_sysclk(freq, &sys)) {
        *why = "no system clock for that project clock (3 Hz .. 125 MHz)";
        return -1;
    }
    // PWM.freq(freq), TOP_MAX 65534.
    const uint32_t top_max = 65534u;
    uint32_t div16, top;
    if ((sys + freq / 2u) / freq < top_max) {
        div16 = 16u;
        top = (sys + freq / 2u) / freq - 1u;
    } else {
        uint64_t denom = (uint64_t)top_max * freq;
        div16 = (uint32_t)((16u * (uint64_t)sys + denom - 1u) / denom);  // get_slice_hz_ceil
        uint64_t d = (uint64_t)div16 * freq;
        top = slice_hz(sys, (uint32_t)(d / 2u), d) - 1u;                  // get_slice_hz_round
    }
    if (div16 < 16u || div16 >= 256u * 16u) {
        *why = "the PWM cannot make that clock";
        return -1;
    }
    plan->clock_hz = freq;
    plan->sysclk_hz = sys;
    plan->div16 = div16;
    plan->top = top;
    // PWM.duty_u16(int(0xffff / 2)), as ttboard's Pin.pwm() passes it.
    plan->level = (uint32_t)((32767u * ((uint64_t)top + 1u) + 65535u / 2u) / 65535u);
    uint64_t per = (uint64_t)div16 * (top + 1u);
    plan->actual_hz = slice_hz(sys, (uint32_t)(per / 2u), per);
    return 0;
}

uint32_t fw_clock_period(uint32_t sysclk_hz, uint32_t clock_hz) {
    if (!clock_hz) return 0;
    uint32_t period = (uint32_t)(((uint64_t)sysclk_hz + clock_hz / 2u) / clock_hz);
    if (period < 2u || period > 32u) return 0;
    uint64_t made = (uint64_t)period * clock_hz;
    uint64_t err = made > sysclk_hz ? made - sysclk_hz : sysclk_hz - made;
    if (err * 200u > sysclk_hz) return 0;
    return period;
}
