---
type: how-to
owner: vgacap maintainers
reader: someone who wants a demo board capture piped live into another program
review: 2027-05-27
---

# How to stream a capture into another program

**You want a capture to flow into another program as it arrives, such as a GStreamer pipeline, rather than into a file.**

`ttcap capture --out -` writes the stream to standard output and every message to standard error. This page pipes it into GStreamer's `vgadecode`; `ttcap demo` builds such a pipeline for you ([How to make a video of a design with ttcap demo](how-to-make-video.md)).

## What you need

- A capture that works to a file, as in [How to capture a stream from a demo board](how-to-capture-stream.md).
- The vgacap GStreamer plugin built into `build/`, as in [Developing vgacap](../../docs/development.md).

## Steps

1. In the checkout, point GStreamer at the plugin with `export GST_PLUGIN_PATH=$PWD/build`, so `gst-inspect-1.0 vgadecode` finds the element.
2. In the checkout, pipe the capture into `vgadecode`, which writes a PNG for each frame as it closes:

   ```sh
   uv run ttcap capture serial:/dev/ttyACM0 --clock-hz 500000 --seconds 0 --out - |
       gst-launch-1.0 -e fdsrc fd=0 ! application/x-vgacap ! vgadecode ! \
       videoconvert ! pngenc ! multifilesink location=frame-%04d.png
   ```

3. In the same terminal, press Ctrl-C once when you have enough frames, and the board finishes its chunk and the stream ends.

`--seconds 0` with no other limit is allowed only with `--out -`, where Ctrl-C stops the run. The first Ctrl-C asks the board to stop at the end of its DMA buffer: about 22 ms on an RP2350 at 750 kHz, and 137 ms on an RP2040 at 60 kHz, at the default `--buf-words`. A second Ctrl-C stops the capture at once and loses the board's closing report. A program that stops reading ends the capture the same clean way.

## Check

`frame-0000.png` and the frames after it appear in the directory as the capture runs. On standard error, `ttcap` ends with its stats line and `wrote stdout`, or with `capture ended: the consumer closed the stream`, and exits 0.

## If it fails

| Step | What you see | Likely cause | Fix |
|---|---|---|---|
| 2 | `erroneous pipeline: no element "vgadecode"` | `GST_PLUGIN_PATH` does not name the plugin's directory | build the plugin and repeat step 1 |
| 2 | `the capture would never stop` | `--seconds 0` with a file in `--out` | use `--out -` or give a limit |
| 2 | no PNG appears | the capture has not yet closed a frame | wait for three frame periods |
| 3 | `rxstall=1` in the stats | samples were lost on the board | lower `--clock-hz` |

## Next

- [How to make a video of a design with ttcap demo](how-to-make-video.md)
- [The GStreamer plugin](../../gst/README.md)
- [ttcap commands and options](reference-commands.md#ttcap-capture)
