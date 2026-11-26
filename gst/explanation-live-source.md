---
type: explanation
owner: vgacap maintainers
reader: someone running a live capture in a GStreamer pipeline who wants it to end cleanly
review: 2027-05-27
---

# How vgacapttsrc runs and stops a capture

This page explains how `vgacapttsrc` gets a capture from a board, why stopping it takes care, and why a live capture cannot be paused. It is for someone who runs a live pipeline and wants each capture to end with its losses accounted for. The properties are on [The vgacap GStreamer elements](reference-elements.md).

## ttcap does the talking

`vgacapttsrc` runs `ttcap capture --out -` as a child process. It pushes what the child writes to stdout as 64 KiB buffers. The board protocol stays in [`ttcap`](../python/ttcap/README.md), in Python, so the element works over any link `ttcap` does. The stream it pushes is the one `ttcap` would write to a file.

## Why stopping is a sequence

A board samples into two DMA buffers, and can stop only at a buffer's end. So a capture takes up to one buffer to wind down. During a capture the board writes an `overrun` `TIME` chunk after each buffer it lost. On its first SIGINT, `ttcap` lets the board finish its chunk and write a closing `TIME` chunk with its totals. A capture that is killed instead ends mid-chunk, with no totals.

So on stop, the element sends SIGINT to the child's process group and waits. While it waits, it reads and discards the child's output, so that a full pipe cannot block the child. If `stop-timeout` passes, it closes the pipe, which `ttcap` takes as a clean end. If the child is still alive after that, it is killed. The child is reaped only at the end, so no signal reaches a process that is no longer the element's.

## A live capture cannot be paused

The board samples in real time, and the element's pipe is the only buffer between it and the pipeline. In PAUSED the element stops reading, and the child has one pipe buffer of grace: 64 KiB by default on Linux. At one byte a sample, that is about 87 ms of capture at 750 kHz. After it, the board's buffers overrun and samples are lost.

The `overrun` chunks written then are still in the pipe when the pipeline stops. The stop sequence drains and discards them with the closing `TIME` chunk. So a capture that was paused and then stopped is short of samples, with nothing on the bus to say so. Go straight to PLAYING, and stop when finished; treat anything after a pause as a different capture.

## Failures reach the bus

The child's stderr is read line by line, and logged at `INFO` in the `vgacapttsrc` debug category. A child that exits with an error raises a pipeline `ERROR` that quotes its last lines. So a missing board, a wrong project name or a board that never answers shows as an error. It does not look like a stream that ended early.
