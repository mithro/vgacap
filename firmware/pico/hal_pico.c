// SPDX-License-Identifier: Apache-2.0
// The firmware's own half of the HAL on the RP2350: the host link (TinyUSB
// CDC), the project clock, reset and ui_in, idling, reboot, the capture
// buffer and boot. The capture half -- PIO, DMA, the board's facts -- is
// hal_rp2.c, which the MicroPython module (mpy/) shares.
#include <string.h>

#include "fw_board.h"
#include "fw_hal.h"
#include "hal_rp2.h"
#include "hardware/clocks.h"
#include "hardware/gpio.h"
#include "hardware/pwm.h"
#include "hardware/watchdog.h"
#include "pico/bootrom.h"
#include "pico/time.h"
#include "tusb.h"

// The capture buffer. no_flash: this, the code and the stacks are all SRAM.
static uint32_t arena[FW_ARENA_BYTES / 4u] __attribute__((aligned(4)));

// ---- the host link ------------------------------------------------------------

int hal_read_byte(void) {
    tud_task();
    if (!tud_cdc_available()) return -1;
    uint8_t c;
    return tud_cdc_read(&c, 1) == 1 ? c : -1;
}

void hal_wait_input(void) { tud_task(); }

// Blocks while the TX FIFO is full, servicing USB. A host that drops DTR
// (closes the port) is gone: the bytes are dropped and the capture ends,
// rather than the firmware waiting for ever on a port nobody reads.
int hal_write(const uint8_t *buf, size_t len) {
    while (len) {
        if (!tud_cdc_connected()) return -1;
        uint32_t n = tud_cdc_write(buf, (uint32_t)len);
        if (n) {
            buf += n;
            len -= n;
        } else {
            tud_cdc_write_flush();
            tud_task();
        }
    }
    return 0;
}

int hal_host_gone(void) {
    tud_task();
    return !tud_cdc_connected();
}

void hal_flush(void) {
    tud_cdc_write_flush();
    tud_task();
}

// ---- the project: clock, reset, ui_in ---------------------------------------------

// tt.clock_project_PWM(): the system clock first, then the PWM.
// tt.clock_project_stop(): PWM off, the pin an input again.
int hal_clock(const fw_clock_plan_t *plan, const char **why) {
    const unsigned pin = fw_board_dbv3.clk_gpio;
    const unsigned slice = pwm_gpio_to_slice_num(pin);
    pwm_set_enabled(slice, false);
    if (!plan) {
        gpio_init(pin);
        return 0;
    }
    if (plan->sysclk_hz != clock_get_hz(clk_sys) && !set_sys_clock_hz(plan->sysclk_hz, false)) {
        gpio_init(pin);
        *why = "the system clock cannot be set to that frequency";
        return -1;
    }
    pwm_hw->slice[slice].csr = 0;  // free-running, normal polarity, disabled
    pwm_hw->slice[slice].div = plan->div16;
    pwm_set_wrap(slice, (uint16_t)plan->top);
    pwm_set_chan_level(slice, pwm_gpio_to_channel(pin), (uint16_t)plan->level);
    pwm_set_counter(slice, 0);
    gpio_set_function(pin, GPIO_FUNC_PWM);
    pwm_set_enabled(slice, true);
    return 0;
}

static void drive(unsigned gpio, int level) {
    if (gpio_get_function(gpio) != GPIO_FUNC_SIO) gpio_init(gpio);
    gpio_put(gpio, level != 0);  // the level first, so no glitch the other way
    gpio_set_dir(gpio, GPIO_OUT);
}

void hal_reset(int hold) { drive(fw_board_dbv3.rst_n_gpio, !hold); }

// GPIO 0/1/2: ctrl_ena, ctrl_sel_nrst, ctrl_sel_inc (GPIOMapTTDBv3).
void hal_asic_ctrl(int ena, int nrst, int inc) {
    drive(2, inc);
    drive(1, nrst);
    drive(0, ena);
}

void hal_ui_in(int value) {
    for (unsigned i = 0; i < 8; i++) {
        unsigned gpio = fw_board_dbv3.ui_in_gpio + i;
        if (value < 0) gpio_set_dir(gpio, GPIO_IN);
        else drive(gpio, (value >> i) & 1);
    }
}

void hal_idle(void) { tud_task(); }

// hal_delay_us is hal_rp2.c's busy wait; this one services USB meanwhile.
void hal_wait_us(uint32_t us) {
    absolute_time_t until = make_timeout_time_us(us);
    while (!time_reached(until)) tud_task();
}

void hal_reboot(int bootsel) {
    // Let the `ok` reach the host before the USB device disappears.
    absolute_time_t until = make_timeout_time_ms(100);
    while (!time_reached(until)) tud_task();
    if (bootsel) reset_usb_boot(0, 0);
    watchdog_reboot(0, 0, 10);  // a normal boot: the stock firmware in flash
    for (;;) tight_loop_contents();
}

uint32_t *hal_arena(size_t *bytes) {
    *bytes = sizeof arena;
    return arena;
}

// The whole chip is the firmware's: nothing to make room for.
int hal_rp2_pio_prepare(unsigned pio, unsigned sm, const char **why) {
    (void)pio;
    (void)sm;
    (void)why;
    return 0;
}

// ---- boot --------------------------------------------------------------------------

void hal_pico_init(void) {
    // Restore what the stock firmware leaves driven (fw_board.h).
    drive(FW_CTRL_RESET_N_GPIO, 1);
    // uo_out: inputs, out of isolation (hal_rp2.c does this again before
    // each capture). RP2350 pads come out of reset with ISO set and read 0
    // for ever otherwise (prelude.py init_input_pins). Only these pads: the
    // clock, ui_in and uio are left as they are.
    for (unsigned i = 0; i < 8; i++) gpio_init(fw_board_dbv3.uo_gpio + i);
    // The project reset and ui_in are left alone until a command drives
    // them: the FPGA boards take ui_in from their own inputs. The DMA
    // interrupt handler is hal_rp2.c's, added for each capture.
    tusb_init();
}
