/* SPDX-License-Identifier: Apache-2.0 */
/* vgacap GStreamer plugin entry point.
 *
 * Registers the elements of the vgacap plugin: `vgadecode` turns a capture
 * stream into video, and `vgacapttsrc` produces one from a board by running
 * `ttcap capture --out -`. `vgacapbin` (source-by-URI plus vgadecode) joins
 * them next - see the hook in plugin_init().
 */
#include <gst/gst.h>

#include "gstvgacapttsrc.h"
#include "gstvgadecode.h"

#ifndef PACKAGE
#define PACKAGE "vgacap"
#endif
#ifndef VGACAP_VERSION
#define VGACAP_VERSION "0.0.1"
#endif

static gboolean plugin_init(GstPlugin *plugin)
{
    if (!gst_element_register(plugin, "vgadecode", GST_RANK_NONE, GST_TYPE_VGADECODE))
        return FALSE;
    if (!gst_element_register(plugin, "vgacapttsrc", GST_RANK_NONE, GST_TYPE_VGACAPTTSRC))
        return FALSE;
    /* Task 4 hook: register "vgacapbin" here. */
    return TRUE;
}

/* The licence token has to come from the set GStreamer enumerates in
 * gstplugin.c - anything else makes it log `unknown license` on every plugin
 * load - and "Apache 2.0" (with a space) is the entry that matches this
 * code's actual licence, Apache-2.0, as the SPDX headers state. */
GST_PLUGIN_DEFINE(GST_VERSION_MAJOR, GST_VERSION_MINOR, vgacap,
                  "Tiny Tapeout VGA capture: stream decoding and capture sources",
                  plugin_init, VGACAP_VERSION, "Apache 2.0", PACKAGE,
                  "https://github.com/mithro/vgacap")
