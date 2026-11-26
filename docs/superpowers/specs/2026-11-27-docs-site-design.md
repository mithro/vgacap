# The vgacap documentation site: design

Working document for contributors; excluded from the built site.

## Goal

A complete documentation site for vgacap on Read the Docs, written to the
docs.fpgas.online rules:
[`fpgas.online-docs/docs/developer/about-these-docs/`](https://github.com/fpgas-online/fpgas.online-docs/tree/main/docs/developer/about-these-docs).
The rule numbers below (**1.1**, **3.2** ...) are those of its
[`page-contents.md`](https://github.com/fpgas-online/fpgas.online-docs/blob/main/docs/developer/about-these-docs/page-contents.md).
Every writer and reviewer reads all five of its pages first:
`structure.md`, `page-contents.md`, `review.md`, `references.md`,
`limit-sources.md`.

## Decisions (Tim, 2026-11-27)

- **The READMEs are the pages.** Each README is split into typed pages in
  place, beside it (`firmware/README.md`, `firmware/how-to-load.md`, ...).
  GitHub and the site show the same files. No copy under `docs/`.
- **Bench runs may flash** a Welland board (fpga-1, claimed through the
  fpgas-online broker), with the byte-exact backup and restore used for
  vgacap #11.
- **Merge when green**: a docs PR merges after a clean independent review
  and green CI and Read the Docs. Tim follows a procedure afterwards.
- **Diagrams where needed**: drawn by a Python generator as SVG (as
  `docs/diagrams/overview.py` is), never Mermaid; a light and a dark twin
  from one palette (**8.13**); reviewed blind (the reviewer sees only the
  picture and says what it shows); confusion is fixed with structure and
  short labels, never with added sentences.

## Readers and sections

Sections by audience (structure.md). A section exists only when a page sits
under it.

| Section | Reader | Holds |
|---|---|---|
| Capturing | someone with a Tiny Tapeout VGA design and a demo board, a Raspberry Pi 5, an FPGA or a simulator | the tutorial; how-tos for every capture source and consumer; explanations of how capture works |
| Reference | anyone looking something up | formats, protocol, command options, element properties, mode table, pin maps, exit codes and messages |
| Developing vgacap | someone with a checkout | building, testing, CI, the hardware-in-the-loop check, the docs themselves, where to make which change |

A heading holds at most seven entries (**5.9**). The sidebar is built from
the landing pages' list lines (`docs/_ext/vgacap_landing.py`): walked
breadth-first from the root, each page's parent is the first landing page
that reaches it, so a page has one place in the sidebar and a later line to
it, or a line back up, is a cross-link. A group larger than seven gets a
child landing page, never a caption.

## Page types and where each lives

Every page carries front matter (**1.3**):

```yaml
---
type: how-to            # tutorial | how-to | reference | explanation | landing
owner: vgacap maintainers
reader: someone with a demo board and a Tiny Tapeout VGA design
review: 2027-05-27      # six months on
---
```

GitHub shows front matter as a small table above the page; that is accepted.

- A component's `README.md` becomes its **landing page** (**4.7**): one
  line for each page of that component, nothing else. A README is the
  index page of its directory, which is the page structure.md gives the
  landing type to.
- The top `README.md` becomes an **explanation** (**4.2**), "vgacap" with
  the overview figure: what vgacap is, its capture sources and its
  consumers, in prose. It is the page GitHub shows first. The lines to each
  section belong to `docs/index.md`, not to it.
- `docs/index.md` is the site's root landing page (`root_doc`).
  `docs/<section>/index.md` are the section landings. No page holds a
  toctree directive, and the build fails one that does: it makes each
  landing page's toctree from its lines.
- New pages sit beside the code they are about, named by type:
  `tutorial-*.md`, `how-to-*.md`, `reference-*.md`, `explanation-*.md`.
  Existing reference files keep their names (`firmware/PROTOCOL.md`,
  `docs/frames-format.md`, `docs/trace-format.md`).

## Content rules that change today's text

- **7.2**: no future, unproven or unused content on a reader page. Every
  such sentence in today's READMEs ("waiting for a Pi 5 wired to a Tiny
  Tapeout board", "being added", "simulator only") becomes a GitHub issue,
  and the page says what is true now, or the page is held back.
- **2.3**: no dated record of work on a page; measurements link to the
  dated result in tt-vga-capture instead.
- **6.1-6.3**: banned words and phrases; **5.2** 25-word sentences;
  **5.3** 3-5 sentence paragraphs; **5.4** 2-7 item lists; **5.7** 15
  links; **3.2** the fixed how-to headings: What you need, Steps, Check,
  If it fails, Next.
- **4.4**: reference generated from its source where a data file exists:
  the `ttcap` options from its argparse parser, the mode table from
  `src/frame/modes.c`, at build time, so it cannot go stale (**10.15**).
- The names agreed for the READMEs stay: "the stock firmware", "the vgacap
  firmware", "the fpgas.online bridge (`fpgas-tt`)", "the Welland bench",
  "demo board with an FPGA breakout" / "... ASIC breakout".

## Diagrams

At least these, each light and dark, each in the page that needs it:

| Diagram | Page |
|---|---|
| the overview (exists; gains a dark twin) | top README |
| the stream: header and chunks, as bytes | stream format reference |
| the demo board data path: pins, PIO, DMA ring, USB, host | explanation of capture on the demo board |
| a run-length line: samples against runs | explanation of run-length capture |
| VGA timing: sync pulses, porches, active area | explanation of how frames are rebuilt |
| the firmware protocol: a capture's command and reply sequence | protocol reference |
| the FPGA raw framer: its ports and FIFO | `rtl/` explanation |
| the Raspberry Pi 5 wiring: TT board `uo_out` to the Pi's GPIO | Pi 5 capture how-to |

The tutorial shows its success state first (**4.1**): a real captured
frame. Terminal output is text (**8.14**). The demo board is pictured with
the KiCad render already in `docs/diagrams/images/`.

## Build checks

`docs/_ext/vgacap_rules.py`, with tests in `docs/tests/`, enforces what a
machine can decide. It fails a page on:

| Rule | Check |
|---|---|
| **10.14** | front matter with type, owner, reader and a `YYYY-MM-DD` review date; the type one of **1.1** |
| **10.1** | a banned word (**6.1**) or a forbidden label (**6.3**) outside code |
| **10.2**, **10.3** | a title over 65 characters; a title shared with another page |
| **10.4** | a step sentence over 25 words, a code span counting as one word |
| **10.5**, **10.6**, **3.2** | a how-to heading missing, extra, out of order, or with sub-headings |
| **10.8** | an image without alt text, alt text over 150 characters (**5.10**), or an image left in raw HTML |
| **10.9** | a drawing (a local SVG) not a light and dark pair; a switched image without its twin; a twin shown in every theme |
| **10.10** | more than seven sidebar entries under one page, the root included |
| **10.11** | more than five heading levels |
| **10.12**, **1.6** | a page of links alone; a page of two sentences or fewer, a table row or list item counting as one |

It logs, without failing, more than 15 links (**10.19**), more than two
callouts (**10.20**) and an expired review date (**10.21**). Broken
internal links already fail (**10.16**).

A drawing appears on GitHub and on the site as one `<picture>` element
with a `prefers-color-scheme: dark` source; the build turns it into the
theme-switched pair the site shows.

## Review and merge (review.md)

Each content PR:

1. **Writer** (a sub-agent in its own worktree and branch) writes the pages
   and their diagrams, commits as it goes, pushes, opens the PR. The body
   names each page's reader and type, the diagrams, and the issues filed.
2. **Bench run** by a different agent: every procedure executed as written,
   on fpga-1 for a bench page (claimed through the broker, released after,
   board restored), in a clean checkout for a software page. Every stop is
   recorded and answered on the page.
3. **Independent review** by a third agent, given the PR, the rendered pages
   as screenshots (desktop and phone width, light and dark), and the code:
   a use pass (one row per step) and an audit pass (each fact against the
   code or a measurement), hunting for shortcuts. Findings cite rule
   numbers. Repeat until clean.
4. Merge when the review is clean and CI and Read the Docs are green.

## Order of work

1. Infrastructure (this PR): `docs/index.md`, the section landings, the
   build checks, the landing-page sidebar and the `<picture>` handling. The
   existing pages stay on `vgacap_rules_pending` until their content PR.
2. Content, one PR per component tree, in parallel:
   - **A** `python/ttcap/`: the tutorial, capture how-tos, the CLI
     reference, explanations of PIO capture and run-length capture;
   - **B** `firmware/` and `mpy/`: load and build how-tos (including the
     flashed module firmware), the protocol reference, explanations;
   - **C** the stream, `src/`, `src/tools/`, `gst/`: the stream format
     reference, frame rebuilding, the C tools, the GStreamer elements;
   - **D** `capture-rp1/`, `rtl/`, simulation traces;
   - **E** developing: building, testing, CI, `tools/` (the HIL check),
     the docs rules and how to add a page.
3. A final whole-site review across all pages for duplication (one source
   per fact), naming and the sidebar.
