"""Guard the honesty of the contract schema's unenforced pattern fields.

`patterns[].when` and `patterns[].environments` are accepted by the schema but
NOT acted on by this engine version. They must be documented as reserved so a
bundle/contract author doesn't write them expecting behaviour and get a silent
no-op. `variables` IS consumed (merged into the render context) and must not be
mislabelled reserved.
"""

from __future__ import annotations

import json
from pathlib import Path

import data_product_forge_custom_scaffold as pkg
from data_product_forge_custom_scaffold.manifest import PatternEntry

SCHEMA = Path(pkg.__file__).resolve().parent / "schemas" / "custom-scaffold.v1.json"


def _pattern_properties() -> dict:
    schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
    return schema["properties"]["patterns"]["items"]["properties"]


def test_reserved_fields_documented_as_not_enforced():
    props = _pattern_properties()
    for field_name in ("when", "environments"):
        desc = props[field_name].get("description", "")
        assert "RESERVED" in desc, f"{field_name!r} must be marked RESERVED"
        assert "NOT" in desc, f"{field_name!r} must say it is NOT acted on"


def test_consumed_variables_field_not_mislabelled_reserved():
    desc = _pattern_properties()["variables"].get("description", "")
    assert desc, "variables should carry a description"
    assert "RESERVED" not in desc, "variables IS consumed — must not be marked reserved"


def test_pattern_entry_docstring_marks_field_status():
    doc = (PatternEntry.__doc__ or "").lower()
    # supported_ci_systems stays advisory (no target CI-system to gate on);
    # variables_schema + supported_product_types are now ENFORCED.
    assert "advisory" in doc, "supported_ci_systems must be documented as advisory"
    assert (
        "enforced" in doc
    ), "variables_schema / supported_product_types must be documented enforced"
