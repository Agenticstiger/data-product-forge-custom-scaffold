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


def test_valid_git_refs_accepted() -> None:
    """Real-world git refs (tag, branch, SHA, slashed branch) must pass."""
    for ref in ("v1.2.3", "main", "release/2026.05", "abc1234", "0.9.0-rc1"):
        errors = _run(
            {
                "customScaffold": {
                    "libraries": [
                        {
                            "id": "ci",
                            "source": {"kind": "git", "url": "https://x", "ref": ref},
                        }
                    ],
                    "patterns": [{"use": "ci:basic"}],
                }
            }
        )
        assert errors == [], f"ref {ref!r} was rejected unexpectedly: {errors}"


def test_dangerous_ref_values_rejected() -> None:
    """Defense-in-depth: a ref shaped like a git flag must be rejected by
    the schema before reaching the resolver. Subprocess list-form already
    blocks argv injection, but the schema should refuse values that have
    no legitimate use as a git ref.
    """
    dangerous = (
        "--upload-pack=evil",  # flag-shaped
        "ref with spaces",  # whitespace
        "$(rm -rf /)",  # shell metacharacters
        ";cat /etc/passwd",  # injection-shaped
        "ref\nwith\nnewlines",  # control characters
        "ref\twith\ttabs",
    )
    for ref in dangerous:
        errors = _run(
            {
                "customScaffold": {
                    "libraries": [
                        {
                            "id": "ci",
                            "source": {"kind": "git", "url": "https://x", "ref": ref},
                        }
                    ],
                    "patterns": [{"use": "ci:basic"}],
                }
            }
        )
        assert any(
            "ref" in e for e in errors
        ), f"dangerous ref {ref!r} was accepted; expected rejection: {errors}"


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
