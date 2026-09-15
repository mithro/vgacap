# SPDX-License-Identifier: Apache-2.0
"""Count the buffers `vgadecode` produces before and after a flushing seek.

Run with the *system* python, not the uv venv: it needs gst-python (`gi`),
which is an OS package. `test_plugin.py` shells out to it and skips when it
is not importable.

    python3 seek_probe.py STREAM BYTE_OFFSET
    -> "before=18 after_mid=7 after_zero=18"

Each pass runs to EOS, then a flushing seek restarts the flow; that is the
discontinuity the element has to survive. A seek back to 0 is the control,
since it replays the VGCH header from the file; a seek into the middle of the
file does not, so it only works if the element re-establishes the header
itself on FLUSH_STOP.
"""
import sys

import gi

gi.require_version("Gst", "1.0")
from gi.repository import Gst  # noqa: E402

TIMEOUT = 30 * Gst.SECOND if hasattr(Gst, "SECOND") else 30_000_000_000


def main() -> int:
    stream, offset = sys.argv[1], int(sys.argv[2])
    Gst.init(None)
    pipeline = Gst.parse_launch(
        f'filesrc location="{stream}" ! vgadecode name=dec ! fakesink name=sink sync=false')
    counted = [0]

    def on_buffer(pad, info):
        counted[0] += 1
        return Gst.PadProbeReturn.OK

    pipeline.get_by_name("dec").get_static_pad("src").add_probe(Gst.PadProbeType.BUFFER, on_buffer)
    bus = pipeline.get_bus()

    def run_to_eos() -> int:
        counted[0] = 0
        msg = bus.timed_pop_filtered(TIMEOUT, Gst.MessageType.EOS | Gst.MessageType.ERROR)
        if msg is None:
            print("timed out waiting for EOS", file=sys.stderr)
            return -1
        if msg.type == Gst.MessageType.ERROR:
            print("pipeline error: %s" % (msg.parse_error(),), file=sys.stderr)
            return -1
        return counted[0]

    def seek(pos: int) -> bool:
        return pipeline.seek(1.0, Gst.Format.BYTES, Gst.SeekFlags.FLUSH,
                             Gst.SeekType.SET, pos, Gst.SeekType.NONE, 0)

    pipeline.set_state(Gst.State.PLAYING)
    before = run_to_eos()
    results = {"before": before}
    for name, pos in (("after_mid", offset), ("after_zero", 0)):
        if not seek(pos):
            print(f"seek to {pos} refused", file=sys.stderr)
            results[name] = -1
            continue
        results[name] = run_to_eos()
    pipeline.set_state(Gst.State.NULL)

    print(" ".join(f"{k}={v}" for k, v in results.items()))
    return 0 if all(v >= 0 for v in results.values()) else 1


if __name__ == "__main__":
    sys.exit(main())
