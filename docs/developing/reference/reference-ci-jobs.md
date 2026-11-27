---
type: reference
owner: vgacap maintainers
reader: someone with a vgacap checkout whose pull request a CI job failed
review: 2027-05-27
---

# Continuous integration jobs

This page lists the jobs of vgacap's GitHub Actions workflow, [`ci.yml`](../../../.github/workflows/ci.yml), with their steps and the commits they pin. It is for someone reading a CI failure or changing what CI covers. Why each layer of tests exists is on [The test layers of vgacap](../explanation-testing.md).

The tables are made from `ci.yml` when this site is built. A job's table gives its id, its name in the Actions log and its runner, then its steps in order. A step is shown by its name, or else by its action or the first line of its command.

## Jobs

```{vgacap-ci-jobs}
```

## Pinned versions and settings

Variable is the environment variable, Value what the workflow sets it to, and Set for the jobs or step that see it.

```{vgacap-ci-env}
```
