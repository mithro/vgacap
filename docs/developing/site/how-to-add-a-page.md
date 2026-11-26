---
type: how-to
owner: vgacap maintainers
reader: someone with a vgacap checkout who wants to add a page to these docs
review: 2027-05-27
---

# How to add a page to the documentation

**You want a new page on the vgacap site, beside the code it is about, that passes the build checks.**

This page covers a page's file, its front matter, its place in the sidebar and a drawing on it. What a page of each type must contain is on the [docs.fpgas.online rules](https://docs.fpgas.online/en/latest/developer/about-these-docs/page-contents.html), which this page does not restate. Building the site is on [How to build the documentation site](how-to-build-docs.md).

## What you need

- The page's type, one of tutorial, how-to, reference or explanation, chosen by [the structure rules](https://docs.fpgas.online/en/latest/developer/about-these-docs/structure.html).
- The page's reader, in one line.

## Steps

1. Beside the code the page is about, create a file named for its type, such as `how-to-<task>.md` or `reference-<thing>.md`.
2. At the top of the file, write front matter with `type`, `owner: vgacap maintainers`, `reader` and a `review` date six months on.
3. Under the front matter, write a `#` title of 65 characters or fewer that no other page has, then one to three opening sentences.
4. For a how-to, write the headings What you need, Steps, Check, If it fails and Next, in that order, with numbered steps.
5. For a drawing, write a Python generator beside the page that writes `<name>.svg` and `<name>-dark.svg` from one palette, and run it.
6. In the page, show the drawing with a `<picture>` element after a blank line, as [The test layers of vgacap](../explanation-testing.md) does.
7. On the landing page above the new page, add one line `- [Title](path.md): what it is.` to put it in the sidebar.

## Check

The build of [How to build the documentation site](how-to-build-docs.md) ends with `build succeeded.`, and the new page shows in the sidebar under its landing page. The drawing switches with the site's light and dark theme. The same file on GitHub shows the same text, with the drawing that fits the viewer's theme.

## If it fails

| Step | What you see | Likely cause | Fix |
|---|---|---|---|
| 2 | `rule 1.3: front matter is missing ...` | a key left out or empty | Add the key the message names |
| 3 | `rule 10.3: title ... is also the title of ...` | another page has the title | Choose a title that says what only this page covers |
| 4 | `rule 10.5` or `rule 10.6` | a how-to heading missing or added | Use exactly the five headings |
| 6 | `rule 10.9: drawing ... is not a light and dark pair` | an SVG shown with `![]()` or `<img>` alone | Show it with a `<picture>` element |
| 7 | `rule 10.10` | the landing page has more than seven lines | Group the lines under a child landing page |

## Next

- [Page rules the build enforces](reference-page-rules.md)
- [About these pages](explanation-about-these-docs.md)
- [How to build the documentation site](how-to-build-docs.md)
