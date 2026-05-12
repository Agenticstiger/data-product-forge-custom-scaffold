"""Unit tests for the path resolver."""

from __future__ import annotations

from pathlib import Path

import pytest

from data_product_forge_custom_scaffold.resolvers import ResolutionError, resolve_bundle

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"


def test_path_resolver_happy_path() -> None:
    resolved = resolve_bundle({"kind": "path", "path": str(FIXTURES / "reference_bundle")})
    assert resolved.kind == "path"
    assert resolved.bundle_root == (FIXTURES / "reference_bundle").resolve()
    assert resolved.resolved_version == "local"


def test_path_resolver_missing_dir(tmp_path: Path) -> None:
    with pytest.raises(ResolutionError, match="not a directory"):
        resolve_bundle({"kind": "path", "path": str(tmp_path / "nope")})


def test_path_resolver_missing_manifest(tmp_path: Path) -> None:
    with pytest.raises(ResolutionError, match="missing fluid-scaffold.yaml"):
        resolve_bundle({"kind": "path", "path": str(tmp_path)})


def test_unknown_source_kind() -> None:
    with pytest.raises(ResolutionError, match="unknown source kind"):
        resolve_bundle({"kind": "ftp", "url": "ftp://nope"})


def test_missing_path_field_rejected() -> None:
    with pytest.raises(ResolutionError, match="missing required 'path'"):
        resolve_bundle({"kind": "path"})
