// SPDX-License-Identifier: Apache-2.0
// The two capture PIO programs, built for the operands of one capture.
//
// firmware/pio/vgacap_capture.pio is the readable source of both programs
// (and is what pioasm assembles in the firmware build, so a syntax or
// encoding mistake there fails the build). Their operands -- the edge to
// sample, the sync polarities, the sample delay and the system clocks per
// project clock -- are only known at capture time, so the words actually
// loaded are built here, one encoder per instruction kind, in the same
// order as the .pio source. The tests hold the result to two references:
// the pioasm template (firmware/pio/vgacap_capture.pio, at its default
// operands) and the words MicroPython's own `rp2.asm_pio` assembles for
// python/ttcap/mp/capture_rp2.py `make_sampler()` and frame_rp2.py
// `make_trigger()` (python/tests/test_fw_sim.py, through the emulator).
//
// Program words carry a `wait gpio` index as the GPIO number mod 32, which
// is what pioasm and MicroPython's assembler both emit; the loader (pico-sdk
// pio_add_program, MicroPython's rp2 module) relocates it for a PIO block
// whose GPIO window is based at 16. The configuration register values are
// the RP2040/RP2350 SHIFTCTRL and EXECCTRL layouts, with wrap addresses
// relative to the program's first instruction.
#ifndef VGACAP_FW_PIO_H
#define VGACAP_FW_PIO_H

#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

#define FW_PIO_MAX_WORDS 16

typedef struct fw_pio_program {
    uint16_t words[FW_PIO_MAX_WORDS];
    uint8_t length;
    uint8_t wrap_target;  // relative to the first instruction
    uint8_t wrap;
    uint32_t shiftctrl;   // SMx_SHIFTCTRL as MicroPython's asm_pio packs it
    uint32_t execctrl;    // SMx_EXECCTRL: only the wrap fields, relative
} fw_pio_program_t;

// Instruction encoders (RP2040 datasheet 3.4; identical on RP2350).
uint16_t fw_pio_wait_gpio(unsigned polarity, unsigned gpio, unsigned delay);
uint16_t fw_pio_in_pins(unsigned bits, unsigned delay);
uint16_t fw_pio_jmp(unsigned cond, unsigned addr);
uint16_t fw_pio_pull_block(void);
uint16_t fw_pio_mov_x_osr(void);

#define FW_PIO_JMP_ALWAYS 0u
#define FW_PIO_JMP_X_DEC 2u

// The streaming sampler (capture_rp2.py make_sampler): wait for an edge of
// the project clock, sample `in_count` pins, wrap. `rising` swaps the two
// waits. Autopush at `push_thresh` bits, ISR shifting left, RX FIFO joined.
void fw_pio_sampler(fw_pio_program_t *p, unsigned clk_gpio, unsigned in_count,
                    unsigned push_thresh, int rising);

// The frame trigger + phase-locked sampler (frame_rp2.py make_trigger):
// pull the lines to skip, wait for the vsync leading edge, count that many
// hsync leading edges, sync to one project-clock edge plus `delay` system
// clocks, then `in pins` once every `period` system clocks for ever.
// `*_act` is the level of the sync during its pulse; `clk_act` 1 samples
// on the rising edge. Autopush, ISR shifting left, FIFOs not joined.
void fw_pio_frame(fw_pio_program_t *p, unsigned clk_gpio, unsigned hs_gpio,
                  unsigned vs_gpio, unsigned hs_act, unsigned vs_act, unsigned in_count,
                  unsigned push_thresh, unsigned clk_act, unsigned delay, unsigned period);

#ifdef __cplusplus
}
#endif
#endif
