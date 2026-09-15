/* SPDX-License-Identifier: Apache-2.0 */
/**
 * SECTION:element-vgadecode
 *
 * Decodes a vgacap capture stream (`application/x-vgacap`) into RGB video
 * frames by driving `vgacap_reader` and `libvgaframe`.
 *
 * ## Example
 * |[
 * gst-launch-1.0 filesrc location=capture.vgacap ! vgadecode ! \
 *     videoconvert ! autovideosink
 * ]|
 *
 * ## Per-frame metadata
 *
 * A partial frame (not every line covered) is flagged
 * %GST_BUFFER_FLAG_CORRUPTED and the frame counter is carried in
 * %GST_BUFFER_OFFSET (with `offset_end = offset + 1`). This is deliberately
 * the cheap option: a custom #GstMeta would need a registered GType, an API
 * header for downstream users and a per-buffer meta allocation in the hot
 * path, and a bus message per frame would be far too noisy, whereas a flag
 * and an offset are two stores into a buffer the element already owns.
 *
 * ## Timing
 *
 * The detected timing is posted on the bus as a #GstMessage of type
 * %GST_MESSAGE_ELEMENT whose structure is named `vgacap-timing`, once it is
 * first known and again whenever it changes (which the calibration `modes`
 * design can do mid-stream). The glitch count is reported as the running
 * total at the time of the message; it is not itself a reason to post one,
 * or a stream with the odd sync glitch would post continuously.
 */
#include "gstvgadecode.h"

#include <string.h>

GST_DEBUG_CATEGORY_STATIC(vgadecode_debug);
#define GST_CAT_DEFAULT vgadecode_debug

/* Repeats inserted for one frame are capped: a stream whose project clock
 * implies a multi-minute gap (a truncated or corrupt capture can) would
 * otherwise push frames until the disk filled. Past the cap the cadence
 * simply jumps to the new frame's slot. */
#define VGADECODE_MAX_REPEATS 240

enum {
    PROP_0,
    PROP_REPEAT_LAST_FRAME,
    PROP_OUTPUT_FPS,
    PROP_MAX_WIDTH,
    PROP_MAX_HEIGHT,
    PROP_FORCE_MODE,
    PROP_PARTIAL
};

#define DEFAULT_REPEAT_LAST_FRAME FALSE
#define DEFAULT_FPS_N 30
#define DEFAULT_FPS_D 1
#define DEFAULT_MAX_WIDTH 1400u
#define DEFAULT_MAX_HEIGHT 900u
#define DEFAULT_PARTIAL FALSE

struct _GstVgaDecode {
    GstElement element;
    GstPad *sinkpad, *srcpad;

    /* properties (guarded by the object lock) */
    gboolean repeat_last_frame;
    gint fps_n, fps_d;
    guint max_width, max_height;
    gchar *force_mode_name;
    gboolean push_partial;

    /* values latched at start(), so a property change mid-stream cannot
     * resize a buffer that is already bound to the vgaframe */
    guint alloc_width, alloc_height;
    gint out_fps_n, out_fps_d;
    const vgaframe_mode_t *force_mode;
    /* latched once per input buffer, so the streaming thread never reads a
     * property while another thread is setting it */
    gboolean repeat_active, partial_active;

    /* reconstruction state; only touched from the streaming thread */
    guint8 *raw, *rgb, *cover;
    vgacap_reader_t reader;
    vgaframe_t frame;
    gboolean frame_ready;
    guint32 clock_hz;
    guint64 clock_pos;   /* clocks consumed by the stream so far */
    guint64 frame_clk;   /* clock_pos as the frame being emitted was closed */
    gboolean have_first_clk;
    guint64 first_clk;
    guint64 frames_out;
    guint64 next_slot;   /* next free output-fps slot (repeat-last-frame) */

    /* negotiation and output */
    GstCaps *out_caps;
    GstVideoInfo vinfo;
    GstBufferPool *pool;
    GstBuffer *last_buffer;
    gboolean sent_segment;
    GstFlowReturn flow;

    /* last reported timing, for change detection */
    gboolean have_timing;
    guint32 t_cpl, t_lpf;
    guint8 t_hpos, t_vpos;
    const gchar *t_mode;
};

