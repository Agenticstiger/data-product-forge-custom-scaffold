"""Unit tests for ``validation`` — extensions.customScaffold validator."""

from __future__ import annotations

from typing import List

from data_product_forge_custom_scaffold.validation import validate


def _run(extensions: dict) -> List[str]:
    errors: List[str] = []
    validate(extensions, errors)
    return errors


def test_no_block_is_no_op() -> None:
    assert _run({}) == []
    assert _run({"otherPlugin": {}}) == []


def test_valid_minimal_block() -> None:
    errors = _run(
        {
            "customScaffold": {
                "libraries": [{"id": "ci", "source": {"kind": "path", "path": "./bundle"}}],
                "patterns": [{"use": "ci:basic"}],
            }
        }
    )
    assert errors == []


def test_missing_required_fields_caught() -> None:
    errors = _run({"customScaffold": {"libraries": []}})
    assert any("patterns" in e for e in errors), errors


def test_invalid_source_kind_rejected() -> None:
    errors = _run(
        {
            "customScaffold": {
                "libraries": [{"id": "x", "source": {"kind": "ftp"}}],
                "patterns": [{"use": "x:y"}],
            }
        }
    )
    assert any("kind" in e for e in errors)


def test_invalid_use_pattern_rejected() -> None:
    errors = _run(
        {
            "customScaffold": {
                "libraries": [{"id": "ci", "source": {"kind": "path", "path": "./x"}}],
                "patterns": [{"use": "no-colon-here"}],
            }
        }
    )
    assert any("use" in e for e in errors)


def test_unknown_library_ref_in_use_rejected() -> None:
    errors = _run(
        {
            "customScaffold": {
                "libraries": [{"id": "ci", "source": {"kind": "path", "path": "./x"}}],
                "patterns": [{"use": "missing-lib:basic"}],
            }
        }
    )
    assert any("missing-lib" in e for e in errors)


def test_invalid_secret_ref_rejected() -> None:
    errors = _run(
        {
            "customScaffold": {
                "libraries": [
                    {
                        "id": "ci",
                        "source": {
                            "kind": "git",
                            "url": "https://x",
                            "ref": "v1",
                            "auth": {"secret_ref": "has spaces"},
                        },
                    }
                ],
                "patterns": [{"use": "ci:basic"}],
            }
        }
    )
    assert any("secret_ref" in e for e in errors)
