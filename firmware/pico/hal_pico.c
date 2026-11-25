// SPDX-License-Identifier: Apache-2.0
// The firmware HAL on the RP2350: TinyUSB CDC, PWM, PIO, DMA, watchdog.
//
// Each capture piece mirrors the MicroPython program that proved it on
// the board, and the comments there carry the measurements:
//   streaming   python/ttcap/mp/capture_rp2.py (ping-pong DMA re-armed from
//               each channel's own completion interrupt)
//   frame       python/ttcap/mp/frame_rp2.py (a data channel chained to a
//               "stopper" that disables the state machine and interrupts)
//   pins/PIO    python/ttcap/mp/prelude.py (GPIO window at 16, absolute GPIO
//               numbers, the clock pad never reconfigured as an input)
#include <string.h>

#include "fw_board.h"
#include "fw_hal.h"
#include "hardware/clocks.h"
#include "hardware/dma.h"
#include "hardware/gpio.h"
#include "hardware/irq.h"
#include "hardware/pio.h"
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

// ---- the board --------------------------------------------------------------------

const fw_board_t *hal_board(void) { return &fw_board_dbv3; }
uint32_t hal_sysclk_hz(void) { return clock_get_hz(clk_sys); }

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

int hal_gpio_get(unsigned gpio) { return gpio_get(gpio) ? 1 : 0; }
void hal_delay_us(uint32_t us) { busy_wait_us_32(us); }
void hal_wait_us(uint32_t us) {
    absolute_time_t until = make_timeout_time_us(us);
    while (!time_reached(until)) tud_task();
}
void hal_idle(void) { tud_task(); }

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

// ---- PIO ---------------------------------------------------------------------------

static struct {
    PIO pio;
    unsigned sm;
    pio_program_t program;
    uint16_t words[FW_PIO_MAX_WORDS];
    int offset;  // -1: nothing loaded
} P = { .offset = -1 };

static PIO pio_block(unsigned index) {
    return index == 0 ? pio0 : index == 1 ? pio1 : pio2;
}

static uint32_t rxstall_bit(void) { return 1u << (PIO_FDEBUG_RXSTALL_LSB + P.sm); }

// Load `prog` and configure the state machine, inputs from uo_out[0].
// pico-sdk relocates each `wait gpio` for the window at 16, exactly as
// MicroPython's loader does (pio.c add_program_at_offset).
static void unload(void);

static int load(const fw_pio_program_t *prog, unsigned pio, unsigned sm, const char **why) {
    P.pio = pio_block(pio);
    P.sm = sm;
    memcpy(P.words, prog->words, sizeof P.words);
    P.program = (pio_program_t){
        .instructions = P.words,
        .length = prog->length,
        .origin = -1,
        .pio_version = 0,
        .used_gpio_ranges = 0x6,  // GPIO 16..47
    };
    if (pio_set_gpio_base(P.pio, fw_board_dbv3.pio_gpio_base) != PICO_OK) {
        *why = "the PIO block's GPIO window cannot be moved to 16 (a program is loaded)";
        return -1;
    }
    if (!pio_can_add_program(P.pio, &P.program)) {
        *why = "no room for the program in that PIO block";
        return -1;
    }
    P.offset = pio_add_program(P.pio, &P.program);
    pio_sm_config c = pio_get_default_sm_config();
    sm_config_set_in_pin_base(&c, fw_board_dbv3.uo_gpio);
    sm_config_set_wrap(&c, (uint)P.offset + prog->wrap_target, (uint)P.offset + prog->wrap);
    c.shiftctrl = prog->shiftctrl;  // asm_pio's packing (fw_pio.h)
    pio_sm_set_enabled(P.pio, P.sm, false);
    if (pio_sm_init(P.pio, P.sm, (uint)P.offset, &c) != PICO_OK) {
        unload();
        *why = "the state machine configuration does not fit the PIO's GPIO window";
        return -1;
    }
    return 0;
}

static void unload(void) {
    if (P.offset < 0) return;
    pio_sm_set_enabled(P.pio, P.sm, false);
    pio_remove_program(P.pio, &P.program, (uint)P.offset);
    P.offset = -1;
}

// ---- DMA ---------------------------------------------------------------------------

enum { MODE_NONE, MODE_STREAM, MODE_FRAME };

static struct {
    volatile int mode;
    int ch[2];  // stream: the ping-pong pair; frame: data, stopper
    uint32_t *buf[2];
    uint32_t words;
    volatile int full[2];
    volatile int torn[2];
    volatile uint32_t overruns;
    volatile int frame_done;
    uint32_t stop_word;
} D = { .ch = { -1, -1 } };

