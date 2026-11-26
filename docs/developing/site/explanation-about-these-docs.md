---
type: explanation
owner: vgacap maintainers
reader: someone with a vgacap checkout who wonders why these pages are written as they are
review: 2027-05-27
---

# About these pages

This page explains why the vgacap documentation follows the docs.fpgas.online rules, and how the build and the review hold pages to them. It is for someone about to write or review a page here. It does not restate the rules, which live on docs.fpgas.online.

## One set of rules for two sites

vgacap captures Tiny Tapeout designs on boards that fpgas.online hosts, and its readers move between the two sites. So vgacap uses the rules that docs.fpgas.online wrote for itself, with the same rule numbers. [The structure of the documentation](https://docs.fpgas.online/en/latest/developer/about-these-docs/structure.html) gives the sections and the four page types. [What a page contains](https://docs.fpgas.online/en/latest/developer/about-these-docs/page-contents.html) gives each type's parts and the limits. The research behind them is on [References on writing documentation](https://docs.fpgas.online/en/latest/developer/about-these-docs/references.html).

## The files are the pages

A page here is a Markdown file in the repository, beside the code it is about. GitHub shows the file as it is, and the site is built from the same file, so the two never disagree. A component's `README.md` is its landing page, one line for each of its pages. A change that makes a page wrong carries the page's fix in the same pull request.

## What the build checks and what a person checks

A machine can decide some rules, such as a title's length, a banned word or a missing heading. The build checks those on every change and fails on them, as [Page rules the build enforces](reference-page-rules.md) lists. Where a table comes from a file in the repository, such as `ci.yml` or a command's options, the build makes the table from that file, so it cannot go stale. The rest is judged by people, as [Review and verification of a page](https://docs.fpgas.online/en/latest/developer/about-these-docs/review.html) describes: a reviewer who is not the author, and a run of every procedure as written.

## Drawings in two themes

The site has a light and a dark theme, and a drawing made on white is a bright slab on the dark one. So each drawing is made twice by a generator, from one palette, and shown with a `<picture>` element. GitHub picks the twin that fits the viewer's theme, and the build turns the element into the pair the site switches between. Each drawing is also shown to a reader who has not seen the page, who says what it shows, and a confusion is fixed in the drawing.
