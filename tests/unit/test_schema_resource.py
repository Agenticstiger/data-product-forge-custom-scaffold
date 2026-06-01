"""Unit tests for the shipped customScaffold JSON-Schema resource (A1)."""

from __future__ import annotations

from jsonschema import Draft7Validator

from data_product_forge_custom_scaffold.validation import (
    CUSTOM_SCAFFOLD_SCHEMA,
    load_schema,
)


def test_schema_file_is_shipped_and_loadable() -> None:
    schema = load_schema()
    assert isinstance(schema, dict)
    assert schema["title"] == "extensions.customScaffold"
    assert schema["$schema"].startswith("http://json-schema.org/draft-07")


def test_constant_matches_loaded_file() -> None:
    # Single source of truth: the module constant IS the loaded file.
    assert CUSTOM_SCAFFOLD_SCHEMA == load_schema()


def test_schema_is_valid_draft7() -> None:
    # Meta-validate: the shipped schema is itself a valid draft-07 schema.
    Draft7Validator.check_schema(load_schema())


def test_required_constraints_preserved() -> None:
    schema = load_schema()
    assert schema["required"] == ["libraries", "patterns"]
    assert schema["additionalProperties"] is False
    src = schema["properties"]["libraries"]["items"]["properties"]["source"]
    assert src["properties"]["kind"]["enum"] == ["path", "git", "entrypoint"]
