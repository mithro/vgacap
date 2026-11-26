// SPDX-License-Identifier: Apache-2.0
#include "fw_args.h"

#include <stdio.h>
#include <string.h>

#define MAX_SPECS 16

static int is_space(char c) { return c == ' ' || c == '\t'; }

// Parse a decimal integer occupying exactly [s, s + n). No sign other than
// one leading '-', no leading '+', no spaces, at most 12 digits.
static int parse_int(const char *s, size_t n, int64_t *out) {
    size_t i = 0;
    int neg = 0;
    if (n && s[0] == '-') { neg = 1; i = 1; }
    if (i == n || n - i > 12) return -1;
    int64_t v = 0;
    for (; i < n; i++) {
        if (s[i] < '0' || s[i] > '9') return -1;
        v = v * 10 + (s[i] - '0');
    }
    *out = neg ? -v : v;
    return 0;
}

// Decimal text of `v`. By hand rather than "%lld": the MicroPython module
// builds this file into MicroPython, whose own printf has no long long
// (py/mpprint.c), and whose snprintf that is.
static const char *i64_text(int64_t v, char buf[24]) {
    char *p = buf + 23;
    *p = 0;
    uint64_t u = v < 0 ? 0u - (uint64_t)v : (uint64_t)v;
    do {
        *--p = (char)('0' + (int)(u % 10u));
        u /= 10u;
    } while (u);
    if (v < 0) *--p = '-';
    return p;
}

int fw_args_parse(const char *text, const fw_arg_spec_t *specs, size_t nspecs,
                  int64_t *values, char *err, size_t errlen) {
    int seen[MAX_SPECS] = { 0 };
    if (nspecs > MAX_SPECS) {
        snprintf(err, errlen, "internal: too many keys");
        return -1;
    }
    const char *p = text;
    for (;;) {
        while (is_space(*p)) p++;
        if (!*p) break;
        const char *tok = p;
        while (*p && !is_space(*p)) p++;
        size_t toklen = (size_t)(p - tok);
        const char *eq = memchr(tok, '=', toklen);
        if (!eq || eq == tok) {
            snprintf(err, errlen, "expected key=value, got '%.*s'", (int)toklen, tok);
            return -1;
        }
        size_t klen = (size_t)(eq - tok);
        const char *val = eq + 1;
        size_t vlen = toklen - klen - 1;
        size_t i;
        for (i = 0; i < nspecs; i++) {
            if (strlen(specs[i].key) == klen && memcmp(specs[i].key, tok, klen) == 0) break;
        }
        if (i == nspecs) {
            snprintf(err, errlen, "unknown key '%.*s'", (int)klen, tok);
            return -1;
        }
        if (seen[i]) {
            snprintf(err, errlen, "key '%s' given twice", specs[i].key);
            return -1;
        }
        seen[i] = 1;
        if (specs[i].names) {
            int64_t k;
            for (k = 0; specs[i].names[k]; k++) {
                if (strlen(specs[i].names[k]) == vlen && memcmp(specs[i].names[k], val, vlen) == 0) break;
            }
            if (!specs[i].names[k]) {
                char choices[96] = "";
                for (int64_t j = 0; specs[i].names[j]; j++) {
                    size_t used = strlen(choices);
                    snprintf(choices + used, sizeof choices - used, "%s%s", j ? "|" : "", specs[i].names[j]);
                }
                snprintf(err, errlen, "%s must be %s, got '%.*s'", specs[i].key, choices, (int)vlen, val);
                return -1;
            }
            values[i] = k;
        } else {
            int64_t v;
            if (parse_int(val, vlen, &v) || v < specs[i].min || v > specs[i].max) {
                char lo[24], hi[24];
                snprintf(err, errlen, "%s must be an integer %s..%s, got '%.*s'", specs[i].key,
                         i64_text(specs[i].min, lo), i64_text(specs[i].max, hi), (int)vlen, val);
                return -1;
            }
            values[i] = v;
        }
    }
    for (size_t i = 0; i < nspecs; i++) {
        if (!seen[i]) {
            snprintf(err, errlen, "missing key '%s'", specs[i].key);
            return -1;
        }
    }
    return 0;
}
