---
type: how-to
owner: vgacap maintainers
reader: someone who wants to see or edit the GStreamer pipeline ttcap demo runs
review: 2027-05-27
---

# How to print the demo's GStreamer pipeline

**You want the exact `gst-launch-1.0` command `ttcap demo` would run, to read it, change it or run it yourself.**

`ttcap demo --dry-run` prints the pipeline and exits. It touches no board, creates no directory and opens no port. A real run prints the same command before it starts.

## What you need

- A vgacap checkout with `uv sync` run in it. GStreamer and the plugin are needed to run the printed command, not to print it.

## Steps

1. In the checkout, run the demo with the options you want and `--dry-run`, which prints the pipeline on standard output:

   ```sh
   uv run ttcap demo --link serial:/dev/ttyACM0 --clock-hz 400000 --outdir dry --dry-run
   ```

2. In the terminal, copy the printed command, edit it, and run it with `GST_PLUGIN_PATH` set, which runs your version of the pipeline.

Without the plugin, step 1 prints a `warning:` line on standard error first and still prints the pipeline. Options are checked as in a real run, so a wrong number or board name is refused here too.

## Check

Step 1 prints one line for the source and decoder, then one for each output:

```text
gst-launch-1.0 -e -m vgacapttsrc name=src link=serial:/dev/ttyACM0 clock-hz=400000 seconds=10 profile=auto 'ttcap-command=uv run --no-sync ttcap' ! vgadecode name=dec ! tee name=t \
    t. ! queue ! pngenc ! multifilesink post-messages=true location=dry/frame-%04d.png \
    t. ! queue ! videoconvert ! x264enc tune=zerolatency speed-preset=veryfast key-int-max=30 ! matroskamux ! filesink location=dry/capture.mkv
```

The encoder is the first of `x264enc` and `vp8enc` that GStreamer has. With `--serve`, the MJPEG branch writes to file descriptor 3, which only the demo itself provides.

## If it fails

| Step | What you see | Likely cause | Fix |
|---|---|---|---|
| 1 | `no video encoder` | neither encoder is installed | install one or add `--no-video` |
| 2 | `erroneous pipeline: no element "vgacapttsrc"` | `GST_PLUGIN_PATH` does not name the plugin | `export GST_PLUGIN_PATH=$PWD/build` |
| 2 | the board does not answer | another program holds the port | close it, then run step 2 again |

## Next

- [How to make a video of a design with ttcap demo](how-to-make-video.md)
- [The GStreamer plugin](../../gst/README.md)
