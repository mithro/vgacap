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

## Running the sanitizers job locally

The `sanitizers` job builds only `test_tbv1` and `test_centrev1`, with `-fsanitize=address,undefined -fno-sanitize-recover=all`, into its own build directory. In a checkout, these commands do the same:

```sh
cmake -S . -B build-san -DCMAKE_C_FLAGS="-fsanitize=address,undefined -fno-sanitize-recover=all -fno-omit-frame-pointer"
cmake --build build-san -j --target test_tbv1 test_centrev1
ctest --test-dir build-san --output-on-failure -R '^test_(tbv1|centrev1)$'
```

A sanitizer report stops the test, and the report names the file and line of the bad access.

## Pinned versions and settings

Variable is the environment variable, Value what the workflow sets it to, and Set for the jobs or step that see it.

```{vgacap-ci-env}
```
