---
type: how-to
owner: vgacap maintainers
reader: someone who wants to watch a demo board's picture while it is captured
review: 2027-05-27
---

# How to watch a capture live in a window or a browser

**You want to see your design's picture as it is captured, in a window on your screen or in a web browser.**

`ttcap demo --window` adds a window, and `--serve PORT` adds an MJPEG stream on `http://127.0.0.1:PORT/`. Both come from the same capture as the files, so they can run together. The files and the pipeline are on [How to make a video of a design with ttcap demo](how-to-make-video.md).

## What you need

- A demo run that works, as in [How to make a video of a design with ttcap demo](how-to-make-video.md).
- For `--window`, a desktop session; for `--serve`, a free TCP port above 1023 and a browser on the same machine.

## Steps

1. In the checkout, run the demo with a window, a server port and a frame rate, which starts the capture and the server:

   ```sh
   uv run ttcap demo --link serial:/dev/ttyACM0 --project tt_um_rejunity_vga \
       --clock-hz 400000 --seconds 0 --fps 5 --outdir live --window --serve 8080
   ```

2. In a browser on the same machine, open `http://127.0.0.1:8080/`, which shows the stream as frames arrive.
3. In the terminal, press Ctrl-C once when you are done, and the demo ends the stream and finishes the files.

`--seconds 0` captures until Ctrl-C. The server listens on the loopback address only, so the browser must run on the machine that runs the demo. A viewer that falls behind loses frames rather than slowing the capture. Add `--no-png --no-video` to watch without writing files.

## Check

The demo prints `serving http://127.0.0.1:8080/ -- open it in a browser` before the pipeline starts. The window and the page show the design's picture, and the summary counts the frames published:

```text
  a window (autovideosink)
  N frame(s) published on port 8080
```

## If it fails

| Step | What you see | Likely cause | Fix |
|---|---|---|---|
| 1 | `--serve 8080: Address already in use` | another program holds the port | pick another port |
| 1 | `Ports below 1024 need root` | the port is privileged | pick a port above 1023 |
| 1 | no window appears | the shell has no display | run the demo in the desktop session |
| 2 | the page never loads | the browser is on another machine | open it on the machine that runs the demo |

## Next

- [How to make a video of a design with ttcap demo](how-to-make-video.md)
- [How to print the demo's GStreamer pipeline](how-to-print-pipeline.md)