G_DEFINE_TYPE(GstVgaDecode, gst_vgadecode, GST_TYPE_ELEMENT)

static GstStaticPadTemplate sink_template = GST_STATIC_PAD_TEMPLATE(
    "sink", GST_PAD_SINK, GST_PAD_ALWAYS,
    GST_STATIC_CAPS("application/x-vgacap"));

static GstStaticPadTemplate src_template = GST_STATIC_PAD_TEMPLATE(
    "src", GST_PAD_SRC, GST_PAD_ALWAYS,
    GST_STATIC_CAPS("video/x-raw, "
                    "format = (string) RGB, "
                    "width = (int) [ 1, 4096 ], "
                    "height = (int) [ 1, 4096 ], "
                    "framerate = (fraction) [ 0/1, 120/1 ]"));

/* ---------------------------------------------------------------- helpers */

static const vgaframe_mode_t *lookup_mode(const gchar *name)
{
    size_t count = 0, i;
    const vgaframe_mode_t *modes = vgaframe_modes(&count);
    if (!name || !*name)
        return NULL;
    for (i = 0; i < count; i++)
        if (g_strcmp0(modes[i].name, name) == 0)
            return &modes[i];
    return NULL;
}

static void post_timing(GstVgaDecode *self, const vgaframe_timing_t *t)
{
    const gchar *mode = t->mode ? t->mode->name : "";
    GstStructure *s;

    if (self->have_timing && self->t_cpl == t->clocks_per_line &&
        self->t_lpf == t->lines_per_frame && self->t_hpos == t->hsync_positive &&
        self->t_vpos == t->vsync_positive && g_strcmp0(self->t_mode, mode) == 0)
        return;

    self->have_timing = TRUE;
    self->t_cpl = t->clocks_per_line;
    self->t_lpf = t->lines_per_frame;
    self->t_hpos = t->hsync_positive;
    self->t_vpos = t->vsync_positive;
    self->t_mode = t->mode ? t->mode->name : "";

    s = gst_structure_new("vgacap-timing",
                          "mode", G_TYPE_STRING, mode,
                          "clocks-per-line", G_TYPE_UINT, (guint)t->clocks_per_line,
                          "lines-per-frame", G_TYPE_UINT, (guint)t->lines_per_frame,
                          "hsync-positive", G_TYPE_BOOLEAN, t->hsync_positive ? TRUE : FALSE,
                          "vsync-positive", G_TYPE_BOOLEAN, t->vsync_positive ? TRUE : FALSE,
                          "glitches", G_TYPE_UINT, (guint)t->glitches,
                          NULL);
    GST_INFO_OBJECT(self, "timing %" GST_PTR_FORMAT, s);
    gst_element_post_message(GST_ELEMENT_CAST(self),
                             gst_message_new_element(GST_OBJECT_CAST(self), s));
}

/* Nominal frame rate for the caps: the project clock divided by the clocks
 * per frame when both are known, otherwise the output-fps property. With
 * repeat-last-frame the element itself sets the cadence, so output-fps wins. */
static void output_framerate(GstVgaDecode *self, const vgaframe_timing_t *t,
                             gint *fps_n, gint *fps_d)
{
    guint64 cpf = (guint64)t->clocks_per_line * (guint64)t->lines_per_frame;

    *fps_n = self->out_fps_n;
    *fps_d = self->out_fps_d;
    if (self->repeat_active || self->clock_hz == 0 || cpf == 0 ||
        cpf > (guint64)G_MAXINT || self->clock_hz > (guint32)G_MAXINT)
        return;
    if (!gst_util_fraction_multiply((gint)self->clock_hz, 1, 1, (gint)cpf, fps_n, fps_d)) {
        *fps_n = self->out_fps_n;
        *fps_d = self->out_fps_d;
    }
}

/* The output segment is in time, whatever upstream's is (filesrc pushes a
 * BYTES segment), and it can only go out after the caps: sticky events are
 * ordered, and the caps are not known until the first frame is complete. */
