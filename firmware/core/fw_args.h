// SPDX-License-Identifier: Apache-2.0
// `key=value` arguments of one protocol command (firmware/PROTOCOL.md).
//
// Strict by design: every key a command takes must be given exactly once,
// an unknown key is an error, and a value outside its range is an error
// naming the range. The host always sends every key, so there are no
// defaults to disagree about.
#ifndef VGACAP_FW_ARGS_H
#define VGACAP_FW_ARGS_H

#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

typedef struct fw_arg_spec {
    const char *key;
    int64_t min, max;            // inclusive, for integers
    const char *const *names;    // NULL-terminated choices, or NULL for an integer
} fw_arg_spec_t;

// Parse `text` (the part of the line after the command word) against
// `specs`. On success fills values[i] for specs[i] -- the integer, or the
// index of the chosen name -- and returns 0. On failure writes a message
// to `err` and returns -1. `text` is not modified.
int fw_args_parse(const char *text, const fw_arg_spec_t *specs, size_t nspecs,
                  int64_t *values, char *err, size_t errlen);

#ifdef __cplusplus
}
#endif
#endif
