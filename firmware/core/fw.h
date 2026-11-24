// SPDX-License-Identifier: Apache-2.0
// The capture firmware's command loop and capture engines (portable).
//
// firmware/PROTOCOL.md is the contract: line commands in, `ok`/`err` lines
// out, and during a capture vgacap chunks followed by a `DONE` marker. The
// streaming engine is python/ttcap/mp/capture_rp2.py's main() and the
// frame engine frame_rp2.py's main(), statement for statement where the
// order matters (when a stop is noticed, what is counted in max_bytes,
// which TIME chunks are written and what they say), so a stream from this
// firmware and one from those scripts are read by the same host code.
#ifndef VGACAP_FW_H
#define VGACAP_FW_H

#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

#define FW_PROTOCOL_VERSION 1
#define FW_LINE_MAX 256

// Bytes that ask a running capture to stop: Ctrl-C, and "q" for a human.
#define FW_STOP_BYTE 0x03
#define FW_STOP_BYTE_HUMAN 'q'

void fw_init(void);
// Read whatever the host has sent and run every complete line. Returns
// when no byte is waiting (a capture runs to its end inside this call).
void fw_poll(void);
// Run one command line (without its newline), as fw_poll would.
void fw_execute(const char *line);
// Set once a `reboot`/`bootsel` command has been answered (the HAL reboots;
// the simulator ends).
int fw_reboot_requested(void);

#ifdef __cplusplus
}
#endif
#endif
