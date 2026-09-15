/* SPDX-License-Identifier: Apache-2.0 */
/* The `vgacapbin` URI grammar, on its own so it can be unit tested.
 *
 * Three schemes name a source of vgacap bytes:
 *
 *   tt-serial:///dev/ttyACM0?project=X&clock-hz=N   a board on a serial port
 *   tt-ws://host:8765/serial?project=X&clock-hz=N   a board behind the bridge
 *   file:///path/to/capture.vgacap                  a capture already on disk
 *
 * The parse says which element to build, which of its properties carries the
 * location, and what the query string asked for. Everything else - making the
 * element, type-converting the parameters, reporting what it could not set -
 * belongs to the bin, and none of it is needed to test the grammar. This file
 * therefore depends on GLib alone, not on GStreamer.
 */
#ifndef GST_VGACAP_URI_H
#define GST_VGACAP_URI_H

#include <glib.h>

G_BEGIN_DECLS

typedef struct {
    gchar *element;      /* factory name: "vgacapttsrc" or "filesrc" */
    gchar *property;     /* the property @value belongs to: "link" or "location" */
    gchar *value;        /* serial:/dev/ttyACM0, ws://host:8765/serial, or a path */
    GHashTable *params;  /* query parameters, gchar* -> gchar*; never NULL */
} VgaCapUri;

typedef enum {
    VGACAP_URI_ERROR_SYNTAX,  /* not a URI at all, or a malformed query string */
    VGACAP_URI_ERROR_SCHEME,  /* a URI, but not one of ours */
    VGACAP_URI_ERROR_VALUE    /* our scheme, wrongly filled in */
} VgaCapUriError;

#define VGACAP_URI_ERROR (vgacap_uri_error_quark())
GQuark vgacap_uri_error_quark(void);

/* Parse @uri into @out, which is only touched on success. */
gboolean vgacap_uri_parse(const gchar *uri, VgaCapUri *out, GError **error);
void vgacap_uri_clear(VgaCapUri *uri);

G_END_DECLS

#endif /* GST_VGACAP_URI_H */
