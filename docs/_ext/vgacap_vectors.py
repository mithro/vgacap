# SPDX-License-Identifier: Apache-2.0
"""The tb-v1 test vectors, as tables built from the vectors themselves at
build time (rule 4.4), so the page cannot go stale against them (10.15).

    ```{vgacap-tbv1-vectors} results
    ```

is one row for each case directory of tests/vectors/tbv1, from the header
of its frames.txt (the lines before `distinct:`).

    ```{vgacap-tbv1-vectors} provenance
    ```

is the `generator:` and `vgacap:` lines of its MANIFEST.txt. Each file read
is recorded as a dependency, so a change to a vector rebuilds the page. A
file it cannot read, or a key it cannot find, is an error.
"""

import pathlib

from docutils import nodes
from sphinx.util.docutils import SphinxDirective

REPO = pathlib.Path(__file__).resolve().parent.parent.parent
VECTORS = REPO / "tests" / "vectors" / "tbv1"

# (frames.txt key, column heading)
RESULT_COLUMNS = (
    ("status", "Status"),
    ("sampler_status", "Sampler status"),
    ("mode", "Mode"),
    ("clock_hz", "Clock (Hz)"),
    ("clocks_per_pixel", "Clocks a pixel"),
    ("frames", "Frames"),
    ("distinct_frames", "Distinct frames"),
)
PROVENANCE = (("generator", "tinytapeout-vga-videos"), ("vgacap", "vgacap"))


class VectorError(Exception):
    pass


def frames_header(path):
    """The `key: value` lines of a frames.txt before its `distinct:` line."""
    values = {}
    for line in path.read_text().splitlines():
        if line == "distinct:":
            break
        key, sep, value = line.partition(": ")
        if sep:
            values[key] = value
    return values


def cases(root=VECTORS):
    found = sorted(p.name for p in root.iterdir() if (p / "frames.txt").is_file())
    if not found:
        raise VectorError(f"{root}: no case directory with a frames.txt")
    return found


def results(root=VECTORS):
    """[(case, path read, [value of each RESULT_COLUMNS key])]."""
    rows = []
    for case in cases(root):
        path = root / case / "frames.txt"
        values = frames_header(path)
        missing = [k for k, _ in RESULT_COLUMNS if k not in values]
        if missing:
            raise VectorError(f"{path}: no {', '.join(missing)}")
        rows.append((case, path, [values[k] for k, _ in RESULT_COLUMNS]))
    return rows


def provenance(root=VECTORS):
    """[(repository, commit)] from MANIFEST.txt's `name: commit` lines."""
    path = root / "MANIFEST.txt"
    found = {}
    for line in path.read_text().splitlines():
        key, sep, value = line.partition(": ")
        if sep:
            found[key] = value.split()[-1] if value.split() else ""
    missing = [k for k, _ in PROVENANCE if k not in found]
    if missing:
        raise VectorError(f"{path}: no {', '.join(missing)} line")
    return [(repo, found[key]) for key, repo in PROVENANCE]


def table(headers, rows):
    tgroup = nodes.tgroup(cols=len(headers))
    for _ in headers:
        tgroup += nodes.colspec(colwidth=1)
    head, body = nodes.thead(), nodes.tbody()
    for cells, parent in [(headers, head)] + [(r, body) for r in rows]:
        row = nodes.row()
        for cell in cells:
            entry = nodes.entry()
            para = nodes.paragraph()
            para += cell if isinstance(cell, nodes.Node) else nodes.Text(str(cell))
            entry += para
            row += entry
        parent += row
    tgroup += head
    tgroup += body
    return nodes.table("", tgroup)


def code(text):
    return nodes.literal(text, text)


class VectorsDirective(SphinxDirective):
    required_arguments = 1
    has_content = False

    def run(self):
        which = self.arguments[0]
        try:
            if which == "results":
                rows = results()
                for _, path, _ in rows:
                    self.env.note_dependency(str(path))
                return [table(["Case"] + [h for _, h in RESULT_COLUMNS],
                              [[code(case)] + values for case, _, values in rows])]
            if which == "provenance":
                self.env.note_dependency(str(VECTORS / "MANIFEST.txt"))
                return [table(["Repository", "Commit"],
                              [[repo, code(commit)] for repo, commit in provenance()])]
        except (OSError, VectorError) as e:
            raise self.error(str(e))
        raise self.error(f"unknown table {which!r}: results or provenance")


def setup(app):
    app.add_directive("vgacap-tbv1-vectors", VectorsDirective)
    return {"parallel_read_safe": True, "parallel_write_safe": True}