static void push_segment(GstVgaDecode *self)
{
    GstSegment seg;

    if (self->sent_segment)
        return;
    self->sent_segment = TRUE;
    gst_segment_init(&seg, GST_FORMAT_TIME);
    gst_pad_push_event(self->srcpad, gst_event_new_segment(&seg));
}

static gboolean ensure_caps(GstVgaDecode *self, const vgaframe_output_t *out)
{
    GstCaps *caps;
    GstStructure *config;
    gint fps_n, fps_d;

    output_framerate(self, out->timing, &fps_n, &fps_d);
    caps = gst_caps_new_simple("video/x-raw",
                               "format", G_TYPE_STRING, "RGB",
                               "width", G_TYPE_INT, (gint)out->width,
                               "height", G_TYPE_INT, (gint)out->height,
                               "framerate", GST_TYPE_FRACTION, fps_n, fps_d,
                               "pixel-aspect-ratio", GST_TYPE_FRACTION, 1, 1,
                               NULL);

    if (self->out_caps && gst_caps_is_equal(self->out_caps, caps)) {
        gst_caps_unref(caps);
        return TRUE;
    }

    GST_DEBUG_OBJECT(self, "negotiating %" GST_PTR_FORMAT, caps);
    if (!gst_video_info_from_caps(&self->vinfo, caps)) {
        GST_ERROR_OBJECT(self, "cannot parse own caps %" GST_PTR_FORMAT, caps);
        gst_caps_unref(caps);
        self->flow = GST_FLOW_NOT_NEGOTIATED;
        return FALSE;
    }
    if (!gst_pad_push_event(self->srcpad, gst_event_new_caps(caps))) {
        GST_WARNING_OBJECT(self, "downstream refused %" GST_PTR_FORMAT, caps);
        gst_caps_unref(caps);
        self->flow = GST_FLOW_NOT_NEGOTIATED;
        return FALSE;
    }
    gst_caps_replace(&self->out_caps, caps);
    push_segment(self);

    /* A pool per caps configuration, not per frame: acquire/release recycles
     * the frame memory, so the steady state allocates nothing. */
    gst_buffer_replace(&self->last_buffer, NULL);
    if (self->pool) {
        gst_buffer_pool_set_active(self->pool, FALSE);
        gst_object_unref(self->pool);
    }
    self->pool = gst_buffer_pool_new();
    config = gst_buffer_pool_get_config(self->pool);
    gst_buffer_pool_config_set_params(config, caps, GST_VIDEO_INFO_SIZE(&self->vinfo), 3, 0);
    gst_caps_unref(caps);
    if (!gst_buffer_pool_set_config(self->pool, config) ||
        !gst_buffer_pool_set_active(self->pool, TRUE)) {
        GST_ERROR_OBJECT(self, "cannot configure the output buffer pool");
        self->flow = GST_FLOW_ERROR;
        return FALSE;
    }
    return TRUE;
}

static GstClockTime slot_time(const GstVgaDecode *self, guint64 slot)
{
    return gst_util_uint64_scale(slot, GST_SECOND * (guint64)self->out_fps_d,
                                 (guint64)self->out_fps_n);
}

/* PTS/duration in project time: clocks since the first emitted frame divided
 * by the stream's nominal clock. With no clock_hz there is nothing to derive
 * from, so frames are laid out on the output-fps grid instead. */
static void frame_times(GstVgaDecode *self, const vgaframe_output_t *out,
                        GstClockTime *pts, GstClockTime *dur)
{
    guint64 cpf = (guint64)out->timing->clocks_per_line * (guint64)out->timing->lines_per_frame;

    if (self->clock_hz != 0) {
        if (!self->have_first_clk) {
            self->have_first_clk = TRUE;
            self->first_clk = self->frame_clk;
        }
        *pts = gst_util_uint64_scale(self->frame_clk - self->first_clk, GST_SECOND,
                                     self->clock_hz);
        *dur = cpf ? gst_util_uint64_scale(cpf, GST_SECOND, self->clock_hz)
                   : slot_time(self, 1);
    } else {
        *pts = slot_time(self, self->frames_out);
        *dur = slot_time(self, 1);
    }
}

