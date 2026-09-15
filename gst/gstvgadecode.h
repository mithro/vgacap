/* SPDX-License-Identifier: Apache-2.0 */
/* vgadecode: vgacap capture stream (application/x-vgacap) to video/x-raw RGB.
 *
 * A plain GstElement with a chain function rather than a GstBaseTransform:
 * the mapping from input buffers to output frames is N:M (a 64 KiB input
 * buffer may complete zero, one or several frames, and one frame may span
 * many input buffers), the output caps come from the *content* rather than
 * from the input caps, and frames are produced from inside a callback nested
 * three deep in vgacap_reader_feed(). GstBaseTransform's contract - one
 * output buffer per transform() call, caps derived by transform_caps() - fits
 * none of that.
 */
#ifndef GST_VGADECODE_H
#define GST_VGADECODE_H

#include <gst/gst.h>
#include <gst/video/video.h>

#include "vgacap/frame.h"
#include "vgacap/stream.h"

G_BEGIN_DECLS

#define GST_TYPE_VGADECODE (gst_vgadecode_get_type())
G_DECLARE_FINAL_TYPE(GstVgaDecode, gst_vgadecode, GST, VGADECODE, GstElement)

G_END_DECLS

#endif /* GST_VGADECODE_H */
