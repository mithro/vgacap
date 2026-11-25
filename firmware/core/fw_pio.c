// SPDX-License-Identifier: Apache-2.0
#include "fw_pio.h"

#include <string.h>

// Bits 12:8 of every instruction are the delay (no side-set is used).
static uint16_t with_delay(unsigned word, unsigned delay) {
    return (uint16_t)(word | ((delay & 0x1Fu) << 8));
}

uint16_t fw_pio_wait_gpio(unsigned polarity, unsigned gpio, unsigned delay) {
    // WAIT: 001 delay pol src=00(gpio) index
    return with_delay(0x2000u | ((polarity & 1u) << 7) | (gpio & 0x1Fu), delay);
}

uint16_t fw_pio_in_pins(unsigned bits, unsigned delay) {
    // IN: 010 delay src=000(pins) bitcount (32 is encoded as 0)
    return with_delay(0x4000u | (bits & 0x1Fu), delay);
}

uint16_t fw_pio_jmp(unsigned cond, unsigned addr) {
    return (uint16_t)(((cond & 7u) << 5) | (addr & 0x1Fu));
}

uint16_t fw_pio_pull_block(void) {
    // PULL: 100 delay 1 ifempty=0 block=1 00000
    return 0x80A0u;
}

uint16_t fw_pio_mov_x_osr(void) {
    // MOV: 101 delay dest=001(x) op=00 src=111(osr)
    return 0xA027u;
}

// SHIFTCTRL as rp2.asm_pio packs it: fifo_join << 30, pull_thresh << 25,
// push_thresh << 20, out_shiftdir << 19, in_shiftdir << 18, autopull << 17,
// autopush << 16. Thresholds of 32 are encoded as 0; SHIFT_LEFT is 0, which
// is both programs' in_shiftdir and asm_pio's default out_shiftdir.
static uint32_t shiftctrl(unsigned fifo_join, unsigned push_thresh) {
    return ((uint32_t)fifo_join << 30) | ((uint32_t)(32u & 0x1Fu) << 25) |
           ((uint32_t)(push_thresh & 0x1Fu) << 20) | (1u << 16);
}

static uint32_t execctrl(unsigned wrap_target, unsigned wrap) {
    return ((uint32_t)wrap << 12) | ((uint32_t)wrap_target << 7);
}

#define FIFO_JOIN_NONE 0u
#define FIFO_JOIN_RX 2u

void fw_pio_sampler(fw_pio_program_t *p, unsigned clk_gpio, unsigned in_count,
                    unsigned push_thresh, int rising) {
    memset(p, 0, sizeof *p);
    unsigned first = rising ? 0u : 1u;  // the level the clock leaves
    p->words[0] = fw_pio_wait_gpio(first, clk_gpio, 0);
    p->words[1] = fw_pio_wait_gpio(1u - first, clk_gpio, 0);
    p->words[2] = fw_pio_in_pins(in_count, 0);
    p->length = 3;
    p->wrap_target = 0;
    p->wrap = 2;
    p->shiftctrl = shiftctrl(FIFO_JOIN_RX, push_thresh);
    p->execctrl = execctrl(p->wrap_target, p->wrap);
}

void fw_pio_frame(fw_pio_program_t *p, unsigned clk_gpio, unsigned hs_gpio,
                  unsigned vs_gpio, unsigned hs_act, unsigned vs_act, unsigned in_count,
                  unsigned push_thresh, unsigned clk_act, unsigned delay, unsigned period) {
    memset(p, 0, sizeof *p);
    enum { SKIP = 4, LINE = 6, GO = 9, SAMPLE = 11 };
    unsigned i = 0;
    p->words[i++] = fw_pio_pull_block();                        // 0: lines to skip
    p->words[i++] = fw_pio_mov_x_osr();                         // 1
    p->words[i++] = fw_pio_wait_gpio(1u - vs_act, vs_gpio, 0);  // 2
    p->words[i++] = fw_pio_wait_gpio(vs_act, vs_gpio, 0);       // 3: vsync leading edge
    p->words[i++] = fw_pio_jmp(FW_PIO_JMP_X_DEC, LINE);         // 4 skip: x lines still to skip?
    p->words[i++] = fw_pio_jmp(FW_PIO_JMP_ALWAYS, GO);          // 5
    p->words[i++] = fw_pio_wait_gpio(1u - hs_act, hs_gpio, 0);  // 6 line:
    p->words[i++] = fw_pio_wait_gpio(hs_act, hs_gpio, 0);       // 7: hsync leading edge
    p->words[i++] = fw_pio_jmp(FW_PIO_JMP_ALWAYS, SKIP);        // 8
    p->words[i++] = fw_pio_wait_gpio(1u - clk_act, clk_gpio, 0);  // 9 go:
    p->words[i++] = fw_pio_wait_gpio(clk_act, clk_gpio, delay);   // 10: the edge, then `delay`
    p->words[i++] = fw_pio_in_pins(in_count, period - 1u);        // 11: one sample per clock
    p->length = (uint8_t)i;
    p->wrap_target = SAMPLE;
    p->wrap = SAMPLE;
    p->shiftctrl = shiftctrl(FIFO_JOIN_NONE, push_thresh);
    p->execctrl = execctrl(p->wrap_target, p->wrap);
}
