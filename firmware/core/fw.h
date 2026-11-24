// SPDX-License-Identifier: Apache-2.0
// The capture firmware's command loop (portable).
//
// firmware/PROTOCOL.md is the contract: line commands in, `ok`/`err` lines
// out, and during a capture vgacap chunks followed by a `DONE` marker. The
// captures themselves are fw_capture.c's engines, which the MicroPython
// module (mpy/) drives too; this file is the protocol around them: the
// line reader, the board-state commands, the replies and the DONE marker.
#ifndef VGACAP_FW_H
#define VGACAP_FW_H

#include <stddef.h>
#include <stdint.h>

#include "fw_capture.h"

#ifdef __cplusplus
extern "C" {
#endif

#define FW_PROTOCOL_VERSION 1
#define FW_LINE_MAX 256

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
