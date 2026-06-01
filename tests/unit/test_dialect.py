"""Unit tests for ScaffoldDialect + dialect-aware validation / engine (C, B2)."""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from pathlib import Path
from typing import List

import pytest

from data_product_forge_custom_scaffold import (
    DEFAULT_DIALECT,
    Engine,
    ScaffoldDialect,
    make_schema_provider,
    make_validator,
)
from data_product_forge_custom_scaffold.manifest import BundleManifest, ManifestError

ACME = ScaffoldDialect(
    extension_key="acmeScaffold",
    manifest_api_versions=("acme.dev/scaffold.v1",),
    command_name="acme-scaffold",
    contract_default_path="contract.acme.yaml",
    display_name="acme scaffold",
    error_prefix="acme-scaffold",
    aggregate_plugin_name="acme-scaffold-engine",
)


def _valid_block() -> dict:
    return {
        "libraries": [{"id": "ci", "source": {"kind": "path", "path": "./bundle"}}],
        "patterns": [{"use": "ci:basic"}],
    }


# ── DEFAULT preserves the historical fluid values ────────────────────


def test_default_dialect_fields() -> None:
    assert DEFAULT_DIALECT.extension_key == "customScaffold"
    assert DEFAULT_DIALECT.manifest_api_versions == ("fluid.dev/custom-scaffold.v1",)
    assert DEFAULT_DIALECT.primary_api_version == "fluid.dev/custom-scaffold.v1"
    assert DEFAULT_DIALECT.manifest_filename == "fluid-scaffold.yaml"
    assert DEFAULT_DIALECT.command_name == "custom-scaffold"
    assert DEFAULT_DIALECT.contract_default_path == "contract.fluid.yaml"
    assert DEFAULT_DIALECT.aggregate_plugin_name == "custom-scaffold-engine"
    assert DEFAULT_DIALECT.schema_title == "extensions.customScaffold"


def test_dialect_is_frozen() -> None:
    with pytest.raises(FrozenInstanceError):
        DEFAULT_DIALECT.extension_key = "x"  # type: ignore[misc]


# ── make_validator binds to the dialect's extension key ──────────────


def test_branded_validator_reads_its_key_only() -> None:
    acme_validate = make_validator(ACME)
    errs: List[str] = []
    acme_validate({"acmeScaffold": _valid_block()}, errs)
    assert errs == []
    # The same block under customScaffold is invisible to the ACME validator.
    errs = []
    acme_validate({"customScaffold": _valid_block()}, errs)
    assert errs == []


def test_default_validator_ignores_branded_key() -> None:
    default_validate = make_validator(DEFAULT_DIALECT)
    errs: List[str] = []
    default_validate({"acmeScaffold": {"anything": True}}, errs)
    assert errs == []


def test_branded_validator_error_prefix() -> None:
    acme_validate = make_validator(ACME)
    errs: List[str] = []
    acme_validate(
        {
            "acmeScaffold": {
                "libraries": [{"id": "ci", "source": {}}],  # missing source.kind
                "patterns": [{"use": "nope:x"}],  # references unknown lib id
            }
        },
        errs,
    )
    assert errs
    assert all(e.startswith("extensions.acmeScaffold.") for e in errs), errs


# ── make_schema_provider rewrites the title to the dialect's key ─────


def test_schema_provider_titles() -> None:
    assert make_schema_provider(DEFAULT_DIALECT)(None)["title"] == "extensions.customScaffold"
    assert make_schema_provider(ACME)(None)["title"] == "extensions.acmeScaffold"


# ── manifest apiVersion accept/reject per dialect ────────────────────


def _manifest(api_version: str) -> dict:
    return {
        "apiVersion": api_version,
        "bundle": {"name": "b", "version": "1.0.0"},
        "patterns": [{"name": "basic", "templates": [{"from": "t.j2", "to": "t"}]}],
    }


def test_manifest_accepts_dialect_version_rejects_others() -> None:
    BundleManifest.from_dict(_manifest("acme.dev/scaffold.v1"), dialect=ACME)
    with pytest.raises(ManifestError, match="unsupported apiVersion"):
        BundleManifest.from_dict(_manifest("fluid.dev/custom-scaffold.v1"), dialect=ACME)
    # DEFAULT accepts fluid, rejects acme.
    BundleManifest.from_dict(_manifest("fluid.dev/custom-scaffold.v1"))
    with pytest.raises(ManifestError, match="unsupported apiVersion"):
        BundleManifest.from_dict(_manifest("acme.dev/scaffold.v1"))


def test_manifest_multi_version_dialect() -> None:
    multi = ScaffoldDialect(
        extension_key="acmeScaffold",
        manifest_api_versions=("acme.dev/scaffold.v1", "acme.dev/scaffold.v2"),
    )
    BundleManifest.from_dict(_manifest("acme.dev/scaffold.v1"), dialect=multi)
    BundleManifest.from_dict(_manifest("acme.dev/scaffold.v2"), dialect=multi)
    with pytest.raises(ManifestError, match="unsupported apiVersion"):
        BundleManifest.from_dict(_manifest("acme.dev/scaffold.v3"), dialect=multi)


# ── end-to-end engine run under a branded dialect ────────────────────


def _write_bundle(root: Path) -> Path:
    bundle = root / "bundle"
    (bundle / "templates").mkdir(parents=True)
    (bundle / "fluid-scaffold.yaml").write_text(
        "apiVersion: acme.dev/scaffold.v1\n"
        "bundle:\n  name: acme-bundle\n  version: 1.0.0\n"
        "patterns:\n"
        "  - name: basic\n"
        "    templates:\n"
        "      - from: templates/README.md.j2\n"
        "        to: README.md\n",
        encoding="utf-8",
    )
    (bundle / "templates" / "README.md.j2").write_text("# {{ product_name }}\n", encoding="utf-8")
    return bundle


def test_engine_round_trip_branded_dialect(tmp_path: Path) -> None:
    bundle = _write_bundle(tmp_path)
    contract = {
        "fluidVersion": "0.7.4",
        "kind": "DataProduct",
        "id": "acme-widget",
        "name": "ACME Widget",
        "metadata": {"owner": {"team": "platform", "email": "p@acme.dev"}},
        "extensions": {
            "acmeScaffold": {
                "libraries": [{"id": "ref", "source": {"kind": "path", "path": str(bundle)}}],
                "patterns": [{"use": "ref:basic"}],
            }
        },
    }
    out = tmp_path / "out"
    out.mkdir()
    engine = Engine(output_root=out, contract_dir=tmp_path, dialect=ACME)
    result = engine.run(contract)

    assert (out / "README.md").is_file()
    assert result.apply_result is not None
    assert result.apply_result.plugin == "acme-scaffold-engine"

    # A default-dialect engine sees no `customScaffold` block in this contract.
    default_engine = Engine(output_root=tmp_path / "out2", dialect=DEFAULT_DIALECT)
    assert default_engine.run(contract).actions == []
