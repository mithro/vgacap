// SPDX-License-Identifier: Apache-2.0
//
// vgacap-rp1's PIO programs, as piolib's pio_encode_* builds them. Shared by
// vgacap_rp1.c and print_programs.c, the host tool python/tests/
// test_rp1_sampler.py compiles to check the sampler against the RP2's.
//
// The includer provides pio_encode_*, and this header includes neither of
// piolib's two sets. vgacap_rp1.c has piolib.h's, which dispatch through the
// opened chip (so they need /dev/pio0) to pio_rp1.c, whose RP1 encoders
// return hardware/pio_instructions.h's, renamed and unchanged (piolib at
// 05e8808e). print_programs.c includes hardware/pio_instructions.h directly,
// so it encodes the same words with no device.

#ifndef VGACAP_RP1_PROGRAMS_H
#define VGACAP_RP1_PROGRAMS_H

#include <stdbool.h>
#include <stdint.h>

#define RP1_SAMPLER_LEN 3u
#define RP1_GENERATOR_LEN 2u
#define RP1_COUNTER_LEN 2u

// The sampler: the RP2's program (ttcap/mp/capture_rp2.py), word for word.
// Wait for the clock's edge on absolute GPIO `clk`, then `in pins, 8`.
static inline void rp1_sampler_program(uint16_t insns[RP1_SAMPLER_LEN], bool rising, uint clk)
{
    insns[0] = (uint16_t)pio_encode_wait_gpio(!rising, clk);
    insns[1] = (uint16_t)pio_encode_wait_gpio(rising, clk);
    insns[2] = (uint16_t)pio_encode_in(pio_pins, 8);
}

// The --gen generator: `out pins, 8` with the clock (side-set) low, then the
// clock high; each held `delay` extra PIO clocks.
static inline void rp1_generator_program(uint16_t insns[RP1_GENERATOR_LEN], uint delay)
{
    insns[0] = (uint16_t)(pio_encode_out(pio_pins, 8) | pio_encode_sideset(1, 0) | pio_encode_delay(delay));
    insns[1] = (uint16_t)(pio_encode_nop() | pio_encode_sideset(1, 1) | pio_encode_delay(delay));
}

// The --gen-counter generator: X onto the pins with the clock low, then
// `jmp x--` back to the program's start (0, program-relative: pio_add_program
// relocates it) with the clock high.
static inline void rp1_counter_program(uint16_t insns[RP1_COUNTER_LEN], uint delay)
{
    insns[0] = (uint16_t)(pio_encode_mov(pio_pins, pio_x) | pio_encode_sideset(1, 0) | pio_encode_delay(delay));
    insns[1] = (uint16_t)(pio_encode_jmp_x_dec(0) | pio_encode_sideset(1, 1) | pio_encode_delay(delay));
}

#endif