static void push_repeats_upto(GstVgaDecode *self, guint64 slot)
{
    if (!self->last_buffer)
        return;
    if (slot > self->next_slot + VGADECODE_MAX_REPEATS) {
        GST_WARNING_OBJECT(self, "gap of %" G_GUINT64_FORMAT " frames is beyond the "
                           "repeat cap; jumping the cadence forward",
                           slot - self->next_slot);
        self->next_slot = slot;
    }
    while (self->next_slot < slot && self->flow == GST_FLOW_OK) {
        /* gst_buffer_copy() shares the pixel memory by reference and only
         * allocates the (pooled) buffer header. */
        GstBuffer *rep = gst_buffer_copy(self->last_buffer);
        GST_BUFFER_PTS(rep) = slot_time(self, self->next_slot);
        GST_BUFFER_DTS(rep) = GST_CLOCK_TIME_NONE;
        GST_BUFFER_DURATION(rep) = slot_time(self, self->next_slot + 1) -
                                   slot_time(self, self->next_slot);
        self->next_slot++;
        self->flow = gst_pad_push(self->srcpad, rep);
    }
}

static void on_frame(void *user, const vgaframe_output_t *out)
{
    GstVgaDecode *self = (GstVgaDecode *)user;
    GstBuffer *buf = NULL;
    GstMapInfo map;
    GstClockTime pts, dur;
    gint dstride;
    gsize row;
    guint16 y;

    post_timing(self, out->timing);

    if (self->flow != GST_FLOW_OK)
        return;
    if (out->partial && !self->partial_active)
        return;
    if (out->width == 0 || out->height == 0)
        return;
    if (!ensure_caps(self, out))
        return;

    self->flow = gst_buffer_pool_acquire_buffer(self->pool, &buf, NULL);
    if (self->flow != GST_FLOW_OK)
        return;
    if (!gst_buffer_map(buf, &map, GST_MAP_WRITE)) {
        gst_buffer_unref(buf);
        self->flow = GST_FLOW_ERROR;
        return;
    }
    dstride = GST_VIDEO_INFO_PLANE_STRIDE(&self->vinfo, 0);
    row = (gsize)out->width * 3;
    for (y = 0; y < out->height; y++)
        memcpy(map.data + (gsize)y * (gsize)dstride, out->rgb24 + (gsize)y * out->stride, row);
    gst_buffer_unmap(buf, &map);

    GST_BUFFER_OFFSET(buf) = out->frame_counter;
    GST_BUFFER_OFFSET_END(buf) = (guint64)out->frame_counter + 1;
    if (out->partial)
        GST_BUFFER_FLAG_SET(buf, GST_BUFFER_FLAG_CORRUPTED);
    else
        GST_BUFFER_FLAG_UNSET(buf, GST_BUFFER_FLAG_CORRUPTED);

    frame_times(self, out, &pts, &dur);
    self->frames_out++;

    if (self->repeat_active) {
        guint64 slot = gst_util_uint64_scale(pts, (guint64)self->out_fps_n,
                                             GST_SECOND * (guint64)self->out_fps_d);
        if (slot < self->next_slot)
            slot = self->next_slot;
        push_repeats_upto(self, slot);
        if (self->flow != GST_FLOW_OK) {
            gst_buffer_unref(buf);
            return;
        }
        pts = slot_time(self, slot);
        dur = slot_time(self, slot + 1) - pts;
        self->next_slot = slot + 1;
    }
    GST_BUFFER_PTS(buf) = pts;
    GST_BUFFER_DTS(buf) = GST_CLOCK_TIME_NONE;
    GST_BUFFER_DURATION(buf) = dur;
    if (self->repeat_active)
        gst_buffer_replace(&self->last_buffer, buf);

    GST_LOG_OBJECT(self, "frame %u %ux%u partial=%u pts=%" GST_TIME_FORMAT,
                   (guint)out->frame_counter, (guint)out->width, (guint)out->height,
                   (guint)out->partial, GST_TIME_ARGS(pts));
    self->flow = gst_pad_push(self->srcpad, buf);
}