static void __isr dma_irq(void) {
    uint32_t ints = dma_hw->ints0;
    if (D.mode == MODE_STREAM) {
        for (int i = 0; i < 2; i++) {
            uint32_t bit = 1u << D.ch[i];
            if (!(ints & bit)) continue;
            dma_hw->ints0 = bit;
            // Re-arm first, through the non-trigger aliases: the partner's
            // chain restarts this channel as soon as the partner completes.
            dma_channel_set_write_addr((uint)D.ch[i], D.buf[i], false);
            dma_channel_set_trans_count((uint)D.ch[i], D.words, false);
            // This completion has just started the partner on its buffer
            // (fw_hal.h hal_stream_full; capture_rp2.py on_a/on_b).
            if (D.full[1 - i]) {
                D.overruns++;
                D.torn[1 - i] = 1;
            }
            D.full[i] = 1;
        }
    } else if (D.mode == MODE_FRAME) {
        uint32_t bit = 1u << D.ch[1];
        if (ints & bit) {
            dma_hw->ints0 = bit;
            D.frame_done = 1;
        }
    }
    dma_hw->ints0 = ints;  // anything else that was pending is not ours
}

static void claim_channels(void) {
    D.ch[0] = dma_claim_unused_channel(true);
    D.ch[1] = dma_claim_unused_channel(true);
}

// RP2350-E5: clear the enable bit of both chained channels before aborting
// either, or the partner can re-trigger the one being aborted.
static void release_channels(void) {
    uint32_t mask = 0;
    for (int i = 0; i < 2; i++) {
        if (D.ch[i] < 0) continue;
        dma_channel_set_irq0_enabled((uint)D.ch[i], false);
        hw_clear_bits(&dma_hw->ch[D.ch[i]].al1_ctrl, DMA_CH0_CTRL_TRIG_EN_BITS);
        mask |= 1u << D.ch[i];
    }
    for (int i = 0; i < 2; i++) {
        if (D.ch[i] >= 0) dma_channel_abort((uint)D.ch[i]);
    }
    dma_hw->ints0 = mask;
    for (int i = 0; i < 2; i++) {
        if (D.ch[i] >= 0) dma_channel_unclaim((uint)D.ch[i]);
        D.ch[i] = -1;
    }
    D.mode = MODE_NONE;
}

// ---- streaming --------------------------------------------------------------------

int hal_stream_start(const fw_pio_program_t *prog, unsigned pio, unsigned sm, uint32_t *buf0,
                     uint32_t *buf1, uint32_t buf_words, const char **why) {
    if (load(prog, pio, sm, why)) return -1;
    claim_channels();
    D.buf[0] = buf0;
    D.buf[1] = buf1;
    D.words = buf_words;
    D.full[0] = D.full[1] = 0;
    D.torn[0] = D.torn[1] = 0;
    D.overruns = 0;
    const uint dreq = pio_get_dreq(P.pio, P.sm, false);
    for (int i = 0; i < 2; i++) {
        dma_channel_config c = dma_channel_get_default_config((uint)D.ch[i]);
        channel_config_set_transfer_data_size(&c, DMA_SIZE_32);
        channel_config_set_read_increment(&c, false);
        channel_config_set_write_increment(&c, true);
        channel_config_set_dreq(&c, dreq);
        channel_config_set_chain_to(&c, (uint)D.ch[1 - i]);
        channel_config_set_irq_quiet(&c, false);
        dma_channel_configure((uint)D.ch[i], &c, D.buf[i], &P.pio->rxf[P.sm], buf_words, false);
        dma_channel_set_irq0_enabled((uint)D.ch[i], true);
    }
    D.mode = MODE_STREAM;
    P.pio->fdebug = rxstall_bit();  // write 1 to clear a stale stall
    // The DMA before the state machine, so the first samples cannot stall.
    dma_channel_start((uint)D.ch[0]);
    pio_sm_set_enabled(P.pio, P.sm, true);
    return 0;
}

int hal_stream_full(int i) { return D.full[i]; }
void hal_stream_release(int i) { D.full[i] = 0; }
int hal_stream_take_torn(int i) {
    int torn = D.torn[i];
    D.torn[i] = 0;  // no handler sets it while buffer i is not full
    return torn;
}
uint32_t hal_stream_overruns(void) { return D.overruns; }

