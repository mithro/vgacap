// SPDX-License-Identifier: Apache-2.0
// vgacap capture firmware for the Tiny Tapeout demo board v3 (RP2350).
// firmware/README.md says what it is and how to run it; PROTOCOL.md is
// what the host speaks to it.
#include "fw.h"
#include "tusb.h"

void hal_pico_init(void);

int main(void) {
    hal_pico_init();
    fw_init();
    for (;;) {
        tud_task();
        fw_poll();
    }
}