static void on_event(void *user, const vgacap_event_t *ev)
{
    GstVgaDecode *self = (GstVgaDecode *)user;

    switch (ev->type) {
    case VGACAP_EV_HEADER: {
        vgaframe_config_t cfg;
        memset(&cfg, 0, sizeof cfg);
        cfg.max_clocks_per_line = (guint16)self->alloc_width;
        cfg.max_lines = (guint16)self->alloc_height;
        memcpy(cfg.signal_map, ev->u.header->signal_map, sizeof cfg.signal_map);
        cfg.force_mode = self->force_mode;
        cfg.on_frame = on_frame;
        cfg.user = self;
        if (vgaframe_init(&self->frame, &cfg, self->raw, self->rgb, self->cover) != 0) {
            GST_ELEMENT_ERROR(self, STREAM, DECODE, ("cannot initialise frame reconstruction"),
                              (NULL));
            self->flow = GST_FLOW_ERROR;
            return;
        }
        self->frame_ready = TRUE;
        self->clock_hz = ev->u.header->clock_hz;
        GST_INFO_OBJECT(self, "stream header: %u sample bits, clock %u Hz, desc '%s'",
                        (guint)ev->u.header->sample_bits, (guint)ev->u.header->clock_hz,
                        ev->u.header->desc);
        break;
    }
    case VGACAP_EV_RUN:
        if (!self->frame_ready)
            break;
        self->frame_clk = self->clock_pos;
        self->clock_pos += ev->u.run.run;
        vgaframe_push(&self->frame, ev->u.run.value, ev->u.run.run);
        break;
    case VGACAP_EV_FRAME_BEGIN:
        if (!self->frame_ready)
            break;
        vgaframe_frame_begin(&self->frame, ev->u.frame.frame_counter, ev->u.frame.first_line,
                             ev->u.frame.line_count, ev->u.frame.clocks_per_line,
                             ev->u.frame.sample_count);
        break;
    case VGACAP_EV_TIME:
        if (ev->u.time.clock_hz)
            self->clock_hz = ev->u.time.clock_hz;
        break;
    case VGACAP_EV_RESYNC:
        /* Not fatal: the frame layer re-derives timing from the sync bits, so
         * reconstruction resumes on its own a frame or two later. */
        GST_WARNING_OBJECT(self, "stream resync: skipped %u bytes (%s)",
                           (guint)ev->u.resync.skipped,
                           ev->u.resync.what ? ev->u.resync.what : "?");
        break;
    case VGACAP_EV_ERROR:
        /* The reader stops accepting input; the element keeps running so the
         * pipeline reaches EOS rather than hanging on a truncated capture. */
        GST_ELEMENT_WARNING(self, STREAM, DECODE,
                            ("vgacap stream error: %s",
                             ev->u.error.what ? ev->u.error.what : "?"),
                            (NULL));
        break;
    }
}

/* ------------------------------------------------------------ state reset */

static void reset_stream_state(GstVgaDecode *self)
{
    vgacap_reader_init(&self->reader, on_event, self);
    self->frame_ready = FALSE;
    self->clock_hz = 0;
    self->clock_pos = 0;
    self->frame_clk = 0;
    self->have_first_clk = FALSE;
    self->first_clk = 0;
    self->frames_out = 0;
    self->next_slot = 0;
    self->have_timing = FALSE;
    self->t_mode = NULL;
    self->sent_segment = FALSE;
    self->flow = GST_FLOW_OK;
    gst_buffer_replace(&self->last_buffer, NULL);
    gst_caps_replace(&self->out_caps, NULL);
    if (self->pool) {
        gst_buffer_pool_set_active(self->pool, FALSE);
        gst_object_unref(self->pool);
        self->pool = NULL;
    }
}

