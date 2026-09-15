// SPDX-License-Identifier: Apache-2.0
// vgacap-frames <in.vgacap> <out-prefix> [--max-frames N] [--partial]
//
// Reads a vgacap capture stream and reconstructs each frame into a binary
// PPM (P6) image, <out-prefix>-NNNN.ppm. Partial frames (not every line
// covered) are only written when --partial is given.
//
// NOTE: this tool links against vgacap_stream (for vgacap_reader_*) which is
// implemented on a separate branch; it is intentionally not wired into
// CMakeLists.txt here. The controller adds the executable target after the
// two branches are merged.
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "vgacap/frame.h"
#include "vgacap/stream.h"

#define MAX_CPL 1400
#define MAX_LINES 900

typedef struct {
    vgaframe_t frame;
    uint8_t raw[(size_t)MAX_CPL * MAX_LINES];
    uint8_t rgb[(size_t)MAX_CPL * MAX_LINES * 3];
    uint8_t cover[MAX_LINES];
    const char *out_prefix;
    int max_frames;
    int write_partial;
    uint32_t frames_written;
    int have_frame;
    int failed;
} app_t;

static int write_ppm(const char *path, const uint8_t *rgb24, uint16_t width, uint16_t height) {
    FILE *fp = fopen(path, "wb");
    if (!fp) return -1;
    if (fprintf(fp, "P6\n%u %u\n255\n", (unsigned)width, (unsigned)height) < 0) { fclose(fp); return -1; }
    size_t n = (size_t)width * height * 3;
    int ok = fwrite(rgb24, 1, n, fp) == n;
    if (fclose(fp) != 0) ok = 0;
    return ok ? 0 : -1;
}

static const char *sync_str(uint8_t positive) { return positive ? "pos" : "neg"; }

static void on_frame(void *user, const vgaframe_output_t *out) {
    app_t *app = (app_t *)user;
    if (app->failed) return;
    if (out->partial && !app->write_partial) return;
    if (app->max_frames > 0 && (int)app->frames_written >= app->max_frames) return;

    char path[4096];
    snprintf(path, sizeof path, "%s-%04u.ppm", app->out_prefix, (unsigned)app->frames_written);
    if (write_ppm(path, out->rgb24, out->width, out->height) != 0) {
        fprintf(stderr, "vgacap-frames: failed to write %s\n", path);
        app->failed = 1;
        return;
    }

    const vgaframe_mode_t *m = out->timing->mode;
    fprintf(stdout, "frame %u: %ux%u mode=%s cpl=%u lpf=%u hsync=%s vsync=%s partial=%u\n",
           (unsigned)app->frames_written, (unsigned)out->width, (unsigned)out->height,
           m ? m->name : "?", (unsigned)out->timing->clocks_per_line,
           (unsigned)out->timing->lines_per_frame, sync_str(out->timing->hsync_positive),
           sync_str(out->timing->vsync_positive), (unsigned)out->partial);
    app->frames_written++;
    app->have_frame = 1;
}

static void on_event(void *user, const vgacap_event_t *ev) {
    app_t *app = (app_t *)user;
    if (app->failed) return;
    switch (ev->type) {
    case VGACAP_EV_HEADER: {
        vgaframe_config_t cfg;
        memset(&cfg, 0, sizeof cfg);
        cfg.max_clocks_per_line = MAX_CPL;
        cfg.max_lines = MAX_LINES;
        memcpy(cfg.signal_map, ev->u.header->signal_map, sizeof cfg.signal_map);
        cfg.on_frame = on_frame;
        cfg.user = app;
        if (vgaframe_init(&app->frame, &cfg, app->raw, app->rgb, app->cover) != 0) {
            fprintf(stderr, "vgacap-frames: failed to initialise frame reconstruction\n");
            app->failed = 1;
        }
        break;
    }
    case VGACAP_EV_RUN:
        vgaframe_push(&app->frame, ev->u.run.value, ev->u.run.run);
        break;
    case VGACAP_EV_FRAME_BEGIN:
        vgaframe_frame_begin(&app->frame, ev->u.frame.frame_counter, ev->u.frame.first_line,
                             ev->u.frame.line_count, ev->u.frame.clocks_per_line,
                             ev->u.frame.sample_count);
        break;
    case VGACAP_EV_TIME:
        break;
    case VGACAP_EV_ERROR:
        fprintf(stderr, "vgacap-frames: stream error: %s\n", ev->u.error.what ? ev->u.error.what : "?");
        app->failed = 1;
        break;
    }
}

int main(int argc, char **argv) {
    const char *in_path = NULL, *out_prefix = NULL;
    int max_frames = 0, want_partial = 0;

    for (int i = 1; i < argc; i++) {
        if (strcmp(argv[i], "--max-frames") == 0 && i + 1 < argc) {
            max_frames = atoi(argv[++i]);
        } else if (strcmp(argv[i], "--partial") == 0) {
            want_partial = 1;
        } else if (!in_path) {
            in_path = argv[i];
        } else if (!out_prefix) {
            out_prefix = argv[i];
        } else {
            fprintf(stderr, "vgacap-frames: unexpected argument '%s'\n", argv[i]);
            return 2;
        }
    }
    if (!in_path || !out_prefix) {
        fprintf(stderr, "usage: vgacap-frames <in.vgacap> <out-prefix> [--max-frames N] [--partial]\n");
        return 2;
    }

    FILE *fp = fopen(in_path, "rb");
    if (!fp) {
        fprintf(stderr, "vgacap-frames: cannot open %s\n", in_path);
        return 2;
    }

    static app_t app; /* too large for the stack */
    memset(&app, 0, sizeof app);
    app.out_prefix = out_prefix;
    app.max_frames = max_frames;
    app.write_partial = want_partial;

    vgacap_reader_t reader;
    vgacap_reader_init(&reader, on_event, &app);

    uint8_t buf[65536];
    size_t n;
    while (!app.failed && (n = fread(buf, 1, sizeof buf, fp)) > 0) {
        if (vgacap_reader_feed(&reader, buf, n) != 0) { app.failed = 1; break; }
        if (app.max_frames > 0 && (int)app.frames_written >= app.max_frames) break;
    }
    fclose(fp);

    if (!app.failed && (app.max_frames <= 0 || (int)app.frames_written < app.max_frames))
        vgaframe_flush(&app.frame);

    fprintf(stdout, "frames=%u\n", (unsigned)app.frames_written);

    if (app.failed) return 2;
    if (!app.have_frame) return 2;
    return 0;
}
