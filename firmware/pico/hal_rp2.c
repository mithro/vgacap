// SPDX-License-Identifier: Apache-2.0
// The capture half of the HAL on the RP2350: PIO, DMA, and the board's
// fixed facts (hal_rp2.h says who uses it and what is left to them).
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
#include "hal_rp2.h"
#include "hardware/clocks.h"
#include "hardware/dma.h"
#include "hardware/gpio.h"
#include "hardware/irq.h"
#include "hardware/pio.h"
#include "pico/time.h"

#define DMA_IRQ (DMA_IRQ_0 + HAL_RP2_DMA_IRQ_INDEX)

// ---- the board --------------------------------------------------------------------

const fw_board_t *hal_board(void) { return &fw_board_dbv3; }
uint32_t hal_sysclk_hz(void) { return clock_get_hz(clk_sys); }
int hal_gpio_get(unsigned gpio) { return gpio_get(gpio) ? 1 : 0; }
void hal_delay_us(uint32_t us) { busy_wait_us_32(us); }

// ---- PIO ---------------------------------------------------------------------------

static struct {
    PIO pio;
    unsigned sm;
    pio_program_t program;
    uint16_t words[FW_PIO_MAX_WORDS];
    int offset;  // -1: nothing loaded
} P = { .offset = -1 };

// RP2350 pads come out of reset with ISO set and read 0 for ever otherwise.
// Only these pads: the clock, ui_in and uio are left as they are.
void hal_rp2_uo_inputs(void) {
    for (unsigned i = 0; i < 8; i++) gpio_init(fw_board_dbv3.uo_gpio + i);
}

static PIO pio_block(unsigned index) {
    return index == 0 ? pio0 : index == 1 ? pio1 : pio2;
}

static uint32_t rxstall_bit(void) { return 1u << (PIO_FDEBUG_RXSTALL_LSB + P.sm); }

static void unload(void) {
    if (P.offset < 0) return;
    pio_sm_set_enabled(P.pio, P.sm, false);
    pio_remove_program(P.pio, &P.program, (uint)P.offset);
    P.offset = -1;
}