static gboolean gst_vgadecode_start(GstVgaDecode *self)
{
    vgaframe_config_t cfg;
    gchar *name;

    GST_OBJECT_LOCK(self);
    self->alloc_width = self->max_width;
    self->alloc_height = self->max_height;
    self->out_fps_n = self->fps_n;
    self->out_fps_d = self->fps_d;
    name = g_strdup(self->force_mode_name);
    GST_OBJECT_UNLOCK(self);

    if (name && *name) {
        self->force_mode = lookup_mode(name);
        if (!self->force_mode) {
            GST_ELEMENT_ERROR(self, LIBRARY, SETTINGS,
                              ("unknown force-mode '%s'", name), (NULL));
            g_free(name);
            return FALSE;
        }
    } else {
        self->force_mode = NULL;
    }
    g_free(name);

    /* Every buffer the hot path needs is allocated here, once. */
    memset(&cfg, 0, sizeof cfg);
    cfg.max_clocks_per_line = (guint16)self->alloc_width;
    cfg.max_lines = (guint16)self->alloc_height;
    self->raw = g_try_malloc(vgaframe_raw_size(&cfg));
    self->rgb = g_try_malloc(vgaframe_rgb_size(&cfg));
    self->cover = g_try_malloc(cfg.max_lines);
    if (!self->raw || !self->rgb || !self->cover) {
        GST_ELEMENT_ERROR(self, RESOURCE, NO_SPACE_LEFT,
                          ("cannot allocate %ux%u reconstruction buffers",
                           self->alloc_width, self->alloc_height), (NULL));
        return FALSE;
    }
    reset_stream_state(self);
    return TRUE;
}

static void gst_vgadecode_stop(GstVgaDecode *self)
{
    reset_stream_state(self);
    g_clear_pointer(&self->raw, g_free);
    g_clear_pointer(&self->rgb, g_free);
    g_clear_pointer(&self->cover, g_free);
}

/* --------------------------------------------------------------- pad work */

static void latch_properties(GstVgaDecode *self)
{
    GST_OBJECT_LOCK(self);
    self->repeat_active = self->repeat_last_frame;
    self->partial_active = self->push_partial;
    GST_OBJECT_UNLOCK(self);
}

static GstFlowReturn gst_vgadecode_chain(GstPad *pad, GstObject *parent, GstBuffer *buf)
{
    GstVgaDecode *self = GST_VGADECODE(parent);
    GstMapInfo map;

    (void)pad;
    if (self->flow != GST_FLOW_OK) {
        gst_buffer_unref(buf);
        return self->flow;
    }
    if (!gst_buffer_map(buf, &map, GST_MAP_READ)) {
        gst_buffer_unref(buf);
        return GST_FLOW_ERROR;
    }
    latch_properties(self);
    (void)vgacap_reader_feed(&self->reader, map.data, map.size);
    gst_buffer_unmap(buf, &map);
    gst_buffer_unref(buf);
    return self->flow;
}

static gboolean gst_vgadecode_sink_event(GstPad *pad, GstObject *parent, GstEvent *event)
{
    GstVgaDecode *self = GST_VGADECODE(parent);

    (void)pad;
    switch (GST_EVENT_TYPE(event)) {
    case GST_EVENT_CAPS:
        /* The sink caps carry no information: the stream describes itself. */
        gst_event_unref(event);
        return TRUE;
    case GST_EVENT_SEGMENT:
        /* Swallowed: push_segment() sends our own TIME segment once the caps
         * are known. */
        gst_event_unref(event);
        return TRUE;
    case GST_EVENT_EOS:
        latch_properties(self);
        if (self->frame_ready && self->flow == GST_FLOW_OK)
            vgaframe_flush(&self->frame);
        /* A stream that produced no frame at all still needs a segment
         * before downstream will accept the EOS. */
        push_segment(self);
        return gst_pad_event_default(pad, parent, event);
    case GST_EVENT_FLUSH_STOP:
        reset_stream_state(self);
        return gst_pad_event_default(pad, parent, event);
    default:
        return gst_pad_event_default(pad, parent, event);
    }
}

static GstStateChangeReturn gst_vgadecode_change_state(GstElement *element,
                                                       GstStateChange transition)
{
    GstVgaDecode *self = GST_VGADECODE(element);
    GstStateChangeReturn ret;

    switch (transition) {
    case GST_STATE_CHANGE_READY_TO_PAUSED:
        if (!gst_vgadecode_start(self))
            return GST_STATE_CHANGE_FAILURE;
        break;
    default:
        break;
    }

    ret = GST_ELEMENT_CLASS(gst_vgadecode_parent_class)->change_state(element, transition);

    switch (transition) {
    case GST_STATE_CHANGE_PAUSED_TO_READY:
        gst_vgadecode_stop(self);
        break;
    default:
        break;
    }
    return ret;
}

