// SPDX-License-Identifier: Apache-2.0
//
// vgacap-rp1-programs: print vgacap-rp1's PIO programs as hex words, built by
// the same code (programs.h) the capture tool loads. Runs on any host; it
// needs only piolib's headers. python/tests/test_rp1_sampler.py compares its
// sampler against MicroPython's assembly of the RP2 sampler.
//
//   vgacap-rp1-programs CLK rising|falling DELAY
//   -> sampler 200c 208c 4008
//      generator ....
//      counter ....

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

// The encoders pio_rp1.c wraps for the RP1 chip; see programs.h.
#include "pio_platform.h"
#include "hardware/pio_instructions.h"
#include "programs.h"

static void print(const char *name, const uint16_t *insns, unsigned n)
{
    printf("%s", name);
    for (unsigned i = 0; i < n; i++)
        printf(" %04x", insns[i]);
    printf("\n");
}

int main(int argc, char **argv)
{
    if (argc != 4 || (strcmp(argv[2], "rising") && strcmp(argv[2], "falling"))) {
        fprintf(stderr, "usage: vgacap-rp1-programs CLK rising|falling DELAY\n");
        return 2;
    }
    uint clk = (uint)strtoul(argv[1], NULL, 0);
    bool rising = !strcmp(argv[2], "rising");
    uint delay = (uint)strtoul(argv[3], NULL, 0);
    if (clk > 31 || delay > 15) {
        fprintf(stderr, "vgacap-rp1-programs: CLK must be 0..31 and DELAY 0..15\n");
        return 2;
    }
    uint16_t sampler[RP1_SAMPLER_LEN], generator[RP1_GENERATOR_LEN], counter[RP1_COUNTER_LEN];
    rp1_sampler_program(sampler, rising, clk);
    rp1_generator_program(generator, delay);
    rp1_counter_program(counter, delay);
    print("sampler", sampler, RP1_SAMPLER_LEN);
    print("generator", generator, RP1_GENERATOR_LEN);
    print("counter", counter, RP1_COUNTER_LEN);
    return 0;
}
