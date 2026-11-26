---
type: reference
owner: vgacap maintainers
reader: someone with a vgacap checkout whose page the docs build refused
review: 2027-05-27
---

# Page rules the build enforces

This page lists the page rules and sidebar checks that the vgacap docs build makes, with their limits and fixed sets. It is for someone reading a build warning that names a rule. What each rule means is on the [docs.fpgas.online rules](https://docs.fpgas.online/en/latest/developer/about-these-docs/page-contents.html), whose numbers these are.

The tables are made from [`docs/_ext/vgacap_rules.py`](../../_ext/vgacap_rules.py) and [`docs/_ext/vgacap_landing.py`](../../_ext/vgacap_landing.py) when this site is built. A rule whose Result is `fails` stops the build, which runs with `-W`. A rule whose Result is `logged` prints a line and lets the build go on.

## Checks

Rule is the rule's number, or `pending` for a check of the pending list. What the build catches is the case that sets it off, and Message the start of the warning, with … for a part that varies.

```{vgacap-page-rules}
```

## Sidebar checks

The sidebar is made from the landing pages' lines, and a page's one line is its place in it. Each of these checks fails the build.

```{vgacap-sidebar-checks}
```

## Limits

Limit names the limit, Value its number, and Counted in what it counts.

```{vgacap-page-limits}
```

## Banned words and phrases

Each of these fails the build in prose, in any case. Code, links and markup are not prose.

```{vgacap-banned-words}
```

## Pages on the pending list

A page listed in `vgacap_rules_pending` in [`docs/conf.py`](../../conf.py) has its rule failures logged, not failed. A listed page that passes every rule fails the build, so the list only shrinks, and so does an entry that is not a page.
