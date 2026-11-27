---
type: reference
owner: vgacap maintainers
reader: someone building a GStreamer pipeline with the vgacap elements
review: 2027-05-27
---

# The vgacap GStreamer elements

**You want every property, pad, message and URI key of `vgadecode`, `vgacapttsrc` and `vgacapbin`.**

The three elements are in `libgstvgacap.so`, built from [`gst/`](.). `gst-inspect-1.0 ELEMENT` prints the same properties from the built plugin; this page adds what each value means. In the property tables, Default is the value an element starts with, Range what it accepts, and What it sets its effect.

## Elements and pads

| Element | Sink pad | Source pad |
|---|---|---|
| `vgadecode` | `application/x-vgacap` | `video/x-raw` |
| `vgacapttsrc` | none | `application/x-vgacap` in 64 KiB buffers |
| `vgacapbin` | none | `video/x-raw` |

Field is a field of the `video/x-raw` caps that `vgadecode` and `vgacapbin` push, and Value its range.

| Field | Value |
|---|---|
| `format` | `RGB` |
| `width` | 1 to 4096 |
| `height` | 1 to 4096 |
| `framerate` | 0/1 to 120/1 |
| `pixel-aspect-ratio` | 1/1 |

## vgadecode properties

| Property | Type | Default | Range | What it sets |
|---|---|---|---|---|
| `repeat-last-frame` | boolean | `false` | -- | push the last frame again to hold an `output-fps` cadence |
| `output-fps` | fraction | `30/1` | 1/1000 to 1000/1 | the frame rate when repeating, and when the stream gives no clock |
| `max-width` | unsigned | 1400 | 1 to 4096 | the widest line the buffers hold, in project clocks |
| `max-height` | unsigned | 900 | 1 to 4096 | the tallest frame the buffers hold, in lines |
| `force-mode` | string | empty | a name from [the mode table](../src/frame/reference-modes.md) | use that mode instead of detecting one; empty detects |
| `partial` | boolean | `false` | -- | also push frames whose lines were not all covered |

`max-width` and `max-height` are read once, when the element starts.

## vgadecode output buffers

| Field | Value |
|---|---|
| PTS | clocks since the first frame divided by the header's `clock_hz`; at `output-fps` with no clock |
| `GST_BUFFER_OFFSET` | the source's frame number |
| `GST_BUFFER_OFFSET_END` | the offset + 1 |
| `GST_BUFFER_FLAG_CORRUPTED` | set on a partial frame |
| `GST_BUFFER_FLAG_DISCONT` | set on the first frame after an overrun reset |
| caps frame rate, with a clock | `clock_hz` / (clocks a line × lines a frame) |
| caps frame rate, with no clock | `output-fps` |
| caps frame rate, with `repeat-last-frame` | `output-fps` |

The frame number in `GST_BUFFER_OFFSET` is the `FRAM` counter in a frame capture, and the capture's own count otherwise. It skips partial frames that are not pushed. It goes back to 0 on a new `VGCH` or a flushing seek.

A `TIME` chunk can change the clock rate mid-stream. The PTS then keeps the time reached and goes on at the new rate, so it never goes back.

## vgadecode bus message

An element message named `vgacap-timing` is posted when the timing is first known, and again when it changes.

| Field | Type | Value |
|---|---|---|
| `mode` | string | the matched mode's name, or empty |
| `clocks-per-line` | unsigned | the measured clocks a line |
| `lines-per-frame` | unsigned | the measured lines a frame |
| `hsync-positive` | boolean | the hsync pulse is high |
| `vsync-positive` | boolean | the vsync pulse is high |
| `glitches` | unsigned | spurious sync pulses ignored so far |

## vgacapttsrc properties

In a URI says whether a `vgacapbin` URI's query may set the property.

