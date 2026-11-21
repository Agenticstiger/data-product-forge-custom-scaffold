"""Hermetic docs-accuracy guards.

Cheap, network-free pins for two onboarding-path defects that had drifted from
the code: a 404 clone URL (wrong GitHub org) and a Python-version prerequisite
below the real ``requires-python``. A regression in either fails here rather
than stranding a first-run user.
"""

from __future__ import annotations

import inspect
import re
from pathlib import Path
from typing import List

import pytest

import data_product_forge_custom_scaffold as pkg

ROOT = Path(__file__).resolve().parent.parent.parent
DOCS = sorted((ROOT / "docs").rglob("*.md")) + [ROOT / "README.md"]
DOC_FILES = [p for p in DOCS if p.is_file()]


def test_reproducibility_feature_is_documented():
    # The headline reproducibility feature (lockfile / --pin / --update) must be
    # discoverable in the docs — a feature shipped invisible to users is not
    # world-class. Guards against the docs silently lagging the code.
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    for token in ("--update", "--pin", "fluid-scaffold.lock"):
        assert token in readme, f"README must document {token!r}"

    walkthrough = ROOT / "docs" / "walkthrough" / "reproducible-updates.md"
    assert walkthrough.is_file(), "the reproducibility/update walkthrough must exist"
    text = walkthrough.read_text(encoding="utf-8")
    for token in ("--update", "--pin", "3-way", "fluid-scaffold.lock"):
        assert token in text, f"the reproducibility walkthrough must cover {token!r}"


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


@pytest.mark.parametrize("doc", DOC_FILES, ids=lambda p: p.name)
def test_correct_command_name(doc: Path):
    # The command is registered top-level as `fluid custom-scaffold` (the entry
    # point name `generate-custom-scaffold` is NOT the invocation). Docs must not
    # advertise `fluid generate custom-scaffold`, which does not exist.
    text = doc.read_text(encoding="utf-8")
    assert "generate custom-scaffold" not in text, (
        f"{doc.name} says `fluid generate custom-scaffold` — the real command is "
        "`fluid custom-scaffold` (verified: the former does not exist)"
    )


# ── the library entry point: documented, and the docs track the signature ────

LIBRARY_REFERENCE = ROOT / "docs" / "reference" / "library-api.md"


def _signature_names() -> List[str]:
    """Parameter names of ``generate``, with a ``*`` marking where keyword-only starts."""
    names: List[str] = []
    seen_keyword_only = False
    for name, param in inspect.signature(pkg.generate).parameters.items():
        if param.kind is inspect.Parameter.KEYWORD_ONLY and not seen_keyword_only:
            names.append("*")
            seen_keyword_only = True
        names.append(name)
    return names


def _documented_names(text: str) -> List[str]:
    """Parameter names in the ``def generate(...) -> GenerationResult`` block of *text*."""
    match = re.search(r"def generate\((?P<params>.*?)\)\s*->\s*GenerationResult", text, re.DOTALL)
    assert match, "no `def generate(...) -> GenerationResult` signature block found"
    parts: List[str] = []
    depth, current = 0, ""
    for ch in match.group("params"):
        depth += ch in "[("
        depth -= ch in "])"
        if ch == "," and depth == 0:
            parts.append(current)
            current = ""
        else:
            current += ch
    parts.append(current)
    return [
        "*" if part.strip() == "*" else re.split(r"[:=\s]", part.strip(), maxsplit=1)[0]
        for part in parts
        if part.strip()
    ]


def test_library_api_is_documented_and_discoverable():
    # generate() is the public entry point other tools build on; shipping it without
    # a reference page (or a README pointer to it) would leave it undiscoverable.
    assert LIBRARY_REFERENCE.is_file(), "the library API reference page must exist"
    text = LIBRARY_REFERENCE.read_text(encoding="utf-8")
    for name in (
        "GenerationResult",
        "GeneratedFile",
        "EngineError",
        "ContractError",
        "BundleError",
        "PlanError",
        "UnsafePathError",
        "ApplyError",
    ):
        assert name in text, f"the reference must cover {name}"
        assert hasattr(pkg, name), f"{name} is documented but not exported"

    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "docs/reference/library-api.md" in readme
    assert "library-api.md" in (ROOT / "docs" / "README.md").read_text(encoding="utf-8")


@pytest.mark.parametrize("doc", [ROOT / "README.md", LIBRARY_REFERENCE], ids=lambda p: p.name)
def test_documented_signature_matches_the_code(doc: Path):
    assert _documented_names(doc.read_text(encoding="utf-8")) == _signature_names()


def test_every_parameter_has_a_row_in_the_reference():
    text = LIBRARY_REFERENCE.read_text(encoding="utf-8")
    for name in inspect.signature(pkg.generate).parameters:
        assert f"| `{name}` |" in text, f"parameter {name!r} has no row in the parameters table"
