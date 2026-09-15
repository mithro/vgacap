/* SPDX-License-Identifier: Apache-2.0 */
/* vgacap GStreamer plugin entry point.
 *
 * Registers the elements of the vgacap plugin. Only `vgadecode` exists so
 * far; `vgacapttsrc` (a GstPushSrc running `ttcap capture --out -`) and
 * `vgacapbin` (source-by-URI plus vgadecode) arrive with M5 Task 4 and
 * register here alongside it - see the hook in plugin_init().
 */
#include <gst/gst.h>

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
    /* Task 4 hook: register "vgacapttsrc" and "vgacapbin" here. */
    return TRUE;
}

GST_PLUGIN_DEFINE(GST_VERSION_MAJOR, GST_VERSION_MINOR, vgacap,
                  "Tiny Tapeout VGA capture: stream decoding and capture sources",
                  plugin_init, VGACAP_VERSION, "Apache-2.0", PACKAGE,
                  "https://github.com/mithro/vgacap")
