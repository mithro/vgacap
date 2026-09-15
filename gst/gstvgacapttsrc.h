/* SPDX-License-Identifier: Apache-2.0 */
/* vgacapttsrc: a live capture from a Tiny Tapeout board, as
 * `application/x-vgacap` bytes.
 *
 * The element does not speak to the board itself: it runs
 * `ttcap capture --out -` as a child process and pushes that child's stdout.
 * The board protocol - the RAW REPL, the GPIO map probe, the PIO sampler, the
 * DMA double buffer and the recovery paths - lives in Python, in one place,
 * and the element stays small enough to read in one sitting. The same child
 * command reaches a board over a serial link or over the WebSocket bridge,
 * so nothing here has to know the difference.
 *
 * A #GstPushSrc rather than a #GstBaseSrc: the child is a live, unseekable
 * byte stream of unknown length, which is exactly the contract
 * `GstPushSrc::create` has and nothing like `GstBaseSrc::fill`'s
 * offset-addressed one.
 *
 * POSIX hosts only. Stopping the child well needs a process group, a signal
 * and `waitpid()` with a deadline, and the boards this drives hang off Linux
 * workstations and Raspberry Pis; a Windows port would need its own job
 * object and is not attempted.
 */
#ifndef GST_VGACAPTTSRC_H
#define GST_VGACAPTTSRC_H

#include <gst/base/gstpushsrc.h>
#include <gst/gst.h>

G_BEGIN_DECLS

#define GST_TYPE_VGACAPTTSRC (gst_vgacapttsrc_get_type())
G_DECLARE_FINAL_TYPE(GstVgaCapTtSrc, gst_vgacapttsrc, GST, VGACAPTTSRC, GstPushSrc)

G_END_DECLS

#endif /* GST_VGACAPTTSRC_H */
