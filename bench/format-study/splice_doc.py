# /// script
# requires-python = ">=3.11"
# ///
# SPDX-License-Identifier: Apache-2.0
"""Puts analyze.py's tables into the study document, unedited but for their
headings, which go one level deeper:

    uv run --script splice_doc.py OUT/results/tables.md DOC

Replaces everything between the DOC's two marker lines (BEGIN and END
below) with the tables. Run by run_all.sh's analyze stage.
"""
import sys

BEGIN = "<!-- analyze.py tables: begin (bench/format-study/splice_doc.py writes this part) -->"
END = "<!-- analyze.py tables: end -->"
tables, doc = sys.argv[1], sys.argv[2]
text = open(doc).read()
if text.count(BEGIN) != 1 or text.count(END) != 1:
    raise SystemExit(f"{doc}: needs exactly one of each marker line")
head, rest = text.split(BEGIN + "\n", 1)
_, tail = rest.split(END, 1)
body = "\n".join("#" + line if line.startswith("#") else line for line in open(tables).read().splitlines())
open(doc, "w").write(head + BEGIN + "\n\n" + body.strip() + "\n\n" + END + tail)
