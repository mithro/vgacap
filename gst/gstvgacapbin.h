/* SPDX-License-Identifier: Apache-2.0 */
/* vgacapbin: a URI in, RGB video out.
 *
 * A #GstBin holding a source chosen by the URI scheme and a `vgadecode`
 * behind it, with `vgadecode`'s src pad ghosted. It exists so that a demo, a
 * `gst-launch` line or a Python script can name a board or a file the same
 * way and get frames either way:
 *
 *   vgacapbin uri=tt-serial:///dev/ttyACM0?project=tt_um_x&clock-hz=60000
 *   vgacapbin uri=tt-ws://welland:8765/serial?clock-hz=60000
 *   vgacapbin uri=file:///captures/tt08.vgacap
 *
 * The query string sets properties on the source by name, and the source's
 * own properties are mirrored on the bin, so either of these works:
 *
 *   vgacapbin uri=tt-ws://welland:8765/serial?seconds=10
 *   vgacapbin uri=tt-ws://welland:8765/serial seconds=10
 *
 * ...but only for the capture parameters. A query may not set `ttcap-command`
 * or the link, because a URI can come from somewhere that is not a trusted
 * shell and `ttcap-command` names a program to run; see URI_SETTABLE in
 * gstvgacapbin.c. Anything else in a query is a loud error, never a silent
 * no-op.
 *
 * The source is built when the bin leaves the NULL state and torn down when
 * it returns, so changing `uri` between runs picks a different board. The
 * ghost pad, though, exists from the moment the bin is constructed: gst-launch
 * links elements while the pipeline is still in NULL, and a pad that appeared
 * later would be too late to link to.
 */
#ifndef GST_VGACAPBIN_H
#define GST_VGACAPBIN_H

#include <gst/gst.h>

G_BEGIN_DECLS

#define GST_TYPE_VGACAPBIN (gst_vgacapbin_get_type())
G_DECLARE_FINAL_TYPE(GstVgaCapBin, gst_vgacapbin, GST, VGACAPBIN, GstBin)

G_END_DECLS

#endif /* GST_VGACAPBIN_H */
