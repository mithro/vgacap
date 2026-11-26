// SPDX-License-Identifier: Apache-2.0
// The capture half of the HAL on the RP2350, shared by the two programs
// that run the capture engines on the chip: the capture firmware
// (firmware/pico: it owns the whole chip) and the MicroPython module
// (mpy/: MicroPython owns it, and the capture borrows a PIO state machine,
// two DMA channels and one DMA interrupt for the length of one capture).
//
// hal_rp2.c implements fw_hal.h's board facts, hal_gpio_get, hal_delay_us,
// and the streaming and frame pieces. The rest -- the host link, hal_idle,
// the clock, reset, ui_in, reboot and the arena -- is each program's own.
#ifndef VGACAP_HAL_RP2_H
#define VGACAP_HAL_RP2_H

#ifdef __cplusplus
extern "C" {
#endif

// The DMA interrupt (DMA_IRQ_<n>) the capture's channels raise. Its handler
// is a shared one, added when a capture starts and removed when it stops,
// and it acknowledges only the capture's own channels, so it can sit
// alongside other users of that interrupt. The firmware takes 0; the
// MicroPython module takes 1, because MicroPython's rp2.DMA owns 0.
#ifndef HAL_RP2_DMA_IRQ_INDEX
#define HAL_RP2_DMA_IRQ_INDEX 0
#endif

// Called before a capture loads its program into PIO block `pio` and runs
// it on state machine `sm`: the owner of the chip makes room there. Return
// 0, or -1 with *why set to refuse the capture. The firmware has nothing to
// do. The MicroPython module does what the capture scripts do through
// `rp2` (python/ttcap/mp/prelude.py new_state_machine()): clears
// MicroPython's own programs from the block (never block 0, which holds
// the stock firmware's FPGA loader) and takes the state machine as
// MicroPython's, which refuses one claimed by something else.
int hal_rp2_pio_prepare(unsigned pio, unsigned sm, const char **why);

// Bring the uo_out pads up as inputs, out of the RP2350's pad isolation, and
// nothing else (prelude.py init_input_pins). Each capture does it before it
// loads its program; the module also does it before a frame capture
// measures the sync polarities.
void hal_rp2_uo_inputs(void);

#ifdef __cplusplus
}
#endif
#endif
