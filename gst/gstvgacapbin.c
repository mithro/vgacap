/* SPDX-License-Identifier: Apache-2.0 */
/**
 * SECTION:element-vgacapbin
 *
 * A URI in, RGB video out: the source the URI names, plus `vgadecode`.
 *
 * ## Example
 * |[
 * gst-launch-1.0 vgacapbin \
 *     uri="tt-ws://welland:8765/serial?project=tt_um_rejunity_vga&clock-hz=60000" ! \
 *     videoconvert ! autovideosink
 * ]|
 */
#include "gstvgacapbin.h"

#include "gstvgacapuri.h"

GST_DEBUG_CATEGORY_STATIC(vgacapbin_debug);
#define GST_CAT_DEFAULT vgacapbin_debug

enum {
    PROP_0,
    PROP_URI,
    /* Mirrors of vgacapttsrc's properties. They are declared again rather
     * than shared because GObject property specs belong to one class; the
     * defaults are kept identical, and
     * test_bin_forwards_the_source_properties_unchanged compares the two
     * elements' gst-inspect output so a change to one that misses the other
     * fails the build rather than the demo. */
    PROP_LINK,
    PROP_PROJECT,
    PROP_DESIGN,
    PROP_CLOCK_HZ,
    PROP_PROFILE,
    PROP_PIO,
    PROP_BUF_WORDS,
    PROP_SECONDS,
    PROP_TTCAP_COMMAND,
    PROP_STOP_TIMEOUT
};

struct _GstVgaCapBin {
    GstBin bin;

    gchar *uri;           /* object lock */
    GstStructure *wanted; /* forwarded properties actually set; object lock */

    GstElement *decode;   /* made in init(), lives as long as the bin */
    GstElement *source;   /* made on the way out of NULL, dropped on the way back */
};

G_DEFINE_TYPE(GstVgaCapBin, gst_vgacapbin, GST_TYPE_BIN)

static GstStaticPadTemplate src_template = GST_STATIC_PAD_TEMPLATE(
    "src", GST_PAD_SRC, GST_PAD_ALWAYS,
    GST_STATIC_CAPS("video/x-raw, "
                    "format = (string) RGB, "
                    "width = (int) [ 1, 4096 ], "
                    "height = (int) [ 1, 4096 ], "
                    "framerate = (fraction) [ 0/1, 120/1 ]"));

/* prop_id -> property name, for the mirrored half of the property table. */
static const gchar *forwarded_name(guint prop_id)
{
    switch (prop_id) {
    case PROP_LINK: return "link";
    case PROP_PROJECT: return "project";
    case PROP_DESIGN: return "design";
    case PROP_CLOCK_HZ: return "clock-hz";
    case PROP_PROFILE: return "profile";
    case PROP_PIO: return "pio";
    case PROP_BUF_WORDS: return "buf-words";
    case PROP_SECONDS: return "seconds";
    case PROP_TTCAP_COMMAND: return "ttcap-command";
    case PROP_STOP_TIMEOUT: return "stop-timeout";
    default: return NULL;
    }
}

/* ------------------------------------------------------------ the children */

static gboolean set_from_string(GstVgaCapBin *self, GstElement *target, const gchar *name,
                                const gchar *text, const gchar *whence)
{
    GParamSpec *pspec = g_object_class_find_property(G_OBJECT_GET_CLASS(target), name);
    GValue value = G_VALUE_INIT;

    if (!pspec) {
        GST_ELEMENT_ERROR(self, LIBRARY, SETTINGS,
                          ("%s sets '%s', which %s has no such property for", whence, name,
                           GST_OBJECT_NAME(target)),
                          (NULL));
        return FALSE;
    }
    g_value_init(&value, pspec->value_type);
    /* gst_value_deserialize rather than gst_util_set_object_arg: the same
     * conversions, but it says when it could not read the text, which is the
     * difference between "clock-hz=sixty" being an error and being ignored. */
    if (!gst_value_deserialize(&value, text)) {
        GST_ELEMENT_ERROR(self, LIBRARY, SETTINGS,
                          ("%s sets %s='%s', which is not a %s", whence, name, text,
                           g_type_name(pspec->value_type)),
                          (NULL));
        g_value_unset(&value);
        return FALSE;
    }
    g_object_set_property(G_OBJECT(target), name, &value);
    g_value_unset(&value);
    return TRUE;
}

