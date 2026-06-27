"""Hermetic docs-accuracy guards.

Cheap, network-free pins for two onboarding-path defects that had drifted from
the code: a 404 clone URL (wrong GitHub org) and a Python-version prerequisite
below the real ``requires-python``. A regression in either fails here rather
than stranding a first-run user.
"""

from __future__ import annotations

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent.parent
DOCS = sorted((ROOT / "docs").rglob("*.md")) + [ROOT / "README.md"]
DOC_FILES = [p for p in DOCS if p.is_file()]


@pytest.mark.parametrize("doc", DOC_FILES, ids=lambda p: p.name)
def test_no_stale_github_org(doc: Path):
    # The canonical repo is github.com/Agenticstiger/...; the old fluid-build
    # org 404s and would break the documented `git clone`.
    text = doc.read_text(encoding="utf-8")
    assert "github.com/fluid-build" not in text, f"{doc.name} references the 404 fluid-build org"


@pytest.mark.parametrize("doc", DOC_FILES, ids=lambda p: p.name)
def test_no_python_version_below_requires_python(doc: Path):
    # requires-python is >=3.10 (pyproject.toml); docs must not advertise 3.9,
    # which steers a 3.9 user into a guaranteed pip resolution failure.
    text = doc.read_text(encoding="utf-8")
    for stale in ("Python 3.9", ">=3.9", ">= 3.9"):
        assert stale not in text, f"{doc.name} advertises {stale!r} below requires-python >=3.10"