// Load `prog` and configure the state machine, inputs from uo_out[0].
// pico-sdk relocates each `wait gpio` for the window at 16, exactly as
// MicroPython's loader does (pio.c add_program_at_offset).
static int load(const fw_pio_program_t *prog, unsigned pio, unsigned sm, const char **why) {
    if (hal_rp2_pio_prepare(pio, sm, why)) return -1;
    hal_rp2_uo_inputs();
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

// ---- DMA ---------------------------------------------------------------------------

enum { MODE_NONE, MODE_STREAM, MODE_FRAME };

static struct {
    volatile int mode;
    int ch[2];  // stream: the ping-pong pair; frame: data, stopper
    uint32_t *buf[2];
    uint32_t words;
    volatile int full[2];
    volatile uint32_t overruns;
    volatile int frame_done;
    uint32_t stop_word;
} D = { .ch = { -1, -1 } };

// A shared handler: it acknowledges only this capture's channels, so
// anything else on the same interrupt keeps its own.
static void __isr dma_irq(void) {
    if (D.mode == MODE_STREAM) {
        for (int i = 0; i < 2; i++) {
            if (!dma_irqn_get_channel_status(HAL_RP2_DMA_IRQ_INDEX, (uint)D.ch[i])) continue;
            dma_irqn_acknowledge_channel(HAL_RP2_DMA_IRQ_INDEX, (uint)D.ch[i]);
            // Re-arm first, through the non-trigger aliases: the partner's
            // chain restarts this channel as soon as the partner completes.
            dma_channel_set_write_addr((uint)D.ch[i], D.buf[i], false);
            dma_channel_set_trans_count((uint)D.ch[i], D.words, false);
            if (D.full[i]) D.overruns++;
            D.full[i] = 1;
        }
    } else if (D.mode == MODE_FRAME) {
        if (dma_irqn_get_channel_status(HAL_RP2_DMA_IRQ_INDEX, (uint)D.ch[1])) {
            dma_irqn_acknowledge_channel(HAL_RP2_DMA_IRQ_INDEX, (uint)D.ch[1]);
            D.frame_done = 1;
        }
    }
}

static void irq_on(void) {
    irq_add_shared_handler(DMA_IRQ, dma_irq, PICO_SHARED_IRQ_HANDLER_DEFAULT_ORDER_PRIORITY);
    irq_set_enabled(DMA_IRQ, true);
}

static void irq_off(void) {
    irq_remove_handler(DMA_IRQ, dma_irq);
    // Leave the interrupt enabled for anyone else still sharing it.
    if (!irq_has_shared_handler(DMA_IRQ)) irq_set_enabled(DMA_IRQ, false);
}

// RP2350-E5: clear the enable bit of both chained channels before aborting
// either, or the partner can re-trigger the one being aborted.
static void release_channels(void) {
    for (int i = 0; i < 2; i++) {
        if (D.ch[i] < 0) continue;
        dma_irqn_set_channel_enabled(HAL_RP2_DMA_IRQ_INDEX, (uint)D.ch[i], false);
        hw_clear_bits(&dma_hw->ch[D.ch[i]].al1_ctrl, DMA_CH0_CTRL_TRIG_EN_BITS);
    }
    for (int i = 0; i < 2; i++) {
        if (D.ch[i] >= 0) dma_channel_abort((uint)D.ch[i]);
    }
    for (int i = 0; i < 2; i++) {
        if (D.ch[i] >= 0) dma_irqn_acknowledge_channel(HAL_RP2_DMA_IRQ_INDEX, (uint)D.ch[i]);
    }
    for (int i = 0; i < 2; i++) {
        if (D.ch[i] >= 0) dma_channel_unclaim((uint)D.ch[i]);
        D.ch[i] = -1;
    }
    D.mode = MODE_NONE;
}

// Two channels and the interrupt, or none of them and *why set.
static int claim_channels(const char **why) {
    D.ch[0] = dma_claim_unused_channel(false);
    D.ch[1] = dma_claim_unused_channel(false);
    if (D.ch[0] < 0 || D.ch[1] < 0) {
        for (int i = 0; i < 2; i++) {
            if (D.ch[i] >= 0) dma_channel_unclaim((uint)D.ch[i]);
            D.ch[i] = -1;
        }
        *why = "no two free DMA channels";
        return -1;
    }
    irq_on();
    return 0;
}

// Everything a capture took, given back: the state machine stopped, the
// channels aborted and released, the interrupt handler removed, the
// program unloaded.
static void teardown(void) {
    for (int i = 0; i < 2; i++) {
        if (D.ch[i] >= 0) dma_irqn_set_channel_enabled(HAL_RP2_DMA_IRQ_INDEX, (uint)D.ch[i], false);
    }
    D.mode = MODE_NONE;
    pio_sm_set_enabled(P.pio, P.sm, false);
    release_channels();
    irq_off();
    unload();
}

// ---- streaming --------------------------------------------------------------------

int hal_stream_start(const fw_pio_program_t *prog, unsigned pio, unsigned sm, uint32_t *buf0,
                     uint32_t *buf1, uint32_t buf_words, const char **why) {
    if (load(prog, pio, sm, why)) return -1;
    if (claim_channels(why)) {
        unload();
        return -1;
    }
    D.buf[0] = buf0;
    D.buf[1] = buf1;
    D.words = buf_words;
    D.full[0] = D.full[1] = 0;
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
    }
    D.mode = MODE_STREAM;
    for (int i = 0; i < 2; i++) dma_irqn_set_channel_enabled(HAL_RP2_DMA_IRQ_INDEX, (uint)D.ch[i], true);
    P.pio->fdebug = rxstall_bit();  // write 1 to clear a stale stall
    // The DMA before the state machine, so the first samples cannot stall.
    dma_channel_start((uint)D.ch[0]);
    pio_sm_set_enabled(P.pio, P.sm, true);
    return 0;
}

int hal_stream_full(int i) { return D.full[i]; }
void hal_stream_release(int i) { D.full[i] = 0; }
uint32_t hal_stream_overruns(void) { return D.overruns; }

void hal_stream_stop(uint32_t *overruns, int *rxstall) {
    *overruns = D.overruns;
    *rxstall = (P.pio->fdebug & rxstall_bit()) ? 1 : 0;
    // The handlers go first (teardown): an abort can raise a spurious
    // completion that would inflate the count just taken.
    teardown();
}

// ---- frame -----------------------------------------------------------------------

int hal_frame_start(const fw_pio_program_t *prog, unsigned pio, unsigned sm, const char **why) {
    if (load(prog, pio, sm, why)) return -1;
    if (claim_channels(why)) {
        unload();
        return -1;
    }
    D.stop_word = 1u << (PIO_CTRL_SM_ENABLE_LSB + sm);
    D.frame_done = 0;
    D.mode = MODE_FRAME;
    dma_irqn_set_channel_enabled(HAL_RP2_DMA_IRQ_INDEX, (uint)D.ch[1], true);
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
    teardown();
    return rxstall;
}