/* ------------------------------------------------------------- properties */

static void gst_vgadecode_set_property(GObject *object, guint prop_id, const GValue *value,
                                       GParamSpec *pspec)
{
    GstVgaDecode *self = GST_VGADECODE(object);

    GST_OBJECT_LOCK(self);
    switch (prop_id) {
    case PROP_REPEAT_LAST_FRAME:
        self->repeat_last_frame = g_value_get_boolean(value);
        break;
    case PROP_OUTPUT_FPS:
        self->fps_n = gst_value_get_fraction_numerator(value);
        self->fps_d = gst_value_get_fraction_denominator(value);
        break;
    case PROP_MAX_WIDTH:
        self->max_width = g_value_get_uint(value);
        break;
    case PROP_MAX_HEIGHT:
        self->max_height = g_value_get_uint(value);
        break;
    case PROP_FORCE_MODE:
        g_free(self->force_mode_name);
        self->force_mode_name = g_value_dup_string(value);
        break;
    case PROP_PARTIAL:
        self->push_partial = g_value_get_boolean(value);
        break;
    default:
        G_OBJECT_WARN_INVALID_PROPERTY_ID(object, prop_id, pspec);
        break;
    }
    GST_OBJECT_UNLOCK(self);
}

static void gst_vgadecode_get_property(GObject *object, guint prop_id, GValue *value,
                                       GParamSpec *pspec)
{
    GstVgaDecode *self = GST_VGADECODE(object);

    GST_OBJECT_LOCK(self);
    switch (prop_id) {
    case PROP_REPEAT_LAST_FRAME:
        g_value_set_boolean(value, self->repeat_last_frame);
        break;
    case PROP_OUTPUT_FPS:
        gst_value_set_fraction(value, self->fps_n, self->fps_d);
        break;
    case PROP_MAX_WIDTH:
        g_value_set_uint(value, self->max_width);
        break;
    case PROP_MAX_HEIGHT:
        g_value_set_uint(value, self->max_height);
        break;
    case PROP_FORCE_MODE:
        g_value_set_string(value, self->force_mode_name);
        break;
    case PROP_PARTIAL:
        g_value_set_boolean(value, self->push_partial);
        break;
    default:
        G_OBJECT_WARN_INVALID_PROPERTY_ID(object, prop_id, pspec);
        break;
    }
    GST_OBJECT_UNLOCK(self);
}

static void gst_vgadecode_finalize(GObject *object)
{
    GstVgaDecode *self = GST_VGADECODE(object);

    gst_vgadecode_stop(self);
    g_clear_pointer(&self->force_mode_name, g_free);
    G_OBJECT_CLASS(gst_vgadecode_parent_class)->finalize(object);
}

static gchar *force_mode_blurb(void)
{
    size_t count = 0, i;
    const vgaframe_mode_t *modes = vgaframe_modes(&count);
    GString *s = g_string_new("Force this built-in mode instead of detecting one; "
                              "empty to detect. One of: ");
    for (i = 0; i < count; i++)
        g_string_append_printf(s, "%s%s", i ? ", " : "", modes[i].name);
    return g_string_free(s, FALSE);
}

