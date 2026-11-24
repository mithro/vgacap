// SPDX-License-Identifier: Apache-2.0
// The text outputs of a sampler run: frames.txt and frame_hashes.<method>.txt.
// docs/frames-format.md is the format; python/vgacap/framesfile.py reads it
// and writes it byte-identically. Host only.
#ifndef VGACAP_OUTPUTS_H
#define VGACAP_OUTPUTS_H

#include <stddef.h>
#include <stdint.h>
#include <stdio.h>

#include "vgacap/sample.h"

#ifdef __cplusplus
extern "C" {
#endif

// One frame, in frame-number order (record k is frame k).
typedef struct vgaout_frame {
    int64_t distinct;        // index into the distinct list, or -1: a gap (no frame)
    uint64_t start_clock;    // 0 for a gap
    uint32_t missing;        // 0 for a gap
} vgaout_frame_t;

typedef struct vgaout {
    // Header values. The strings are written as given (no newlines).
    const char *id;               // the trace's META(open) project
    const char *status;           // the run's status from the trace
    const char *sampler;          // "tb-v1" or "centre-v1"
    const char *sampler_status;   // "ok", "no-sync", "bad-timing" or "bad-window"
    const char *mode;
    uint16_t width, height;
    uint32_t clock_hz;
    double clocks_per_pixel;
    uint64_t capture_after_clock;
    const char *store;            // the store file's name
    uint64_t store_length;
    const char *store_sha256;     // lowercase hex
    // centre-v1 diagnostics; written when has_centre.
    int has_centre;
    int window_dx, window_dy;
    int hback, vback;             // the porches used (hback', vback')
    uint64_t outside;
    int suggest_dx, suggest_dy;
    uint64_t colour_changes;
    uint64_t colour_changes_off_grid;   // UINT64_MAX: written as "-"
    // Distinct frames, in store order: SHA-256 of each; fnv1a64 for the FNV
    // hash file (may be NULL when only frames.txt and SHA files are written).
    size_t n_distinct;
    const uint8_t (*sha256)[32];
    const uint64_t *fnv;
    // Frames.
    size_t n_frames;
    const vgaout_frame_t *frames;
} vgaout_t;

// Fills the centre-v1 diagnostics from the sampler's geometry and finish().
void vgaout_set_centre(vgaout_t *o, const vgasample_geometry_t *g, const centrev1_diag_t *d);

// The median (element size / 2 after sorting) of the distances between
// consecutive non-gap frame starts; 0 with fewer than two such frames.
uint64_t vgaout_frame_clocks(const vgaout_t *o);

// Return 0, or -1 on a write error, an unknown method, frames not usable
// (distinct out of range) or a missing fnv list for the FNV method.
int vgaout_frames_txt(FILE *f, const vgaout_t *o);
int vgaout_hash_file(FILE *f, const char *method, const vgaout_t *o);

#ifdef __cplusplus
}
#endif
#endif