/* The properties set on the bin itself, onto the source. A source that has no
 * such property (a filesrc asked for `seconds`) is not an error: the mirrored
 * set is the union of what any source takes, and only the URI's own query
 * string is a statement about this particular source. */
static void apply_wanted(GstVgaCapBin *self, GstElement *target)
{
    gint i, n;

    GST_OBJECT_LOCK(self);
    n = gst_structure_n_fields(self->wanted);
    for (i = 0; i < n; i++) {
        const gchar *name = gst_structure_nth_field_name(self->wanted, i);
        const GValue *value = gst_structure_get_value(self->wanted, name);
        if (!g_object_class_find_property(G_OBJECT_GET_CLASS(target), name)) {
            GST_INFO_OBJECT(self, "%s takes no '%s'; leaving it unset",
                            GST_OBJECT_NAME(target), name);
            continue;
        }
        g_object_set_property(G_OBJECT(target), name, value);
    }
    GST_OBJECT_UNLOCK(self);
}

static gboolean build_source(GstVgaCapBin *self)
{
    VgaCapUri parsed;
    GError *error = NULL;
    GstElement *source;
    GHashTableIter iter;
    gpointer key, value;
    gchar *uri;

    if (self->source)
        return TRUE;
    if (!self->decode) {
        GST_ELEMENT_ERROR(self, CORE, MISSING_PLUGIN, ("no vgadecode element"), (NULL));
        return FALSE;
    }

    GST_OBJECT_LOCK(self);
    uri = g_strdup(self->uri);
    GST_OBJECT_UNLOCK(self);

    if (!vgacap_uri_parse(uri, &parsed, &error)) {
        GST_ELEMENT_ERROR(self, RESOURCE, NOT_FOUND, ("%s", error->message),
                          ("uri: %s", uri ? uri : "(unset)"));
        g_clear_error(&error);
        g_free(uri);
        return FALSE;
    }
    g_free(uri);

    source = gst_element_factory_make(parsed.element, "source");
    if (!source) {
        GST_ELEMENT_ERROR(self, CORE, MISSING_PLUGIN,
                          ("no %s element; is the plugin installed?", parsed.element),
                          (NULL));
        vgacap_uri_clear(&parsed);
        return FALSE;
    }
    g_object_set(source, parsed.property, parsed.value, NULL);
    GST_INFO_OBJECT(self, "source %s with %s=%s", parsed.element, parsed.property,
                    parsed.value);

    /* The bin's own properties first, so the query string - the more specific
     * statement, written next to the link it belongs with - wins. */
    apply_wanted(self, source);
    g_hash_table_iter_init(&iter, parsed.params);
    while (g_hash_table_iter_next(&iter, &key, &value)) {
        if (!set_from_string(self, source, key, value, "the uri's query string")) {
            gst_object_unref(source);
            vgacap_uri_clear(&parsed);
            return FALSE;
        }
    }
    vgacap_uri_clear(&parsed);

    gst_bin_add(GST_BIN_CAST(self), source);
    if (!gst_element_link(source, self->decode)) {
        GST_ELEMENT_ERROR(self, CORE, NEGOTIATION,
                          ("cannot link the source to vgadecode"), (NULL));
        gst_bin_remove(GST_BIN_CAST(self), source);
        return FALSE;
    }
    self->source = source;
    return TRUE;
}

static void drop_source(GstVgaCapBin *self)
{
    if (!self->source)
        return;
    gst_element_set_state(self->source, GST_STATE_NULL);
    gst_element_unlink(self->source, self->decode);
    gst_bin_remove(GST_BIN_CAST(self), self->source);
    self->source = NULL; /* the bin held the only reference */
}

