---
type: explanation
owner: vgacap maintainers
reader: someone running a live capture in a GStreamer pipeline who wants it to end cleanly
review: 2027-05-27
---

# How vgacapttsrc runs and stops a capture

This page explains how `vgacapttsrc` gets a capture from a board, why stopping it takes care, and why a live capture cannot be paused. It is for someone who runs a live pipeline and wants every capture to end with all its samples accounted for. The properties are on [The vgacap GStreamer elements](reference-elements.md).

## ttcap does the talking

`vgacapttsrc` runs `ttcap capture --out -` as a child process and pushes what it writes to stdout as 64 KiB buffers. The board protocol stays in [`ttcap`](../python/ttcap/README.md), in Python, so the element works with any board and link `ttcap` does: USB serial, or the fpgas.online bridge (`fpgas-tt`). The element adds no protocol of its own, and the stream it pushes is the one `ttcap` would write to a file.

## Why stopping is a sequence

A board samples into two DMA buffers and can stop only at a buffer's end, so a capture takes up to one buffer to wind down. On its first SIGINT, `ttcap` lets the board finish its chunk and write the closing `TIME` chunk. That chunk is the only place the board reports overruns, so a capture that is killed instead ends mid-chunk with no report.

So on stop the element sends SIGINT to the child's process group and waits, reading and discarding the child's output so that a full pipe cannot block it. If `stop-timeout` passes, it closes the pipe, which `ttcap` takes as a clean end. If the child is still alive, it is killed. The child is reaped only at the end, so no signal ever reaches a process that is no longer the element's.

## A live capture cannot be paused

The board samples in real time, and the element's pipe is the only buffer between it and the pipeline. In PAUSED the element stops reading, and the child has one pipe buffer of grace, measured at 65,548 bytes. That is about 87 ms of capture on an RP2350 at 750 kHz, and about 0.55 s on an RP2040 at 60 kHz. After it the board's buffers overrun and samples are lost.

The overrun count is in the closing `TIME` chunk, which the stop sequence drains and discards. So a capture that was paused and then stopped is short of samples with nothing on the bus to say so. Go straight to PLAYING and stop when finished; treat anything after a pause as a different capture.

## Failures reach the bus

The child's stderr is read line by line and logged at `INFO` in the `vgacapttsrc` debug category. A child that exits with an error raises a pipeline `ERROR` that quotes its last lines. So a board that is missing, a project name that is wrong, or a board that never answers shows as an error, not as a stream that ends early.
