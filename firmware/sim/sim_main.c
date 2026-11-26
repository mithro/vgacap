// SPDX-License-Identifier: Apache-2.0
// vgacap-fw-sim: the capture firmware on a simulated board, speaking
// firmware/PROTOCOL.md on stdin/stdout. Used by the Python tests
// (python/tests/test_fw_sim.py) as the device behind ttcap's firmware
// transport, and handy for trying the protocol by hand:
//
//   vgacap-fw-sim --uo frame.bin
//   hello
//   clock hz=25000000
//   ...
//
// --uo FILE           the design's uo_out, one byte per project clock,
//                     repeated for ever (required)
// --bytes-per-s N     USB rate the simulated board sends at (default 1e6)
// --arena-bytes N     capture buffer size (default the firmware's)
// --rxstall           report RXSTALL from every capture
#define _POSIX_C_SOURCE 200809L
#include <signal.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "fw.h"
#include "fw_hal.h"
#include "sim.h"

static void usage(void) {
    fprintf(stderr, "usage: vgacap-fw-sim --uo FILE [--bytes-per-s N] [--arena-bytes N] [--rxstall] [--asic-index N]\n");
    exit(2);
}

int main(int argc, char **argv) {
    const char *uo_path = NULL;
    double bytes_per_s = 1e6;
    unsigned long arena = 0;
    int rxstall = 0;
    int asic_index = -1;
    for (int i = 1; i < argc; i++) {
        if (!strcmp(argv[i], "--uo") && i + 1 < argc) uo_path = argv[++i];
        else if (!strcmp(argv[i], "--bytes-per-s") && i + 1 < argc) bytes_per_s = strtod(argv[++i], NULL);
        else if (!strcmp(argv[i], "--arena-bytes") && i + 1 < argc) arena = strtoul(argv[++i], NULL, 0);
        else if (!strcmp(argv[i], "--rxstall")) rxstall = 1;
        else if (!strcmp(argv[i], "--asic-index") && i + 1 < argc) asic_index = atoi(argv[++i]);
        else usage();
    }
    if (!uo_path || !(bytes_per_s > 0)) usage();

    FILE *f = fopen(uo_path, "rb");
    if (!f) {
        perror(uo_path);
        return 2;
    }
    size_t cap = 1u << 20, len = 0;
    uint8_t *uo = malloc(cap);
    for (;;) {
        if (!uo) return 2;
        size_t n = fread(uo + len, 1, cap - len, f);
        len += n;
        if (n == 0) break;
        if (len == cap) uo = realloc(uo, cap *= 2u);
    }
    fclose(f);
    if (!len) {
        fprintf(stderr, "%s: empty\n", uo_path);
        return 2;
    }

    signal(SIGPIPE, SIG_IGN);
    sim_config_t cfg = { .uo = uo, .uo_len = len, .seconds_per_byte = 1.0 / bytes_per_s,
                         .arena_bytes = arena, .asic_index_plus1 = asic_index + 1 };
    sim_init(&cfg);
    sim_force_rxstall(rxstall);
    sim_io_fds(0, 1);
    fw_init();
    while (!fw_reboot_requested()) {
        fw_poll();
        if (fw_reboot_requested() || sim_input_closed()) break;
        hal_wait_input();
    }
    free(uo);
    return 0;
}