void hal_stream_stop(uint32_t *overruns, int *rxstall) {
    *overruns = D.overruns;
    *rxstall = (P.pio->fdebug & rxstall_bit()) ? 1 : 0;
    // The handlers go first: an abort can raise a spurious completion that
    // would inflate the count just taken.
    for (int i = 0; i < 2; i++) dma_channel_set_irq0_enabled((uint)D.ch[i], false);
    D.mode = MODE_NONE;
    pio_sm_set_enabled(P.pio, P.sm, false);
    release_channels();
    unload();
}

// ---- frame -----------------------------------------------------------------------

int hal_frame_start(const fw_pio_program_t *prog, unsigned pio, unsigned sm, const char **why) {
    if (load(prog, pio, sm, why)) return -1;
    claim_channels();
    D.stop_word = 1u << (PIO_CTRL_SM_ENABLE_LSB + sm);
    D.frame_done = 0;
    D.mode = MODE_FRAME;
    dma_channel_set_irq0_enabled((uint)D.ch[1], true);
    return 0;
}

void hal_frame_arm(uint32_t skip_lines, uint32_t *buf, uint32_t words) {
    // A clean state machine at its first instruction, the skip count in its
    // TX FIFO -- frame_rp2.py's sm.active(0); drain; sm.restart(); put().
    pio_sm_set_enabled(P.pio, P.sm, false);
    pio_sm_clear_fifos(P.pio, P.sm);
    pio_sm_restart(P.pio, P.sm);
    pio_sm_exec(P.pio, P.sm, pio_encode_jmp((uint)P.offset));
    pio_sm_put(P.pio, P.sm, skip_lines);

    // The stopper: one unpaced word, this state machine's enable bit, into
    // the PIO CTRL atomic-clear alias; the only transfer that interrupts.
    dma_channel_config s = dma_channel_get_default_config((uint)D.ch[1]);
    channel_config_set_transfer_data_size(&s, DMA_SIZE_32);
    channel_config_set_read_increment(&s, false);
    channel_config_set_write_increment(&s, false);
    channel_config_set_dreq(&s, DREQ_FORCE);
    channel_config_set_irq_quiet(&s, false);
    dma_channel_configure((uint)D.ch[1], &s, hw_clear_alias(&P.pio->ctrl), &D.stop_word, 1, false);
    // The window: paced by the RX FIFO, then the stopper.
    dma_channel_config d = dma_channel_get_default_config((uint)D.ch[0]);
    channel_config_set_transfer_data_size(&d, DMA_SIZE_32);
    channel_config_set_read_increment(&d, false);
    channel_config_set_write_increment(&d, true);
    channel_config_set_dreq(&d, pio_get_dreq(P.pio, P.sm, false));
    channel_config_set_chain_to(&d, (uint)D.ch[1]);
    channel_config_set_irq_quiet(&d, true);
    dma_channel_configure((uint)D.ch[0], &d, buf, &P.pio->rxf[P.sm], words, false);

    D.frame_done = 0;
    P.pio->fdebug = rxstall_bit();
    dma_channel_start((uint)D.ch[0]);
    pio_sm_set_enabled(P.pio, P.sm, true);
}

int hal_frame_done(void) { return D.frame_done; }
int hal_frame_rxstall(void) { return (P.pio->fdebug & rxstall_bit()) ? 1 : 0; }

int hal_frame_stop(void) {
    int rxstall = hal_frame_rxstall();
    dma_channel_set_irq0_enabled((uint)D.ch[1], false);
    D.mode = MODE_NONE;
    pio_sm_set_enabled(P.pio, P.sm, false);
    release_channels();
    unload();
    return rxstall;
}

// ---- boot --------------------------------------------------------------------------

void hal_pico_init(void) {
    // Restore what the stock firmware leaves driven (fw_board.h).
    drive(FW_CTRL_RESET_N_GPIO, 1);
    // uo_out: inputs, out of isolation. RP2350 pads come out of reset with
    // ISO set and read 0 for ever otherwise (prelude.py init_input_pins).
    // Only these pads: the clock, ui_in and uio are left as they are.
    for (unsigned i = 0; i < 8; i++) gpio_init(fw_board_dbv3.uo_gpio + i);
    // The project reset and ui_in are left alone until a command drives
    // them: the FPGA boards take ui_in from their own inputs.
    irq_set_exclusive_handler(DMA_IRQ_0, dma_irq);
    irq_set_enabled(DMA_IRQ_0, true);
    tusb_init();
}
