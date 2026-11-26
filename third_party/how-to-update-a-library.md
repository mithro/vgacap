---
type: how-to
owner: vgacap maintainers
reader: someone with a vgacap checkout who wants a newer version of a vendored library
review: 2027-05-27
---

# How to update a vendored library

**You want a newer upstream version of one of the libraries in `third_party/`, with vgacap still building and passing its tests.**

This page replaces one library's files with those of a newer upstream version, and records the change. The libraries, their versions and their local changes are on [Vendored libraries](reference-vendored-libraries.md). It does not cover adding a new library.

## What you need

- The upstream tag or commit to take, from the library's repository on [Vendored libraries](reference-vendored-libraries.md).
- A clone of that repository outside the vgacap checkout.

## Steps

1. In the upstream clone, run `git checkout <tag or commit>` to get the version to take.
2. For each file in the library's row of the Files taken table, copy the upstream file over the one under `third_party/`.
3. If the library has a row under Local changes, make that change again in the new files.
4. If the new version adds or removes source files, change the library's list in [`third_party/CMakeLists.txt`](CMakeLists.txt) to match.
5. In [Vendored libraries](reference-vendored-libraries.md), change the library's version and any file or change that moved.
6. In the vgacap checkout, run steps 1 to 3 of [How to build vgacap and run its tests](../docs/development.md) to build and test it.

## Check

`test_third_party` passes in `ctest`, and so do the trace tests that use the library: `test_trace_write`, `test_trace_read`, `test_export_fst` and `test_hashes`. `git diff --stat third_party/` lists only the library's files and the reference page.

## If it fails

| Step | What you see | Likely cause | Fix |
|---|---|---|---|
| 6 | `undefined reference to` a library function | a source file the new version added is not built | Add it to the library's list in `CMakeLists.txt` |
| 6 | `No such file or directory` for a header | a header the new version added is not copied | Copy it, and add it to the Files taken table |

## Next

- [Vendored libraries](reference-vendored-libraries.md)
- [How to build vgacap and run its tests](../docs/development.md)
