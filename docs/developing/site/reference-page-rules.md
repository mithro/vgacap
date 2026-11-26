---
type: reference
owner: vgacap maintainers
reader: someone with a vgacap checkout whose page the docs build refused
review: 2027-05-27
---

# Page rules the build enforces

This page lists the page rules that the vgacap docs build checks, with their limits and fixed sets. It is for someone reading a build warning that names a rule. What each rule means is on the [docs.fpgas.online rules](https://docs.fpgas.online/en/latest/developer/about-these-docs/page-contents.html), whose numbers these are.

The tables are made from [`docs/_ext/vgacap_rules.py`](../../_ext/vgacap_rules.py) when this site is built. A rule whose Result is `fails` stops the build, which runs with `-W`. A rule whose Result is `logged` prints a line and lets the build go on.

## Checks

Rule is the rule's number, and What the build catches is the case that sets it off.

```{vgacap-page-rules}
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

A page listed in `vgacap_rules_pending` in [`docs/conf.py`](../../conf.py) has its failures logged, not failed. A listed page that passes every rule fails the build, so the list only shrinks.
