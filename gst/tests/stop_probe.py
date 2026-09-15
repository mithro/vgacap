# SPDX-License-Identifier: Apache-2.0
"""Stop a running `vgacapttsrc` mid-stream and report what that cost.

Run with the *system* python, not the uv venv: it needs gst-python (`gi`),
which is an OS package. `test_capture_source.py` shells out to it and skips
when it is not importable.

    python3 stop_probe.py --ttcap-command "..." --pid-file P [--extra "k=v"]
    -> {"buffers": 41, "stop_seconds": 0.62, "child": "gone"}

Why a probe rather than `gst-launch-1.0` plus a signal: the question is
whether the *element* reaps its child, and that can only be seen while the
element's own process is still alive. Once gst-launch exits, init adopts and
reaps anything it left behind, and a zombie becomes invisible.
"""
import argparse
import json
import os
import sys
import time

import gi

gi.require_version("Gst", "1.0")
from gi.repository import Gst  # noqa: E402


def child_state(pid: int) -> str:
    """"gone", or the single-letter process state from /proc; "Z" is a zombie
    the element failed to reap."""
    try:
        with open(f"/proc/{pid}/stat") as fp:
            fields = fp.read().rsplit(") ", 1)[1].split()
        return fields[0]
    except (FileNotFoundError, ProcessLookupError, IndexError):
        return "gone"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--ttcap-command", required=True)
    parser.add_argument("--pid-file", required=True)
    parser.add_argument("--link", default="serial:/dev/fake")
    parser.add_argument("--clock-hz", type=int, default=60000)
    parser.add_argument("--stop-timeout", type=float, default=10.0)
    parser.add_argument("--min-buffers", type=int, default=8)
    parser.add_argument("--until-eos", action="store_true",
                        help="let the capture finish by itself instead of stopping "
                             "it mid-stream")
    parser.add_argument("--dwell", type=float, default=0.0,
                        help="seconds to leave the pipeline standing before tearing "
                             "it down; an application may leave it standing for any "
                             "length of time, which is the point")
    parser.add_argument("--wait", type=float, default=30.0)
    parser.add_argument("--child-grace", type=float, default=3.0,
                        help="how long to let a signalled child actually die before "
                             "reporting what state it is in; the element delivers "
                             "the signal, the kernel and init do the rest")
    args = parser.parse_args()

    Gst.init(None)
    pipeline = Gst.parse_launch(
        f'vgacapttsrc name=src ttcap-command="{args.ttcap_command}" seconds=0 '
        f'link="{args.link}" clock-hz={args.clock_hz} '
        f"stop-timeout={args.stop_timeout} ! vgadecode ! fakesink sync=false")
    counted = [0]

    def on_buffer(pad, info):
        counted[0] += 1
        return Gst.PadProbeReturn.OK

    pipeline.get_by_name("src").get_static_pad("src").add_probe(
        Gst.PadProbeType.BUFFER, on_buffer)
    bus = pipeline.get_bus()
    pipeline.set_state(Gst.State.PLAYING)

    eos = False
    if args.until_eos:
        # Let the capture end by itself. The element notices inside create(),
        # and the pipeline then stands until whoever owns it takes it down.
        msg = bus.timed_pop_filtered(int(args.wait * Gst.SECOND),
                                     Gst.MessageType.EOS | Gst.MessageType.ERROR)
        if msg is None or msg.type == Gst.MessageType.ERROR:
            print("no EOS: %s" % (msg.parse_error() if msg else "timed out",),
                  file=sys.stderr)
            pipeline.set_state(Gst.State.NULL)
            return 1
        eos = True
    else:
        # Let the capture get properly under way, then stop it mid-stream.
        give_up = time.monotonic() + args.wait
        while counted[0] < args.min_buffers and time.monotonic() < give_up:
            msg = bus.timed_pop_filtered(50 * Gst.MSECOND, Gst.MessageType.ERROR)
            if msg is not None:
                print("pipeline error: %s" % (msg.parse_error(),), file=sys.stderr)
                pipeline.set_state(Gst.State.NULL)
                return 1
            time.sleep(0.01)
        if counted[0] < args.min_buffers:
            print(f"only {counted[0]} buffers in {args.wait}s", file=sys.stderr)
            pipeline.set_state(Gst.State.NULL)
            return 1

    if args.dwell:
        time.sleep(args.dwell)

    pid = int(open(args.pid_file).read().strip())
    began = time.monotonic()
    pipeline.set_state(Gst.State.NULL)
    pipeline.get_state(Gst.CLOCK_TIME_NONE)
    took = time.monotonic() - began

    # `child` is the state after a grace period, because the element's job is
    # to deliver the signal, not to outrun the scheduler; `child_at_once` is
    # what it was the instant teardown returned, which is what tells a prompt
    # exit apart from one that needed the kill.
    at_once = child_state(pid)
    state = at_once
    give_up = time.monotonic() + args.child_grace
    while state not in ("gone", "Z") and time.monotonic() < give_up:
        time.sleep(0.02)
        state = child_state(pid)

    result = {"buffers": counted[0], "stop_seconds": took, "child": state,
              "child_at_once": at_once, "eos": eos, "child_pid": pid,
              "parent": os.getpid()}
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    sys.exit(main())