static GstStateChangeReturn gst_vgacapbin_change_state(GstElement *element,
                                                       GstStateChange transition)
{
    GstVgaCapBin *self = GST_VGACAPBIN(element);
    GstStateChangeReturn ret;

    switch (transition) {
    case GST_STATE_CHANGE_NULL_TO_READY:
        /* Before chaining up: GstBin walks its children on the way up, and a
         * source added afterwards would be left in NULL. */
        if (!build_source(self))
            return GST_STATE_CHANGE_FAILURE;
        break;
    default:
        break;
    }

    ret = GST_ELEMENT_CLASS(gst_vgacapbin_parent_class)->change_state(element, transition);
    if (ret == GST_STATE_CHANGE_FAILURE && transition == GST_STATE_CHANGE_NULL_TO_READY)
        drop_source(self);

    switch (transition) {
    case GST_STATE_CHANGE_READY_TO_NULL:
        drop_source(self);
        break;
    default:
        break;
    }
    return ret;
}

/* ------------------------------------------------------------- properties */

static void gst_vgacapbin_set_property(GObject *object, guint prop_id, const GValue *value,
                                       GParamSpec *pspec)
{
    GstVgaCapBin *self = GST_VGACAPBIN(object);
    const gchar *name;

    if (prop_id == PROP_URI) {
        GST_OBJECT_LOCK(self);
        g_free(self->uri);
        self->uri = g_value_dup_string(value);
        GST_OBJECT_UNLOCK(self);
        return;
    }
    name = forwarded_name(prop_id);
    if (!name) {
        G_OBJECT_WARN_INVALID_PROPERTY_ID(object, prop_id, pspec);
        return;
    }
    GST_OBJECT_LOCK(self);
    gst_structure_set_value(self->wanted, name, value);
    GST_OBJECT_UNLOCK(self);
    /* A live source takes it straight away; one that has not been built yet
     * gets it from `wanted` when it is. */
    if (self->source && g_object_class_find_property(G_OBJECT_GET_CLASS(self->source), name))
        g_object_set_property(G_OBJECT(self->source), name, value);
}

static void gst_vgacapbin_get_property(GObject *object, guint prop_id, GValue *value,
                                       GParamSpec *pspec)
{
    GstVgaCapBin *self = GST_VGACAPBIN(object);
    const GValue *stored;
    const gchar *name;

    if (prop_id == PROP_URI) {
        GST_OBJECT_LOCK(self);
        g_value_set_string(value, self->uri);
        GST_OBJECT_UNLOCK(self);
        return;
    }
    name = forwarded_name(prop_id);
    if (!name) {
        G_OBJECT_WARN_INVALID_PROPERTY_ID(object, prop_id, pspec);
        return;
    }
    if (self->source && g_object_class_find_property(G_OBJECT_GET_CLASS(self->source), name)) {
        g_object_get_property(G_OBJECT(self->source), name, value);
        return;
    }
    GST_OBJECT_LOCK(self);
    stored = gst_structure_get_value(self->wanted, name);
    if (stored)
        g_value_copy(stored, value);
    else
        g_param_value_set_default(pspec, value);
    GST_OBJECT_UNLOCK(self);
}

static void gst_vgacapbin_finalize(GObject *object)
{
    GstVgaCapBin *self = GST_VGACAPBIN(object);

    g_clear_pointer(&self->uri, g_free);
    g_clear_pointer(&self->wanted, gst_structure_free);
    G_OBJECT_CLASS(gst_vgacapbin_parent_class)->finalize(object);
}

