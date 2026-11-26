---
type: how-to
owner: vgacap maintainers
reader: someone with a vgacap checkout who changes a page of these docs
review: 2027-05-27
---

# How to build the documentation site

**You changed a page of these docs, and you want to build the site as Read the Docs does and look at it.**

This page builds the site from the Markdown files where they sit, checks every page against the build rules, and serves the result. It does not cover writing a page, which [How to add a page to the documentation](how-to-add-a-page.md) does. The rules the build checks are on [Page rules the build enforces](reference-page-rules.md).

## What you need

- [uv](https://docs.astral.sh/uv/), which fetches Python 3.13 and the `docs` dependency group of `uv.lock` on first use.
- A free local port for the web server; these steps use 8000.

## Steps

1. In the checkout, run `uv run --locked --python 3.13 --only-group docs pytest -q docs/tests` to test the build checks themselves.
2. In the checkout, run `uv run --locked --python 3.13 --only-group docs sphinx-build -W --keep-going -c docs . build/docs` to build the site into `build/docs`.
3. In the checkout, run `uv run --no-project python -m http.server 8000 --bind 127.0.0.1 --directory build/docs` to serve the site.
4. In a browser, open `http://127.0.0.1:8000/` to see the root landing page, then the page you changed.

## Check

Step 1 ends with a line of passed tests and no failures. Step 2 ends with these lines, and step 4 shows the page with its sidebar entry:

```text
build succeeded.

The HTML pages are in build/docs.
```

## If it fails

| Step | What you see | Likely cause | Fix |
|---|---|---|---|
| 1 or 2 | `The lockfile at uv.lock needs to be updated` | `pyproject.toml` changed without `uv.lock` | Run `uv lock`, then the step again |
| 2 | `WARNING: rule <number>: ...` | the page breaks a build rule | Fix the page as [Page rules the build enforces](reference-page-rules.md) says for that rule |
| 2 | `passes every rule: take it out of vgacap_rules_pending` | a page on the pending list now passes | Remove its line from `vgacap_rules_pending` in [`docs/conf.py`](../../conf.py) |
| 2 | `document isn't included in any toctree` | no landing page links to the new page | Add its line to the landing page above it |
| 2 | `link to <path> is outside the repository or not a file` | a link to a file that does not exist | Correct the link's path |
| 3 | `OSError: [Errno 98] Address already in use` | port 8000 is taken | Use another port in steps 3 and 4 |

## Next

- [How to add a page to the documentation](how-to-add-a-page.md)
- [Page rules the build enforces](reference-page-rules.md)
- [About these pages](explanation-about-these-docs.md)