static void gst_vgadecode_class_init(GstVgaDecodeClass *klass)
{
    GObjectClass *gobject_class = G_OBJECT_CLASS(klass);
    GstElementClass *element_class = GST_ELEMENT_CLASS(klass);
    gchar *blurb = force_mode_blurb();

    gobject_class->set_property = gst_vgadecode_set_property;
    gobject_class->get_property = gst_vgadecode_get_property;
    gobject_class->finalize = gst_vgadecode_finalize;

    g_object_class_install_property(gobject_class, PROP_REPEAT_LAST_FRAME,
        g_param_spec_boolean("repeat-last-frame", "Repeat last frame",
                             "Re-push the last frame to keep a steady output-fps cadence",
                             DEFAULT_REPEAT_LAST_FRAME,
                             G_PARAM_READWRITE | G_PARAM_STATIC_STRINGS));
    g_object_class_install_property(gobject_class, PROP_OUTPUT_FPS,
        gst_param_spec_fraction("output-fps", "Output frame rate",
                                "Frame rate used when repeating frames, and when the "
                                "stream declares no project clock",
                                1, 1000, 1000, 1, DEFAULT_FPS_N, DEFAULT_FPS_D,
                                G_PARAM_READWRITE | G_PARAM_STATIC_STRINGS));
    g_object_class_install_property(gobject_class, PROP_MAX_WIDTH,
        g_param_spec_uint("max-width", "Maximum width",
                          "Widest line, in project clocks, the reconstruction buffers "
                          "are sized for (allocated once when the element starts)",
                          1, 4096, DEFAULT_MAX_WIDTH,
                          G_PARAM_READWRITE | G_PARAM_STATIC_STRINGS));
    g_object_class_install_property(gobject_class, PROP_MAX_HEIGHT,
        g_param_spec_uint("max-height", "Maximum height",
                          "Tallest frame, in lines, the reconstruction buffers are "
                          "sized for (allocated once when the element starts)",
                          1, 4096, DEFAULT_MAX_HEIGHT,
                          G_PARAM_READWRITE | G_PARAM_STATIC_STRINGS));
    /* No G_PARAM_STATIC_STRINGS here: the blurb lists the mode table and is
     * built at runtime, so GObject has to take its own copy. */
    g_object_class_install_property(gobject_class, PROP_FORCE_MODE,
        g_param_spec_string("force-mode", "Force mode", blurb, "", G_PARAM_READWRITE));
    g_object_class_install_property(gobject_class, PROP_PARTIAL,
        g_param_spec_boolean("partial", "Push partial frames",
                             "Also push frames whose lines were not all covered; such "
                             "buffers carry GST_BUFFER_FLAG_CORRUPTED",
                             DEFAULT_PARTIAL,
                             G_PARAM_READWRITE | G_PARAM_STATIC_STRINGS));
    g_free(blurb);

    element_class->change_state = GST_DEBUG_FUNCPTR(gst_vgadecode_change_state);

    gst_element_class_add_static_pad_template(element_class, &sink_template);
    gst_element_class_add_static_pad_template(element_class, &src_template);
    gst_element_class_set_static_metadata(element_class,
        "vgacap stream decoder", "Codec/Decoder/Video",
        "Reconstructs VGA frames from a Tiny Tapeout vgacap capture stream",
        "vgacap contributors <https://github.com/mithro/vgacap>");

    GST_DEBUG_CATEGORY_INIT(vgadecode_debug, "vgadecode", 0, "vgacap stream decoder");
}

static void gst_vgadecode_init(GstVgaDecode *self)
{
    self->sinkpad = gst_pad_new_from_static_template(&sink_template, "sink");
    gst_pad_set_chain_function(self->sinkpad, GST_DEBUG_FUNCPTR(gst_vgadecode_chain));
    gst_pad_set_event_function(self->sinkpad, GST_DEBUG_FUNCPTR(gst_vgadecode_sink_event));
    gst_element_add_pad(GST_ELEMENT_CAST(self), self->sinkpad);

    self->srcpad = gst_pad_new_from_static_template(&src_template, "src");
    gst_pad_use_fixed_caps(self->srcpad);
    gst_element_add_pad(GST_ELEMENT_CAST(self), self->srcpad);

    self->repeat_last_frame = DEFAULT_REPEAT_LAST_FRAME;
    self->fps_n = DEFAULT_FPS_N;
    self->fps_d = DEFAULT_FPS_D;
    self->out_fps_n = DEFAULT_FPS_N;
    self->out_fps_d = DEFAULT_FPS_D;
    self->max_width = DEFAULT_MAX_WIDTH;
    self->max_height = DEFAULT_MAX_HEIGHT;
    self->force_mode_name = g_strdup("");
    self->push_partial = DEFAULT_PARTIAL;
    self->flow = GST_FLOW_OK;
    gst_video_info_init(&self->vinfo);
}