static void gst_vgacapbin_class_init(GstVgaCapBinClass *klass)
{
    GObjectClass *gobject_class = G_OBJECT_CLASS(klass);
    GstElementClass *element_class = GST_ELEMENT_CLASS(klass);
    const GParamFlags rw = G_PARAM_READWRITE | G_PARAM_STATIC_STRINGS;

    gobject_class->set_property = gst_vgacapbin_set_property;
    gobject_class->get_property = gst_vgacapbin_get_property;
    gobject_class->finalize = gst_vgacapbin_finalize;

    g_object_class_install_property(gobject_class, PROP_URI,
        g_param_spec_string("uri", "URI",
                            "What to decode: tt-serial:///dev/ttyACM0?k=v, "
                            "tt-ws://host:8765/serial?k=v, tt-wss://... or "
                            "file:///path/to.vgacap. The query string sets the "
                            "source's properties by name", "", rw));
    g_object_class_install_property(gobject_class, PROP_LINK,
        g_param_spec_string("link", "Link",
                            "How to reach the board: serial:/dev/ttyACM0, or "
                            "ws://host:8765/serial for the bridge", "", rw));
    g_object_class_install_property(gobject_class, PROP_PROJECT,
        g_param_spec_string("project", "Project",
                            "tt.shuttle macro to enable, e.g. tt_um_rejunity_vga; "
                            "empty leaves whatever is already selected", "", rw));
    g_object_class_install_property(gobject_class, PROP_DESIGN,
        g_param_spec_string("design", "Design",
                            "FPGA boards: bitstream to enable, through the same "
                            "tt.shuttle", "", rw));
    g_object_class_install_property(gobject_class, PROP_CLOCK_HZ,
        g_param_spec_uint("clock-hz", "Project clock",
                          "Project clock to program, in Hz; required", 0, 200000000u, 0,
                          rw));
    g_object_class_install_property(gobject_class, PROP_PROFILE,
        g_param_spec_string("profile", "Board profile",
                            "Board profile, or auto to ask the board for its GPIOMap",
                            "auto", rw));
    g_object_class_install_property(gobject_class, PROP_PIO,
        g_param_spec_int("pio", "PIO block",
                         "PIO block for the sampler; -1 leaves ttcap's default",
                         -1, 7, -1, rw));
    g_object_class_install_property(gobject_class, PROP_BUF_WORDS,
        g_param_spec_int("buf-words", "DMA buffer words",
                         "Words per DMA buffer, two are allocated; -1 leaves ttcap's "
                         "default. Also the stop latency: one buffer, which is 2.2 s "
                         "at a 60 kHz project clock",
                         -1, 1 << 24, -1, rw));
    g_object_class_install_property(gobject_class, PROP_SECONDS,
        g_param_spec_double("seconds", "Capture seconds",
                            "How long to capture; 0 captures until the element is "
                            "stopped", 0.0, 86400.0, 0.0, rw));
    g_object_class_install_property(gobject_class, PROP_TTCAP_COMMAND,
        g_param_spec_string("ttcap-command", "ttcap command",
                            "The command that runs ttcap, split with shell quoting "
                            "rules; the capture arguments are appended to it",
                            "uv run --no-sync ttcap", rw));
    g_object_class_install_property(gobject_class, PROP_STOP_TIMEOUT,
        g_param_spec_double("stop-timeout", "Stop timeout",
                            "How long a cooperative stop may take before the pipe is "
                            "closed and, failing that, the child is killed. Allow at "
                            "least one DMA buffer for the board to wind down",
                            0.0, 600.0, 15.0, rw));

    element_class->change_state = GST_DEBUG_FUNCPTR(gst_vgacapbin_change_state);

    gst_element_class_add_static_pad_template(element_class, &src_template);
    gst_element_class_set_static_metadata(element_class,
        "Tiny Tapeout VGA capture bin", "Source/Video",
        "Decodes a vgacap URI - a board over serial or the bridge, or a file - "
        "into RGB video",
        "vgacap contributors <https://github.com/mithro/vgacap>");

    GST_DEBUG_CATEGORY_INIT(vgacapbin_debug, "vgacapbin", 0, "Tiny Tapeout VGA capture bin");
}

static void gst_vgacapbin_init(GstVgaCapBin *self)
{
    GstPad *target, *ghost;
    GstPadTemplate *templ;

    self->uri = g_strdup("");
    self->wanted = gst_structure_new_empty("vgacapbin-properties");

    self->decode = gst_element_factory_make("vgadecode", "decode");
    if (!self->decode) {
        GST_ERROR_OBJECT(self, "no vgadecode element; the bin will not work");
        return;
    }
    gst_bin_add(GST_BIN_CAST(self), self->decode);

    target = gst_element_get_static_pad(self->decode, "src");
    templ = gst_static_pad_template_get(&src_template);
    ghost = gst_ghost_pad_new_from_template("src", target, templ);
    gst_object_unref(templ);
    gst_object_unref(target);
    gst_element_add_pad(GST_ELEMENT_CAST(self), ghost);
}
