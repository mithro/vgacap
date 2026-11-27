# Third-party code used only by the trace format study

| Directory | What | Source | Licence |
|---|---|---|---|
| `lz4hc/` | `lz4hc.c`, `lz4hc.h`: LZ4's high-compression mode. It includes the `lz4.c` already vendored in [`third_party/libfst`](../../../third_party/libfst), which is the same v1.10.0 file. | [lz4/lz4 v1.10.0 `lib/`](https://github.com/lz4/lz4/tree/v1.10.0/lib) | BSD-2-Clause, [`lz4hc/LICENSE`](lz4hc/LICENSE) |
| `lxt2/` | `lxt2_write.c`, `lxt2_write.h`: GTKWave's LXT2 writer, and a `config.h` with the three feature macros its build would set. | [gtkwave/gtkwave `lib/liblxt` at e40ba79](https://github.com/gtkwave/gtkwave/tree/e40ba7937336824f12f7db870ea36f6f090dd90d/lib/liblxt) | MIT, in each file's header |

They are built only by [`bench/format-study/CMakeLists.txt`](../CMakeLists.txt), never by vgacap itself.