| Property | Type | Default | Range | In a URI | What it sets |
|---|---|---|---|---|---|
| `link` | string | empty | -- | no | the board, as `ttcap capture` takes it; required |
| `clock-hz` | unsigned | 0 | 0 to 200,000,000 | yes | the project clock to program, in Hz; required |
| `project` | string | empty | -- | yes | the `tt.shuttle` project to enable; empty keeps the one selected |
| `design` | string | empty | -- | yes | on a demo board with an FPGA breakout, the bitstream to enable |
| `profile` | string | `auto` | `ttcap capture --profile`'s choices | yes | the board profile; `auto` asks the board |
| `pio` | integer | -1 | -1 to 7 | yes | the PIO block for the sampler; -1 leaves `ttcap`'s default |
| `buf-words` | integer | -1 | -1 to 16,777,216 | yes | words a DMA buffer; -1 leaves `ttcap`'s default |
| `seconds` | double | 0 | 0 to 86,400 | yes | how long to capture; 0 until the element stops |
| `stop-timeout` | double | 15 | 0 to 600 | yes | seconds a cooperative stop may take |
| `capture-mode` | string | `stream` | `ttcap capture --mode`'s choices | yes | the capture mode |
| `firmware` | string | `micropython` | `ttcap capture --firmware`'s choices | yes | what runs on the board |
| `vga-mode` | string | empty | `ttcap capture --vga-mode`'s choices | yes | frame and window modes: the timing to expect; empty is 640x480@60 |
| `window-lines` | integer | 0 | 0 to 4096 | yes | frame and window modes: most lines a frame; 0 leaves `ttcap`'s default |
| `ttcap-command` | string | `uv run --no-sync ttcap` | -- | no | the command that runs `ttcap`, split by shell rules |

A `link` is `serial:/dev/ttyACM0` for a board on USB, or `ws://host:8765/serial` for the fpgas.online bridge (`fpgas-tt`). The element runs `ttcap capture LINK --out - --clock-hz N --seconds S`, adding an option for each property set away from its default. The choices of `ttcap capture` are on [the ttcap page](../python/ttcap/README.md). `ttcap-command` runs in the pipeline's working directory, so give an absolute path when that is not the vgacap checkout.

## vgacapbin properties

| Property | Type | Default | What it sets |
|---|---|---|---|
| `uri` | string | empty | the source, as in the next table; required |
| every `vgacapttsrc` property | as `vgacapttsrc` | as `vgacapttsrc` | passed to a `vgacapttsrc` source; a `filesrc` source ignores them |

A key in the URI's query wins over the same property set on the bin.

## vgacapbin URIs

Scheme is the URI's scheme, Example a URI, Source the element made, and Set the property the URI's location sets on it.

| Scheme | Example | Source | Set |
|---|---|---|---|
| `tt-serial` | `tt-serial:///dev/ttyACM0?clock-hz=60000` | `vgacapttsrc` | `link=serial:/dev/ttyACM0` |
| `tt-ws` | `tt-ws://host:8765/serial?clock-hz=60000` | `vgacapttsrc` | `link=ws://host:8765/serial` |
| `tt-wss` | `tt-wss://host/serial?clock-hz=60000` | `vgacapttsrc` | `link=wss://host/serial` |
| `file` | `file:///captures/tt08.vgacap` | `filesrc` | `location=/captures/tt08.vgacap` |

| Rule | What happens |
|---|---|
| a `tt-ws` or `tt-wss` URI with no path | the path is `/serial` |
| a `tt-serial` URI with a host (two slashes) | an error: write three slashes |
| a `file` URI with a host other than `localhost` | an error |
| user information | an error |
| a `#` fragment | an error |
| a query key not marked "in a URI" above, such as `link` | an error naming the allowed keys |
| a value that does not convert to the property's type, such as `clock-hz=sixty` | an error |

A URI can come from somewhere that is not trusted: a form field, a saved playlist or a shared link. So its query may set only capture parameters. `ttcap-command`, a program to run, and `link`, which would move the capture away from the URI's own location, stay element properties.

On a command line, quote the URI twice: `'uri="tt-serial:///dev/ttyACM0?project=tt_um_x&clock-hz=60000"'`. The outer quotes stop the shell at `&` and `?`; the inner ones are for GStreamer's pipeline parser.

## Debug categories

- `vgadecode` logs the timing, the caps and the frames.
- `vgacapttsrc` logs the child's command, its stderr at `INFO`, and the stop sequence.
- `vgacapbin` logs the source it built from the URI.
