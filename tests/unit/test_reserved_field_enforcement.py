"""Enforcement of the (formerly reserved) `variables` JSON Schema + the
`supportedProductTypes` gate, at the TemplatedCustomScaffold.plan chokepoint."""

from __future__ import annotations

import pytest
import yaml
from fluid_sdk import PluginError

from data_product_forge_custom_scaffold.manifest import ManifestError
from data_product_forge_custom_scaffold.templated import TemplatedCustomScaffold


def _make_bundle(tmp_path, *, variables=None, supported_product_types=None):
    bundle = tmp_path / "bundle"
    (bundle / "templates").mkdir(parents=True)
    (bundle / "templates" / "out.txt.j2").write_text("ok\n", encoding="utf-8")
    pattern = {
        "name": "p",
        "templates": [{"from": "templates/out.txt.j2", "to": "out.txt"}],
    }
    if variables is not None:
        pattern["variables"] = variables
    if supported_product_types is not None:
        pattern["supportedProductTypes"] = supported_product_types
    manifest = {
        "apiVersion": "fluid.dev/custom-scaffold.v1",
        "bundle": {"name": "t", "version": "1.0.0"},
        "patterns": [pattern],
    }
    (bundle / "fluid-scaffold.yaml").write_text(yaml.safe_dump(manifest), encoding="utf-8")
    return bundle


_SCHEMA = {
    "type": "object",
    "properties": {"region": {"type": "string"}},
    "required": ["region"],
    "additionalProperties": False,
}


# ── variables JSON Schema ────────────────────────────────────────────


def test_valid_variables_pass(tmp_path):
    b = _make_bundle(tmp_path, variables=_SCHEMA)
    actions = TemplatedCustomScaffold(
        bundle_root=b, pattern_name="p", variables={"region": "us-east-1"}
    ).plan({})
    assert actions  # rendered


def test_wrong_type_variable_rejected(tmp_path):
    b = _make_bundle(tmp_path, variables=_SCHEMA)
    with pytest.raises(PluginError, match="region"):
        TemplatedCustomScaffold(bundle_root=b, pattern_name="p", variables={"region": 123}).plan({})


def test_missing_required_variable_rejected(tmp_path):
    b = _make_bundle(tmp_path, variables=_SCHEMA)
    with pytest.raises(PluginError, match="region"):
        TemplatedCustomScaffold(bundle_root=b, pattern_name="p", variables={}).plan({})


def test_unknown_variable_rejected(tmp_path):
    b = _make_bundle(tmp_path, variables=_SCHEMA)
    with pytest.raises(PluginError):
        TemplatedCustomScaffold(
            bundle_root=b, pattern_name="p", variables={"region": "x", "bogus": 1}
        ).plan({})


def test_no_variables_schema_is_a_noop(tmp_path):
    b = _make_bundle(tmp_path)  # no `variables` declared
    actions = TemplatedCustomScaffold(
        bundle_root=b, pattern_name="p", variables={"anything": 1}
    ).plan({})
    assert actions  # unconstrained → fine


def test_malformed_bundle_schema_is_a_manifest_error(tmp_path):
    # `type: not-a-type` is an invalid Draft-07 schema — the bundle AUTHOR's fault.
    b = _make_bundle(tmp_path, variables={"type": "not-a-type"})
    with pytest.raises(ManifestError):
        TemplatedCustomScaffold(bundle_root=b, pattern_name="p", variables={}).plan({})


# ── supportedProductTypes gate ───────────────────────────────────────


def test_matching_product_type_passes(tmp_path):
    b = _make_bundle(tmp_path, supported_product_types=["SDP", "ADP"])
    actions = TemplatedCustomScaffold(bundle_root=b, pattern_name="p").plan(
        {"metadata": {"productType": "SDP"}}
    )
    assert actions


def test_unsupported_product_type_rejected(tmp_path):
    b = _make_bundle(tmp_path, supported_product_types=["SDP", "ADP"])
    with pytest.raises(PluginError, match="product type"):
        TemplatedCustomScaffold(bundle_root=b, pattern_name="p").plan(
            {"metadata": {"productType": "CDP"}}
        )


def test_contract_without_product_type_is_not_gated(tmp_path):
    b = _make_bundle(tmp_path, supported_product_types=["SDP"])
    actions = TemplatedCustomScaffold(bundle_root=b, pattern_name="p").plan({})
    assert actions  # nothing to gate on


def test_no_supported_product_types_is_a_noop(tmp_path):
    b = _make_bundle(tmp_path)
    actions = TemplatedCustomScaffold(bundle_root=b, pattern_name="p").plan(
        {"metadata": {"productType": "CDP"}}
    )
    assert actions
