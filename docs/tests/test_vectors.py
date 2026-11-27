# SPDX-License-Identifier: Apache-2.0
"""docs/_ext/vgacap_vectors.py: the tb-v1 vector tables come from the vectors."""
from __future__ import annotations

import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "_ext"))
import vgacap_vectors  # noqa: E402


def test_every_case_directory_is_a_row():
    rows = vgacap_vectors.results()
    cases = sorted(p.name for p in vgacap_vectors.VECTORS.iterdir() if p.is_dir())
    assert [case for case, _, _ in rows] == cases
    status = {case: values[0] for case, _, values in rows}
    assert status["nosync"] == "no-sync" and status["bars640"] == "ok"


def test_provenance_names_both_commits():
    found = dict(vgacap_vectors.provenance())
    assert set(found) == {"tinytapeout-vga-videos", "vgacap"}
    assert all(len(commit) == 40 for commit in found.values())


def test_a_frames_txt_without_a_key_is_an_error(tmp_path):
    (tmp_path / "case").mkdir()
    (tmp_path / "case" / "frames.txt").write_text("status: ok\ndistinct:\n")
    with pytest.raises(vgacap_vectors.VectorError, match="no sampler_status"):
        vgacap_vectors.results(tmp_path)


def test_a_manifest_without_provenance_is_an_error(tmp_path):
    (tmp_path / "MANIFEST.txt").write_text("generator: abc\n")
    with pytest.raises(vgacap_vectors.VectorError, match="no vgacap line"):
        vgacap_vectors.provenance(tmp_path)
