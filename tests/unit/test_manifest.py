"""Unit tests for ``manifest`` — parsing fluid-scaffold.yaml."""

from __future__ import annotations

from pathlib import Path

import pytest

from data_product_forge_custom_scaffold.manifest import (
    BundleManifest,
    ManifestError,
    TemplateEntry,
)

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"


def test_parse_reference_bundle() -> None:
    m = BundleManifest.from_path(FIXTURES / "reference_bundle")
    assert m.bundle.name == "reference-bundle"
    assert m.bundle.version == "1.0.0"
    assert m.pattern_names() == ["basic"]

    p = m.get_pattern("basic")
    assert p is not None
    assert "metadata.owner.email" in p.required_contract_fields
    # Reference bundle has 2 templates (README + CI). Static files are
    # handled separately by the renderer, not declared in templates[].
    assert len(p.templates) == 2


def test_template_entry_from_dict_validates_required() -> None:
    with pytest.raises(ManifestError, match="missing required 'from'"):
        TemplateEntry.from_dict({"to": "x"})
    with pytest.raises(ManifestError, match="missing required 'to'"):
        TemplateEntry.from_dict({"from": "x"})


def test_unknown_api_version_rejected() -> None:
    with pytest.raises(ManifestError, match="unsupported apiVersion"):
        BundleManifest.from_dict(
            {
                "apiVersion": "wrong.version/v0",
                "bundle": {"name": "x"},
                "patterns": [{"name": "y"}],
            }
        )


def test_duplicate_pattern_names_rejected() -> None:
    with pytest.raises(ManifestError, match="must be unique"):
        BundleManifest.from_dict(
            {
                "apiVersion": "fluid.dev/custom-scaffold.v1",
                "bundle": {"name": "x"},
                "patterns": [{"name": "dup"}, {"name": "dup"}],
            }
        )


def test_missing_patterns_rejected() -> None:
    with pytest.raises(ManifestError, match="at least one pattern"):
        BundleManifest.from_dict(
            {
                "apiVersion": "fluid.dev/custom-scaffold.v1",
                "bundle": {"name": "x"},
                "patterns": [],
            }
        )
